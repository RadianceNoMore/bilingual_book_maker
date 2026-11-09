"""text206: unverified suggestions are optional; rejected replies keep name evidence."""

import json

import pytest

from book_maker.marker_names import WINDOW_WORKFLOW
from book_maker.marker_workflow import MarkerWorkflow
from book_maker.session_context import SessionHistory
from book_maker.source_windows import (
    SourceWindow,
    SourceWindowError,
    WindowUnit,
    collect_unverified_name_suggestions,
    extraction_candidates,
    prepare_window,
    validate_extraction,
)

CANDIDATES = ["食蜂操祈", "一色", "御坂美琴"]
UNVERIFIED = ["食蜂派阀"]
W032_CANDIDATES = ["一方通行", "布束砥信", "御坂美琴", "爱玲", "食蜂操祈"]
W032_UNVERIFIED = ["御坂一色", "白井黑子"]


def _window(source="食蜂操祈和一色一起出现，御坂美琴看着她抬起小手。"):
    unit = WindowUnit("u1", "u1", source, 0, "chapter-1")
    return SourceWindow("w000022", (unit,), len(source))


def _reply(names, excluded=(), window_id="w000022"):
    return json.dumps(
        {
            "window_id": window_id,
            "names": list(names),
            "excluded_candidates": list(excluded),
        },
        ensure_ascii=False,
    )


def test_text206_rejected_reply_harvests_names_despite_current_source_exclusion():
    window = _window()
    raw = _reply(
        [*CANDIDATES, *UNVERIFIED],
        ["小手"],
    )

    with pytest.raises(SourceWindowError, match="cannot hide literal"):
        validate_extraction(
            raw,
            window,
            CANDIDATES,
            unverified_name_suggestions=UNVERIFIED,
        )

    # The rejected response still carries valid exact and unverified evidence.
    assert set(extraction_candidates(raw, window)) == set(CANDIDATES)
    assert collect_unverified_name_suggestions(raw, window) == UNVERIFIED
    assert "小手" not in extraction_candidates(raw, window)
    assert "小手" not in collect_unverified_name_suggestions(raw, window)


def test_text206_attempt_two_accepts_omitted_unverified_suggestions():
    window = _window()
    raw = _reply(CANDIDATES, ["交通工具"])
    ignored = []

    extracted = validate_extraction(
        raw,
        window,
        CANDIDATES,
        unverified_name_suggestions=UNVERIFIED,
        ignored_exclusions=ignored,
    )

    assert {row["source"] for row in extracted} == set(CANDIDATES)
    assert ignored == ["交通工具"]
    prepared, _spans = prepare_window(window, extracted)
    assert "食蜂派阀" not in prepared["u1"]
    assert "交通工具" not in prepared["u1"]


def test_text206_attempt_three_missing_exact_candidate_still_fails():
    window = _window()
    raw = _reply(["食蜂操祈", "一色"], ["交通工具"])

    with pytest.raises(
        SourceWindowError, match="without deciding them: 御坂美琴"
    ):
        validate_extraction(
            raw,
            window,
            CANDIDATES,
            unverified_name_suggestions=UNVERIFIED,
        )


def test_text206_current_source_literal_still_cannot_be_excluded():
    window = _window()
    raw = _reply(CANDIDATES, ["小手"])

    with pytest.raises(SourceWindowError, match="cannot hide literal"):
        validate_extraction(
            raw,
            window,
            CANDIDATES,
            unverified_name_suggestions=UNVERIFIED,
        )

    assert set(extraction_candidates(raw, window)) == set(CANDIDATES)


def test_text206_unverified_suggestion_in_names_is_discarded():
    window = _window()
    raw = _reply([*CANDIDATES, *UNVERIFIED])

    extracted = validate_extraction(
        raw,
        window,
        CANDIDATES,
        unverified_name_suggestions=UNVERIFIED,
    )
    assert {row["source"] for row in extracted} == set(CANDIDATES)
    assert all(row["source"] not in UNVERIFIED for row in extracted)


def test_text206_known_suggestion_repeated_in_names_and_exclusions_keeps_evidence():
    window = _window()
    raw = _reply([*CANDIDATES, *UNVERIFIED], [*UNVERIFIED])

    assert set(
        extraction_candidates(
            raw,
            window,
            prior_unverified_name_suggestions=UNVERIFIED,
        )
    ) == set(CANDIDATES)
    assert collect_unverified_name_suggestions(
        raw,
        window,
        prior_unverified_name_suggestions=UNVERIFIED,
    ) == []


