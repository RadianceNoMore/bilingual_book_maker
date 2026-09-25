"""Single-key Gemini fallback wrapper for bilingual_book_maker.

Best -> average model chain on ONE Google API key, resuming where it stopped.

Why this exists:
- `--model_list best,fallback` ROTATES every request (and is banned with
  `--use_context session`), it does not fall back on quota errors.
- `--resume` with a different `--model` is REFUSED for epub books
  (resume-cache run fingerprint binds language/prompt/model).
- Gemini quota errors (429 / RESOURCE_EXHAUSTED) on the `gemini` route
  rotate the *key*, not the model -- with one key that just retries the
  same key 7x and then writes "[Translation unavailable]" markers.

So this wrapper runs make_book.py once per model, in priority order,
adding `--resume` after the first attempt and (epub only) clearing the
stored `run_fingerprint` from the `.temp.bin` checkpoint so the next
model is allowed to continue the same book. Language/prompt/plan stay
identical -- only the model changes. The output book WILL mix models;
that is the documented tradeoff.

Usage:
    python make_gg_book.py --book_name book.epub --key $KEY ^
        --models "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemma-4-31b-it,gemma-4-26b-a4b-it" ^
        --language en --source_lang ko --single_translate -- --quiet --accumulated_num 3500

Verified 2026-09-24 against the live API (generateContent probe):
  OK now: gemini-3-flash-preview, gemma-4-26b-a4b-it
  Exists but busy right now (503/timeout -- wrapper falls through them):
    gemini-3.8/3.7/3.6/3.5-flash, gemini-3.1-flash-lite,
    gemini-3.5-flash-lite, gemma-4-31b-it
  NO free tier (429 limit: 0, always): gemini-3.1-pro-preview -- dropped
    from the chain, it only burns ~2 min of retries per run.
  DEAD (404 "no longer available to new users"): every gemini-2.5-* id,
    and gemma-3-27b-it does not exist (it is gemma-4-* now).

Windows + CJK books: the loader echoes every source paragraph (rich
print), which crashes a cp1252 console with UnicodeEncodeError. The
wrapper forces PYTHONUTF8=1/PYTHONIOENCODING=utf-8 on the child and
echoes safely itself -- and `--quiet` skips those echoes entirely,
which is strongly recommended for big books.

    Everything after `--` is forwarded verbatim to make_book.py.
    Do NOT pass --model / --model_list / --key / --api_format / --resume
    after `--` -- the wrapper owns those.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Quota / overload signals from google-genai + tenacity + our loader.
# Falls back on: 429 quota/rate limits AND 503/500 overload ("high demand",
# timeouts) -- the fleet is heavily loaded right now and a busy model
# should yield to the next one instead of retrying itself 7x.
# Deliberately excluded: auth errors, safety blocks, bad model ids (404) --
# those would fail on every model the same way, so they fail fast.
QUOTA_RE = re.compile(
    r"429"
    r"|RESOURCE_EXHAUSTED"
    r"|RATE_LIMIT_EXCEEDED"
    r"|RESOURCE_LIMIT"
    r"|quota[^a-z0-9]{0,10}(exceed|exhaust|spent|limit)"
    r"|rate[^a-z0-9]{0,10}limit"
    r"|requests per (day|minute)\b"
    r"|\bRPD\b|\bRPM\b"
    r"|rateLimitExceeded"
    r"|\b503\b|\b500\b"
    r"|UNAVAILABLE|INTERNAL"
    r"|high demand|overloaded|overload"
    r"|timed? ?out|timeout|deadline exceeded",
    re.IGNORECASE,
)

# The refusal this wrapper exists to bypass (after stripping the fingerprint
# it should never appear -- if it does, fail fast instead of looping).
FINGERPRINT_RE = re.compile(
    r"resume cache .* different language, prompt or model", re.IGNORECASE
)

# Windows cp1252 console crash on CJK echoes (epub_loader prints each source
# paragraph unless --quiet). Identical on every model, so never a fallback.
CONSOLE_RE = re.compile(
    r"UnicodeEncodeError|charmap|codec can.t encode", re.IGNORECASE
)

# A *daily* quota death (quotaId ...PerDay..., quotaValue 20) stays dead
# until UTC midnight, so remember it across invocations instead of re-burning
# ~2 min of retries on every rerun. Per-minute limits and 503 overloads are
# transient and never recorded.
DAILY_QUOTA_RE = re.compile(r"PerDay", re.IGNORECASE)
SKIP_FILE_NAME = "make_gg_book.skip.json"


def skip_cache_path() -> Path:
    return Path(__file__).resolve().parent / SKIP_FILE_NAME


def load_skips() -> dict:
    try:
        data = json.loads(skip_cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_skips(skips: dict) -> None:
    try:
        skip_cache_path().write_text(json.dumps(skips, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[wrapper] WARNING: could not write {SKIP_FILE_NAME}: {e}")


def next_utc_midnight_ts() -> float:
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight.timestamp() + 86400) if midnight <= now else midnight.timestamp()


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M UTC")


def default_interval_for(model: str) -> float:
    """Safe --interval for free-tier RPMs *and* Gemma's tight 16K TPM.
    6s x ~1500 input tokens ~= 15K TPM, just under the cap; RPM 30 would
    allow 2s, but TPM is the binding constraint on Gemma."""
    m = model.lower()
    if "gemma" in m:
        return 6.0
    if "lite" in m:
        return 5.0  # 15 RPM (3.1/3.5 lite); 250K TPM is never binding here
    return 13.0  # 5 RPM standard Flash and everything unknown


def resume_bin_for(book_name: str) -> Path:
    p = Path(book_name)
    return p.parent / f".{p.stem}.temp.bin"


def strip_run_fingerprint(bin_path: Path) -> bool:
    """Drop `run_fingerprint` from an epub pickle checkpoint so --resume with
    a different --model warns instead of exiting(1). Returns True if patched.
    Backs up the original to .bak (once). Other book types have no model
    fingerprint and need no patching."""
    if not bin_path.is_file():
        return False
    try:
        with open(bin_path, "rb") as f:
            state = pickle.load(f)
    except Exception:
        return False
    if not isinstance(state, dict) or "run_fingerprint" not in state:
        return False
    bak = bin_path.with_suffix(bin_path.suffix + ".bak")
    try:
        if not bak.is_file():
            shutil.copy2(bin_path, bak)
        del state["run_fingerprint"]
        with open(bin_path, "wb") as f:
            pickle.dump(state, f)
        return True
    except Exception as e:
        print(f"[wrapper] WARNING: could not patch {bin_path}: {e}")
        return False


def _safe_write(text: str) -> None:
    """Echo a child line without dying on CJK text under a cp1252 console."""
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        sys.stdout.write(
            text.encode("utf-8", "backslashreplace").decode("ascii", "replace")
        )
    sys.stdout.flush()


def run_make_book(
    make_book: Path, args: list[str], *, dry_run: bool
) -> tuple[int, str]:
    """Run one make_book.py attempt, streaming output live. Returns
    (returncode, captured_tail). Tail is capped to keep quota-scan cheap."""
    cmd = [sys.executable, str(make_book), *args]
    print(f"\n[wrapper] $ {' '.join(cmd)}", flush=True)
    if dry_run:
        return 0, ""
    # Korean source text is echoed by the loader (unless --quiet). Under a
    # Windows cp1252 console that echo crashes rich with UnicodeEncodeError
    # before any translation happens -- force UTF-8 I/O for the child.
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
        env=env,
    )
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        _safe_write(line)
        tail.append(line)
        if len(tail) > 400:
            del tail[:200]
    proc.wait()
    return proc.returncode, "".join(tail)


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Gemini best->fallback wrapper (one API key, resume between models)."
    )
    ap.add_argument("--book_name", required=True, help="book path, as for make_book.py")
    ap.add_argument("--key", default=os.environ.get("GEMINI_API_KEY", ""),
                    help="single Google API key (or $GEMINI_API_KEY)")
    ap.add_argument("--models", required=True,
                    help='comma list, best first, e.g. "gemini-3-flash-preview,gemini-3.1-flash-lite,gemma-4-26b-a4b-it"')
    ap.add_argument("--intervals", default="",
                    help='optional comma list matching --models, e.g. "13,5,6". Default: auto (13 flash / 5 lite / 6 gemma).')
    ap.add_argument("--interval", type=float, default=None,
                    help="force one --interval for every model (overrides --intervals/auto)")
    ap.add_argument("--make-book", default="",
                    help="path to make_book.py (default: bilingual_book_maker/make_book.py next to this script)")
    ap.add_argument("--no-strip-fingerprint", action="store_true",
                    help="do NOT clear epub run_fingerprint on model switch (resume will refuse with another model)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the commands without running them")
    ap.add_argument("--reset-skips", action="store_true",
                    help="clear the daily-quota skip memory and try every model")
    ap.add_argument("passthrough", nargs=argparse.REMAINDER,
                    help="everything after `--` goes verbatim to make_book.py")
    ns = ap.parse_args(argv)
    # argparse.REMAINDER keeps the leading `--`; drop it.
    if ns.passthrough and ns.passthrough[0] == "--":
        ns.passthrough = ns.passthrough[1:]
    return ns


def main(argv: list[str] | None = None) -> int:
    try:  # wrapper's own echo of CJK child lines must not die on cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
    ns = parse_args(argv if argv is not None else sys.argv[1:])

    if not ns.key:
        print("[wrapper] ERROR: no key -- pass --key or set GEMINI_API_KEY", file=sys.stderr)
        return 1

    models = [m.strip() for m in ns.models.split(",") if m.strip()]
    if not models:
        print("[wrapper] ERROR: --models is empty", file=sys.stderr)
        return 1

    if ns.intervals:
        parts = [p.strip() for p in ns.intervals.split(",") if p.strip()]
        if len(parts) != len(models):
            print("[wrapper] ERROR: --intervals must match --models in length", file=sys.stderr)
            return 1
        intervals = [float(p) for p in parts]
    else:
        intervals = [default_interval_for(m) for m in models]
    if ns.interval is not None:
        intervals = [ns.interval] * len(models)

    here = Path(__file__).resolve().parent
    make_book = Path(ns.make_book) if ns.make_book else here / "bilingual_book_maker" / "make_book.py"
    if not make_book.is_file():
        print(f"[wrapper] ERROR: make_book.py not found at {make_book}", file=sys.stderr)
        return 1

    # The wrapper owns the endpoint identity. Forwarding any of these would
    # split the run across routes/keys and break the resume chain.
    owned = {"--model", "--model_list", "--api_format", "--resume", "--key", "--api_key", "--interval"}
    for tok in ns.passthrough:
        if tok in owned or any(tok.startswith(o + "=") for o in owned):
            print(f"[wrapper] ERROR: {tok} is owned by the wrapper -- remove it from passthrough", file=sys.stderr)
            return 1

    # Batched tag mode (accumulated_num > 1, no plan mode) NEVER writes
    # the checkpoint: translations go straight into the in-memory book,
    # so a crash restarts from 0 and --resume is a no-op. Say so loud.
    acc = 1
    for j, tok in enumerate(ns.passthrough):
        if tok == "--accumulated_num" and j + 1 < len(ns.passthrough):
            try:
                acc = int(ns.passthrough[j + 1])
            except ValueError:
                pass
        elif tok.startswith("--accumulated_num="):
            try:
                acc = int(tok.split("=", 1)[1])
            except ValueError:
                pass
    plan_mode = any(
        t == "--plan-classify" or t.startswith("--plan-classify=")
        for t in ns.passthrough
    )
    if acc > 1 and not plan_mode:
        print("[wrapper] WARNING: batched tag mode never checkpoints -- "
              "a crash restarts from 0 and --resume does nothing. "
              "For crash-safety use --plan-classify all (resumable).")

    bin_path = resume_bin_for(ns.book_name)

    # Models remembered as daily-exhausted (PerDay quotaId) from earlier
    # invocations are skipped until UTC midnight instead of re-burning
    # retries. Per-minute limits and 503s are transient: never recorded.
    skips: dict = {}
    if ns.reset_skips:
        try:
            skip_cache_path().unlink()
        except FileNotFoundError:
            pass
    else:
        skips = load_skips()
    now = time.time()
    skips = {m: exp for m, exp in skips.items() if exp > now}
    if skips:
        print("[wrapper] skipping daily-exhausted: "
              + ", ".join(f"{m} (till {fmt_ts(e)})" for m, e in skips.items()
                           if m in models))

    attempted_any = False
    for i, (model, interval) in enumerate(zip(models, intervals)):
        if model in skips:
            print(f"[wrapper] attempt {i + 1}/{len(models)}: model={model} SKIPPED (daily quota)")
            continue
        attempted_any = True
        # A checkpoint from ANY earlier run (this chain or a previous
        # invocation) carries the model that wrote it, and epub --resume
        # refuses a different --model. So strip before EVERY attempt,
        # including the first: a same-model resume still works (it just
        # warns instead of verifying), and a model switch is the point.
        patched = False
        if not ns.no_strip_fingerprint:
            patched = strip_run_fingerprint(bin_path)
            if patched:
                print(f"[wrapper] epub checkpoint {bin_path.name}: cleared run_fingerprint "
                      f"so {model} may --resume (output mixes models by design)")

        # Only resume when the checkpoint is really there. A failed fresh
        # run banks nothing (tag mode + batching saves only at batch
        # boundaries), so a blind --resume dies with "can not load
        # resume file" instead of starting clean on the next model.
        use_resume = bin_path.is_file()
        cmd = [
            "--book_name", ns.book_name,
            "--api_format", "gemini",
            "--key", ns.key,
            "--model", model,
            "--interval", str(interval),
            *(["--resume"] if use_resume else []),
            *ns.passthrough,
        ]
        print(f"[wrapper] attempt {i + 1}/{len(models)}: model={model} "
              f"interval={interval}s {'--resume' if use_resume else '(fresh)'}"
              + (" [fingerprint cleared]" if patched else ""))

        rc, output = run_make_book(make_book, cmd, dry_run=ns.dry_run)

        if ns.dry_run:
            continue  # preview the whole chain; nothing actually ran
        if rc == 0:
            print(f"[wrapper] DONE on {model}")
            return 0

        if FINGERPRINT_RE.search(output):
            print("[wrapper] ERROR: resume refused on model change. "
                  "This means the fingerprint strip did not apply "
                  "(non-epub path? --no-strip-fingerprint? already-migrated checkpoint?). "
                  "Delete the .temp.bin to restart, or rerun with the original model.",
                  file=sys.stderr)
            return 1

        if CONSOLE_RE.search(output):
            print("[wrapper] ERROR: crashed printing CJK text to this console "
                  f"(exit {rc}). The child now runs with PYTHONUTF8=1 -- if you "
                  "still see this, rerun with `--quiet` after `--` (skips the "
                  "per-paragraph echoes) and/or run `chcp 65001` first. "
                  "Then rerun the same command (it resumes).",
                  file=sys.stderr)
            return 1

        if QUOTA_RE.search(output):
            if DAILY_QUOTA_RE.search(output):
                # quotaId ...PerDay...: dead till UTC midnight on every
                # future invocation too -- remember it.
                skips[model] = next_utc_midnight_ts()
                save_skips(skips)
                print(f"[wrapper] {model}: daily quota spent, skipped till "
                      f"{fmt_ts(skips[model])}")
            if i < len(models) - 1:
                print(f"[wrapper] quota/rate limit hit on {model} "
                      f"-> falling back to {models[i + 1]} with --resume")
                continue
            print("[wrapper] ERROR: all models quota-exhausted. "
                  "Free-tier RPD resets daily -- rerun tomorrow with --resume, "
                  "or raise --interval / lower --accumulated_num.", file=sys.stderr)
            return 2

        # Non-quota failure: wrong key, bad model id, missing book, syntax --
        # retrying on another model would fail identically.
        print(f"[wrapper] ERROR: {model} failed WITHOUT quota signals "
              f"(exit {rc}) -- not falling back. Fix the error and rerun with --resume.",
              file=sys.stderr)
        return 1

    if ns.dry_run:
        return 0
    if not attempted_any:
        print("[wrapper] ERROR: every model is skipped on daily quota. "
              "Wait for UTC midnight, or pass --reset-skips to force a retry.",
              file=sys.stderr)
        return 2
    return 1  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
