"""Regression coverage for a failed cumulative source-summary stage.

The real run failed after window w000001 because the summary model wrote a few
catalogue labels as plain prose, the strict validator rejected each draft, and
the third attempt's transient provider boilerplate ended the run.  These tests
pin the revised policy and the independent retry repair:

* plain original-language name mentions are accepted without rewriting, while
  malformed or unknown marker syntax is still rejected;
* a provider boilerplate reply is retried on its own budget without appending
  validation feedback to a prompt that produced no draft.
"""

from hashlib import sha256

import pytest

from book_maker.marker_names import (
    WINDOW_WORKFLOW,
    MarkerError,
    bracket_known_labels,
    markers,
)
from book_maker.marker_workflow import MarkerValidationError, MarkerWorkflow
from book_maker.provider_refusal import ProviderRefusal
from book_maker.session_context import SessionHistory
from book_maker.source_windows import SourceWindow, WindowUnit, prepare_window


PROVIDER_BOILERPLATE = "Sorry, something went wrong. Please try your request again."


class SummaryModel:
    language = "en"
    source_language = "zh"
    model = "offline"
    max_chunk_attempts = 5

    def __init__(self, script):
        self.session = SessionHistory()
        self.script = list(script)
        self.requests = []
        self._protected_last_response = ""
        self._protected_response_metadata = None

    def summarize_source_window(self, prompt, *, max_tokens):
        self.requests.append((prompt, max_tokens))
        value = self.script.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _prepared_workflow(tmp_path, model, source_text, labels):
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"test source")
    workflow = MarkerWorkflow(
        model,
        str(source_path),
        settings={
            "workflow": WINDOW_WORKFLOW,
            "run_fingerprint": "fingerprint",
            "name_window_tokens": 8192,
            "source_summary_tokens": 1500,
        },
    )
    unit = WindowUnit("unit-1", "job-1", source_text, 0, "chapter-1")
    window = SourceWindow("w000001", (unit,), 20)
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=1500,
        run_fingerprint="fingerprint",
        cross_chapters=False,
    )
    extracted = [{"source": label, "unit_ids": ["unit-1"]} for label in labels]
    prepared, spans = prepare_window(window, extracted)
    workflow.commit_window_preparation(
        window,
        extracted,
        prepared,
        spans,
        extraction_request_sha256=sha256(b"extract").hexdigest(),
    )
    translation = prepared["unit-1"]
    workflow.mark_window_unit_accepted("w000001", {"unit-1": translation})
    return workflow, window


# ------------------------------------------------------------ label repair


def test_bracket_known_labels_repairs_the_real_unmarked_summary():
    labels = {
        "御坂一色",
        "一色",
        "御坂美琴",
        "美琴",
        "学园都市",
        "常盘台",
        "常盘台中学",
    }
    raw = (
        "${御坂一色}$穿越降临至学园都市，发现自己占据了${御坂美琴}$义妹的身躯。"
        "两人随后一同回到常盘台中学第七学区生徒寮，在夕阳下苏醒的一色被${御坂美琴}找到。"
    )
    repaired, inserted = bracket_known_labels(raw, labels)

    assert inserted == 3
    assert "${学园都市}" in repaired
    assert "${常盘台中学}" in repaired
    assert "${一色}" in repaired
    # Existing markers are preserved, never nested or doubled.
    assert "${${" not in repaired
    assert "${御坂美琴}" in repaired
    # Longest-first: 常盘台中学 is one marker, not ${常盘台}中学.
    assert "${常盘台}中学" not in repaired
    markers(repaired)


def test_bracket_known_labels_is_idempotent_and_leaves_unknown_text_alone():
    labels = {"Alice", "Al"}
    once, inserted = bracket_known_labels("Alice met Al and Bob.", labels)
    assert once == "${Alice} met ${Al} and Bob."
    assert inserted == 2

    twice, again = bracket_known_labels(once, labels)
    assert twice == once
    assert again == 0


def test_bracket_known_labels_refuses_malformed_marker_syntax():
    with pytest.raises(MarkerError):
        bracket_known_labels("Alice ${returned", {"Alice"})


def test_source_summary_prompt_lists_only_relevant_catalogue_labels():
    from book_maker.source_windows import source_summary_prompt

    unit = WindowUnit("unit-1", "job-1", "Alice met Carol.", 0, "chapter-1")
    window = SourceWindow("w000001", (unit,), 5)
    prompt = source_summary_prompt(
        "Earlier, ${Alice} arrived.",
        window,
        "English",
        1500,
        catalogue_labels={"Alice", "Bob", "Carol"},
    )
    data = __import__("json").loads(prompt.rsplit("\n", 1)[-1])
    assert data["catalogue_labels"] == ["Alice", "Carol"]
    assert "Plain original-language name mentions are allowed" in prompt
    assert "Markers are optional here" in prompt
    assert "must be written as ${label} at every mention" not in prompt