def test_text206_off_window_name_repeated_in_names_and_exclusions_keeps_exact_evidence():
    window = _window()
    raw = _reply([*CANDIDATES, "食蜂派阀"], ["食蜂派阀"])

    assert set(extraction_candidates(raw, window)) == set(CANDIDATES)
    assert collect_unverified_name_suggestions(raw, window) == []
    with pytest.raises(SourceWindowError, match="does not occur literally"):
        validate_extraction(raw, window, CANDIDATES)


def test_text206_retry_prompt_keeps_exact_evidence_and_discards_off_window_conflict(
    tmp_path, monkeypatch
):
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"text206 exact evidence survives an absent-name conflict")
    window = _window()

    class Model:
        language = "zh"
        source_language = "zh"
        model = "offline"

        def __init__(self):
            self.session = SessionHistory()
            self.requests = []

        def extract_window_names(self, prompt):
            self.requests.append(prompt)
            if len(self.requests) == 1:
                raw = _reply([*CANDIDATES, "食蜂派阀"], ["食蜂派阀"])
            else:
                raw = _reply(CANDIDATES)
            self._protected_last_request = {
                "messages": [{"role": "user", "content": prompt}]
            }
            self._protected_last_response = raw
            return raw

    model = Model()
    workflow = MarkerWorkflow(
        model, str(source_path), settings={"workflow": WINDOW_WORKFLOW}
    )
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=1500,
        run_fingerprint="text206-absent-conflict-retry",
        cross_chapters=False,
    )
    monkeypatch.setattr(workflow, "_backoff", lambda *args, **kwargs: None)

    record, prepared, _spans = workflow.prepare_source_window(
        window, source_language="Chinese"
    )

    assert len(model.requests) == 2
    retry_json = model.requests[1][model.requests[1].rfind('{"window_id":'):]
    retry_data = json.loads(retry_json)
    assert set(retry_data["retry_candidates"]) == set(CANDIDATES)
    assert "食蜂派阀" not in retry_data["unverified_name_suggestions"]
    assert "食蜂派阀" in retry_data["previous_validation_error"]
    assert set(record["extracted_names"]) == set(CANDIDATES)
    assert set(workflow.catalogue) == set(CANDIDATES)
    assert "食蜂派阀" not in workflow.catalogue
    assert "食蜂派阀" not in prepared["u1"]


def test_text206_current_reply_off_window_names_are_discarded_with_literal_names():
    window = _window()
    discarded = []
    raw = _reply([*CANDIDATES, *UNVERIFIED])

    extracted = validate_extraction(
        raw,
        window,
        CANDIDATES,
        unverified_name_suggestions=UNVERIFIED,
        discarded_name_suggestions=discarded,
    )

    assert {row["source"] for row in extracted} == set(CANDIDATES)
    assert set(discarded) == set(UNVERIFIED)


def test_text206_context_only_summary_name_is_discarded_from_current_names():
    window = _window()
    context_only = ["旧摘要称呼"]
    discarded = []

    extracted = validate_extraction(
        _reply([*CANDIDATES, *context_only]),
        window,
        CANDIDATES,
        context_only=context_only,
        discarded_name_suggestions=discarded,
    )

    assert {row["source"] for row in extracted} == set(CANDIDATES)
    assert discarded == context_only


def test_text206_context_only_summary_name_does_not_satisfy_retry_candidate():
    context_only = "旧摘要称呼"

    with pytest.raises(SourceWindowError, match="without deciding them: 旧摘要称呼"):
        validate_extraction(
            _reply(CANDIDATES),
            _window(),
            [*CANDIDATES, context_only],
            context_only=[context_only],
        )


def test_text206_unknown_off_window_name_still_requires_retry():
    with pytest.raises(SourceWindowError, match="does not occur literally"):
        validate_extraction(_reply([*CANDIDATES, "旧摘要称呼"]), _window())


def test_text206_off_window_only_reply_still_fails():
    with pytest.raises(SourceWindowError, match="does not occur literally"):
        validate_extraction(_reply(["旧摘要称呼"]), _window())


def test_text206_unverified_name_does_not_hide_a_real_contradiction():
    raw = _reply(CANDIDATES, [CANDIDATES[0]])
    with pytest.raises(SourceWindowError, match="both extracted and excluded"):
        validate_extraction(
            raw,
            _window(),
            CANDIDATES,
            unverified_name_suggestions=UNVERIFIED,
        )


def test_text206_duplicate_optional_exclusions_are_inert():
    raw = _reply(CANDIDATES, [UNVERIFIED[0], UNVERIFIED[0]])
    extracted = validate_extraction(
        raw,
        _window(),
        CANDIDATES,
        unverified_name_suggestions=UNVERIFIED,
    )
    assert {row["source"] for row in extracted} == set(CANDIDATES)


