import json
from zipfile import ZIP_STORED, ZipFile

import pytest

from book_maker.marker_bundle import (
    BundleError,
    read_state,
    validate_state,
    write_bundle,
)
from book_maker.marker_names import WINDOW_WORKFLOW
from book_maker.marker_workflow import MarkerValidationError, MarkerWorkflow
from book_maker.session_context import SessionHistory
from book_maker.source_windows import (
    SourceWindow,
    SourceWindowError,
    WindowUnit,
    collect_unverified_name_suggestions,
    extraction_candidates,
    extraction_retry_state_digest,
    prepare_window,
    source_window_prompt,
    validate_extraction,
)


def _window(source="食蜂操祈和一色一起出现。"):
    unit = WindowUnit("u1", "u1", source, 0, "chapter-1")
    return SourceWindow("w000015", (unit,), len(source))


def _reply(names, excluded=(), *, window_id="w000015"):
    return json.dumps(
        {
            "window_id": window_id,
            "names": names,
            "excluded_candidates": list(excluded),
        },
        ensure_ascii=False,
    )


def test_text204_nonliteral_name_can_be_explicitly_excluded_on_retry():
    window = _window()
    first_reply = _reply(["食蜂操祈", "一色", "食蜂派阀"])

    with pytest.raises(SourceWindowError, match="does not occur literally"):
        validate_extraction(first_reply, window)

    candidates = extraction_candidates(first_reply, window)
    suggestions = collect_unverified_name_suggestions(first_reply, window)
    assert candidates == ["食蜂操祈", "一色"]
    assert suggestions == ["食蜂派阀"]

    retry = _reply(
        ["食蜂操祈", "一色"],
        ["食蜂派阀"],
    )
    extracted = validate_extraction(
        retry,
        window,
        candidates,
        unverified_name_suggestions=suggestions,
    )
    prepared, _spans = prepare_window(window, extracted)

    assert [row["source"] for row in extracted] == ["食蜂操祈", "一色"]
    assert prepared["u1"] == "${食蜂操祈}和${一色}一起出现。"
    assert "食蜂派阀" not in prepared["u1"]


def test_unverified_suggestion_in_names_is_discarded():
    window = _window()
    extracted = validate_extraction(
        _reply(["食蜂操祈", "一色", "食蜂派阀"], ["食蜂派阀"]),
        window,
        ["食蜂操祈", "一色"],
        unverified_name_suggestions=["食蜂派阀"],
    )

    assert {row["source"] for row in extracted} == {"食蜂操祈", "一色"}
    assert all(row["source"] != "食蜂派阀" for row in extracted)


def test_unverified_suggestions_may_be_omitted():
    window = _window()
    extracted = validate_extraction(
        _reply(["食蜂操祈", "一色"]),
        window,
        ["食蜂操祈", "一色"],
        unverified_name_suggestions=["食蜂派阀"],
    )

    assert {row["source"] for row in extracted} == {"食蜂操祈", "一色"}
    assert all(row["source"] != "食蜂派阀" for row in extracted)


def test_current_window_text_cannot_be_hidden_by_unverified_retry_state():
    window = _window("食蜂派阀出现在原文。")
    with pytest.raises(SourceWindowError, match="cannot hide literal"):
        validate_extraction(
            _reply([], ["食蜂派阀"]),
            window,
            unverified_name_suggestions=["食蜂派阀"],
        )


@pytest.mark.parametrize(
    "raw",
    [
        _reply(["食蜂操祈"], ["食蜂操祈"]),
        _reply(["食蜂操祈", 123]),
    ],
)
def test_malformed_or_contradictory_replies_cannot_seed_retry_candidates(raw):
    window = _window()

    assert extraction_candidates(raw, window) == []
    assert collect_unverified_name_suggestions(raw, window) == []


def test_duplicate_unsupported_literal_exclusion_does_not_erase_name_evidence():
    window = _window()
    reply = _reply(["食蜂操祈"], ["一色", "一色"])

    # This response is still invalid as a final extraction, but the unsupported
    # exclusion cannot suppress the separately source-backed name evidence.
    assert extraction_candidates(reply, window) == ["食蜂操祈"]
    assert collect_unverified_name_suggestions(reply, window) == []
    with pytest.raises(SourceWindowError, match="cannot hide literal"):
        validate_extraction(reply, window)


