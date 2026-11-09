#!/usr/bin/env python3
"""Refusal-fallback proxy for the grey translation workflow.

Sits between the translator and the gemini gateway. The translator keeps
talking to one OpenAI-compatible address (this proxy, default
``http://127.0.0.1:4998/v1``, model ``gemini-anon``); the proxy forwards
everything to the primary gateway (default ``127.0.0.1:4995/v1``) and
inspects exactly one shape of reply. When Gemini answers with its standalone
policy refusal ("I can help with many kinds of requests, but it appears this
one would go against my gemini.google/policy-guidelines...", or the
structured-refusal / content-filter shapes ``book_maker.provider_refusal``
already treats the same way), that one request — that chunk — is re-issued
to the Venice guest bridge (default ``127.0.0.1:4996/v1``, model alias
``venice-anon``) and Venice's reply is returned instead. Nothing else is
ever rerouted, so Venice is billed only for chunks Gemini refused.

The refusal signature is not duplicated here: the classifier comes from the
repository's own ``book_maker.provider_refusal``, so proxy behaviour and the
repository's terminal-``ProviderRefusal`` semantics cannot drift apart.
Retryable provider-error boilerplate ("Sorry, something went wrong...")
is deliberately left to the repository's own retry loop.

When the Venice bridge is not reachable, the proxy starts it in its gateway
directory (``python -X utf8 server.py --port ...``, never editing any
AI-BRIDGE file) and polls ``/health`` until the bridge answers as
``venice-guest-direct-https-bridge`` with ``ok: true``. That identity check
is why a different service squatting on the Venice port is reported instead
of trusted. If Venice still cannot serve, the original Gemini refusal is
returned unchanged and the run behaves exactly as it would without this
proxy: the refusal stays the repository's terminal ``ProviderRefusal``,
progress is saved, and the operator can resume.

Because Venice's guest allowance is expensive and resets on a rolling
window, a cap bounds how many refusals may be rerouted within one window
(default: 8 reroutes per 24 h). A book that upsets Gemini on every chunk
therefore stops falling back long before it stops the run.

Streamed requests (``stream: true``) are never classified: the repository's
translators do not stream, so streamed traffic is forwarded byte-for-byte
as a future-proofing pass-through.

The proxy shares no state with ``make_book.py``. The translator sees either
the reply it always saw, or a well-formed Venice reply, plus an
``X-BBM-Route`` header naming the path taken.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PROXY_SERVICE = "bbm-refusal-fallback-proxy"
VENICE_SERVICE = "venice-guest-direct-https-bridge"
VENICE_MODEL_ALIAS = "venice-anon"
CHAT_PATH = "/v1/chat/completions"
HEALTH_PATHS = ("/", "/health")

# Windows process creation flags for a detached, windowless child, the way
# AI-BRIDGE's own launcher detaches its gateways.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

BLOCKING_FINISH_REASONS = ("content_filter", "safety", "blocked")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_classifier():
    """The repository's own refusal classifier, or nothing to serve with.

    Starting without the classifier would mean deciding reroutes by guessing,
    exactly the drift this design exists to avoid, so a proxy launched from
    outside the repository refuses to serve at all and the launcher reports
    the fallback as down.
    """
    try:
        from book_maker.provider_refusal import classify_provider_reply
    except ImportError as error:  # pragma: no cover - depends on launch site
        sys.stderr.write(
            f"refusal fallback proxy: cannot import book_maker.provider_refusal: {error}\n"
            f"The proxy decides reroutes with this repository's classifier; refusing "
            f"to start so the launcher reports the fallback as unavailable.\n"
        )
        raise SystemExit(2)
    return classify_provider_reply


def _origin(openai_base_url):
    """``http://host:port/v1`` -> ``http://host:port`` (health lives at root)."""
    base = (openai_base_url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base.rstrip("/")


def _preview(text, limit=120):
    squeezed = " ".join(str(text or "").split())
    return squeezed[:limit] + ("..." if len(squeezed) > limit else "")


def _upstream_headers(client_headers):
    """The few headers worth carrying; Content-Length is urllib's problem."""
    forwarded = {
        "Content-Type": client_headers.get("Content-Type") or "application/json",
        "Accept": client_headers.get("Accept") or "application/json",
        "User-Agent": "bbm-refusal-fallback/1.0",
    }
    authorization = client_headers.get("Authorization")
    if authorization:
        # The local gateways ignore keys; pass what the client authenticated
        # with rather than inventing one.
        forwarded["Authorization"] = authorization
    return forwarded


class FallbackRouter:
    """Shared decisions and accounting for every request the handler sees."""

    def __init__(self, options, classify_provider_reply):
        self.classify = classify_provider_reply
        self.gemini_origin = _origin(options.gemini_url)
        self.gemini_chat_url = self.gemini_origin + CHAT_PATH
        self.venice_origin = _origin(options.venice_url)
        self.venice_port = urlparse(self.venice_origin).port or 80
        self.venice_chat_url = self.venice_origin + CHAT_PATH
        self.venice_dir = Path(options.venice_dir)
        self.venice_python = options.venice_python
        self.venice_model = options.venice_model
        self.gemini_timeout = options.gemini_timeout
        self.venice_timeout = options.venice_timeout
        self.venice_spawn_cooldown = options.venice_spawn_cooldown
        self.venice_wait = options.venice_wait
        self.venice_limit = options.venice_limit
        self.venice_window = options.venice_window_hours * 3600.0
        self.log_file = Path(options.log_file) if options.log_file else None
        self._accounting = threading.Lock()
        self._venice_lock = threading.Lock()
        self._fall_times = deque()
        self._refusals_seen = 0
        self._requests = 0
        self._venice_ready = False
        self._venice_reason = "not tried yet"
        self._last_spawn_attempt = 0.0
        self._streamed_notice_sent = False
        if self.log_file is not None:
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
                self.log_file.touch(exist_ok=True)
            except OSError:
                self.log_file = None
                print("refusal fallback proxy: log file unusable; logging to stdout only",
                      file=sys.stderr, flush=True)
        self.log(
            f"proxy starting; gemini={self.gemini_origin} venice={self.venice_origin} "
            f"venice-limit={self.venice_limit}/{int(options.venice_window_hours)}h"
        )

    # -- accounting --------------------------------------------------------

    def _prune_window_locked(self):
        now = time.time()
        while self._fall_times and now - self._fall_times[0] > self.venice_window:
            self._fall_times.popleft()

    def note_request(self):
        with self._accounting:
            self._requests += 1

    def note_refusal(self):
        with self._accounting:
            self._refusals_seen += 1
            self._prune_window_locked()
            return self._refusals_seen, len(self._fall_times), self.venice_limit

    def record_fallback(self):
        with self._accounting:
            self._fall_times.append(time.time())
            self._prune_window_locked()
            return len(self._fall_times), self.venice_limit

    def window_counts(self):
        with self._accounting:
            self._prune_window_locked()
            return {
                "requests": self._requests,
                "refusals_seen": self._refusals_seen,
                "fallback_used": len(self._fall_times),
            }

    # -- decisions ---------------------------------------------------------

    def classify_reply(self, reply):
        """The refusal triggers ``check_provider_refusal`` would raise on."""
        if not isinstance(reply, dict):
            return False, ""
        for choice in reply.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            content = message.get("content")
            category, matched = self.classify(content if isinstance(content, str) else "")
            if category == "policy-refusal":
                return True, f"policy-refusal text: {_preview(matched)}"
            structured = message.get("refusal")
            if structured:
                return True, f"structured refusal: {_preview(structured)}"
            finish = choice.get("finish_reason")
            if finish in BLOCKING_FINISH_REASONS:
                return True, f"finish_reason={finish}"
        return False, ""

    def ensure_venice(self):
        """Serve readiness: identity-checked, lazily started, cooled down."""
        with self._venice_lock:
            health = self._read_venice_health()
            if self._venice_serving(health):
                self._venice_ready = True
                self._venice_reason = "serving"
                return True, ""
            if health is not None and health.get("service", "") != VENICE_SERVICE:
                self._venice_ready = False
                self._venice_reason = (
                    f"port {self.venice_port} is held by another service "
                    f"(health reports service={health.get('service')!r}); point the "
                    f"launcher at the real Venice with -VenicePort or stop the "
                    f"squatting process"
                )
                self.log(f"venice unavailable: {self._venice_reason}")
                return False, self._venice_reason
            if health is not None:
                # A Venice bridge answers its health endpoint but reports it
                # cannot serve yet (the egress pool may still be recovering):
                # never stack a second instance onto that port.
                self._venice_ready = False
                self._venice_reason = "Venice answers but reports ok:false (not serving yet)"
                self.log(f"venice unavailable: {self._venice_reason}")
                return False, self._venice_reason
            # Nothing answered on the Venice port, so a start is meaningful
            # here; every other possibility has already returned above.
            if not (self.venice_dir / "server.py").is_file():
                self._venice_ready = False
                self._venice_reason = f"no server.py in {self.venice_dir}"
                self.log(f"venice unavailable: {self._venice_reason}")
                return False, self._venice_reason
            now = time.time()
            if now - self._last_spawn_attempt < self.venice_spawn_cooldown:
                self._venice_ready = False
                remaining = int(self.venice_spawn_cooldown - (now - self._last_spawn_attempt))
                self._venice_reason = (
                    f"another start attempt after a failure cools down for {remaining}s"
                )
                return False, self._venice_reason
            self.log(
                f"venice gateway not serving on port {self.venice_port}; "
                f"starting it from {self.venice_dir}"
            )
            try:
                self._spawn_venice()
            except OSError as error:
                self._venice_ready = False
                self._venice_reason = f"could not start {self.venice_python}: {error}"
                self.log(f"venice unavailable: {self._venice_reason}")
                return False, self._venice_reason
            self._last_spawn_attempt = time.time()
            deadline = time.time() + self.venice_wait
            while time.time() < deadline:
                time.sleep(2.0)
                health = self._read_venice_health()
                if self._venice_serving(health):
                    self._venice_ready = True
                    self._venice_reason = "serving"
                    self.log(
                        f"venice gateway is serving on port {self.venice_port} "
                        f"(took {int(self.venice_wait - (deadline - time.time()))}s)"
                    )
                    return True, ""
            self._venice_ready = False
            self._venice_reason = f"still not serving after {int(self.venice_wait)}s"
            self.log(f"venice unavailable: {self._venice_reason}")
            return False, self._venice_reason

    def _venice_serving(self, health):
        return bool(
            isinstance(health, dict)
            and health.get("service", "") == VENICE_SERVICE
            and health.get("ok") is True
        )

    def _read_venice_health(self):
        status, payload, _ctype = self._forward(
            "GET", self.venice_origin + "/health", {}, None, 10.0
        )
        if status != 200:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _spawn_venice(self):
        command = [self.venice_python, "-X", "utf8", "server.py", "--port", str(self.venice_port)]
        popen_kwargs = {
            "cwd": str(self.venice_dir),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (
                DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            )
        else:
            popen_kwargs["start_new_session"] = True
        subprocess.Popen(command, **popen_kwargs)

    # -- transport ---------------------------------------------------------

    def _forward(self, method, url, headers, body, timeout):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return (
                    response.status,
                    response.read(),
                    response.headers.get("Content-Type") or "application/json",
                )
        except urllib.error.HTTPError as error:
            return (
                error.code,
                error.read(),
                error.headers.get("Content-Type") or "application/json",
            )
        except OSError as error:
            return None, f"upstream {url} failed: {error}".encode("utf-8"), "text/plain"

    def openai_error(self, status, message):
        """A clean OpenAI-shaped hint to the SDK; transport-failure shaped."""
        return (
            status,
            json.dumps({
                "error": {
                    "message": message,
                    "type": "upstream_unreachable",
                    "code": "upstream_unreachable",
                },
            }).encode("utf-8"),
            "application/json",
            [("X-BBM-Route", "gemini")],
        )

    def relay(self, method, path, client_headers, body=None):
        """Every endpoint but the chat call: byte-for-byte to the gemini route."""
        headers = _upstream_headers(client_headers)
        return self._forward(method, self.gemini_origin + path, headers, body, self.gemini_timeout)

    def chat(self, body_bytes, client_headers):
        """The chat call: gemini first, Venice only for a policy refusal."""
        self.note_request()
        gemini_headers = _upstream_headers(client_headers)
        gemini_route = [("X-BBM-Route", "gemini")]
        try:
            request_payload = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Not ours to decide; the upstream answers it exactly as reached
            # directly, whatever becomes of it.
            status, payload, ctype = self._forward(
                "POST", self.gemini_chat_url, gemini_headers, body_bytes, self.gemini_timeout
            )
            if status is None:
                self.log(f"gemini gateway unreachable: {payload.decode('utf-8', 'replace')[:200]}")
                return self.openai_error(502, "gemini gateway is unreachable")
            return (status, payload, ctype, gemini_route)
        if isinstance(request_payload, dict) and request_payload.get("stream"):
            # Streaming traffic is never classified; nothing in this
            # repository streams, so this is a future-proofing pass-through.
            if not self._streamed_notice_sent:
                self._streamed_notice_sent = True
                self.log("stream: true seen; streamed replies are never rerouted to venice")
            status, payload, ctype = self._forward(
                "POST", self.gemini_chat_url, gemini_headers, body_bytes, self.gemini_timeout
            )
            if status is None:
                self.log(f"gemini gateway unreachable: {payload.decode('utf-8', 'replace')[:200]}")
                return self.openai_error(502, "gemini gateway is unreachable")
            return (status, payload, ctype, gemini_route)

        status, payload, ctype = self._forward(
            "POST", self.gemini_chat_url, gemini_headers, body_bytes, self.gemini_timeout
        )
        if status is None:
            self.log(f"gemini gateway unreachable: {payload.decode('utf-8', 'replace')[:200]}")
            return self.openai_error(502, "gemini gateway is unreachable")
        if status != 200:
            self.log(f"gemini answered {status}; passing it through unclassified")
            return (status, payload, ctype, gemini_route)
        try:
            reply = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self.log(f"gemini reply was not JSON ({error}); passing it through unclassified")
            return (status, payload, ctype, gemini_route)
        declined, evidence = self.classify_reply(reply)
        if not declined:
            return (status, payload, ctype, gemini_route)
        _refusals, used, limit = self.note_refusal()
        self.log(f"gemini policy refusal ({evidence}); refusal #{_refusals}")
        if used >= limit:
            self.log(
                f"venice fallback cap reached ({used}/{limit} within the window); the "
                f"refusal passes through and keeps the repository's terminal-refusal behaviour"
            )
            return (status, payload, ctype, gemini_route)
        serviceable, reason = self.ensure_venice()
        if not serviceable:
            self.log(f"venice unavailable ({reason}); the original refusal passes through")
            return (status, payload, ctype, gemini_route)
        rerouted = dict(request_payload)
        rerouted["model"] = self.venice_model
        rerouted_bytes = json.dumps(rerouted).encode("utf-8")
        self.log(f"rerouting one refused chunk to venice (model {self.venice_model})")
        vstatus, vpayload, vctype = self._forward(
            "POST", self.venice_chat_url, gemini_headers, rerouted_bytes, self.venice_timeout
        )
        if vstatus is None:
            self.log(
                f"venice unreachable: {vpayload.decode('utf-8', 'replace')[:200]}; "
                f"the original refusal passes through"
            )
            return (status, payload, ctype, gemini_route)
        if vstatus != 200:
            self.log(
                f"venice answered {vstatus}: {_preview(vpayload.decode('utf-8', 'replace'))}; "
                f"the original refusal passes through"
            )
            return (status, payload, ctype, gemini_route)
        try:
            vreply = json.loads(vpayload.decode("utf-8"))
            if not (isinstance(vreply, dict) and (vreply.get("choices") or [])):
                raise ValueError("not a chat completion")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self.log(f"venice reply unusable ({error}); the original refusal passes through")
            return (status, payload, ctype, gemini_route)
        used, limit = self.record_fallback()
        self.log(f"refused chunk answered by venice ({used}/{limit} within the window)")
        return (
            200,
            vpayload,
            vctype if vctype and vctype != "text/plain" else "application/json",
            [
                ("X-BBM-Route", "venice"),
                ("X-BBM-Fallback-For", "gemini-policy-refusal"),
            ],
        )

    # -- self reporting ----------------------------------------------------

    def health_snapshot(self):
        counts = self.window_counts()
        with self._venice_lock:
            venice = {
                "base": self.venice_origin,
                "model": self.venice_model,
                "ready": self._venice_ready,
                "reason": self._venice_reason,
            }
        return {
            "service": PROXY_SERVICE,
            "ok": True,
            "gemini": {"base": self.gemini_origin},
            "venice": venice,
            "limit": self.venice_limit,
            "window_hours": self.venice_window / 3600.0,
            **counts,
        }

    def log(self, message):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        if self.log_file is not None:
            try:
                with self.log_file.open("a", encoding="utf-8") as sink:
                    sink.write(line + "\n")
            except OSError:
                pass
        print(line, file=sys.stderr, flush=True)


class _Handler(BaseHTTPRequestHandler):
    """One request thread; the router holds every decision."""

    protocol_version = "HTTP/1.1"
    router = None

    def do_GET(self):  # noqa: N802
        if self.path in HEALTH_PATHS:
            self._answered(
                200,
                json.dumps(self.router.health_snapshot()).encode("utf-8"),
                "application/json",
                [("X-BBM-Route", "proxy")],
            )
            return
        status, payload, ctype = self.router.relay("GET", self.path, self.headers)
        self._answered(status, payload, ctype, [("X-BBM-Route", "gemini")])

    def do_POST(self):  # noqa: N802
        if self.path == CHAT_PATH:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length > 0 else b""
            status, payload, ctype, extra = self.router.chat(body, self.headers)
            self._answered(status, payload, ctype, extra)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b""
        status, payload, ctype = self.router.relay("POST", self.path, self.headers, body)
        self._answered(status, payload, ctype, [("X-BBM-Route", "gemini")])

    def _answered(self, status, payload, ctype, extra):
        if status is None:
            status, payload, ctype, extra = self.router.openai_error(
                502, f"gemini gateway is unreachable while forwarding {self.path}"
            )
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            for name, value in extra or []:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The SDK client gave up (its 300 s ceiling, or a run stopped by
            # the operator); the upstream answer is simply dropped.
            pass

    def log_message(self, fmt, *args):  # noqa: A002 - access lines are noise
        return


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="OpenAI-compatible local proxy that rescues Gemini-refused "
                    "chunks with the Venice guest bridge.",
    )
    parser.add_argument("--listen-host", default="127.0.0.1",
                        help="Bind address (default 127.0.0.1).")
    parser.add_argument("--listen-port", type=int, default=4998,
                        help="Port the translator points --api_base at (default 4998).")
    parser.add_argument("--gemini-url", default="http://127.0.0.1:4995/v1",
                        help="Primary gateway base URL (gemini-anon should live here).")
    parser.add_argument("--venice-url", default="http://127.0.0.1:4996/v1",
                        help="Rescue gateway base URL (Venice guest bridge /v1).")
    parser.add_argument("--venice-dir",
                        default=r"C:\Users\Tina_\Documents\PlayingWithRepo\AI-BRIDGE\Venice",
                        help="Directory holding the Venice bridge's server.py.")
    parser.add_argument("--venice-python", default=sys.executable,
                        help="Interpreter used to start the Venice bridge (default: this one).")
    parser.add_argument("--venice-model", default=VENICE_MODEL_ALIAS,
                        help="Model name sent to Venice (its Auto-router alias).")
    parser.add_argument("--venice-limit", type=int, default=8,
                        help="Max rerouted refusals per window (default 8).")
    parser.add_argument("--venice-window-hours", type=float, default=24.0,
                        help="Rolling window for the cap, in hours (default 24).")
    parser.add_argument("--venice-wait", type=float, default=90.0,
                        help="Seconds to wait for a freshly started Venice to serve (default 90).")
    parser.add_argument("--venice-spawn-cooldown", type=float, default=600.0,
                        help="Seconds between start attempts after a failure (default 600).")
    parser.add_argument("--gemini-timeout", type=float, default=110.0,
                        help="Seconds for the primary route (default 110; client budget is 300 s).")
    parser.add_argument("--venice-timeout", type=float, default=175.0,
                        help="Seconds for the rescue route (default 175; keeps under the 300 s budget).")
    parser.add_argument("--log-file", default=str(REPO_ROOT / "log" / "venice_fallback.log"),
                        help="Decision log path (default log/venice_fallback.log in this repo).")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    _Handler.router = FallbackRouter(args, _load_classifier())
    try:
        server = ThreadingHTTPServer((args.listen_host, args.listen_port), _Handler)
    except OSError as error:
        _Handler.router.log(f"cannot bind {args.listen_host}:{args.listen_port}: {error}")
        return 2
    server.daemon_threads = True
    _Handler.router.log(
        f"listening on http://{args.listen_host}:{args.listen_port}/v1; "
        f"point --api_base here for the refusal-fallback workflow"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _Handler.router.log("stopped by keyboard interrupt")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
