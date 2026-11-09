"""Selective paragraph recovery, journaling, and relaxed marker grounding."""

import json
from types import SimpleNamespace

import pytest
from lxml import etree

from book_maker.marker_names import MarkerError, validate_batch
from book_maker.marker_workflow import (
    MarkerValidationError,
    MarkerWorkflow,
    parse_paragraph_reply,
    parse_paragraph_response,
    paragraph_request,
)
from book_maker.provider_refusal import ProviderRefusal
from book_maker.session_context import SessionHistory
from book_maker.translator.chatgptapi_translator import ChatGPTAPI


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
        return json.dumps({"mappings": []})


class AdapterModel(ChatGPTAPI):
    """Real ``translate_marker_units`` with a scripted ``_protected_completion``.

    The regression this covers lives in the adapter, so a coordinator-only fake
    that returns raw strings would not exercise it.
    """

    def __init__(self, responses, max_attempts=5):
        self.responses = list(responses)
        self.session = SessionHistory()
        self.language = "en"
        self.source_language = "zh"
        self.model = "offline"
        self.max_chunk_attempts = max_attempts
        self._fatal_error_detected = False
        self._marker_pending_exchanges = None
        self._marker_state = None
        self._protected_last_response = None
        self._protected_last_request = None
        self._protected_response_metadata = None
        self.sends = []

    def standing_instructions(self):
        return "system"

    def _protected_completion(self, messages, stage):
        self.sends.append(messages)
        entry = self.responses.pop(0)
        if isinstance(entry, BaseException):
            self._protected_last_response = (
                '<bbm-unit id="leak">${Alice} fragment</bbm-unit>'
            )
            raise entry
        raw, finish = entry
        self._protected_last_response = raw
        self._protected_response_metadata = {
            "finish_reason": finish,
            "model": "fake",
        }
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=raw, refusal=None),
                    finish_reason=finish,
                )
            ]
        )

    def _record_session_exchange(self, user, reply):
        self.session.append(user, reply)

    def marker_mapping_request(self, prompt):
        return json.dumps({"mappings": []})


def _workflow(tmp_path, model=None, max_attempts=5):
    source = tmp_path / "source.epub"
    source.write_bytes(b"stable marker source")
    model = model or Model()
    model.max_chunk_attempts = max_attempts
    return MarkerWorkflow(
        model,
        str(source),
        settings={
            "request_max_units": 40,
            "request_max_tokens": 8000,
            "run_fingerprint": "selective",
        },
    )


def _payload_ids(payload):
    root = etree.fromstring(payload.encode())
    return [node.get("id") for node in root]


def _unit_id(index, payload):
    return _payload_ids(payload)[index]


def test_source_review_can_approve_exact_pair_with_extra_reference(tmp_path):
    from book_maker.marker_review import digest

    workflow = _workflow(tmp_path)
    workflow.model.marker_review_enabled = True
    text = "${一色} curved ${一色}'s lips."
    reviews = []

    def reviewer(prompt):
        data = json.loads(prompt.split("\n\n", 1)[1])
        reviews.append(data)
        return json.dumps({"id": data["id"], "decision": "approve",
                           "translation": data["candidate"],
                           "allow_extra_references": True,
                           "reason": "Both references identify the same source subject."})

    workflow.model.review_marker_paragraph = reviewer
    def send(payload, feedback=""):
        return f'<bbm-response><bbm-unit id="p">{text}</bbm-unit></bbm-response>'

    assert workflow.translate(["一色勾起嘴角。"], send, lambda *args: None,
                              identities=["p"]) == [text]
    assert len(reviews) == 1
    assert reviews[0]["original"] == "一色勾起嘴角。"
    proof = workflow.state["paragraph_reviews"]["p"]
    assert proof["source_sha256"] == digest("一色勾起嘴角。")
    assert proof["translation_sha256"] == digest(text)
    assert workflow.catalogue["一色"]["count"] == 2
    from copy import deepcopy
    from book_maker.marker_bundle import BundleError, validate_state
    validate_state(workflow.state)
    altered = deepcopy(workflow.state)
    altered["paragraph_recovery"]["entries"][0]["master"] += " Altered."
    with pytest.raises(BundleError, match="approval does not match"):
        validate_state(altered)

    # That approval cannot authorize a different output with an invented name.
    text = "${未知} curved ${一色}'s lips."
    with pytest.raises(MarkerValidationError):
        workflow.translate(["一色勾起嘴角。"], send, lambda *args: None,
                           identities=["different-p"])
    assert "未知" not in workflow.catalogue


def test_source_review_rewrites_changed_dialogue_perspective(tmp_path):
    workflow = _workflow(tmp_path)
    workflow.model.marker_review_enabled = True
    source = "我和一色很好。食蜂操祈笑了。"
    candidate = '"${食蜂操祈} and ${一色} get along." ${食蜂操祈} smiled.'
    corrected = '"${一色} and I get along." ${食蜂操祈} smiled.'

    def reviewer(prompt):
        data = json.loads(prompt.split("\n\n", 1)[1])
        assert data["original"] == source
        assert data["candidate"] == candidate
        return json.dumps({"id": "p", "decision": "rewrite",
                           "translation": corrected,
                           "allow_extra_references": False,
                           "reason": "Source dialogue 我 is the first-person speaker."})

    workflow.model.review_marker_paragraph = reviewer
    assert workflow.translate(
        [source], lambda *args: f'<bbm-response><bbm-unit id="p">{candidate}</bbm-unit></bbm-response>',
        lambda *args: None, identities=["p"],
    ) == [corrected]
    assert workflow.catalogue["食蜂操祈"]["count"] == 1


def test_reviewer_request_has_no_translation_conversation():
    model = AdapterModel([('{"decision":"defer"}', "stop")])
    model.session.append("earlier source secret", "earlier translation secret")
    assert model.review_marker_paragraph("isolated source and candidate") == '{"decision":"defer"}'
    messages = model.sends[0]
    assert len(messages) == 2
    assert messages[-1]["content"] == "isolated source and candidate"
    assert all("secret" not in message["content"] for message in messages)


def test_only_failed_paragraphs_are_requested_and_successes_are_unchanged(tmp_path):
    workflow = _workflow(tmp_path)
    sources = [f"第{index}段：美琴笑了。" for index in range(1, 41)]
    bad = {2, 17, 33}
    calls = []
    first_round = {}

    def send(payload, feedback=""):
        ids = _payload_ids(payload)
        calls.append(ids)
        units = []
        for identity in ids:
            index = int(identity.rsplit("_", 1)[1])
            valid = (
                index not in bad
                or len(calls) >= 3
                or (len(calls) == 2 and index != 17)
            )
            label = "美琴" if valid else "未知"
            text = f"${{{label}}} smiled in paragraph {index + 1}."
            if len(calls) == 1 and valid:
                first_round[index] = text
            units.append(
                f'<bbm-unit id="{identity}">{text}</bbm-unit>'
            )
        return "<bbm-response>\n" + "\n".join(units) + "\n</bbm-response>"

    translations = workflow.translate(sources, send, lambda *args: None)
    assert len(calls) == 3
    assert calls[0] == [
        _unit_id(index, paragraph_request(sources)[1]) for index in range(40)
    ]
    assert calls[1] == [calls[0][index] for index in sorted(bad)]
    assert calls[2] == [calls[0][17]]
    for index, text in first_round.items():
        assert translations[index] == text
    assert workflow.catalogue["美琴"]["count"] == 40
    assert "未知" not in workflow.catalogue
    assert len(workflow.model.session.messages()) == 6  # three accepted rounds


