"""Regression coverage for the text_194 w000003 extraction retry-contract failure.

The fixture is the exact 143-unit window whose reconstructed digest matches
the saved ``9cafbfd1...`` plan, plus the three provider replies from the failed
run.  ``哈基黑`` occurs nowhere in the window; it is a w000002 alias for
``白井黑子`` carried in by the prior summary.  ``操祈`` occurs literally and
was accepted in attempts 1 and 2 but omitted from attempt 3.

The contract under test:

* a rejected reply's names are harvested only as retry candidates;
* a later structurally valid reply must explicitly decide every candidate;
* a declared context-only label may be excluded without being a candidate;
* wrong-window, invalid-schema, malformed-JSON, and bad legacy-citation
  replies are not evidence for candidates.
"""

import json
from pathlib import Path

import pytest

from book_maker.marker_names import WINDOW_WORKFLOW
from book_maker.marker_workflow import MarkerWorkflow
from book_maker.source_windows import (
    SourceWindow,
    SourceWindowError,
    WindowUnit,
    context_only_labels,
    extraction_candidates,
    source_window_prompt,
    validate_extraction,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "text194_w000003_failures.json").read_text(
        encoding="utf-8"
    )
)
REPLIES = [row["response"] for row in FIXTURE["diagnostics"]]


def _window():
    units = tuple(
        WindowUnit(row["unit_id"], row["unit_id"], row["source"], index, "text194")
        for index, row in enumerate(FIXTURE["units"])
    )
    return SourceWindow(FIXTURE["window_id"], units, 8180)


class ScriptedExtractionModel:
    language = "en"
    source_language = "zh"
    model = "offline"
    max_chunk_attempts = 3

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self._protected_last_response = ""
        self._protected_response_metadata = None

    def extract_window_names(self, prompt):
        self.requests.append(prompt)
        self._protected_last_request = {
            "messages": [{"role": "user", "content": prompt}]
        }
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _workflow(tmp_path, replies):
    model = ScriptedExtractionModel(replies)
    source = tmp_path / "source.epub"
    source.write_bytes(b"text194 source placeholder")
    workflow = MarkerWorkflow(
        model,
        str(source),
        settings={
            "workflow": WINDOW_WORKFLOW,
            "run_fingerprint": "text194",
            "name_window_tokens": 8192,
            "source_summary_tokens": 1500,
        },
    )
    window = _window()
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=1500,
        run_fingerprint="text194",
        cross_chapters=False,
    )
    workflow.source_windows_state["current_summary"] = FIXTURE["prior_summary"]
    return workflow, model, window


def _prompt_data(prompt):
    return json.loads(prompt.rsplit("\n", 1)[-1])


def test_text194_first_reply_context_only_name_is_discarded(tmp_path):
    workflow, model, window = _workflow(tmp_path, [REPLIES[0]])

    record, prepared, _spans = workflow.prepare_source_window(
        window, source_language="Chinese"
    )

    assert len(model.requests) == 1
    data = _prompt_data(model.requests[0])
    assert "哈基黑" in data["context_only_labels"]
    assert "哈基黑" not in data["retry_candidates"]
    assert "accepted_candidates" not in data
    assert "哈基黑" not in record["extracted_names"]
    assert "操祈" in record["extracted_names"]
    joined = "\n".join(prepared.values())
    assert "${食蜂操祈}" in joined
    assert "${操祈}" in joined


def test_text194_third_reply_without_candidate_decision_is_rejected():
    window = _window()
    context = set(context_only_labels(window, FIXTURE["prior_summary"]))
    candidates = extraction_candidates(REPLIES[0], window)

    with pytest.raises(SourceWindowError, match="without deciding them: 操祈"):
        validate_extraction(
            REPLIES[2], window, candidates, context_only=context
        )


def test_text194_second_reply_direct_validation_accepts_context_only_exclusion():
    window = _window()
    context = set(context_only_labels(window, FIXTURE["prior_summary"]))
    candidates = extraction_candidates(REPLIES[0], window)

    extracted = validate_extraction(
        REPLIES[1], window, candidates, context_only=context
    )

    names = {row["source"] for row in extracted}
    assert "操祈" in names
    assert "哈基黑" not in names


def test_text194_first_reply_harvests_only_literal_candidates():
    window = _window()
    candidates = extraction_candidates(REPLIES[0], window)
    assert "操祈" in candidates
    assert "哈基黑" not in candidates
    assert len(candidates) == 9


def test_text194_malformed_reply_is_rejected_and_harvests_nothing():
    window = _window()
    assert extraction_candidates("not valid json", window) == []
    with pytest.raises(SourceWindowError):
        validate_extraction("not valid json", window)