def test_text206_duplicate_exact_candidate_exclusion_still_fails():
    candidate = CANDIDATES[0]
    raw = _reply(CANDIDATES[1:], [candidate, candidate])
    with pytest.raises(SourceWindowError, match="must not repeat a supplied retry candidate"):
        validate_extraction(raw, _window(), CANDIDATES)


def test_text206_w032_recovery_discards_absent_names_but_keeps_candidates(
    tmp_path, monkeypatch
):
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"source")
    source = "一方通行见到布束砥信，御坂美琴和爱玲遇到了食蜂操祈。"
    unit = WindowUnit("u32", "epub:32:unit", source, 0, "chapter-32")
    window = SourceWindow("w000032", (unit,), len(source))

    class Model:
        language = "zh"
        source_language = "zh"
        model = "offline"

        def __init__(self):
            self.session = SessionHistory()
            self.requests = []

        def extract_window_names(self, prompt):
            self.requests.append(prompt)
            raw = _reply(
                [*W032_CANDIDATES, *W032_UNVERIFIED],
                ["上条当麻", "亚雷斯塔"],
                window_id=window.identity,
            )
            self._protected_last_request = {
                "messages": [{"role": "user", "content": prompt}]
            }
            self._protected_last_response = raw
            return raw

    model = Model()
    workflow = MarkerWorkflow(
        model, str(source_path), settings={"workflow": WINDOW_WORKFLOW}
    )
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=10500,
        run_fingerprint="text206-w032-resume",
        cross_chapters=False,
    )
    workflow.save_source_window_retry_state(
        window, W032_CANDIDATES, W032_UNVERIFIED
    )
    monkeypatch.setattr(workflow, "_backoff", lambda *args, **kwargs: None)

    record, prepared, _spans = workflow.prepare_source_window(
        window, source_language="Chinese"
    )

    assert len(model.requests) == 1
    extracted_names = set(record["extracted_names"])
    assert extracted_names == set(W032_CANDIDATES)
    assert set(workflow.catalogue) == set(W032_CANDIDATES)
    assert all(f"${{{label}}}" not in prepared[unit.identity]
               for label in W032_UNVERIFIED)
    assert extracted_names.isdisjoint(W032_UNVERIFIED)


@pytest.mark.parametrize(
    "raw",
    [
        _reply(CANDIDATES, ["食蜂操祈"]),
        _reply(CANDIDATES, [123]),
    ],
)
def test_text206_malformed_duplicate_and_contradictory_evidence_still_rejected(raw):
    window = _window()

    assert extraction_candidates(raw, window) == []
    assert collect_unverified_name_suggestions(raw, window) == []
    with pytest.raises(SourceWindowError):
        validate_extraction(
            raw,
            window,
            CANDIDATES,
            unverified_name_suggestions=UNVERIFIED,
        )


def test_text206_unverified_exclusion_does_not_satisfy_candidate():
    window = _window()
    raw = _reply([], ["食蜂派阀"])

    with pytest.raises(
        SourceWindowError, match="without deciding them: 御坂美琴"
    ):
        validate_extraction(
            raw,
            window,
            ["御坂美琴"],
            unverified_name_suggestions=UNVERIFIED,
        )


def test_text206_workflow_omits_unverified_from_output_and_catalogue(
    tmp_path, monkeypatch
):
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"source")
    window = _window()

    class Model:
        language = "zh"
        source_language = "zh"
        model = "offline"

        def __init__(self):
            self.session = SessionHistory()
            self.requests = []

        def extract_window_names(self, prompt):
            self.requests.append(prompt)
            raw = (
                _reply([*CANDIDATES, *UNVERIFIED], ["小手"])
                if len(self.requests) == 1
                else _reply(CANDIDATES, ["交通工具"])
            )
            self._protected_last_request = {
                "messages": [{"role": "user", "content": prompt}]
            }
            self._protected_last_response = raw
            return raw

    model = Model()
    workflow = MarkerWorkflow(
        model, str(source_path), settings={"workflow": WINDOW_WORKFLOW}
    )
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=1500,
        run_fingerprint="text206-omission",
        cross_chapters=False,
    )
    monkeypatch.setattr(workflow, "_backoff", lambda *args, **kwargs: None)

    record, prepared, _spans = workflow.prepare_source_window(
        window, source_language="Chinese"
    )

    assert len(model.requests) == 2
    assert set(record["extracted_names"]) == set(CANDIDATES)
    assert set(workflow.catalogue) == set(CANDIDATES)
    assert "食蜂派阀" not in workflow.catalogue
    assert "食蜂派阀" not in prepared["u1"]
    assert workflow.source_windows_state["extraction_retries"] == []