def test_one_bad_marker_does_not_reject_valid_siblings(tmp_path):
    workflow = _workflow(tmp_path)
    calls = []

    def send(payload, feedback=""):
        ids = _payload_ids(payload)
        calls.append((ids, feedback))
        units = []
        for identity in ids:
            index = int(identity.rsplit("_", 1)[1])
            if index == 0:
                text = "${美琴} smiled."
            elif len(calls) == 1:
                text = "${白井黑子} arrived."
            else:
                text = "${黑子} arrived."
            units.append(
                f'<bbm-unit id="{identity}">{text}</bbm-unit>'
            )
        return "<bbm-response>\n" + "\n".join(units) + "\n</bbm-response>"

    result = workflow.translate(
        ["美琴笑了。", "黑子来了。"], send, lambda *args: None
    )
    assert result == ["${美琴} smiled.", "${黑子} arrived."]
    assert len(calls) == 2
    assert len(calls[1][0]) == 1
    assert calls[1][1] == ""
    assert "白井黑子" in next(f["reason"] for f in workflow.state["diagnostics"]["failures"] if f["stage"] == "paragraph-response")
    assert workflow.catalogue["美琴"]["count"] == 1
    assert workflow.catalogue["黑子"]["count"] == 1
    history = workflow.model.session.messages()
    assert all("Failures from" not in message["content"] for message in history)
    assert all("白井黑子" not in message["content"] for message in history)


def test_five_send_budget_and_a_one_send_budget(tmp_path):
    workflow = _workflow(tmp_path, max_attempts=5)
    calls = []

    def always_bad(payload, feedback=""):
        calls.append(payload)
        identity = _payload_ids(payload)[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            "${未知}</bbm-unit></bbm-response>"
        )

    with pytest.raises(MarkerValidationError, match="exhausted after 5"):
        workflow.translate(["美琴笑了。"], always_bad, lambda *args: None)
    assert len(calls) == 5
    assert workflow.catalogue == {}
    assert workflow.model.session.messages() == []

    one = _workflow(tmp_path, max_attempts=1)
    calls.clear()

    def once(payload, feedback=""):
        calls.append(payload)
        identity = _payload_ids(payload)[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            "${未知}</bbm-unit></bbm-response>"
        )

    with pytest.raises(MarkerValidationError, match="exhausted after 1"):
        one.translate(["美琴笑了。"], once, lambda *args: None)
    assert len(calls) == 1


def test_transport_failures_consume_the_budget_and_stop_bounded(tmp_path, monkeypatch):
    import book_maker.marker_workflow as workflow_module

    monkeypatch.setattr(workflow_module.time, "sleep", lambda _seconds: None)
    workflow = _workflow(tmp_path, max_attempts=2)
    calls = []

    def broken(payload, feedback=""):
        calls.append(payload)
        raise RuntimeError("temporary transport failure")

    with pytest.raises(MarkerValidationError, match="exhausted after 2"):
        workflow.translate(["美琴笑了。"], broken, lambda *args: None)
    assert len(calls) == 2
    assert workflow.state["diagnostics"]["failures"]


def test_provider_refusal_is_terminal_even_with_fragments(tmp_path):
    workflow = _workflow(tmp_path, max_attempts=5)
    calls = []

    def refused(payload, feedback=""):
        calls.append(payload)
        raise ProviderRefusal("policy")

    with pytest.raises(ProviderRefusal):
        workflow.translate(["美琴笑了。"], refused, lambda *args: None)
    assert len(calls) == 1


def test_local_commit_failure_rolls_back_and_resume_skips_committed_work(tmp_path):
    class FailSecondRound(Model):
        fail = True

        def _record_session_exchange(self, user, reply):
            self.session.append(user, reply)
            if self.fail and "${黑子}" in reply:
                raise RuntimeError("local session store unavailable")

    model = FailSecondRound()
    workflow = _workflow(tmp_path, model)
    calls = []

    def send(payload, feedback=""):
        ids = _payload_ids(payload)
        calls.append(ids)
        units = []
        for identity in ids:
            index = int(identity.rsplit("_", 1)[1])
            if index == 0:
                text = "${美琴} smiled."
            elif len(calls) == 1 and model.fail:
                text = "${白井黑子} arrived."
            else:
                text = "${黑子} arrived."
            units.append(
                f'<bbm-unit id="{identity}">{text}</bbm-unit>'
            )
        return "<bbm-response>\n" + "\n".join(units) + "\n</bbm-response>"

    sources = ["美琴笑了。", "黑子来了。"]
    with pytest.raises(MarkerValidationError, match="local journal"):
        workflow.translate(sources, send, lambda *args: None)
    assert set(workflow.catalogue) == {"美琴"}
    assert workflow.catalogue["美琴"]["count"] == 1
    entries = workflow.recovery_entries()
    assert [entry["index"] for entry in entries] == [1]
    assert len(workflow.model.session.messages()) == 2
    request_ids = list(calls[0])

    model.fail = False
    calls.clear()
    result = workflow.translate(sources, send, lambda *args: None)
    assert result == ["${美琴} smiled.", "${黑子} arrived."]
    assert len(calls) == 1
    # A direct coordinator caller has not written a checkpoint yet.  The
    # accepted journal must survive the return; the loader's snapshot is what
    # promotes work into the checkpoint and removes the journal entries.
    assert len(workflow.recovery_entries()) == 2
    workflow.snapshot(
        job_ids=request_ids,
        translations=result,
        localized_cache={},
    )
    assert len(workflow.recovery_entries()) == 0
    assert workflow.catalogue["美琴"]["count"] == 1
    assert workflow.catalogue["黑子"]["count"] == 1
    assert len(workflow.model.session.messages()) == 4