def test_source_summary_prompt_can_require_preserving_prior_context():
    from book_maker.source_windows import source_summary_prompt

    unit = WindowUnit("unit-1", "job-1", "Alice met Carol.", 0, "chapter-1")
    window = SourceWindow("w000002", (unit,), 5)
    prompt = source_summary_prompt(
        "Earlier, Alice arrived.",
        window,
        "English",
        1500,
        target_budget=1500,
        minimum_budget=900,
    )

    assert "Use at least approximately 900 estimated tokens" in prompt
    assert "within approximately 1500 estimated tokens" in prompt


# --------------------------------------------------------- retry behaviour


def test_summary_accepts_plain_labels_without_rewriting(tmp_path):
    model = SummaryModel(["Alice went home; Bob stayed behind."])
    workflow, window = _prepared_workflow(
        tmp_path, model, "Alice met Bob.", ["Alice", "Bob"]
    )

    summary = workflow.summarize_source_window(window, budget=1500)

    assert summary == "Alice went home; Bob stayed behind."
    record = workflow.prepared_window(window)
    assert record["status"] == "complete"
    assert record["summary_validation"]["repaired_markers"] == 0
    assert record["summary_validation"]["policy"] == "plain-labels-allowed-v1"
    assert workflow.source_summary == summary


def test_summary_still_rejects_malformed_or_unknown_markers(tmp_path):
    malformed = SummaryModel(["Alice went ${home."])
    workflow, window = _prepared_workflow(
        tmp_path, malformed, "Alice met Bob.", ["Alice", "Bob"]
    )
    malformed.max_chunk_attempts = 1
    with pytest.raises(MarkerValidationError, match="marker"):
        workflow.summarize_source_window(window, budget=1500)

    unknown = SummaryModel(["${Unknown} left."])
    workflow, window = _prepared_workflow(
        tmp_path, unknown, "Alice met Bob.", ["Alice", "Bob"]
    )
    unknown.max_chunk_attempts = 1
    with pytest.raises(MarkerValidationError, match="[Uu]nknown"):
        workflow.summarize_source_window(window, budget=1500)


def test_provider_error_retries_the_same_prompt_and_succeeds(tmp_path, monkeypatch):
    delays = []

    def fake_backoff(attempts, stage="", *, base=1, cap=8):
        delays.append((attempts, base, cap))

    monkeypatch.setattr(MarkerWorkflow, "_backoff", staticmethod(fake_backoff))

    model = SummaryModel([PROVIDER_BOILERPLATE, "The afternoon passed quietly."])
    workflow, window = _prepared_workflow(tmp_path, model, "Alice left.", ["Alice"])

    summary = workflow.summarize_source_window(window, budget=1500)

    assert summary == "The afternoon passed quietly."
    assert len(model.requests) == 2
    assert model.requests[0][0] == model.requests[1][0]
    assert "previous draft failed validation" not in model.requests[1][0]
    assert delays == [(1, 5, 30)]
    assert workflow.prepared_window(window)["status"] == "complete"


def test_content_failure_still_receives_validation_feedback(tmp_path, monkeypatch):
    monkeypatch.setattr(
        MarkerWorkflow,
        "_backoff",
        staticmethod(lambda attempts, stage="", *, base=1, cap=8: None),
    )

    model = SummaryModel(["", "The afternoon passed quietly."])
    workflow, window = _prepared_workflow(tmp_path, model, "Alice left.", ["Alice"])

    workflow.summarize_source_window(window, budget=1500)

    assert len(model.requests) == 2
    assert "previous draft failed validation" in model.requests[1][0]
    assert "Summary is empty" in model.requests[1][0]
    assert "within approximately 1500 estimated tokens" in model.requests[0][0]
    assert "within approximately 1500 estimated tokens" in model.requests[1][0]


def test_short_cumulative_summary_is_rejected_and_retried_for_coverage(
    tmp_path, monkeypatch
):
    prior = "前文累计事件。"
    short = "简短摘要。"
    accepted = "保留前文关键事件，并加入当前窗口的重要发展。"
    estimates = {prior: 1000, short: 500, accepted: 800}
    monkeypatch.setattr(
        "book_maker.utils.num_tokens_from_text",
        lambda text: estimates.get(text, 1),
    )
    monkeypatch.setattr(
        MarkerWorkflow,
        "_backoff",
        staticmethod(lambda attempts, stage="", *, base=1, cap=8: None),
    )
    model = SummaryModel([short, accepted])
    workflow, window = _prepared_workflow(
        tmp_path, model, "美琴离开了。", ["美琴"]
    )
    workflow.source_windows_state["current_summary"] = prior
    workflow.prepared_window(window)["incoming_summary"] = prior

    summary = workflow.summarize_source_window(window, budget=1500)

    assert summary == accepted
    assert len(model.requests) == 2
    assert "at least approximately 700 estimated tokens" in model.requests[0][0]
    assert "below the required minimum of 700" in model.requests[1][0]
    assert workflow.source_summary == accepted


