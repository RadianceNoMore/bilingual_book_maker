"""Response-boundary diagnostics for the marker translation path."""

import json
from types import SimpleNamespace

from book_maker.marker_trace import trace_location
from book_maker.marker_workflow import (
    MarkerWorkflow,
    paragraph_request,
    parse_paragraph_reply,
)
from book_maker.session_context import SessionHistory
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    response_framing_facts,
)


class FakeCapabilities:
    def sampling_kwargs(self, model, temperature):
        return {}

    def compact_cap_kwargs(self, model, max_tokens):
        return {"max_tokens": int(max_tokens)}

    def note_compact_cap_rejected(self, model):
        return "max_tokens"


class FakeCompletions:
    def __init__(self, outer, content, finish_reason="stop"):
        self.outer = outer
        self.content = content
        self.finish_reason = finish_reason

    def create(self, **request):
        self.outer.requests.append(request)
        return SimpleNamespace(
            model="fake-model",
            usage=SimpleNamespace(
                prompt_tokens=11, completion_tokens=7, total_tokens=18
            ),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content, refusal=None),
                    finish_reason=self.finish_reason,
                )
            ],
        )


class FakeClient:
    def __init__(self, content, finish_reason="stop"):
        self.requests = []
        self.chat = SimpleNamespace(
            completions=FakeCompletions(self, content, finish_reason)
        )


def _translator(content, finish_reason="stop"):
    translator = object.__new__(ChatGPTAPI)
    translator.model = "fake-model"
    translator.extra_body = None
    translator.temperature = 0
    translator.capabilities = FakeCapabilities()
    translator.usage = SimpleNamespace(note=lambda *args, **kwargs: None)
    translator.openai_client = FakeClient(content, finish_reason)
    translator._protected_names_state = None
    translator._marker_state = None
    return translator