def test_compaction_after_partial_acceptance_sees_registered_labels(tmp_path):
    class CompactingModel(Model):
        no_context_compact = False

        def __init__(self):
            super().__init__()
            self.summaries = []
            self.compactions = 0

        def _session_budget(self):
            return 1

        def _record_session_exchange(self, user, reply):
            self.session.append(user, reply)
            labels = []
            for part in reply.split("${")[1:]:
                labels.append(part.split("}", 1)[0])
            summary = "Summary with " + (
                f"${{{labels[0]}}}" if labels else "no names"
            ) + "."
            self._marker_state.validate_compaction(summary)
            self._marker_state.note_compaction(summary, self.session.windows)
            self.summaries.append(summary)
            self.compactions += 1
            self.session.reset(seed=summary)

    model = CompactingModel()
    workflow = _workflow(tmp_path, model)
    calls = []

    def send(payload, feedback=""):
        ids = _payload_ids(payload)
        calls.append(ids)
        units = []
        for identity in ids:
            index = int(identity.rsplit("_", 1)[1])
            if index == 0:
                text = "${美琴} smiled."
            elif len(calls) == 1:
                text = "${白井黑子} arrived."
            else:
                text = "${黑子} arrived."
            units.append(
                f'<bbm-unit id="{identity}">{text}</bbm-unit>'
            )
        return "<bbm-response>\n" + "\n".join(units) + "\n</bbm-response>"

    result = workflow.translate(
        ["美琴笑了。", "黑子来了。"], send, lambda *args: None
    )
    assert result == ["${美琴} smiled.", "${黑子} arrived."]
    assert model.compactions == 2
    assert model.summaries[0] == "Summary with ${美琴}."
    assert "白井黑子" not in json.dumps(
        workflow.model.session.messages(), ensure_ascii=False
    )
    assert workflow.catalogue["美琴"]["count"] == 1
    assert workflow.catalogue["黑子"]["count"] == 1


def test_quality_checks_apply_per_paragraph_and_do_not_exempt_unmarked_text(tmp_path):
    workflow = _workflow(tmp_path, max_attempts=2)
    calls = []

    def untranslated(payload, feedback=""):
        calls.append(payload)
        identity = _payload_ids(payload)[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            "${美琴} 她笑了很久。</bbm-unit></bbm-response>"
        )

    with pytest.raises(MarkerValidationError, match="exhausted after 2"):
        workflow.translate(
            ["美琴笑了。"], untranslated, workflow.validate_quality
        )
    assert len(calls) == 2
    assert workflow.catalogue == {}

    workflow = _workflow(tmp_path, max_attempts=2)
    calls.clear()

    def markdown(payload, feedback=""):
        calls.append(payload)
        identity = _payload_ids(payload)[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            "${美琴} `smiled`.</bbm-unit></bbm-response>"
        )

    with pytest.raises(MarkerValidationError, match="exhausted after 2"):
        workflow.translate(["美琴笑了。"], markdown, workflow.validate_quality)
    assert len(calls) == 2


def test_parser_keeps_valid_siblings_and_rejects_ambiguous_ids():
    identities = ["a", "b", "c"]
    raw = (
        "<bbm-response>"
        '<bbm-unit id="a">A</bbm-unit>'
        '<bbm-unit id="a">A</bbm-unit>'
        '<bbm-unit id="b"><span>nested</span></bbm-unit>'
        '<bbm-unit id="unknown">ignored</bbm-unit>'
        "stray commentary"
        "</bbm-response>"
    )
    reply = parse_paragraph_reply(raw, identities)
    assert reply.texts == {}
    assert "duplicate" in reply.errors["a"]
    assert "nested markup" in reply.errors["b"]
    assert "missing" in reply.errors["c"]
    assert reply.unknown == ["unknown"]

    valid = (
        "<bbm-response>"
        '<bbm-unit id="c">C</bbm-unit>'
        '<bbm-unit id="a">A</bbm-unit>'
        '<bbm-unit id="b">B</bbm-unit>'
        "</bbm-response>"
    )
    assert parse_paragraph_response(valid, identities) == ["A", "B", "C"]

    sibling = (
        "<bbm-response>introduction"
        '<bbm-unit id="a">A</bbm-unit>'
        "between units"
        '<bbm-unit id="other">ignored</bbm-unit>'
        '<bbm-unit id="b">B</bbm-unit>tail'
        "</bbm-response>"
    )
    reply = parse_paragraph_reply(sibling, identities)
    assert reply.texts == {"a": "A", "b": "B"}
    assert reply.unknown == ["other"]
    assert reply.diagnostics
    assert "missing" in reply.errors["c"]


def test_malformed_outer_xml_only_retains_safe_independent_fragments():
    identities = ["a", "b", "c"]
    raw = (
        "commentary before "
        '<bbm-unit id="a">A &amp; B</bbm-unit> '
        "commentary between "
        '<bbm-unit id="b">B</bbm-unit>'
    )
    reply = parse_paragraph_reply(raw, identities)
    assert reply.salvaged
    assert reply.texts == {"a": "A & B", "b": "B"}
    assert "missing" in reply.errors["c"]

    truncated = '<bbm-response><bbm-unit id="a">A</bbm-unit><bbm-unit id="b">B'
    reply = parse_paragraph_reply(truncated, identities)
    assert reply.texts == {"a": "A"}
    assert "ambiguous" in reply.errors["b"] or "incomplete" in reply.errors["b"]

    nested = '<bbm-response><bbm-unit id="a">A<bbm-unit id="b">B</bbm-unit></bbm-unit>'
    reply = parse_paragraph_reply(nested, identities)
    assert reply.texts == {}
    assert "ambiguous" in reply.errors["a"]

    commented = (
        "commentary <!-- <bbm-unit id=\"a\">FAKE</bbm-unit> --> "
        '<bbm-unit id="b">B</bbm-unit>'
    )
    reply = parse_paragraph_reply(commented, identities)
    assert reply.texts == {"b": "B"}
    assert "missing" in reply.errors["a"]


def test_declarations_and_oversized_responses_are_rejected_safely(monkeypatch):
    import book_maker.marker_workflow as workflow_module

    identities = ["a"]
    dtd = (
        '<!DOCTYPE bbm-response [<!ENTITY x "y">]>'
        '<bbm-response><bbm-unit id="a">&x;</bbm-unit></bbm-response>'
    )
    reply = parse_paragraph_reply(dtd, identities)
    assert reply.texts == {}
    assert "forbidden declaration" in reply.errors["a"]

    monkeypatch.setattr(workflow_module, "MAX_PARAGRAPH_RESPONSE_CHARS", 10)
    reply = parse_paragraph_reply("<bbm-response/>", identities)
    assert reply.texts == {}
    assert "exceeds" in reply.errors["a"]


def test_catalogue_counts_come_from_returned_markers_not_source_availability():
    delta = validate_batch(
        ["美琴…美琴…美琴"],
        ["${美琴} arrived."],
        ["美琴"],
    )
    assert delta["美琴"]["count"] == 1
    assert len(delta["美琴"]["occurrences"]) == 1
    with pytest.raises(MarkerError):
        validate_batch(["a-b"], ["${ab}"], [])