def test_prior_exact_source_candidate_can_still_be_excluded():
    window = _window()
    reply = _reply(["食蜂操祈"], ["一色"])

    assert extraction_candidates(reply, window, prior_candidates=["一色"]) == [
        "食蜂操祈"
    ]
    assert collect_unverified_name_suggestions(
        reply, window, prior_candidates=["一色"]
    ) == []


def test_unrelated_absent_exclusion_does_not_suppress_valid_retry_evidence():
    window = _window()
    reply = _reply(["食蜂操祈"], ["毫无关联"])

    assert extraction_candidates(reply, window) == ["食蜂操祈"]
    assert collect_unverified_name_suggestions(reply, window) == []
    assert extraction_candidates(
        _reply([], ["毫无关联"]), window
    ) == []  # An exclusion by itself never creates a candidate.
    assert extraction_candidates(
        _reply(["食蜂操祈"], ["御坂"]),
        window,
        context_only=["御坂美琴"],
    ) == ["食蜂操祈"]
    assert extraction_candidates(
        _reply(["食蜂操祈"], ["食蜂派阀"]),
        window,
        prior_unverified_name_suggestions=["食蜂派阀"],
    ) == ["食蜂操祈"]


def test_text205_unrelated_absent_exclusion_does_not_block_window_preparation(
    tmp_path, monkeypatch, capsys
):
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"source")
    window = _window("食蜂操祈与一色出现在这里。")

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
                return _reply(["食蜂操祈", "一色", "白井黑子"])
            return _reply(
                ["食蜂操祈", "一色"], ["白井黑子", "交通工具"]
            )

    model = Model()
    workflow = MarkerWorkflow(
        model, str(source_path), settings={"workflow": WINDOW_WORKFLOW}
    )
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=1500,
        run_fingerprint="text205-inert-exclusion",
        cross_chapters=False,
    )
    monkeypatch.setattr(workflow, "_backoff", lambda *args, **kwargs: None)

    record, prepared, _spans = workflow.prepare_source_window(
        window, source_language="Chinese"
    )

    assert len(model.requests) == 2
    assert record["extracted_names"] == ["食蜂操祈", "一色"]
    assert set(workflow.catalogue) == {"食蜂操祈", "一色"}
    assert prepared["u1"] == "${食蜂操祈}与${一色}出现在这里。"
    assert "ignored 1 unrelated absent exclusion(s)" in capsys.readouterr().out


def test_unrelated_exclusion_does_not_satisfy_required_retry_decisions():
    window = _window()
    reply = _reply(["食蜂操祈"], ["毫无关联"])

    with pytest.raises(SourceWindowError, match="without deciding them: 一色"):
        validate_extraction(reply, window, candidates=["食蜂操祈", "一色"])

    extracted = validate_extraction(
        _reply(["食蜂操祈", "一色"], ["毫无关联"]),
        window,
        candidates=["食蜂操祈", "一色"],
        unverified_name_suggestions=["食蜂派阀"],
    )
    assert {row["source"] for row in extracted} == {"食蜂操祈", "一色"}


@pytest.mark.parametrize(
    "raw",
    [
        _reply(["食蜂派阀"], window_id="w000014"),
        json.dumps({"window_id": "w000015", "names": ["食蜂派阀"], "extra": True}),
        json.dumps({"window_id": "w000015", "names": ["食蜂派阀"], "excluded_candidates": [123]}),
        json.dumps({"window_id": "w000015", "names": [{"source": "食蜂派阀", "unit_ids": ["u1"]}]}),
        json.dumps({"window_id": "w000015", "names": ["食蜂派阀"], "excluded_candidates": ["食蜂派阀"]}),
    ],
)
def test_unverified_suggestion_collector_rejects_malformed_or_contradictory_replies(raw):
    assert collect_unverified_name_suggestions(raw, _window()) == []


