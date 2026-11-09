"""regressions for auxiliary source-window extraction with summary-only labels."""

import hashlib
import json

import pytest

from book_maker.marker_names import WINDOW_WORKFLOW
from book_maker.marker_workflow import MarkerWorkflow


IDENTITY = "toc:chap_1.xhtml#:首页"


def _window_id(identity=IDENTITY):
    return "aux-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _reply(names, excluded=()):
    return json.dumps(
        {
            "window_id": _window_id(),
            "names": list(names),
            "excluded_candidates": list(excluded),
        },
        ensure_ascii=False,
    )


class AuxModel:
    language = "en"
    source_language = "zh"
    model = "offline"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self._protected_last_request = None
        self._protected_last_response = None

    def extract_window_names(self, prompt):
        self.prompts.append(prompt)
        raw = self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]
        self._protected_last_request = {
            "messages": [{"role": "user", "content": prompt}]
        }
        self._protected_last_response = raw
        return raw


def _workflow(tmp_path, replies, summary="此前的故事提到御坂一色。"):
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"auxiliary extraction regression")
    model = AuxModel(replies)
    workflow = MarkerWorkflow(
        model, str(source_path), settings={"workflow": WINDOW_WORKFLOW}
    )
    workflow.source_windows_state["current_summary"] = summary
    # The cumulative catalogue supplies the plain-summary context labels.
    workflow.catalogue["御坂一色"] = {"label": "御坂一色"}
    return workflow, model


def _prompt_data(prompt):
    return json.loads(prompt.rsplit("\n", 1)[-1])


def test_auxiliary_context_only_repetition_is_discarded_and_no_names_are_output(
    tmp_path,
):
    # First attempt also carries an unknown off-window name, forcing a retry.
    # The summary-only 御坂一色 must remain context, never a candidate/name.
    workflow, model = _workflow(
        tmp_path,
        [
            _reply(["御坂一色", "未知角色"]),
            _reply(["御坂一色"]),
        ],
    )
    persisted = []

    prepared, record = workflow.prepare_auxiliary_source(
        "首页",
        IDENTITY,
        persist=lambda: persisted.append(True),
    )

    assert len(model.prompts) == 2
    assert record["extracted_names"] == []
    assert set(workflow.catalogue) == {"御坂一色"}
    assert "御坂一色" not in prepared
    assert "未知角色" not in prepared
    assert not workflow.source_windows_state["extraction_retries"]
    assert persisted

    first = _prompt_data(model.prompts[0])
    second = _prompt_data(model.prompts[1])
    assert "prior summary is context" in model.prompts[0]
    assert "御坂一色" in first["context_only_labels"]
    assert "御坂一色" in second["context_only_labels"]
    assert "未知角色" in second["unverified_name_suggestions"]
    assert second["retry_candidates"] == []


def test_auxiliary_exact_candidate_survives_retry_and_context_stays_out_of_catalogue(
    tmp_path,
):
    workflow, model = _workflow(
        tmp_path,
        [
            _reply(["御坂美琴", "御坂一色", "未知角色"]),
            _reply(["御坂美琴"]),
        ],
    )
    persisted = []

    prepared, record = workflow.prepare_auxiliary_source(
        "首页御坂美琴",
        IDENTITY,
        persist=lambda: persisted.append(True),
    )

    assert len(model.prompts) == 2
    assert record["extracted_names"] == ["御坂美琴"]
    assert set(workflow.catalogue) == {"御坂一色", "御坂美琴"}
    assert "御坂美琴" in prepared
    assert "御坂一色" not in prepared
    assert "未知角色" not in prepared
    assert persisted

    second = _prompt_data(model.prompts[1])
    assert "御坂美琴" in second["retry_candidates"]
    assert "御坂一色" in second["context_only_labels"]
    assert not workflow.source_windows_state["extraction_retries"]


def test_auxiliary_malformed_reply_is_rejected_and_retry_can_accept_empty(
    tmp_path,
):
    workflow, model = _workflow(
        tmp_path,
        [
            _reply([123]),
            _reply([]),
        ],
    )

    prepared, record = workflow.prepare_auxiliary_source("首页", IDENTITY)

    assert len(model.prompts) == 2
    assert record["extracted_names"] == []
    assert set(workflow.catalogue) == {"御坂一色"}
    assert "御坂一色" not in prepared
    assert not workflow.source_windows_state["extraction_retries"]