def test_identical_source_paragraphs_keep_distinct_journal_identities(tmp_path):
    class FailSecondRound(Model):
        fail = True

        def _record_session_exchange(self, user, reply):
            self.session.append(user, reply)
            if self.fail and "again" in reply:
                raise RuntimeError("local session store unavailable")

    workflow = _workflow(tmp_path, FailSecondRound())
    sources = ["美琴笑了。", "美琴笑了。"]
    calls = []

    def send(payload, feedback=""):
        ids = _payload_ids(payload)
        calls.append(ids)
        units = []
        for identity in ids:
            index = int(identity.rsplit("_", 1)[1])
            if index == 0:
                text = "${美琴} smiled."
            elif len(calls) == 1 and workflow.model.fail:
                text = "${未知} smiled."
            else:
                text = "${美琴} smiled again."
            units.append(
                f'<bbm-unit id="{identity}">{text}</bbm-unit>'
            )
        return "<bbm-response>\n" + "\n".join(units) + "\n</bbm-response>"

    with pytest.raises(MarkerValidationError, match="local journal"):
        workflow.translate(sources, send, lambda *args: None)
    identities = calls[0]
    entries = workflow.recovery_entries()
    assert [entry["identity"] for entry in entries] == [identities[0]]
    assert workflow.recovery_master(identities[0], sources[0]) == "${美琴} smiled."
    assert workflow.recovery_master(identities[1], sources[1]) is None

    workflow.model.fail = False
    calls.clear()
    result = workflow.translate(sources, send, lambda *args: None)
    assert result == ["${美琴} smiled.", "${美琴} smiled again."]
    assert len(calls) == 1
    assert calls[0] == [identities[1]]


def test_snapshot_promotes_journal_only_when_the_checkpoint_has_the_identity(tmp_path):
    workflow = _workflow(tmp_path)
    sources = ["美琴笑了。", "黑子来了。"]
    identities = paragraph_request(sources)[0]

    def send(payload, feedback=""):
        units = []
        for identity in _payload_ids(payload):
            index = int(identity.rsplit("_", 1)[1])
            text = "${美琴} smiled." if index == 0 else "${黑子} arrived."
            units.append(f'<bbm-unit id="{identity}">{text}</bbm-unit>')
        return "<bbm-response>\n" + "\n".join(units) + "\n</bbm-response>"

    translations = workflow.translate(sources, send, lambda *args: None)
    assert [entry["index"] for entry in workflow.recovery_entries()] == [1, 2]

    workflow.snapshot(
        job_ids=identities[:1],
        translations=translations[:1],
        localized_cache={},
    )
    assert [entry["index"] for entry in workflow.recovery_entries()] == [2]

    workflow.snapshot(
        job_ids=identities,
        translations=translations,
        localized_cache={},
    )
    assert workflow.recovery_entries() == []
    assert workflow.catalogue["美琴"]["count"] == 1
    assert workflow.catalogue["黑子"]["count"] == 1


def test_acceptance_is_persisted_before_the_original_chunk_returns(tmp_path):
    workflow = _workflow(tmp_path)
    persisted = []
    calls = []

    def send(payload, feedback=""):
        ids = _payload_ids(payload)
        calls.append(ids)
        units = []
        for identity in ids:
            index = int(identity.rsplit("_", 1)[1])
            if len(calls) == 1 and index == 0:
                text = "${美琴} smiled."
            elif len(calls) == 1:
                text = "${未知} arrived."
            else:
                raise KeyboardInterrupt
            units.append(
                f'<bbm-unit id="{identity}">{text}</bbm-unit>'
            )
        return "<bbm-response>\n" + "\n".join(units) + "\n</bbm-response>"

    with pytest.raises(KeyboardInterrupt):
        workflow.translate(
            ["美琴笑了。", "黑子来了。"],
            send,
            lambda *args: None,
            persist=lambda: persisted.append(list(workflow.recovery_entries())),
        )
    assert len(persisted) == 1
    assert [entry["identity"] for entry in persisted[0]] == [calls[0][0]]
    assert [entry["index"] for entry in persisted[0]] == [1]


def test_marker_mode_bypasses_outer_whole_chunk_retry(tmp_path):
    from book_maker.loader.epub_loader import EPUBBookLoader

    class LoaderModel(Model):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def translate_marker_units(self, payload, feedback=""):
            self.calls += 1
            identity = _payload_ids(payload)[0]
            return (
                f'<bbm-response><bbm-unit id="{identity}">'
                "${未知}</bbm-unit></bbm-response>"
            )

    model = LoaderModel()
    workflow = _workflow(tmp_path, model, max_attempts=2)
    model._marker_state = workflow
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = model
    loader.language = "en"
    loader.source_lang = "zh"
    loader.request_max_units = 40
    loader.request_max_tokens = 8000
    loader.whole_chapter = False
    loader.strip_obfuscated_codes = True
    loader.max_chunk_attempts = 5
    loader.retry_one_by_one = True
    loader._save_progress = lambda: None

    with pytest.raises(MarkerValidationError, match="exhausted after 2"):
        loader._translate_texts_aligned(["美琴笑了。"])
    assert model.calls == 2


def test_recovery_lookup_uses_the_coordinators_cleaned_source(tmp_path):
    from types import SimpleNamespace

    from book_maker.loader.epub_loader import EPUBBookLoader
    from book_maker.obfuscated_codes import strip_codes

    workflow = _workflow(tmp_path)
    raw_source = "T2就起六揪翼叁捌遛\u3000美琴笑了。"
    cleaned = strip_codes(raw_source)
    assert cleaned != raw_source
    workflow._put_recovery(
        {
            "identity": "job-one",
            "index": 1,
            "source": cleaned,
            "source_sha256": workflow._source_digest(cleaned),
            "master": "${美琴} smiled.",
            "kind": "body",
        }
    )
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader._marker_state = workflow
    loader.strip_obfuscated_codes = True
    job = SimpleNamespace(job_id="job-one", source_text=raw_source)
    assert loader._marker_recovery_value(job) == "${美琴} smiled."

    loader.strip_obfuscated_codes = False
    assert loader._marker_recovery_value(job) is None


def test_recovery_journal_validation_checks_digest_and_version(tmp_path):
    from copy import deepcopy

    from book_maker.marker_bundle import BundleError, validate_state

    workflow = _workflow(tmp_path)
    workflow._put_recovery(
        {
            "identity": "job-one",
            "index": 1,
            "source": "美琴笑了。",
            "source_sha256": workflow._source_digest("美琴笑了。"),
            "master": "${美琴} smiled.",
            "kind": "body",
        }
    )
    state = deepcopy(workflow.state)
    validate_state(state)

    broken = deepcopy(state)
    broken["paragraph_recovery"]["entries"][0]["source_sha256"] = "0" * 64
    with pytest.raises(BundleError, match="digest"):
        validate_state(broken)

    broken = deepcopy(state)
    broken["paragraph_recovery"]["version"] = 99
    with pytest.raises(BundleError, match="recovery version"):
        validate_state(broken)


# --------------------------------------------------------------- salvage scan


def _salvage(text, identities):
    return parse_paragraph_reply(text, list(identities))


NESTED_DUPLICATE = (
    '<broken><bbm-unit id="a">outer'
    '<bbm-unit id="b">first B</bbm-unit></bbm-unit>'
    '<bbm-unit id="b">second B</bbm-unit>'
)
CDATA_EXAMPLE = (
    "<broken><![CDATA["
    '<bbm-unit id="b">example, not a response unit</bbm-unit>]]>'
)
TRUNCATED_DUPLICATE = (
    '<broken><bbm-unit id="b">first B</bbm-unit>'
    '<bbm-unit id="b"'
)


