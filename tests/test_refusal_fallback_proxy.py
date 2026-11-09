"""Hermetic tests for scripts/refusal_fallback_proxy.py.

The proxy is the seam between the translator and the gemini gateway, and the
Venice guest bridge behind it is expensive, so what has to hold offline is
inventory, not behaviour quality:

- a normal reply, a retryable provider-error boilerplate, and a non-200 all
  pass through byte-for-byte with no Venice request at all;
- exactly the reply the repository's own classifier calls a policy refusal
  is re-issued once to the rescue gateway, with the model swapped to the
  Venice alias;
- an unreachable rescue gateway keeps the original refusal intact, which is
  what preserves the repository's terminal-refusal semantics;
- the cap on rescues is honoured, streamed requests are never classified,
  a GET /v1/models is forwarded verbatim, and the proxy's /health
  identifies itself.

Every listener here binds an ephemeral port on 127.0.0.1, no AI-BRIDGE file
is read, no gateway is started, and nothing leaves the loopback. Live
reroutes through the real gateways are a -m live_provider concern nobody
should automate.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from book_maker.provider_refusal import classify_provider_reply

_PROXY_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "refusal_fallback_proxy.py"
)

_REFUSAL_REPLY = {
    "choices": [
        {
            "message": {
                "content": "I can help with many kinds of requests, but it appears "
                           "this one would go against my "
                           "(https://gemini.google/policy-guidelines/). Is there "
                           "something else I can try for you?",
            },
            "finish_reason": "stop",
        }
    ]
}

_CONTENT_REPLY = {
    "choices": [
        {"message": {"content": "<p>他推开门，见到了那把伞。</p>"}, "finish_reason": "stop"}
    ]
}

_VENICE_HEALTH = {
    "service": "venice-guest-direct-https-bridge",
    "ok": True,
    "model_alias": "venice-anon",
}


class _FakeGateway(BaseHTTPRequestHandler):
    """A scripted OpenAI-style listener; every chat body is captured."""

    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._answer(404, {"error": {"message": "no such route"}})
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.captured_bodies.append(json.loads(body.decode("utf-8")))
        self.server.captured_paths.append(self.path)
        if not self.server.responses:
            self._answer(500, {"error": {"message": "script ran dry"}})
            return
        item = self.server.responses.pop(0)
        self._answer(item["status"], item["body"])

    def do_GET(self):  # noqa: N802
        if self.path == "/health" and self.server.identity == "venice":
            self._answer(200, _VENICE_HEALTH)
            return
        if self.path == "/v1/models":
            if not self.server.responses:
                self._answer(500, {"error": {"message": "script ran dry"}})
                return
            item = self.server.responses.pop(0)
            self._answer(item["status"], item["body"])
            return
        self._answer(404, {"error": {"message": "no such route"}})

    def _answer(self, status, body):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):  # noqa: A002
        return


def _start_fake(responses=None, identity="gemini"):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGateway)
    server.daemon_threads = True
    server.identity = identity
    server.responses = list(responses or [])
    server.captured_bodies = []
    server.captured_paths = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stop_fake(server):
    # Half-close the serving loop before the socket: on Windows a bare
    # server_close() under a live selector aborts serve_forever with
    # WinError 10038 and pytest reports every thread's death.
    server.shutdown()
    server.server_close()


def _load_proxy_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("refusal_fallback_proxy", _PROXY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _start_proxy(router, proxy_module):
    proxy_module._Handler.router = router
    server = ThreadingHTTPServer(("127.0.0.1", 0), proxy_module._Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _proxy_options(tmp_path, gemini_port, venice_port, limit=8):
    return SimpleNamespace(
        gemini_url=f"http://127.0.0.1:{gemini_port}/v1",
        # venice_port=0 stands in for "nothing can listen there".
        venice_url=(f"http://127.0.0.1:{venice_port}/v1" if venice_port
                    else f"http://127.0.0.1:{gemini_port + 1}/v1"),
        venice_dir=tmp_path,
        venice_python="python",
        venice_model="venice-anon",
        gemini_timeout=5.0,
        venice_timeout=5.0,
        venice_spawn_cooldown=0.0,
        venice_wait=1.0,
        venice_limit=limit,
        venice_window_hours=24.0,
        log_file=str(tmp_path / "fallback.log"),
    )


def _proxy_base(server):
    host, port = server.server_address
    return f"http://{host}:{port}"


def _chat_request(proxy_server, payload):
    request = urllib.request.Request(
        _proxy_base(proxy_server) + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=10)


@pytest.fixture()
def proxy_module():
    return _load_proxy_module()


def test_refusal_text_is_the_repository_classifier_s_verdict():
    # The reroute trigger in the tests below is the canonical refusal the
    # operator described; if the classifier ever stops recognizing it, the
    # fallback silently stops firing, so pin the classification here.
    category, _ = classify_provider_reply(_REFUSAL_REPLY["choices"][0]["message"]["content"])
    assert category == "policy-refusal"


def test_normal_content_passes_through_without_venice(tmp_path, proxy_module):
    gemini = _start_fake([{"status": 200, "body": _CONTENT_REPLY}])
    venice = _start_fake(identity="venice")  # must never be asked
    proxy = _start_proxy(
        proxy_module.FallbackRouter(
            _proxy_options(tmp_path, gemini.server_address[1], venice.server_address[1]),
            proxy_module._load_classifier(),
        ),
        proxy_module,
    )
    try:
        with _chat_request(proxy, {"model": "gemini-anon",
                                  "messages": [{"role": "user", "content": "x"}]}) as response:
            assert response.headers.get("X-BBM-Route") == "gemini"
            replied = json.loads(response.read().decode("utf-8"))
        assert replied == _CONTENT_REPLY
        assert not venice.captured_bodies
        assert gemini.captured_bodies[0]["model"] == "gemini-anon"
    finally:
        _stop_fake(proxy)
        _stop_fake(gemini)
        _stop_fake(venice)


def test_policy_refusal_reroutes_one_request_to_venice(tmp_path, proxy_module):
    gemini = _start_fake([{"status": 200, "body": _REFUSAL_REPLY}])
    venice = _start_fake([{"status": 200, "body": _CONTENT_REPLY}], identity="venice")
    proxy = _start_proxy(
        proxy_module.FallbackRouter(
            _proxy_options(tmp_path, gemini.server_address[1], venice.server_address[1]),
            proxy_module._load_classifier(),
        ),
        proxy_module,
    )
    try:
        with _chat_request(proxy, {"model": "gemini-anon",
                                   "messages": [{"role": "user", "content": "translate me"}]}) as response:
            assert response.headers.get("X-BBM-Route") == "venice"
            assert response.headers.get("X-BBM-Fallback-For") == "gemini-policy-refusal"
            replied = json.loads(response.read().decode("utf-8"))
        assert replied == _CONTENT_REPLY
        # One request reached the rescue, with this repository's model alias
        # and the chunk's messages unchanged.
        assert venice.captured_bodies == [{
            "model": "venice-anon",
            "messages": [{"role": "user", "content": "translate me"}],
        }]
        assert venice.captured_paths == ["/v1/chat/completions"]
        with urllib.request.urlopen(_proxy_base(proxy) + "/health", timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["fallback_used"] == 1
        assert health["refusals_seen"] == 1
    finally:
        _stop_fake(proxy)
        _stop_fake(gemini)
        _stop_fake(venice)


def test_unreachable_venice_returns_the_original_refusal(tmp_path, proxy_module):
    # The dead rescue gateway is represented by a port that stopped serving
    # before the refusal arrives: ensure() must find nothing there and keep
    # the original refusal intact instead of breaking the reply chain.
    venice = _start_fake(identity="venice")
    dead_venice_port = venice.server_address[1]
    _stop_fake(venice)
    gemini = _start_fake([{"status": 200, "body": _REFUSAL_REPLY}])
    proxy = _start_proxy(
        proxy_module.FallbackRouter(
            _proxy_options(tmp_path, gemini.server_address[1], dead_venice_port),
            proxy_module._load_classifier(),
        ),
        proxy_module,
    )
    try:
        with _chat_request(proxy, {"model": "gemini-anon",
                                   "messages": [{"role": "user", "content": "y"}]}) as response:
            assert response.headers.get("X-BBM-Route") == "gemini"
            replied = json.loads(response.read().decode("utf-8"))
        assert replied == _REFUSAL_REPLY
    finally:
        _stop_fake(proxy)
        _stop_fake(gemini)


def test_retryable_provider_error_boilerplate_never_reroutes(tmp_path, proxy_module):
    boilerplate = {
        "choices": [
            {"message": {"content":
                         "Sorry, something went wrong. Please try your request again."},
             "finish_reason": "stop"},
        ]
    }
    gemini = _start_fake([{"status": 200, "body": boilerplate}])
    venice = _start_fake(identity="venice")  # must never be asked
    proxy = _start_proxy(
        proxy_module.FallbackRouter(
            _proxy_options(tmp_path, gemini.server_address[1], venice.server_address[1]),
            proxy_module._load_classifier(),
        ),
        proxy_module,
    )
    try:
        with _chat_request(proxy, {"model": "gemini-anon",
                                   "messages": [{"role": "user", "content": "z"}]}) as response:
            assert json.loads(response.read().decode("utf-8")) == boilerplate
        assert not venice.captured_bodies
    finally:
        _stop_fake(proxy)
        _stop_fake(gemini)
        _stop_fake(venice)


def test_rescue_cap_parks_the_budget_at_the_limit(tmp_path, proxy_module):
    gemini = _start_fake([{"status": 200, "body": _REFUSAL_REPLY}])
    venice = _start_fake(identity="venice")
    router = proxy_module.FallbackRouter(
        _proxy_options(tmp_path, gemini.server_address[1], venice.server_address[1], limit=1),
        proxy_module._load_classifier(),
    )
    # The window is already at its cap before the refusal arrives: the rescue
    # must not fire, because the operator's budget is the binding promise.
    router.record_fallback()
    proxy = _start_proxy(router, proxy_module)
    try:
        with _chat_request(proxy, {"model": "gemini-anon",
                                   "messages": [{"role": "user", "content": "c"}]}) as response:
            assert response.headers.get("X-BBM-Route") == "gemini"
            assert json.loads(response.read().decode("utf-8")) == _REFUSAL_REPLY
        assert not venice.captured_bodies
    finally:
        _stop_fake(proxy)
        _stop_fake(gemini)
        _stop_fake(venice)


def test_models_listing_is_forwarded_verbatim(tmp_path, proxy_module):
    models = {"object": "list", "data": [{"id": "gemini-anon", "object": "model"}]}
    gemini = _start_fake([{"status": 200, "body": models}])
    proxy = _start_proxy(
        proxy_module.FallbackRouter(
            _proxy_options(tmp_path, gemini.server_address[1], 0),
            proxy_module._load_classifier(),
        ),
        proxy_module,
    )
    try:
        with urllib.request.urlopen(_proxy_base(proxy) + "/v1/models", timeout=10) as response:
            assert json.loads(response.read().decode("utf-8")) == models
    finally:
        _stop_fake(proxy)
        _stop_fake(gemini)


def test_streamed_request_is_never_classified_or_rerouted(tmp_path, proxy_module):
    # Streaming is a future-proofing pass-through: even aGemini refusal reply
    # to a streamed request goes through untouched and uncounted.
    gemini = _start_fake([{"status": 200, "body": _REFUSAL_REPLY}])
    venice = _start_fake(identity="venice")  # must never be asked
    proxy = _start_proxy(
        proxy_module.FallbackRouter(
            _proxy_options(tmp_path, gemini.server_address[1], venice.server_address[1]),
            proxy_module._load_classifier(),
        ),
        proxy_module,
    )
    try:
        with _chat_request(proxy, {"model": "gemini-anon", "stream": True,
                                   "messages": [{"role": "user", "content": "s"}]}) as response:
            assert response.headers.get("X-BBM-Route") == "gemini"
            assert json.loads(response.read().decode("utf-8")) == _REFUSAL_REPLY
        assert not venice.captured_bodies
        with urllib.request.urlopen(_proxy_base(proxy) + "/health", timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["fallback_used"] == 0
    finally:
        _stop_fake(proxy)
        _stop_fake(gemini)
        _stop_fake(venice)


def test_dead_gemini_gateway_answer_with_a_clean_502(tmp_path, proxy_module):
    # A dead primary is a clean, OpenAI-shaped 502 for the translator's
    # transport loop to handle; no rescue is attempted, because Venice exists
    # to answer refused chunks, not to stand in for an unreachable Gemini.
    dead_gemini = _start_fake()
    dead_gemini_port = dead_gemini.server_address[1]
    _stop_fake(dead_gemini)
    proxy = _start_proxy(
        proxy_module.FallbackRouter(
            _proxy_options(tmp_path, dead_gemini_port, 0),
            proxy_module._load_classifier(),
        ),
        proxy_module,
    )
    try:
        with pytest.raises(urllib.error.HTTPError) as raised:
            _chat_request(proxy, {"model": "gemini-anon",
                                  "messages": [{"role": "user", "content": "g"}]})
        assert raised.value.code == 502
    finally:
        _stop_fake(proxy)


def test_health_snapshot_identifies_the_proxy(tmp_path, proxy_module):
    gemini = _start_fake()
    proxy = _start_proxy(
        proxy_module.FallbackRouter(
            _proxy_options(tmp_path, gemini.server_address[1], 0),
            proxy_module._load_classifier(),
        ),
        proxy_module,
    )
    try:
        with urllib.request.urlopen(_proxy_base(proxy) + "/health", timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["service"] == "bbm-refusal-fallback-proxy"
        assert health["ok"] is True
        assert health["venice"]["model"] == "venice-anon"
    finally:
        _stop_fake(proxy)
        _stop_fake(gemini)
