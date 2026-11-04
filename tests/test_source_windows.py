from hashlib import sha256
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from book_maker.marker_bundle import BundleError, validate_state
from book_maker.marker_names import WINDOW_WORKFLOW, marker_key
from book_maker.marker_workflow import MarkerWorkflow
from book_maker.session_context import SessionHistory
from book_maker.source_windows import (
    SourceWindow,
    SourceWindowError,
    WindowUnit,
    join_fragments,
    plan_source_windows,
    prepare_window,
    source_window_prompt,
    validate_extraction,
)


class FakeModel:
    language = "en"
    source_language = "en"
    model = "offline"

    def __init__(self):
        self.session = SessionHistory()


def _job(identity, text, index, group="chapter-1"):
    return SimpleNamespace(
        job_id=identity,
        source_text=text,
        global_index=index,
        context_group=group,
    )


def test_window_plan_covers_order_once_and_respects_chapter_boundaries():
    jobs = [
        _job("a", "aa", 0, "one"),
        _job("b", "bb", 1, "one"),
        _job("c", "cc", 2, "two"),
    ]
    windows = plan_source_windows(
        jobs, budget=4, estimate=len, cross_chapters=False
    )

    assert [[unit.identity for unit in window.units] for window in windows] == [
        ["a", "b"],
        ["c"],
    ]
    assert [unit.source for window in windows for unit in window.units] == [
        "aa", "bb", "cc"
    ]
    assert all(window.estimated_tokens <= 4 for window in windows)


def test_oversized_paragraph_splits_on_sentences_and_reassembles():
    source = "One. Two. Three."
    windows = plan_source_windows(
        [_job("p1", source, 0)],
        budget=10,
        estimate=len,
        max_unit_tokens=6,
    )
    units = [unit for window in windows for unit in window.units]

    assert "".join(unit.source for unit in units) == source
    assert [unit.identity for unit in units] == [
        "p1:f0001", "p1:f0002", "p1:f0003"
    ]
    assert all(len(unit.source) <= 6 for unit in units)
    assert join_fragments(units, ["Uno.", "Dos.", "Tres."], default_separator=" ")[
        "p1"
    ] == "Uno. Dos. Tres."


def test_oversized_single_sentence_fails_before_provider_request():
    with pytest.raises(SourceWindowError, match="no safe sentence boundary"):
        plan_source_windows(
            [_job("p1", "A sentence with no stop", 0)],
            budget=5,
            estimate=len,
        )

    with pytest.raises(SourceWindowError, match=r"--request-max-tokens=5"):
        plan_source_windows(
            [_job("p2", "A sentence with no stop", 0)],
            budget=20,
            max_unit_tokens=5,
            estimate=len,
        )


def test_extraction_prompt_has_valid_contract_and_window_scoped_citations():
    unit = WindowUnit("p1", "p1", "御坂美琴 arrived.", 0, "chapter-1")
    window = SourceWindow("w000001", (unit,), 4)
    prompt = source_window_prompt(window, "", "Japanese")
    example = '{"window_id":"...","names":[{"source":"exact spelling","unit_ids":["unit-id"]}]}'
    assert example in prompt

    extraction = (
        '{"window_id":"w000001","names":'
        '[{"source":"御坂美琴","unit_ids":["p1"]}]}'
    )
    assert validate_extraction(extraction, window) == [
        {"source": "御坂美琴", "unit_ids": ["p1"]}
    ]


def test_extraction_accepts_one_complete_json_markdown_fence():
    unit = WindowUnit("p1", "p1", "御坂美琴 arrived.", 0, "chapter-1")
    window = SourceWindow("w000001", (unit,), 4)
    raw = '```json\n{"window_id":"w000001","names":[]}\n```'
    assert validate_extraction(raw, window) == []


def test_extraction_repair_requires_every_prior_candidate_to_be_decided():
    unit = WindowUnit("p1", "p1", "御坂美琴 saw 美琴.", 0, "chapter-1")
    window = SourceWindow("w000001", (unit,), 8)
    candidates = ["御坂美琴", "美琴"]
    raw = json.dumps({"window_id": "w000001", "names": [
        {"source": "御坂美琴", "unit_ids": ["p1"]}],
        "excluded_candidates": ["美琴"]}, ensure_ascii=False)
    assert [row["source"] for row in validate_extraction(raw, window, candidates)] == [
        "御坂美琴"
    ]
    incomplete = json.dumps({"window_id": "w000001", "names": [
        {"source": "御坂美琴", "unit_ids": ["p1"]}],
        "excluded_candidates": []}, ensure_ascii=False)
    with pytest.raises(SourceWindowError, match="without deciding"):
        validate_extraction(incomplete, window, candidates)