def test_prompt_describes_unverified_suggestions_as_optional():
    prompt = source_window_prompt(
        _window(),
        "",
        "Chinese",
        unverified_name_suggestions=["食蜂派阀"],
    )
    payload = json.loads(prompt.rsplit("\n", 1)[-1])

    assert payload["unverified_name_suggestions"] == ["食蜂派阀"]
    assert "must never appear in names" in prompt
    assert "may omit them" in prompt
    assert "not required decisions" in prompt
    assert "put every one exactly in excluded_candidates" not in prompt
    assert "Unrelated absent labels are ignored" in prompt


def test_duplicate_optional_absent_exclusions_are_normalized():
    window = _window()
    reply = _reply([], ["毫无关联", "毫无关联"])
    ignored = []

    assert extraction_candidates(reply, window) == []
    assert validate_extraction(
        reply, window, ignored_exclusions=ignored
    ) == []
    assert ignored == ["毫无关联"]


def test_resume_rebuilds_unverified_suggestions_from_matching_failure_diagnostics(
    tmp_path, monkeypatch
):
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"source")
    window = _window("食蜂操祈和一色与桃莉一起出现。")

    class Model:
        language = "zh"
        source_language = "zh"
        model = "offline"

        def __init__(self):
            self.session = SessionHistory()
            self.requests = []

        def extract_window_names(self, prompt):
            self.requests.append(prompt)
            raw = _reply(["食蜂操祈", "一色", "桃莉"], ["食蜂派阀"])
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
        run_fingerprint="text204-test",
        cross_chapters=False,
    )
    old_prompt = source_window_prompt(window, "", "Chinese")
    workflow.state["diagnostics"]["failures"].append(
        {
            "stage": "window-name-extraction",
            "request": {"messages": [{"role": "user", "content": old_prompt}]},
            "response": _reply(["桃莉"], ["桃莉"]),
        }
    )
    workflow.state["diagnostics"]["failures"].append(
        {
            "stage": "window-name-extraction",
            "request": {"messages": [{"role": "user", "content": old_prompt}]},
            "response": _reply(["食蜂操祈", "一色", "食蜂派阀"]),
        }
    )
    monkeypatch.setattr(workflow, "_backoff", lambda *args, **kwargs: None)

    record, prepared, _spans = workflow.prepare_source_window(
        window, source_language="Chinese"
    )
    payload = json.loads(model.requests[0].rsplit("\n", 1)[-1])

    assert payload["retry_candidates"] == ["食蜂操祈", "一色"]
    assert payload["unverified_name_suggestions"] == ["食蜂派阀"]
    assert record["extracted_names"] == ["食蜂操祈", "一色", "桃莉"]
    assert set(workflow.catalogue) == {"食蜂操祈", "一色", "桃莉"}
    assert prepared["u1"] == "${食蜂操祈}和${一色}与${桃莉}一起出现。"