def test_completion_metadata_records_attempt_units_usage_and_cap():
    translator = _translator("<bbm-response></bbm-response>")
    identities, payload = paragraph_request(["Alice left.", "Bob arrived."], ["u001", "u002"])
    translator._marker_diagnostic_context = {
        "window_id": "w000006",
        "chunk": 3,
        "chunk_count": 4,
        "requested_units": 2,
    }

    translator._protected_completion(
        [{"role": "user", "content": payload}],
        "paragraph-response",
        max_tokens=128,
    )

    metadata = translator._protected_response_metadata
    assert metadata["stage"] == "paragraph-response"
    assert metadata["window_id"] == "w000006"
    assert metadata["chunk"] == 3
    assert metadata["chunk_count"] == 4
    assert metadata["requested_units"] == 2
    assert metadata["provider_calls"] == 1
    assert metadata["finish_reason"] == "stop"
    assert metadata["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert metadata["response_characters"] == len("<bbm-response></bbm-response>")
    assert len(metadata["response_sha256"]) == 64
    assert metadata["output_cap"] == {
        "requested": {"max_tokens": 128},
        "accepted": True,
    }
    assert metadata["framing"]["multiple_roots"] is False
    assert metadata["attempt_id"].startswith("paragraph-response-")


def test_framing_facts_detect_multiple_roots_at_the_client_boundary():
    facts = response_framing_facts(
        '<bbm-response><bbm-unit id="u001">A</bbm-unit></bbm-response>'
        '<bbm-response><bbm-unit id="u002">B</bbm-unit></bbm-response>'
    )
    assert facts["bbm_response_openings"] == 2
    assert facts["bbm_response_closings"] == 2
    assert facts["multiple_roots"] is True


def test_opt_in_trace_captures_full_completion_and_sanitized_request(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BBM_MARKER_TRACE_DIR", str(tmp_path / "trace"))
    response = (
        '<bbm-response><bbm-unit id="u001">Indeed.</bbm-unit></bbm-response>'
    )
    translator = _translator(response)
    translator._marker_diagnostic_context = {
        "window_id": "w000006",
        "chunk": 3,
        "chunk_count": 4,
        "requested_units": 1,
    }
    _identities, payload = paragraph_request(["Indeed."], ["u001"])

    translator._protected_completion(
        [{"role": "user", "content": payload}], "paragraph-response"
    )

    location = trace_location()
    assert location is not None
    lines = (tmp_path / "trace" / "marker-trace.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["response"] == response
    assert record["request"]["messages"][0]["content"] == payload
    assert "api_key" not in record["request"]
    assert "authorization" not in record["request"]
    assert record["output_cap"]["accepted"] is None
    assert record["provider_calls"] == 1


class _WorkflowModel:
    language = "en"
    source_language = "zh"
    model = "offline"
    max_chunk_attempts = 5

    def __init__(self):
        self.session = SessionHistory()
        self._fatal_error_detected = False
        self._protected_last_request = None
        self._protected_response_metadata = None


def test_failure_record_links_attempt_request_response_and_never_keeps_secrets(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("BBM_MARKER_TRACE_DIR", raising=False)
    source = tmp_path / "source.epub"
    source.write_bytes(b"diagnostic source")
    model = _WorkflowModel()
    model._protected_last_request = {
        "model": "fake",
        "messages": [{"role": "user", "content": "<bbm-request/>"}],
        "api_key": "must-not-be-recorded",
        "headers": {"Authorization": "Bearer secret"},
    }
    model._protected_response_metadata = {
        "attempt_id": "paragraph-response-00001",
        "stage": "paragraph-response",
        "window_id": "w000006",
        "chunk": 3,
        "requested_units": 2,
        "provider_calls": 1,
        "finish_reason": "stop",
        "usage": None,
        "framing": {"multiple_roots": True},
    }
    workflow = MarkerWorkflow(
        model,
        str(source),
        settings={
            "request_max_units": 40,
            "request_max_tokens": 8000,
            "run_fingerprint": "diagnostics",
        },
    )

    workflow.record_failure(
        "paragraph-response", "<bbm-response>raw</bbm-response>", "test reason"
    )

    record = workflow.state["diagnostics"]["failures"][-1]
    assert record["stage"] == "paragraph-response"
    assert record["reason"] == "test reason"
    assert record["response"] == "<bbm-response>raw</bbm-response>"
    assert record["request"]["model"] == "fake"
    assert "api_key" not in record["request"]
    assert "headers" not in record["request"]
    assert record["response_metadata"]["attempt_id"] == "paragraph-response-00001"
    assert record["response_metadata"]["framing"]["multiple_roots"] is True
    assert record["trace"] is None


def test_unfinished_attribute_restart_keeps_only_the_complete_unit():
    raw = (
        '<bbm-response><bbm-unit id="u001">${Alice} left.</bbm-unit>'
        '<bbm-unit id="u002" data-state="unfinished'
    )
    reply = parse_paragraph_reply(raw, ["u001", "u002"])

    assert reply.texts == {"u001": "${Alice} left."}
    assert "u002" not in reply.texts
    assert reply.framing_error
    assert reply.salvaged is True
    assert reply.errors["u002"] == "duplicate or ambiguous bbm-unit id"
    assert any(
        "unfinished or malformed" in item and "ambiguous" in item
        for item in reply.diagnostics
    )


def test_absent_finish_metadata_is_recorded_as_unavailable():
    translator = _translator("<bbm-response></bbm-response>", finish_reason=None)
    _identities, payload = paragraph_request(["Alice left."], ["u001"])

    translator._protected_completion(
        [{"role": "user", "content": payload}], "paragraph-response"
    )

    metadata = translator._protected_response_metadata
    assert metadata["finish_reason"] is None
    assert metadata["usage"]["total_tokens"] == 18


def test_durable_counts_report_journal_only_overlap_separately(tmp_path):
    source = tmp_path / "source.epub"
    source.write_bytes(b"count source")
    model = _WorkflowModel()
    workflow = MarkerWorkflow(
        model,
        str(source),
        settings={
            "request_max_units": 40,
            "request_max_tokens": 8000,
            "run_fingerprint": "counts",
        },
    )
    workflow.state["checkpoint"] = {"accepted_job_ids": ["a", "b"]}
    workflow.state["paragraph_recovery"] = {
        "version": 1,
        "entries": [
            {"identity": "b", "source": "B.", "master": "B."},
            {"identity": "c", "source": "C.", "master": "C."},
        ],
    }

    assert workflow._durable_counts() == (2, 1, 3)