def test_salvage_rejects_nested_duplicate_cdata_and_truncated_examples():
    nested = _salvage(NESTED_DUPLICATE, ["a", "b"])
    assert nested.texts == {}
    assert "b" not in nested.texts
    assert "a" not in nested.texts

    cdata = _salvage(CDATA_EXAMPLE, ["a", "b"])
    assert cdata.texts == {}
    assert "b" not in cdata.texts

    truncated = _salvage(TRUNCATED_DUPLICATE, ["a", "b"])
    assert truncated.texts == {}
    assert "b" not in truncated.texts


def test_salvage_rejects_nested_and_unfinished_duplicates_without_wrappers():
    nested = (
        '<bbm-unit id="a">outer<bbm-unit id="b">first B</bbm-unit></bbm-unit>'
        '<bbm-unit id="b">second B</bbm-unit>'
    )
    reply = _salvage(nested, ["a", "b"])
    assert reply.texts == {}
    assert "duplicate" in reply.errors["b"]
    assert "duplicate" in reply.errors["a"]

    unfinished = '<bbm-unit id="b">first B</bbm-unit><bbm-unit id="b"'
    reply = _salvage(unfinished, ["a", "b"])
    assert reply.texts == {}
    assert "duplicate" in reply.errors["b"]


def test_salvage_keeps_a_safe_sibling_beside_each_ambiguous_example():
    replies = [
        _salvage(
            (
                '<bbm-unit id="a">outer<bbm-unit id="b">first B</bbm-unit>'
                '</bbm-unit><bbm-unit id="b">second B</bbm-unit>'
                '<bbm-unit id="c">safe C</bbm-unit>'
            ),
            ["a", "b", "c"],
        ),
        _salvage(
            (
                '<bbm-unit id="c">safe C</bbm-unit><broken><![CDATA['
                '<bbm-unit id="b">example</bbm-unit>]]>'
            ),
            ["b", "c"],
        ),
        _salvage(
            (
                '<bbm-unit id="c">safe C</bbm-unit>'
                '<bbm-unit id="b">first B</bbm-unit><bbm-unit id="b"'
            ),
            ["b", "c"],
        ),
    ]
    for reply in replies:
        assert reply.texts == {"c": "safe C"}


def test_salvage_rejects_units_nested_under_unknown_markup():
    reply = _salvage(
        '<broken><bbm-unit id="a">A</bbm-unit></broken>'
        '<bbm-unit id="b">B</bbm-unit>',
        ["a", "b"],
    )
    assert reply.texts == {"b": "B"}
    assert "a" not in reply.texts
    assert "nested under unknown markup" in reply.errors["a"]


def test_salvage_rejects_all_duplicate_opening_shapes():
    reversed_order = _salvage(
        '<bbm-unit id="b">B1</bbm-unit><bbm-unit id="a">A</bbm-unit>'
        '<bbm-unit id="b">B2</bbm-unit>',
        ["a", "b"],
    )
    assert reversed_order.texts == {"a": "A"}
    assert "duplicate" in reversed_order.errors["b"]

    complete = _salvage(
        '<bbm-unit id="a">A1</bbm-unit><bbm-unit id="a">A2</bbm-unit>',
        ["a"],
    )
    assert complete.texts == {}

    complete_then_truncated = _salvage(
        '<bbm-unit id="a">A1</bbm-unit><bbm-unit id="a">A2</bbm-unit>'
        '<bbm-unit id="a"',
        ["a"],
    )
    assert complete_then_truncated.texts == {}


def test_salvage_handles_missing_attributes_and_partial_prefixes():
    missing = _salvage(
        '<bbm-unit>A</bbm-unit><bbm-unit id="b">B</bbm-unit>', ["a", "b"]
    )
    assert missing.texts == {"b": "B"}
    assert "missing" in missing.errors["a"]
    assert "<missing id>" in missing.unknown

    prefix = _salvage(
        '<bbm-unit id="c">C</bbm-unit><bbm-unit id="ab">AB</bbm-unit>'
        '<bbm-unit id="a',
        ["ab", "c"],
    )
    assert prefix.texts == {"c": "C"}
    assert "duplicate" in prefix.errors["ab"]

    ambiguous_prefix = _salvage(
        '<bbm-unit id="ab">AB</bbm-unit><bbm-unit id="ac">AC</bbm-unit>'
        '<bbm-unit id="a',
        ["ab", "ac"],
    )
    assert ambiguous_prefix.texts == {}
    assert "duplicate" in ambiguous_prefix.errors["ab"]
    assert "duplicate" in ambiguous_prefix.errors["ac"]


def test_salvage_marks_unattributable_openings_ambiguous():
    for raw in ("<bbm-unit", "<bbm-unit id=", "<bbm-unit id="):
        reply = _salvage(raw, ["a", "b"])
        assert reply.texts == {}
        assert "ambiguous" in reply.errors["a"]
        assert "ambiguous" in reply.errors["b"]


def test_fake_unit_text_in_markup_never_becomes_a_paragraph():
    cases = [
        (
            '<bbm-unit id="a">A</bbm-unit><bbm-unit id="c">C</bbm-unit>'
            '<![CDATA[<bbm-unit id="b">fake</bbm-unit>]]>'
        ),
        (
            '<bbm-unit id="a">A</bbm-unit><bbm-unit id="c">C</bbm-unit>'
            '<!-- <bbm-unit id="b">fake</bbm-unit> -->'
        ),
        (
            '<bbm-unit id="a">A</bbm-unit><bbm-unit id="c">C</bbm-unit>'
            '<!-- <bbm-unit id="b">fake'
        ),
        (
            '<bbm-unit id="a">A</bbm-unit><bbm-unit id="c">C</bbm-unit>'
            '<broken data-x="<bbm-unit id=\'b\'>fake</bbm-unit>">'
        ),
        (
            '<bbm-unit id="a">A</bbm-unit><bbm-unit id="c">C</bbm-unit>'
            '<?pi <bbm-unit id="b">fake</bbm-unit> ?>'
        ),
        (
            '<bbm-unit id="a">A</bbm-unit><bbm-unit id="c">C</bbm-unit>'
            '<?pi <bbm-unit id="b">fake'
        ),
        (
            '<bbm-unit id="a">A</bbm-unit><bbm-unit id="c">C</bbm-unit>'
            '<![CDATA[<bbm-unit id="b">fake'
        ),
    ]
    for raw in cases:
        reply = _salvage(raw, ["a", "b", "c"])
        assert reply.texts.get("a") == "A"
        assert reply.texts.get("c") == "C"
        assert "b" not in reply.texts