@pytest.mark.parametrize(
    "diagnostic_response",
    ['{"window_id":"w000015","names":["食蜂操祈', None],
    ids=["truncated", "evicted"],
)
def test_durable_retry_state_survives_truncated_or_evicted_diagnostics(
    tmp_path, monkeypatch, diagnostic_response
):
    source_path = tmp_path / "source.epub"
    with ZipFile(source_path, "w") as epub:
        epub.writestr(
            "mimetype", "application/epub+zip", compress_type=ZIP_STORED
        )
    window = _window()

    class FirstModel:
        language = "zh"
        source_language = "zh"
        model = "offline"

        def __init__(self):
            self.session = SessionHistory()
            self.requests = []

        def extract_window_names(self, prompt):
            self.requests.append(prompt)
            raw = _reply(["食蜂操祈", "一色", "食蜂派阀"])
            self._protected_last_request = {
                "messages": [{"role": "user", "content": prompt}]
            }
            self._protected_last_response = raw
            return raw

    class ResumeModel:
        language = "zh"
        source_language = "zh"
        model = "offline"

        def __init__(self):
            self.session = SessionHistory()
            self.requests = []

        def extract_window_names(self, prompt):
            self.requests.append(prompt)
            raw = _reply(["食蜂操祈", "一色"], ["食蜂派阀"])
            self._protected_last_request = {
                "messages": [{"role": "user", "content": prompt}]
            }
            self._protected_last_response = raw
            return raw

    first_model = FirstModel()
    workflow = MarkerWorkflow(
        first_model, str(source_path), settings={"workflow": WINDOW_WORKFLOW}
    )
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=1500,
        run_fingerprint="text204-durable-retry",
        cross_chapters=False,
    )
    bundle_path = tmp_path / "retry-state.epub"

    class StopAfterCheckpoint(Exception):
        pass

    def interrupt_after_saved_retry_state(*_args, **_kwargs):
        raise StopAfterCheckpoint()

    monkeypatch.setattr(workflow, "_backoff", interrupt_after_saved_retry_state)

    with pytest.raises(StopAfterCheckpoint):
        workflow.prepare_source_window(
            window,
            source_language="Chinese",
            persist=lambda: write_bundle(source_path, bundle_path, workflow.state),
        )

    resumed_state = read_state(bundle_path, validate=True)
    saved_retry = resumed_state["source_windows"]["extraction_retries"][0]
    assert saved_retry["retry_candidates"] == ["一色", "食蜂操祈"]
    assert saved_retry["unverified_name_suggestions"] == ["食蜂派阀"]
    if diagnostic_response is None:
        resumed_state["diagnostics"]["failures"].clear()
    else:
        resumed_state["diagnostics"]["failures"][-1]["response"] = diagnostic_response
    write_bundle(source_path, bundle_path, resumed_state)
    resumed_state = read_state(bundle_path, validate=True)

    model = ResumeModel()
    resumed = MarkerWorkflow(
        model, str(source_path), state=resumed_state,
        settings={"workflow": WINDOW_WORKFLOW},
    )

    record, _prepared, _spans = resumed.prepare_source_window(
        window, source_language="Chinese"
    )
    payload = json.loads(model.requests[0].rsplit("\n", 1)[-1])

    assert payload["retry_candidates"] == ["食蜂操祈", "一色"]
    assert payload["unverified_name_suggestions"] == ["食蜂派阀"]
    assert record["extracted_names"] == ["食蜂操祈", "一色"]
    assert resumed.source_windows_state["extraction_retries"] == []


def test_retry_bundle_checksum_detects_changed_candidate_labels(tmp_path):
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"source")
    window = _window()
    model = type("RetryStateModel", (), {})()
    workflow = MarkerWorkflow(
        model, str(source_path), settings={"workflow": WINDOW_WORKFLOW}
    )
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=1500,
        run_fingerprint="text204-retry-checksum",
        cross_chapters=False,
    )
    workflow.save_source_window_retry_state(
        window, retry_candidates=["食蜂操祈"]
    )
    validate_state(workflow.state)

    retry = workflow.source_windows_state["extraction_retries"][0]
    retry["retry_candidates"] = ["一色"]
    with pytest.raises(BundleError, match="failed its integrity check"):
        validate_state(workflow.state)


@pytest.mark.parametrize(
    ("candidates", "suggestions"),
    [(["not in source"], []), ([], ["食蜂操祈"])],
    ids=["absent-candidate", "source-backed-unverified"],
)
def test_loaded_retry_state_must_match_its_source_role(
    tmp_path, candidates, suggestions
):
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"source")
    window = _window()
    model = type("RetryStateModel", (), {})()
    workflow = MarkerWorkflow(
        model, str(source_path), settings={"workflow": WINDOW_WORKFLOW}
    )
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=1500,
        run_fingerprint="text204-retry-role",
        cross_chapters=False,
    )
    workflow.source_windows_state.setdefault("extraction_retries", []).append(
        {
            "window_id": window.identity,
            "source_sha256": window.source_digest,
            "retry_candidates": candidates,
            "unverified_name_suggestions": suggestions,
            "retry_state_sha256": extraction_retry_state_digest(
                window.identity,
                window.source_digest,
                candidates,
                suggestions,
            ),
        }
    )
    validate_state(workflow.state)

    with pytest.raises(
        MarkerValidationError,
        match="does not match its source-window role checks",
    ):
        workflow.source_window_retry_state(window)
