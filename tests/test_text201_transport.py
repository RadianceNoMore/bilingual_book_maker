"""text201: bounded endpoint transport circuit and recovery regressions."""

import pytest

from book_maker import redaction
from book_maker.marker_workflow import MarkerValidationError, _ChunkRequestStats
from book_maker.provider_refusal import ProviderRefusal
from test_text198_fresh_run import _payload_ids, _workflow


class APIConnectionError(Exception):
    """Named as the OpenAI SDK names it; the classifier matches the name."""


def _connection_error():
    error = APIConnectionError("Connection error.")
    error.__cause__ = ConnectionRefusedError(111, "Connection refused")
    return error


def test_outage_is_three_same_batch_sends(tmp_path, monkeypatch, capsys):
    workflow = _workflow(tmp_path)
    calls, waits = [], []
    monkeypatch.setattr(workflow, "_backoff", lambda n, **kw: waits.append(n))

    def send(payload, feedback=""):
        calls.append(_payload_ids(payload))
        raise _connection_error()

    with pytest.raises(MarkerValidationError, match="transport circuit") as failure:
        workflow.translate(
            ["Source."] * 40,
            send,
            lambda *args: None,
            identities=[str(index) for index in range(40)],
        )

    assert len(calls) == 3
    assert all(ids == calls[0] for ids in calls)
    assert all(len(ids) == 40 for ids in calls)
    assert waits == [1, 2]
    output = capsys.readouterr().out
    assert "No paragraph attempt charged" in output
    assert "3 logical request(s)" in output
    assert "120 paragraph participation(s)" in output
    assert "transport sends: 3" in output
    assert "40 unit(s) pending" in output


def test_intermittent_outage_preserves_validation_budget(tmp_path, monkeypatch):
    workflow = _workflow(tmp_path, max_attempts=1)
    calls = []
    monkeypatch.setattr(workflow, "_backoff", lambda *args, **kwargs: None)

    def send(payload, feedback=""):
        calls.append(_payload_ids(payload))
        if len(calls) < 3:
            raise ConnectionError("offline")
        return '<bbm-response><bbm-unit id="a">Translated.</bbm-unit></bbm-response>'

    assert workflow.translate(
        ["Source."], send, lambda *args: None, identities=["a"]
    ) == ["Translated."]
    assert len(calls) == 3
    assert all(ids == ["a"] for ids in calls)


def test_partial_acceptance_survives_endpoint_outage(tmp_path, monkeypatch):
    workflow = _workflow(tmp_path)
    calls = []
    monkeypatch.setattr(workflow, "_backoff", lambda *args, **kwargs: None)

    def send(payload, feedback=""):
        ids = _payload_ids(payload)
        calls.append(ids)
        if len(calls) == 1:
            return '<bbm-response><bbm-unit id="a">Accepted.</bbm-unit></bbm-response>'
        raise ConnectionError("offline")

    with pytest.raises(MarkerValidationError, match="transport circuit"):
        workflow.translate(
            ["First.", "Second."],
            send,
            lambda *args: None,
            identities=["a", "b"],
        )

    assert calls == [["a", "b"], ["b"], ["b"], ["b"]]
    assert workflow.recovery_master("a", "First.") == "Accepted."


def test_intermittent_transport_has_finite_total_budget(tmp_path, monkeypatch):
    workflow = _workflow(tmp_path, max_attempts=20)
    calls, waits = [], []
    monkeypatch.setattr(workflow, "_backoff", lambda n, **kw: waits.append(n))

    def send(payload, feedback=""):
        calls.append(payload)
        if len(calls) % 2:
            raise ConnectionError("offline")
        return "<bbm-response></bbm-response>"

    with pytest.raises(MarkerValidationError, match="chunk-wide transport budget"):
        workflow.translate(["Source."], send, lambda *args: None, identities=["a"])

    # Ten transport failures, separated by usable responses, hit the total cap.
    assert len(calls) == 19
    assert len(waits) == 9


def test_explicit_provider_refusal_is_still_one_send(tmp_path, monkeypatch):
    workflow = _workflow(tmp_path)
    calls, sleeps = [], []
    monkeypatch.setattr(workflow, "_backoff", lambda *args, **kwargs: sleeps.append(args))

    def send(payload, feedback=""):
        calls.append(payload)
        raise ProviderRefusal("policy")

    with pytest.raises(ProviderRefusal):
        workflow.translate(["Source."], send, lambda *args: None, identities=["a"])

    assert len(calls) == 1
    assert sleeps == []
    assert workflow.state["diagnostics"]["failures"][-1]["stage"] == "provider-refusal"


def test_no_sleep_after_final_transport_send(tmp_path, monkeypatch):
    import book_maker.marker_workflow as workflow_module

    delays = []
    monkeypatch.setattr(
        workflow_module.time, "sleep", lambda seconds: delays.append(seconds)
    )
    workflow = _workflow(tmp_path)

    def send(payload, feedback=""):
        raise ConnectionError("offline")

    with pytest.raises(MarkerValidationError):
        workflow.translate(["Source."], send, lambda *args: None, identities=["a"])

    assert delays == [1, 2]


def test_endpoint_and_chain_are_explained_without_secrets(tmp_path, capsys):
    workflow = _workflow(tmp_path)
    workflow.model.api_base = (
        "https://user:supersecret@example.test:8443/v1?api_key=sk-verysecret"
    )
    redaction.remember("supersecret", "sk-verysecret")
    try:
        def send(payload, feedback=""):
            raise _connection_error()

        with pytest.raises(MarkerValidationError):
            workflow.translate(["Source."], send, lambda *args: None, identities=["a"])
    finally:
        redaction.forget_all()

    output = capsys.readouterr().out
    assert "https://example.test:8443/v1" in output
    assert "APIConnectionError" in output
    assert "ConnectionRefusedError" in output
    assert "supersecret" not in output
    assert "sk-verysecret" not in output
    assert "user:" not in output
    assert "?api_key" not in output


def test_chunk_stats_count_transport_sends_and_partial_metadata():
    stats = _ChunkRequestStats()
    stats.record_send(40)
    stats.record_missing_response()
    stats.record_transport_failure()

    assert stats.logical_requests == 1
    assert stats.paragraph_participations == 40
    assert stats.transport_sends == 1
    assert stats.provider_calls_text() == "unavailable"
    assert stats.usage_text() == "unavailable"


def test_model_can_lower_the_transport_send_limit(tmp_path, monkeypatch):
    workflow = _workflow(tmp_path)
    workflow.model.max_transport_attempts = 1
    calls = []
    monkeypatch.setattr(workflow, "_backoff", lambda *args, **kwargs: calls.append("sleep"))

    def send(payload, feedback=""):
        calls.append("send")
        raise ConnectionError("offline")

    with pytest.raises(MarkerValidationError):
        workflow.translate(["Source."], send, lambda *args: None, identities=["a"])

    assert calls == ["send"]