def test_salvage_reads_cdata_refs_and_quoted_attributes_correctly():
    cdata = _salvage(
        '<bbm-unit id="a"><![CDATA[Use <b>raw</b> here]]></bbm-unit>'
        '<bbm-unit id="b">B</bbm-unit>',
        ["a", "b"],
    )
    assert cdata.texts == {"a": "Use <b>raw</b> here", "b": "B"}

    encoded = _salvage(
        '<bbm-unit id="a&#38;b">A</bbm-unit><bbm-unit id="c">C</bbm-unit>',
        ["a&b", "c"],
    )
    assert encoded.texts == {"a&b": "A", "c": "C"}

    data_id = _salvage(
        '<bbm-unit data-id="a">A</bbm-unit><bbm-unit id="b">B</bbm-unit>',
        ["a", "b"],
    )
    assert data_id.texts == {"b": "B"}

    duplicate = _salvage(
        '<bbm-unit id="a" id="a">A</bbm-unit><bbm-unit id="c">C</bbm-unit>',
        ["a", "c"],
    )
    assert duplicate.texts == {"c": "C"}
    assert "duplicate" in duplicate.errors["a"]

    conflicting = _salvage(
        '<bbm-unit id="a" id="b">X</bbm-unit><bbm-unit id="c">C</bbm-unit>',
        ["a", "b", "c"],
    )
    assert conflicting.texts == {"c": "C"}
    assert "duplicate" in conflicting.errors["a"]
    assert "duplicate" in conflicting.errors["b"]

    greater_than = _salvage(
        '<bbm-unit id="a" data-x=">">A</bbm-unit>'
        '<bbm-unit id="b">B</bbm-unit>',
        ["a", "b"],
    )
    assert greater_than.texts == {"a": "A", "b": "B"}


def test_salvage_keeps_safe_units_and_never_assigns_by_position():
    reply = _salvage(
        '<bbm-unit id="b">B</bbm-unit><bbm-unit id="a">A</bbm-unit>'
        '<bbm-unit id="c"',
        ["a", "b", "c"],
    )
    assert reply.texts == {"b": "B", "a": "A"}
    assert "c" not in reply.texts
    assert "x" not in reply.texts

    unknown = _salvage(
        '<bbm-unit id="x">X</bbm-unit><bbm-unit id="a">A</bbm-unit>',
        ["a"],
    )
    assert unknown.texts == {"a": "A"}
    assert "x" in unknown.unknown


# ------------------------------------- strict and unfinished id attribution


def test_strict_nested_duplicate_never_accepts_either_id():
    raw = (
        '<bbm-response>'
        '<bbm-unit id="a">outer'
        '<bbm-unit id="b">first B</bbm-unit></bbm-unit>'
        '<bbm-unit id="b">second B</bbm-unit>'
        '</bbm-response>'
    )
    reply = parse_paragraph_reply(raw, ["a", "b"])
    assert reply.texts == {}
    assert "nested markup" in reply.errors["a"]
    assert "duplicate" in reply.errors["b"]
    assert reply.salvaged is False


def test_strict_nested_duplicate_is_rejected_in_either_order_with_safe_sibling():
    duplicated = (
        '<bbm-unit id="a">outer<bbm-unit id="b">first B</bbm-unit></bbm-unit>'
    )
    direct = '<bbm-unit id="b">second B</bbm-unit>'
    for units in (duplicated + direct, direct + duplicated):
        raw = f"<bbm-response>{units}<bbm-unit id=\"c\">C</bbm-unit></bbm-response>"
        reply = parse_paragraph_reply(raw, ["a", "b", "c"])
        assert reply.texts == {"c": "C"}
        assert "a" in reply.errors
        assert "duplicate" in reply.errors["b"]


def test_strict_counts_expected_units_nested_under_unknown_markup():
    duplicate = (
        "<bbm-response>"
        '<broken><bbm-unit id="a">nested A</bbm-unit></broken>'
        '<bbm-unit id="a">direct A</bbm-unit>'
        '<bbm-unit id="b">B</bbm-unit>'
        "</bbm-response>"
    )
    reply = parse_paragraph_reply(duplicate, ["a", "b"])
    assert reply.texts == {"b": "B"}
    assert "duplicate" in reply.errors["a"]

    nested_only = (
        "<bbm-response>"
        '<broken><bbm-unit id="a">nested A</bbm-unit></broken>'
        '<bbm-unit id="b">B</bbm-unit>'
        "</bbm-response>"
    )
    reply = parse_paragraph_reply(nested_only, ["a", "b"])
    assert reply.texts == {"b": "B"}
    assert "nested under unknown markup" in reply.errors["a"]


def test_strict_cdata_and_escaped_examples_do_not_add_phantom_ids():
    raw = (
        "<bbm-response>"
        '<bbm-unit id="a"><![CDATA[<bbm-unit id="b">fake</bbm-unit>]]>'
        "</bbm-unit>"
        '<bbm-unit id="b">&lt;bbm-unit id="c"&gt;fake&lt;/bbm-unit&gt;</bbm-unit>'
        "</bbm-response>"
    )
    reply = parse_paragraph_reply(raw, ["a", "b", "c"])
    assert reply.texts == {
        "a": '<bbm-unit id="b">fake</bbm-unit>',
        "b": '<bbm-unit id="c">fake</bbm-unit>',
    }
    assert reply.errors == {"c": "missing from response"}


def test_unfinished_opening_collects_every_id_attribute():
    reproduction = (
        '<bbm-unit id="b">first B</bbm-unit><bbm-unit id="a" id="b"'
    )
    reply = parse_paragraph_reply(reproduction, ["a", "b"])
    assert reply.texts == {}
    assert "duplicate" in reply.errors["a"]
    assert "duplicate" in reply.errors["b"]

    reversed_attrs = '<bbm-unit id="a">first A</bbm-unit><bbm-unit id="b" id="a"'
    reply = parse_paragraph_reply(reversed_attrs, ["a", "b"])
    assert reply.texts == {}
    assert "duplicate" in reply.errors["a"]
    assert "duplicate" in reply.errors["b"]

    repeated = '<bbm-unit id="a">first A</bbm-unit><bbm-unit id="a" id="a"'
    reply = parse_paragraph_reply(repeated, ["a", "b"])
    assert reply.texts == {}
    assert "duplicate" in reply.errors["a"]


def test_unfinished_opening_keeps_bounded_safe_siblings():
    bounded = (
        '<bbm-unit id="c">C</bbm-unit>'
        '<bbm-unit id="b">B</bbm-unit>'
        '<bbm-unit id="a"'
    )
    reply = parse_paragraph_reply(bounded, ["a", "b", "c"])
    assert reply.texts == {"c": "C", "b": "B"}
    assert "duplicate" in reply.errors["a"]

    unrelated = (
        '<bbm-unit id="c">C</bbm-unit>'
        '<bbm-unit id="a" class="x" id="b"'
    )
    reply = parse_paragraph_reply(unrelated, ["a", "b", "c"])
    assert reply.texts == {"c": "C"}
    assert "duplicate" in reply.errors["a"]
    assert "duplicate" in reply.errors["b"]


def test_unfinished_opening_reads_prefixes_and_entities():
    prefix = (
        '<bbm-unit id="ab">AB</bbm-unit>'
        '<bbm-unit id="ac">AC</bbm-unit>'
        '<bbm-unit id="a'
    )
    reply = parse_paragraph_reply(prefix, ["ab", "ac"])
    assert reply.texts == {}
    assert "duplicate" in reply.errors["ab"]
    assert "duplicate" in reply.errors["ac"]

    encoded = (
        '<bbm-unit id="a&#38;b">first</bbm-unit>'
        '<bbm-unit id="a&#38;c" id="a&#38;b"'
    )
    reply = parse_paragraph_reply(encoded, ["a&b", "a&c"])
    assert reply.texts == {}
    assert "duplicate" in reply.errors["a&b"]
    assert "duplicate" in reply.errors["a&c"]