def test_extraction_requires_one_literal_witness_not_a_list_of_nearby_units():
    units = (
        WindowUnit("p1", "p1", "御坂美琴 arrived.", 0, "chapter-1"),
        WindowUnit("p2", "p2", "美琴 smiled.", 1, "chapter-1"),
    )
    window = SourceWindow("w000001", units, 8)
    raw = json.dumps({"window_id": "w000001", "names": [
        {"source": "御坂美琴", "unit_ids": ["p1", "p2"]}]}, ensure_ascii=False)
    with pytest.raises(SourceWindowError, match="exactly one witness"):
        validate_extraction(raw, window)


@pytest.mark.parametrize(
    "raw, message",
    [
        ('{"window_id":"w2","names":[]}', "does not match"),
        ('{"window_id":"w000001","names":[{"source":"Alice","unit_ids":["p2"]}]}', "unknown"),
        ('{"window_id":"w000001","names":[{"source":"Alice","unit_ids":[]}]}', "exactly one witness"),
        ('{"window_id":"w000001","names":[{"source":"Alice","unit_ids":["p1"],"extra":true}]}', "only source and unit_ids"),
        ('{"window_id":"w000001","names":[{"source":"${御坂美琴}","unit_ids":["p1"]}]}', "reserved"),
    ],
)
def test_extraction_rejects_wrong_shape_and_ungrounded_labels(raw, message):
    unit = WindowUnit("p1", "p1", "御坂美琴 arrived.", 0, "chapter-1")
    window = SourceWindow("w000001", (unit,), 4)
    with pytest.raises(SourceWindowError, match=message):
        validate_extraction(raw, window)


def test_span_insertion_is_longest_first_literal_and_collection_is_durable(tmp_path):
    source_text = "御坂美琴看着美琴，A+B到达。"
    unit = WindowUnit("job-1", "job-1", source_text, 0, "chapter-1")
    window = SourceWindow("w000001", (unit,), 20)
    extracted = [
        {"source": "美琴", "unit_ids": ["job-1"]},
        {"source": "A+B", "unit_ids": ["job-1"]},
        {"source": "御坂美琴", "unit_ids": ["job-1"]},
    ]
    prepared, spans = prepare_window(window, extracted)
    assert prepared["job-1"] == "${御坂美琴}看着${美琴}，${A+B}到达。"

    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"test source")
    settings = {
        "workflow": WINDOW_WORKFLOW,
        "run_fingerprint": "fingerprint",
        "name_window_tokens": 8192,
        "source_summary_tokens": 1500,
    }
    workflow = MarkerWorkflow(FakeModel(), str(source_path), settings=settings)
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=1500,
        run_fingerprint="fingerprint",
        cross_chapters=False,
    )
    extraction_digest = sha256(b"extract prompt").hexdigest()
    workflow.commit_window_preparation(
        window,
        extracted,
        prepared,
        spans,
        extraction_request_sha256=extraction_digest,
    )

    assert set(workflow.catalogue) == {"御坂美琴", "美琴", "A+B"}
    assert workflow.catalogue["御坂美琴"]["count"] == 0
    assert workflow.catalogue["御坂美琴"]["extracted_from"][0]["occurrences"]


def test_window_bundle_accepts_completed_summary_and_extracted_name(tmp_path):
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"test source")
    settings = {
        "workflow": WINDOW_WORKFLOW,
        "run_fingerprint": "fingerprint",
        "name_window_tokens": 8192,
        "source_summary_tokens": 1500,
    }
    workflow = MarkerWorkflow(FakeModel(), str(source_path), settings=settings)
    unit = WindowUnit("unit-1", "job-1", "Alice left.", 0, "chapter-1")
    window = SourceWindow("w000001", (unit,), 3)
    workflow.register_source_window_plan(
        [window],
        name_budget=8192,
        summary_budget=1500,
        run_fingerprint="fingerprint",
        cross_chapters=False,
    )
    extracted = [{"source": "Alice", "unit_ids": ["unit-1"]}]
    prepared, spans = prepare_window(window, extracted)
    workflow.commit_window_preparation(
        window,
        extracted,
        prepared,
        spans,
        extraction_request_sha256=sha256(b"extract").hexdigest(),
    )
    workflow.mark_window_unit_accepted("w000001", {"unit-1": "${Alice} left."})
    workflow.commit_window_summary(
        window,
        "${Alice} returned.",
        language="English",
        budget=1500,
        request_sha256=sha256(b"summary").hexdigest(),
    )
    state = workflow.snapshot(
        job_ids=["job-1"],
        translations=["${Alice} left."],
        jobs=(),
        status="partial",
    )

    assert state["catalogue"]["Alice"]["key"] == marker_key("Alice")
    assert state["source_windows"]["current_summary"] == "${Alice} returned."
    assert validate_state(state) is state

    corrupted = deepcopy(state)
    corrupted["source_windows"]["windows"][0]["units"][0]["translation"] = "She left."
    with pytest.raises(BundleError, match="does not preserve its prepared marker"):
        validate_state(corrupted)