def test_over_budget_summary_retry_lowers_prompt_target_but_keeps_hard_cap(
    tmp_path, monkeypatch
):
    oversized = "第一次的累计摘要。"
    accepted = "压缩后的原文摘要。"
    estimates = {oversized: 1501, accepted: 1200}
    monkeypatch.setattr(
        "book_maker.utils.num_tokens_from_text",
        lambda text: estimates.get(text, 1),
    )
    monkeypatch.setattr(
        MarkerWorkflow,
        "_backoff",
        staticmethod(lambda attempts, stage="", *, base=1, cap=8: None),
    )
    model = SummaryModel([oversized, accepted])
    workflow, window = _prepared_workflow(
        tmp_path, model, "御坂美琴出现了。", ["御坂美琴"]
    )

    summary = workflow.summarize_source_window(window, budget=1500)

    assert summary == accepted
    assert [max_tokens for _prompt, max_tokens in model.requests] == [1500, 1500]
    assert "within approximately 1500 estimated tokens" in model.requests[0][0]
    assert "within approximately 1275 estimated tokens" in model.requests[1][0]
    assert "previous draft measured 1501 estimated tokens" in model.requests[1][0]
    assert "target no more than 1275 estimated tokens" in model.requests[1][0]
    assert workflow.prepared_window(window)["status"] == "complete"


def test_repeated_over_budget_summary_targets_decrease_and_keep_pending_state(
    tmp_path, monkeypatch
):
    drafts = ["第一次累计摘要。", "第二次累计摘要。", "第三次累计摘要。"]
    estimates = dict(zip(drafts, [1601, 1550, 1520]))
    monkeypatch.setattr(
        "book_maker.utils.num_tokens_from_text",
        lambda text: estimates.get(text, 1),
    )
    monkeypatch.setattr(
        MarkerWorkflow,
        "_backoff",
        staticmethod(lambda attempts, stage="", *, base=1, cap=8: None),
    )
    model = SummaryModel(drafts)
    model.max_chunk_attempts = 3
    workflow, window = _prepared_workflow(
        tmp_path, model, "御坂美琴出现了。", ["御坂美琴"]
    )
    prior = "前文发生的关键事件。"
    workflow.source_windows_state["current_summary"] = prior
    workflow.prepared_window(window)["incoming_summary"] = prior

    with pytest.raises(MarkerValidationError, match="after 3 attempts"):
        workflow.summarize_source_window(window, budget=1500)

    assert [max_tokens for _prompt, max_tokens in model.requests] == [1500] * 3
    assert [
        next(
            int(part.split(" estimated tokens")[0])
            for part in prompt.split("within approximately ")[1:]
        )
        for prompt, _max_tokens in model.requests
    ] == [1500, 1275, 1083]
    record = workflow.prepared_window(window)
    assert record["status"] == "summary_pending"
    assert record["incoming_summary"] == prior
    assert set(record["accepted_unit_ids"]) == {"unit-1"}
    assert workflow.source_windows_state["current_summary"] == prior
    assert workflow.source_windows_state["summary_pending_window"] == window.identity


def test_terminal_refusal_stops_without_a_second_request(tmp_path):
    model = SummaryModel([ProviderRefusal("policy")])
    workflow, window = _prepared_workflow(tmp_path, model, "Alice left.", ["Alice"])

    with pytest.raises(MarkerValidationError):
        workflow.summarize_source_window(window, budget=1500)

    assert len(model.requests) == 1
    record = workflow.prepared_window(window)
    assert record["status"] == "summary_pending"
    assert workflow.source_windows_state["summary_pending_window"] == "w000001"


def test_summary_accepts_a_short_known_label_inside_a_longer_expression(tmp_path):
    """Old substring repair would have rewritten Albert into ${Al}bert."""
    model = SummaryModel(["Albert stayed with Bob."])
    workflow, window = _prepared_workflow(
        tmp_path, model, "Al met Bob.", ["Al", "Bob"]
    )

    summary = workflow.summarize_source_window(window, budget=1500)

    assert summary == "Albert stayed with Bob."
    assert "${" not in summary
    assert workflow.prepared_window(window)["summary_validation"][
        "repaired_markers"
    ] == 0


def test_summary_preserves_valid_markers_without_adding_missing_ones(tmp_path):
    model = SummaryModel(["${Alice} left Bob behind."])
    workflow, window = _prepared_workflow(
        tmp_path, model, "Alice met Bob.", ["Alice", "Bob"]
    )

    summary = workflow.summarize_source_window(window, budget=1500)

    assert summary == "${Alice} left Bob behind."
    assert summary.count("${") == 1