def test_unfinished_lookalikes_do_not_taint_unrelated_ids():
    data_id = (
        '<bbm-unit id="c">C</bbm-unit>'
        '<bbm-unit id="a" data-id="b"'
    )
    reply = parse_paragraph_reply(data_id, ["a", "b", "c"])
    assert reply.texts == {"c": "C"}
    assert "duplicate" in reply.errors["a"]
    assert "duplicate" not in reply.errors["b"]

    quoted = (
        '<bbm-unit id="c">C</bbm-unit>'
        '<bbm-unit id="a" data-x="id=\'b\'"'
    )
    reply = parse_paragraph_reply(quoted, ["a", "b", "c"])
    assert reply.texts == {"c": "C"}
    assert "duplicate" in reply.errors["a"]
    assert "duplicate" not in reply.errors["b"]


def test_unfinished_unbounded_opening_blocks_unsafe_acceptance():
    for tail in (
        '<bbm-unit id="c">C</bbm-unit><bbm-unit id="a" i',
        '<bbm-unit id="c">C</bbm-unit><bbm-unit id="a" id=',
        '<bbm-unit id="c">C</bbm-unit><bbm-unit id="a" data-id="b" id',
    ):
        reply = parse_paragraph_reply(tail, ["a", "b", "c"])
        assert reply.texts == {}
        for identity in ("a", "b", "c"):
            assert "ambiguous" in reply.errors[identity]


# ------------------------------------------------------- provider truncation


def _adapter_ids(messages):
    content = messages[-1]["content"]
    start = content.rindex("<bbm-request>")
    end = content.index("</bbm-request>", start) + len("</bbm-request>")
    root = etree.fromstring(content[start:end].encode())
    return [node.get("id") for node in root]


def test_truncated_marker_response_keeps_complete_units_and_repairs_the_rest(tmp_path):
    model = AdapterModel(
        [
            (
                '<bbm-response><bbm-unit id="a">${爱丽丝} smiled.</bbm-unit>'
                '<bbm-unit id="b">${鲍勃} arr',
                "length",
            ),
            (
                '<bbm-response><bbm-unit id="b">${鲍勃} arrived.'
                "</bbm-unit></bbm-response>",
                "stop",
            ),
        ]
    )
    workflow = _workflow(tmp_path, model)
    persisted = []

    def persist():
        persisted.append((len(model.sends), workflow.recovery_entries()))

    result = workflow.translate(
        ["爱丽丝笑了。", "鲍勃到了。"],
        lambda payload, feedback="": model.translate_marker_units(payload, feedback),
        workflow.validate_quality,
        identities=["a", "b"],
        persist=persist,
    )
    assert result == ["${爱丽丝} smiled.", "${鲍勃} arrived."]
    assert len(model.sends) == 2
    first = _adapter_ids(model.sends[0])
    second = _adapter_ids(model.sends[1])
    assert first == ["a", "b"]
    assert second == ["b"]
    # Partial acceptance must not leak into the retry request context.
    assert model.sends[1][:-1] == model.sends[0][:-1]
    from book_maker.marker_workflow import assemble_marker_request
    _, fresh_payload = paragraph_request(["鲍勃到了。"], ["b"])
    assert model.sends[1][-1]["content"] == assemble_marker_request(
        fresh_payload, language="en"
    )
    assert model._marker_request_history is None
    # A was accepted, journaled, and persisted before B's repair request.
    assert persisted[0][0] == 1
    assert [entry["identity"] for entry in persisted[0][1]] == ["a"]
    assert workflow.catalogue["爱丽丝"]["count"] == 1
    assert workflow.catalogue["鲍勃"]["count"] == 1
    assert len(model.session.messages()) == 4
    assert sum(
        "${爱丽丝} smiled." in message["content"]
        for message in model.session.messages()
    ) == 1
    stages = [
        failure["stage"]
        for failure in workflow.state["diagnostics"]["failures"]
    ]
    assert "paragraph-truncated" in stages


def test_empty_truncation_consumes_the_budget_and_accepts_nothing(tmp_path):
    model = AdapterModel([("", "length"), ("", "length")])
    workflow = _workflow(tmp_path, model, max_attempts=2)
    with pytest.raises(MarkerValidationError, match="exhausted after 2"):
        workflow.translate(
            ["爱丽丝笑了。"],
            lambda payload, feedback="": model.translate_marker_units(payload, feedback),
            workflow.validate_quality,
            identities=["a"],
        )
    assert len(model.sends) == 2
    assert workflow.catalogue == {}
    assert workflow.model.session.messages() == []


def test_repeated_truncation_exhausts_only_the_outstanding_paragraph(tmp_path):
    responses = [
        (
            '<bbm-response><bbm-unit id="a">${爱丽丝} smiled.</bbm-unit>'
            '<bbm-unit id="b">${鲍勃} arr',
            "length",
        ),
        ('<bbm-response><bbm-unit id="b">${鲍勃} arr', "length"),
        ('<bbm-response><bbm-unit id="b">${鲍勃} arr', "length"),
    ]
    model = AdapterModel(responses, max_attempts=3)
    workflow = _workflow(tmp_path, model, max_attempts=3)
    persisted = []
    with pytest.raises(MarkerValidationError, match="exhausted after 3"):
        workflow.translate(
            ["爱丽丝笑了。", "鲍勃到了。"],
            lambda payload, feedback="": model.translate_marker_units(payload, feedback),
            workflow.validate_quality,
            identities=["a", "b"],
            persist=lambda: persisted.append(workflow.recovery_entries()),
        )
    assert len(model.sends) == 3
    assert _adapter_ids(model.sends[0]) == ["a", "b"]
    assert _adapter_ids(model.sends[1]) == ["b"]
    assert _adapter_ids(model.sends[2]) == ["b"]
    assert workflow.catalogue["爱丽丝"]["count"] == 1
    assert "鲍勃" not in workflow.catalogue
    assert [entry["identity"] for entry in persisted[0]] == [
        _adapter_ids(model.sends[0])[0]
    ]


def test_length_finished_response_with_all_units_is_accepted_once(tmp_path):
    model = AdapterModel(
        [
            (
                '<bbm-response><bbm-unit id="a">${爱丽丝} smiled.</bbm-unit>'
                '<bbm-unit id="b">${鲍勃} arrived.</bbm-unit></bbm-response>',
                "length",
            )
        ]
    )
    workflow = _workflow(tmp_path, model)
    result = workflow.translate(
        ["爱丽丝笑了。", "鲍勃到了。"],
        lambda payload, feedback="": model.translate_marker_units(payload, feedback),
        workflow.validate_quality,
        identities=["a", "b"],
    )
    assert result == ["${爱丽丝} smiled.", "${鲍勃} arrived."]
    assert len(model.sends) == 1
    assert workflow.catalogue["爱丽丝"]["count"] == 1
    assert workflow.catalogue["鲍勃"]["count"] == 1


