"""text198 fresh-run regressions: queue draining and request accounting."""

from collections import Counter

import pytest

from book_maker.marker_workflow import (
    MarkerValidationError,
    MarkerWorkflow,
    _ChunkRequestStats,
    retry_unit_cap,
)
from book_maker.session_context import SessionHistory


class Model:
    language = "en"
    source_language = "zh"
    model = "offline"
    max_chunk_attempts = 5

    def __init__(self):
        self.session = SessionHistory()
        self._fatal_error_detected = False
        self._marker_pending_exchanges = None
        self._protected_last_request = None
        self._protected_response_metadata = None

    def _record_session_exchange(self, user, reply):
        self.session.append(user, reply)

    def marker_mapping_request(self, prompt):
        return '{"mappings": []}'


def _workflow(tmp_path, model=None, max_attempts=5):
    source = tmp_path / "source.epub"
    source.write_bytes(b"stable text198 source")
    model = model or Model()
    model.max_chunk_attempts = max_attempts
    return MarkerWorkflow(
        model,
        str(source),
        settings={
            "request_max_units": 40,
            "request_max_tokens": 8000,
            "run_fingerprint": "text198",
        },
    )


def _payload_ids(payload):
    from lxml import etree

    return [node.get("id") for node in etree.fromstring(payload.encode())]


def _run_completes_after_exhausted_sibling(tmp_path, monkeypatch):
    workflow = _workflow(tmp_path, max_attempts=5)
    sources = ["Unit u021.", "Unit u022.", "Unit u003."]
    identities = ["u021", "u022", "u003"]
    calls = Counter()

    def scheduler(outstanding, initial_cap, token_budget):
        queue = sorted(outstanding, key=lambda item: item.index)
        if not queue:
            return []
        zero = [item for item in queue if item.attempts == 0]
        if zero:
            return zero
        target = next((item for item in queue if item.identity == "u003"), None)
        others = [item for item in queue if item.identity != "u003"]
        if target is not None and others:
            if target.attempts <= max(item.attempts for item in others):
                return [target]
        if target is not None and not others:
            return [target]
        return others[: retry_unit_cap(max(item.attempts for item in others) + 1, 40)]

    monkeypatch.setattr(
        MarkerWorkflow, "_next_retry_batch", staticmethod(scheduler)
    )

    def send(payload, feedback=""):
        ids = _payload_ids(payload)
        for identity in ids:
            calls[identity] += 1
        if ids == ["u003"]:
            return (
                '<bbm-response><bbm-unit id="u003">One.</bbm-unit>'
                '<bbm-unit id="u003">Two.</bbm-unit></bbm-response>'
            )
        if "u003" in ids:
            return '<bbm-response><bbm-unit id="u021">unfinished'
        if all(calls[identity] >= 5 for identity in ids):
            return (
                "<bbm-response>"
                + "".join(
                    f'<bbm-unit id="{identity}">Translated {identity}.</bbm-unit>'
                    for identity in ids
                )
                + "</bbm-response>"
            )
        return '<bbm-response><bbm-unit id="u021">unfinished'

    with pytest.raises(MarkerValidationError) as failure:
        workflow.translate(
            sources, send, lambda *args: None, identities=identities
        )

    message = str(failure.value)
    assert "u003" in message
    assert "u021" not in message.split("Outstanding ids:")[-1]
    assert "u022" not in message.split("Outstanding ids:")[-1]
    # u003 is never sent a sixth time; its siblings still receive their fifth.
    assert calls["u003"] == 5
    assert calls["u021"] == 5
    assert calls["u022"] == 5
    accepted = {
        entry["identity"]: entry["master"]
        for entry in workflow.recovery_entries()
    }
    assert accepted == {
        "u021": "Translated u021.",
        "u022": "Translated u022.",
    }


def test_exhausted_sibling_does_not_terminate_eligible_repairs(tmp_path, monkeypatch):
    _run_completes_after_exhausted_sibling(tmp_path, monkeypatch)


def test_exhaustion_reports_actual_requests_and_participations(
    tmp_path, monkeypatch, capsys
):
    _run_completes_after_exhausted_sibling(tmp_path, monkeypatch)
    output = capsys.readouterr().out
    assert "logical request(s)" in output
    assert "paragraph participation(s)" in output
    assert "6/5" not in output
    assert "This chunk invocation" in output


def test_chunk_stats_mark_partial_measurements_explicitly():
    stats = _ChunkRequestStats()
    stats.record_send(8)
    stats.record_response(
        {
            "provider_calls": 1,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
    )
    stats.record_send(1)
    stats.record_missing_response()

    assert stats.logical_requests == 2
    assert stats.paragraph_participations == 9
    assert stats.provider_calls_text() == "at least 1 (partial)"
    assert stats.usage_text() == "15 observed (partial)"


def test_retry_schedule_cap_unchanged():
    assert [retry_unit_cap(attempt, 40) for attempt in range(1, 6)] == [
        40,
        8,
        2,
        1,
        1,
    ]


BOILERPLATE = "I encountered an error doing what you asked. Could you try again?"


def test_provider_boilerplate_uses_bounded_backoff_and_exhausts(tmp_path, monkeypatch):
    delays = []
    monkeypatch.setattr(
        MarkerWorkflow,
        "_backoff",
        staticmethod(
            lambda attempts, stage="", *, base=1, cap=8: delays.append(
                (attempts, stage, base, cap)
            )
        ),
    )
    workflow = _workflow(tmp_path, max_attempts=3)

    def send(payload, feedback=""):
        return BOILERPLATE

    with pytest.raises(MarkerValidationError, match="exhausted"):
        workflow.translate(["Alice left."], send, lambda *args: None, identities=["a"])

    # The final attempt is not followed by a pointless sleep.
    assert [row[0] for row in delays] == [1, 2]
    assert all(row[1] == "Provider boilerplate reply" for row in delays)
    assert all(row[2:] == (2, 30) for row in delays)
    assert workflow.recovery_entries() == []


def test_provider_error_streak_resets_after_a_usable_reply(tmp_path, monkeypatch):
    delays = []
    monkeypatch.setattr(
        MarkerWorkflow,
        "_backoff",
        staticmethod(
            lambda attempts, stage="", *, base=1, cap=8: delays.append(attempts)
        ),
    )
    workflow = _workflow(tmp_path, max_attempts=3)

    def send(payload, feedback=""):
        if not delays:
            return BOILERPLATE
        identity = _payload_ids(payload)[0]
        source = "Alice left."
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            f"{source}</bbm-unit></bbm-response>"
        )

    result = workflow.translate(
        ["Alice left."], send, lambda *args: None, identities=["a"]
    )

    assert result == ["Alice left."]
    assert delays == [1]
