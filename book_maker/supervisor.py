"""Checkpoint-aware supervision for the translation launcher.

Only exit 75 plus a freshly verified recovery authorizes automatic resume.
Other exits (including interruption/refusal/configuration/write errors) pass
through. Counters and diagnostics changing are never treated as book progress.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


RECOVERABLE_EXIT = 75
STATUS_ENV = "BBM_RECOVERY_STATUS"


def _error_chain(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def publish_recovery(error, recovery_path, *, fatal=False):
    """Authorize a resume only for a known failure and a readable saved bundle."""
    from book_maker.marker_bundle import read_state
    from book_maker.marker_workflow import RecoverableWorkflowError
    from book_maker.provider_refusal import ProviderRefusal
    from book_maker.loader.helper import _is_retryable

    chain = list(_error_chain(error))
    if fatal or not any(isinstance(item, RecoverableWorkflowError) for item in chain):
        return False
    if any(isinstance(item, ProviderRefusal) or not _is_retryable(item) for item in chain):
        return False
    # Read the on-disk state, not the live objects: an unsuccessful save must
    # never promise durable progress. Corruption/write failures stop the run.
    state = read_state(recovery_path, validate=True)
    windows = (state.get("source_windows") or {}).get("windows") or []
    progress = {
        "run": state.get("run", {}).get("run_id"),
        "prepared": [row["window_id"] for row in windows],
        "accepted": sorted({
            unit["unit_id"] for row in windows for unit in row.get("units", [])
            if unit.get("accepted")
        } | {
            row["identity"] for row in state.get("paragraph_recovery", {}).get("entries", [])
            if isinstance(row.get("identity"), str)
        }),
        "summaries": [row["window_id"] for row in windows if row.get("status") == "complete"],
        "mapped": sorted(label for label, row in state.get("catalogue", {}).items() if row.get("target")),
        "localized": state.get("checkpoint", {}).get("localized_cache", {}),
    }
    signature = hashlib.sha256(json.dumps(progress, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    status = {"version": 1, "progress": signature, "recovery": str(Path(recovery_path).resolve())}
    destination = os.environ.get(STATUS_ENV)
    if destination:
        path = Path(destination)
        candidate = path.with_suffix(".new")
        candidate.write_text(json.dumps(status), encoding="utf-8")
        candidate.replace(path)
    return True


def run_supervised(command, *, max_stalled_resumes=3, retry_delay=30,
                   runner=subprocess.run, sleep=time.sleep):
    previous = None
    stalled = 0
    resumes = 0
    with tempfile.TemporaryDirectory(prefix="bbm-supervisor-") as directory:
        status_path = Path(directory) / "recovery.json"
        environment = dict(os.environ, **{STATUS_ENV: str(status_path), "PYTHONUNBUFFERED": "1"})
        while True:
            status_path.unlink(missing_ok=True)
            try:
                result = runner(command, env=environment)
            except KeyboardInterrupt:
                return 130
            if result.returncode != RECOVERABLE_EXIT:
                return result.returncode
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                if status.get("version") != 1 or not status.get("progress") or not Path(status["recovery"]).is_file():
                    raise ValueError("invalid recovery status")
            except (OSError, ValueError, KeyError, TypeError):
                print("Supervisor: no verified recovery status; stopping.", flush=True)
                return RECOVERABLE_EXIT
            progress = status["progress"]
            stalled = stalled + 1 if progress == previous else 0
            previous = progress
            if stalled >= max_stalled_resumes:
                print(f"Supervisor: {max_stalled_resumes} automatic resumes made no durable progress; "
                      f"stopping with the recovery preserved at {status['recovery']}.", flush=True)
                return RECOVERABLE_EXIT
            resumes += 1
            delay = min(120, retry_delay * 2 ** stalled)
            print(f"Supervisor: retryable stage stopped; automatic resume {resumes} in {delay}s "
                  f"({stalled}/{max_stalled_resumes} without progress). Recovery: {status['recovery']}", flush=True)
            try:
                sleep(delay)
            except KeyboardInterrupt:
                return 130
            if "--resume" not in command:
                command = [*command, "--resume"]


def main():
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        raise SystemExit("usage: python -m book_maker.supervisor -- make_book.py [arguments]")
    raise SystemExit(run_supervised([sys.executable, "-X", "utf8", *args]))


if __name__ == "__main__":
    main()