def test_refusal_is_terminal_even_when_response_text_has_fragments(tmp_path):
    model = AdapterModel([ProviderRefusal("policy")])
    workflow = _workflow(tmp_path, model)
    with pytest.raises(ProviderRefusal):
        workflow.translate(
            ["爱丽丝笑了。"],
            lambda payload, feedback="": model.translate_marker_units(payload, feedback),
            workflow.validate_quality,
            identities=["a"],
        )
    assert len(model.sends) == 1
    assert workflow.catalogue == {}
    assert workflow.recovery_entries() == []
    assert workflow.model.session.messages() == []


def test_markup_examples_never_reach_journal_or_catalogue(tmp_path):
    # The outer document is malformed on purpose, so the CDATA example is seen
    # only by the lexical scanner, never by the strict parser.
    model = AdapterModel(
        [
            (
                '<bbm-response><bbm-unit id="a">${爱丽丝} smiled.</bbm-unit>'
                "</bbm-response>"
                "<![CDATA[<bbm-unit id='b'>${鲍勃} example</bbm-unit>]]>",
                "stop",
            )
        ]
    )
    workflow = _workflow(tmp_path, model)
    result = workflow.translate(
        ["爱丽丝笑了。"],
        lambda payload, feedback="": model.translate_marker_units(payload, feedback),
        workflow.validate_quality,
        identities=["a"],
    )
    assert result == ["${爱丽丝} smiled."]
    assert len(model.sends) == 1
    assert set(workflow.catalogue) == {"爱丽丝"}
    entries = workflow.recovery_entries()
    assert [entry["identity"] for entry in entries] == ["a"]
    assert all("鲍勃" not in entry["master"] for entry in entries)
    assert all(
        "鲍勃" not in message["content"]
        for message in model.session.messages()
    )


# -------------------------------------- ambiguous id repair through workflow


STRICT_AMBIGUOUS_FIRST = (
    "<bbm-response>"
    '<bbm-unit id="a">${爱丽丝} BAD outer'
    '<bbm-unit id="b">${鲍勃} BAD first</bbm-unit></bbm-unit>'
    '<bbm-unit id="b">${鲍勃} BAD second</bbm-unit>'
    '<bbm-unit id="c">${卡罗尔} waved.</bbm-unit>'
    "</bbm-response>"
)
UNFINISHED_AMBIGUOUS_FIRST = (
    '<bbm-unit id="c">${卡罗尔} waved.</bbm-unit>'
    '<bbm-unit id="b">${鲍勃} BAD first</bbm-unit>'
    '<bbm-unit id="a" id="b"'
)
REPAIRED = (
    "<bbm-response>"
    '<bbm-unit id="a">${爱丽丝} smiled.</bbm-unit>'
    '<bbm-unit id="b">${鲍勃} arrived.</bbm-unit>'
    "</bbm-response>"
)
AMBIGUOUS_SOURCES = ["爱丽丝笑了。", "鲍勃到了。", "卡罗尔挥手。"]
AMBIGUOUS_EXPECTED = [
    "${爱丽丝} smiled.",
    "${鲍勃} arrived.",
    "${卡罗尔} waved.",
]


def _run_ambiguous_repair(tmp_path, first_response):
    model = AdapterModel([(first_response, "stop"), (REPAIRED, "stop")])
    workflow = _workflow(tmp_path, model)
    snapshots = []

    def persist():
        snapshots.append(
            {
                "sends": len(model.sends),
                "journal": workflow.recovery_entries(),
                "catalogue": {
                    label: row["count"]
                    for label, row in workflow.catalogue.items()
                },
                "session": json.dumps(
                    model.session.messages(), ensure_ascii=False
                ),
            }
        )

    result = workflow.translate(
        AMBIGUOUS_SOURCES,
        lambda payload, feedback="": model.translate_marker_units(payload, feedback),
        workflow.validate_quality,
        identities=["a", "b", "c"],
        persist=persist,
    )
    return model, workflow, result, snapshots


def _assert_rejected_candidates_stayed_out(model, workflow, snapshots):
    assert "BAD" not in snapshots[0]["session"]
    assert all("BAD" not in entry["master"] for entry in workflow.recovery_entries())
    assert all("BAD" not in label for label in workflow.catalogue)
    session = json.dumps(model.session.messages(), ensure_ascii=False)
    assert "BAD" not in session


def test_strict_nested_duplicate_commits_only_the_safe_sibling_then_repairs(
    tmp_path,
):
    model, workflow, result, snapshots = _run_ambiguous_repair(
        tmp_path, STRICT_AMBIGUOUS_FIRST
    )
    assert result == AMBIGUOUS_EXPECTED
    assert _adapter_ids(model.sends[0]) == ["a", "b", "c"]
    assert _adapter_ids(model.sends[1]) == ["a", "b"]
    assert len(snapshots) == 2
    assert snapshots[0]["sends"] == 1
    assert [entry["identity"] for entry in snapshots[0]["journal"]] == ["c"]
    assert snapshots[0]["journal"][0]["master"] == AMBIGUOUS_EXPECTED[2]
    assert snapshots[0]["catalogue"] == {"卡罗尔": 1}
    assert "爱丽丝" not in snapshots[0]["session"]
    assert "鲍勃" not in snapshots[0]["session"]
    _assert_rejected_candidates_stayed_out(model, workflow, snapshots)
    assert set(workflow.catalogue) == {"爱丽丝", "鲍勃", "卡罗尔"}
    assert {row["count"] for row in workflow.catalogue.values()} == {1}
    assert {entry["identity"] for entry in workflow.recovery_entries()} == {
        "a",
        "b",
        "c",
    }


def test_unfinished_duplicate_attributes_repair_every_ambiguous_id(tmp_path):
    model, workflow, result, snapshots = _run_ambiguous_repair(
        tmp_path, UNFINISHED_AMBIGUOUS_FIRST
    )
    assert result == AMBIGUOUS_EXPECTED
    assert _adapter_ids(model.sends[0]) == ["a", "b", "c"]
    assert _adapter_ids(model.sends[1]) == ["a", "b"]
    assert len(snapshots) == 2
    assert snapshots[0]["sends"] == 1
    assert [entry["identity"] for entry in snapshots[0]["journal"]] == ["c"]
    assert snapshots[0]["catalogue"] == {"卡罗尔": 1}
    assert "爱丽丝" not in snapshots[0]["session"]
    assert "鲍勃" not in snapshots[0]["session"]
    _assert_rejected_candidates_stayed_out(model, workflow, snapshots)
    assert set(workflow.catalogue) == {"爱丽丝", "鲍勃", "卡罗尔"}
    assert {row["count"] for row in workflow.catalogue.values()} == {1}
    assert {entry["identity"] for entry in workflow.recovery_entries()} == {
        "a",
        "b",
        "c",
    }

