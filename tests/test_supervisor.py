import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from book_maker.supervisor import RECOVERABLE_EXIT, STATUS_ENV, publish_recovery, run_supervised
from book_maker.marker_workflow import MarkerValidationError, RecoverableWorkflowError
from book_maker.provider_refusal import ProviderRefusal


def test_supervisor_resumes_only_unfinished_work_and_resets_stall_budget(tmp_path):
    recovery = tmp_path / "recovery.epub"
    recovery.write_bytes(b"test checkpoint")
    calls = []
    delays = []
    progress = iter(["one", "one", "two", "two", "three", None])

    def runner(command, env):
        calls.append(command)
        value = next(progress)
        if value is None:
            return SimpleNamespace(returncode=0)
        Path(env[STATUS_ENV]).write_text(json.dumps({
            "version": 1, "progress": value, "recovery": str(recovery),
        }))
        return SimpleNamespace(returncode=RECOVERABLE_EXIT)

    assert run_supervised(["python", "make_book.py"], runner=runner, sleep=delays.append) == 0
    assert "--resume" not in calls[0]
    assert all(command.count("--resume") == 1 for command in calls[1:])
    assert delays == [30, 60, 30, 60, 30]


def test_supervisor_stops_when_resumes_make_no_progress(tmp_path):
    recovery = tmp_path / "recovery.epub"
    recovery.write_bytes(b"checkpoint")
    calls = []

    def runner(command, env):
        calls.append(command)
        Path(env[STATUS_ENV]).write_text(json.dumps({
            "version": 1, "progress": "same", "recovery": str(recovery),
        }))
        return SimpleNamespace(returncode=RECOVERABLE_EXIT)

    assert run_supervised(["python", "make_book.py"], runner=runner, sleep=lambda _: None) == RECOVERABLE_EXIT
    assert len(calls) == 4  # first run plus three stalled resumes


@pytest.mark.parametrize("code", [0, 1, 130, 75])
def test_no_status_or_terminal_exit_never_restarts(code):
    calls = []

    def runner(command, env):
        calls.append(command)
        return SimpleNamespace(returncode=code)

    assert run_supervised(["python"], runner=runner, sleep=lambda _: None) == code
    assert len(calls) == 1


def test_recovery_classification_preserves_terminal_causes(monkeypatch, tmp_path):
    import book_maker.marker_bundle as bundle

    state = {"run": {"run_id": "stable"}, "source_windows": {"windows": []}}
    monkeypatch.setattr(bundle, "read_state", lambda path, validate: state)
    status_path = tmp_path / "status.json"
    monkeypatch.setenv(STATUS_ENV, str(status_path))
    retryable = RecoverableWorkflowError("exhausted stage")
    outer = MarkerValidationError("window stopped")
    outer.__cause__ = retryable
    assert publish_recovery(outer, tmp_path / "test.epub")
    signature = json.loads(status_path.read_text())["progress"]
    state["diagnostics"] = {"requests": 999}
    assert publish_recovery(outer, tmp_path / "test.epub")
    assert json.loads(status_path.read_text())["progress"] == signature
    assert not publish_recovery(MarkerValidationError("invalid plan"), "missing.epub")
    assert not publish_recovery(outer, "missing.epub", fatal=True)
    retryable.__cause__ = ProviderRefusal("refused")
    assert not publish_recovery(outer, "missing.epub")


def test_unreadable_recovery_cannot_authorize_restart(tmp_path):
    with pytest.raises(Exception):
        publish_recovery(RecoverableWorkflowError("stage exhausted"), tmp_path / "missing.epub")


def test_real_child_process_resumes_through_the_command_entry_point(tmp_path):
    import os
    import subprocess
    import sys

    child = tmp_path / "child.py"
    checkpoint = tmp_path / "checkpoint"
    child.write_text('''import json, os, sys
from pathlib import Path
checkpoint = Path(sys.argv[1])
if "--resume" not in sys.argv:
    checkpoint.write_text("accepted sibling")
    Path(os.environ["BBM_RECOVERY_STATUS"]).write_text(json.dumps({
        "version": 1, "progress": "accepted sibling", "recovery": str(checkpoint)
    }))
    sys.exit(75)
assert checkpoint.read_text() == "accepted sibling"
checkpoint.write_text("completed")
''', encoding="utf-8")
    calls = []

    def runner(command, env):
        calls.append(command)
        return subprocess.run(command, env=env, timeout=10)

    assert run_supervised([sys.executable, str(child), str(checkpoint)],
                          runner=runner, sleep=lambda _: None) == 0
    assert len(calls) == 2
    assert checkpoint.read_text() == "completed"