def test_wrong_window_prior_reply_does_not_seed_candidates(tmp_path):
    window = _window()
    other = SourceWindow(
        "w000009",
        (WindowUnit("u9", "u9", "Alice left.", 0, "other"),),
        5,
    )
    wrong = json.dumps(
        {"window_id": "w000009", "names": ["Alice"], "excluded_candidates": []}
    )
    assert extraction_candidates(wrong, window) == []

    reply = json.dumps(
        {"window_id": "w000003", "names": ["一色"], "excluded_candidates": []},
        ensure_ascii=False,
    )
    workflow, model, window = _workflow(tmp_path, [reply])
    workflow.state["diagnostics"]["failures"].append(
        {
            "stage": "window-name-extraction",
            "request": {
                "messages": [
                    {"role": "user", "content": source_window_prompt(other, "", "en")}
                ]
            },
            "response": wrong,
        }
    )

    workflow.prepare_source_window(window, source_language="Chinese")

    data = _prompt_data(model.requests[0])
    assert data["retry_candidates"] == []


def test_invalid_legacy_citation_prior_reply_does_not_seed_candidates(tmp_path):
    window = _window()
    invalid = json.dumps(
        {
            "window_id": "w000003",
            "names": [{"source": "操祈", "unit_ids": ["unknown-unit"]}],
            "excluded_candidates": [],
        },
        ensure_ascii=False,
    )
    assert extraction_candidates(invalid, window) == []

    reply = json.dumps(
        {"window_id": "w000003", "names": ["一色"], "excluded_candidates": []},
        ensure_ascii=False,
    )
    workflow, model, window = _workflow(tmp_path, [reply])
    workflow.state["diagnostics"]["failures"].append(
        {
            "stage": "window-name-extraction",
            "request": {
                "messages": [
                    {
                        "role": "user",
                        "content": source_window_prompt(
                            window, FIXTURE["prior_summary"], "Chinese"
                        ),
                    }
                ]
            },
            "response": invalid,
        }
    )

    workflow.prepare_source_window(window, source_language="Chinese")

    data = _prompt_data(model.requests[0])
    assert data["retry_candidates"] == []


def test_literal_label_cannot_be_excluded_without_being_a_retry_candidate():
    window = _window()
    raw = json.dumps(
        {
            "window_id": "w000003",
            "names": ["一色"],
            "excluded_candidates": ["操祈"],
        },
        ensure_ascii=False,
    )
    with pytest.raises(SourceWindowError, match="cannot hide literal"):
        validate_extraction(raw, window)


def test_unrelated_absent_exclusion_is_ignored_and_not_protected():
    window = _window()
    raw = json.dumps(
        {
            "window_id": "w000003",
            "names": ["一色"],
            "excluded_candidates": ["不存在"],
        },
        ensure_ascii=False,
    )
    ignored = []
    extracted = validate_extraction(
        raw,
        window,
        context_only={"哈基黑"},
        ignored_exclusions=ignored,
    )
    assert [row["source"] for row in extracted] == ["一色"]
    assert ignored == ["不存在"]


def test_context_only_labels_are_prior_summary_labels_absent_here():
    window = _window()
    context = context_only_labels(window, FIXTURE["prior_summary"])
    assert "哈基黑" in context
    assert "操祈" not in context

    present = SourceWindow(
        "w000003",
        (WindowUnit("u1", "u1", "哈基黑来了。", 0, "c1"),),
        5,
    )
    assert "哈基黑" not in context_only_labels(present, FIXTURE["prior_summary"])


def test_cross_window_diagnostics_do_not_seed_candidates(tmp_path):
    reply = json.dumps(
        {"window_id": "w000003", "names": ["一色"], "excluded_candidates": []},
        ensure_ascii=False,
    )
    workflow, model, window = _workflow(tmp_path, [reply])
    other = SourceWindow(
        "w000009",
        (WindowUnit("u9", "u9", "Alice left.", 0, "other"),),
        5,
    )
    workflow.state["diagnostics"]["failures"].append(
        {
            "stage": "window-name-extraction",
            "request": {
                "messages": [
                    {"role": "user", "content": source_window_prompt(other, "", "en")}
                ]
            },
            "response": json.dumps({"window_id": "w000009", "names": ["Alice"]}),
        }
    )

    workflow.prepare_source_window(window, source_language="Chinese")

    data = _prompt_data(model.requests[0])
    assert data["retry_candidates"] == []
    assert "Alice" not in data["context_only_labels"]
