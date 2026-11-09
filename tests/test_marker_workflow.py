import json
import zipfile
from pathlib import Path

import pytest
from ebooklib import epub
from lxml import etree

from book_maker.marker_bundle import (
    BundleError,
    LocalizationBindings,
    apply_catalogue_update,
    capture_token_destinations,
    export_text_bundle,
    finalize_bundle,
    generate_css,
    read_state,
    regenerate_localization_destinations,
    verify_bundle,
    write_bundle,
)
from book_maker.marker_names import marker_key
from book_maker.marker_workflow import MarkerValidationError, MarkerWorkflow
from book_maker.session_context import SessionHistory
from book_maker.translator.base_translator import Base


class Model:
    language = "en"
    source_language = "zh"
    model = "offline"
    max_chunk_attempts = 2

    def __init__(self):
        self.session = SessionHistory()
        self._protected_last_request = None
        self._protected_response_metadata = None

    def _record_session_exchange(self, user, reply):
        self.session.append(user, reply)

    def marker_mapping_request(self, prompt):
        return "{}"


def _book(tmp_path, body="<p>${御坂美琴} smiled.</p>"):
    book = epub.EpubBook()
    book.set_identifier("marker-bundle-test")
    book.set_title("测试")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="第一章", file_name="chapter.xhtml", lang="zh")
    chapter.content = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第一章</title>'
        f"</head><body><h1>第一章</h1>{body}</body></html>"
    )
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    book.toc = (epub.Link("chapter.xhtml", "第一章", "opening"),)
    source = tmp_path / "source.epub"
    epub.write_epub(str(source), book)
    return source


def _state(tmp_path, source, target="Mikoto", master="${御坂美琴} smiled."):
    model = Model()
    workflow = MarkerWorkflow(
        model,
        str(source),
        settings={
            "request_max_units": 40,
            "request_max_tokens": 4000,
            "run_fingerprint": "fingerprint",
        },
    )
    workflow.catalogue["御坂美琴"] = {
        "key": marker_key("御坂美琴"),
        "label": "御坂美琴",
        "count": 1,
        "paragraphs": [1],
        "references": [{"job_id": "job-1"}],
        "contexts": ["御坂美琴笑了。"],
        "target": target,
        "mapping_status": "mapped" if target else "pending",
    }
    return workflow.snapshot(
        job_ids=["job-1"],
        translations=[master],
        jobs=(),
        localized_cache={},
        status="complete",
        settings={
            "request_max_units": 40,
            "request_max_tokens": 4000,
            "run_fingerprint": "fingerprint",
        },
        localization={"single": True, "values": []},
    )


def test_bundle_renders_spans_shared_css_and_fallback(tmp_path):
    source = _book(tmp_path, "<p>${御坂美琴} smiled.</p><p>${美琴} left.</p>")
    state = _state(tmp_path, source)
    state["catalogue"]["美琴"] = {
        "key": marker_key("美琴"),
        "label": "美琴",
        "count": 1,
        "paragraphs": [2],
        "references": [],
        "contexts": ["美琴走了。"],
        "target": None,
        "mapping_status": "pending",
    }
    output = tmp_path / "out.epub"
    finalize_bundle(source, output, state)
    verify_bundle(output, expected_state=state)
    with zipfile.ZipFile(output) as archive:
        document = archive.read("EPUB/chapter.xhtml").decode()
        css = archive.read("EPUB/bbm-names.css").decode()
        opf = archive.read("EPUB/content.opf").decode()
    assert 'class="bbm-name"' in document
    assert 'class="bbm-original"' in document
    assert "御坂美琴" in document and "美琴" in document
    assert 'rel="stylesheet"' in document and "bbm-names.css" in document
    assert 'content: "Mikoto"' in css
    # An unmapped key gets no hiding rule, so its original stays visible.
    assert marker_key("美琴") not in css
    assert "bbm-names.css" in opf


def test_css_escaping_handles_quotes_backslashes_and_controls():
    state = {
        "catalogue": {
            "name": {
                "key": marker_key("name"),
                "target": 'A "name" \\ with\ncontrol',
            }
        }
    }
    css = generate_css(state["catalogue"]).decode()
    assert '\\"name\\"' in css
    assert "\\\\" in css
    assert "\\a " in css


def test_apply_catalogue_edits_targets_only_and_preserves_state(tmp_path):
    source = _book(tmp_path)
    state = _state(tmp_path, source)
    output = tmp_path / "out.epub"
    finalize_bundle(source, output, state)
    before = read_state(output)
    update = {"catalogue": {"御坂美琴": {"target": "Mikoto Misaka"}}}
    after = apply_catalogue_update(output, update)
    assert after["catalogue"]["御坂美琴"]["target"] == "Mikoto Misaka"
    assert after["checkpoint"] == before["checkpoint"]
    with zipfile.ZipFile(output) as archive:
        css = archive.read("EPUB/bbm-names.css").decode()
    assert "Mikoto Misaka" in css
    with pytest.raises(BundleError):
        apply_catalogue_update(
            output, {"catalogue": {"御坂美琴": {"count": 99}}}
        )


def test_apply_catalogue_regenerates_navigation_and_opf_metadata(tmp_path):
    source = _two_name_book(tmp_path)
    state, bindings = _two_name_state(tmp_path, source)
    output = tmp_path / "localized-out.epub"
    finalize_bundle(source, output, state, bindings=bindings)
    before = read_state(output)["localization"]
    assert before["version"] == 3
    assert before["destinations"]

    apply_catalogue_update(
        output, {"catalogue": {"Alice": {"target": "Alicia"}}}
    )

    nav, ncx, opf, chapter = _navigation_text(output)
    assert _nav_anchor_labels(output) == ["Alicia", "Bob"]
    assert "Alicia" in nav and "Alicia" in ncx and "Alicia" in opf
    assert "<title>Alicia</title>" in chapter
    assert read_state(output)["localization"] == before


def test_export_text_makes_ordinary_epub_without_css_or_markers(tmp_path):
    source = _book(tmp_path)
    state = _state(tmp_path, source)
    css_bundle = tmp_path / "css.epub"
    finalize_bundle(source, css_bundle, state)
    plain = tmp_path / "plain.epub"
    export_text_bundle(css_bundle, plain)
    verify_bundle(plain)
    with zipfile.ZipFile(plain) as archive:
        names = archive.namelist()
        document = archive.read("EPUB/chapter.xhtml").decode()
    assert "bbm-name" not in document
    assert "Mikoto smiled." in document
    assert "bbm-names.css" not in names
    assert read_state(plain)["catalogue"]["御坂美琴"]["target"] == "Mikoto"


def test_missing_state_is_refused(tmp_path):
    source = _book(tmp_path)
    with pytest.raises(BundleError):
        read_state(source, validate=True)


# ----------------------------------------------------------- workflow tests


class SendModel(Model):
    def __init__(self):
        super().__init__()
        self.mapping_requests = []

    def marker_mapping_request(self, prompt):
        self.mapping_requests.append(prompt)
        payload = json.loads(prompt.rsplit("\n\n", 1)[-1])
        return json.dumps(
            {
                "mappings": [
                    {
                        "key": row["key"],
                        "label": row["label"],
                        "target": f"Target {row['key'][:8]}",
                    }
                    for row in payload
                ]
            },
            ensure_ascii=False,
        )


def _workflow(tmp_path, model=None):
    source = tmp_path / "source.epub"
    source.write_bytes(b"stable")
    model = model or SendModel()
    return MarkerWorkflow(
        model,
        str(source),
        settings={
            "request_max_units": 2,
            "request_max_tokens": 4000,
            "run_fingerprint": "fp",
        },
    )


def test_quality_failure_rolls_back_catalogue_and_history(tmp_path):
    workflow = _workflow(tmp_path)

    def send(payload, feedback=""):
        identity = payload.split('id="')[1].split('"')[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            "${御坂美琴} smiled.</bbm-unit></bbm-response>"
        )

    def bad_quality(sources, translations):
        from book_maker.translation_quality import UntranslatedTextError

        raise UntranslatedTextError("surrounding prose was not translated")

    with pytest.raises(Exception):
        workflow.translate(["御坂美琴笑了。"], send, bad_quality)
    assert workflow.catalogue == {}
    assert workflow.model.session.messages() == []


def test_empty_paragraph_rolls_back_catalogue_and_history(tmp_path):
    workflow = _workflow(tmp_path)

    def send(payload, feedback=""):
        identity = payload.split('id="')[1].split('"')[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}"></bbm-unit></bbm-response>'
        )

    with pytest.raises(MarkerValidationError):
        workflow.translate(["御坂美琴笑了。"], send, lambda *args: None)
    assert workflow.catalogue == {}
    assert workflow.model.session.messages() == []


def test_session_commit_failure_does_not_commit_catalogue(tmp_path):
    class BrokenSession(Model):
        def _record_session_exchange(self, user, reply):
            raise RuntimeError("session store unavailable")

    workflow = _workflow(tmp_path, BrokenSession())

    def send(payload, feedback=""):
        identity = payload.split('id="')[1].split('"')[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            "${御坂美琴} smiled.</bbm-unit></bbm-response>"
        )

    with pytest.raises(MarkerValidationError):
        workflow.translate(["御坂美琴笑了。"], send, lambda *args: None)
    assert workflow.catalogue == {}
    assert workflow.model.session.messages() == []


class CompactingModel(SendModel):
    """Session recording through the real Base path, then compaction."""

    no_context_compact = False

    def __init__(self, summary):
        super().__init__()
        self.summary = summary
        self.compactions = 0

    def _session_budget(self):
        return 1

    def _record_session_exchange(self, user, reply):
        Base._record_session_exchange(self, user, reply)

    def _compact_session(self):
        self._marker_state.validate_compaction(self.summary)
        self.compactions += 1
        self._marker_state.note_compaction(self.summary, self.session.windows)
        self.session.reset(seed=self.summary)


def test_new_marker_is_registered_before_compaction_validates(tmp_path):
    model = CompactingModel("Summary with ${御坂美琴} and no English target.")
    workflow = _workflow(tmp_path, model)
    workflow.state["run"]["effective_settings"]["request_max_units"] = 40

    def send(payload, feedback=""):
        identity = payload.split('id="')[1].split('"')[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            "${御坂美琴} smiled.</bbm-unit></bbm-response>"
        )

    assert workflow.translate(["御坂美琴笑了。"], send, lambda *args: None) == [
        "${御坂美琴} smiled."
    ]
    assert model.compactions == 1
    entry = workflow.catalogue["御坂美琴"]
    assert entry["target"] is None
    assert entry["count"] == 1
    assert model.mapping_requests == []
    assert workflow.state["session"]["last_summary"] == model.summary


def test_failed_compaction_validation_rolls_back_catalogue_and_session(tmp_path):
    model = CompactingModel("Summary with ${未知}.")
    workflow = _workflow(tmp_path, model)

    def send(payload, feedback=""):
        identity = payload.split('id="')[1].split('"')[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            "${御坂美琴} smiled.</bbm-unit></bbm-response>"
        )

    with pytest.raises(MarkerValidationError):
        workflow.translate(["御坂美琴笑了。"], send, lambda *args: None)
    assert workflow.catalogue == {}
    assert workflow.model.session.messages() == []
    assert workflow.model.session.estimated_tokens() == 0
    assert workflow.state["session"]["last_summary"] == ""


def test_optional_compaction_failure_keeps_the_accepted_chunk(tmp_path):
    class FailedCompaction(SendModel):
        def _record_session_exchange(self, user, reply):
            self.session.append(user, reply)
            # The translator's compaction path catches its own validation
            # failure and opens an empty window; the body chunk is accepted.
            self.session.reset(seed="")

    model = FailedCompaction()
    workflow = _workflow(tmp_path, model)

    def send(payload, feedback=""):
        identity = payload.split('id="')[1].split('"')[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            "${御坂美琴} smiled.</bbm-unit></bbm-response>"
        )

    assert workflow.translate(["御坂美琴笑了。"], send, lambda *args: None) == [
        "${御坂美琴} smiled."
    ]
    assert workflow.catalogue["御坂美琴"]["count"] == 1
    assert model.session.messages() == []


def test_commit_failure_retries_the_local_transaction_without_double_counts(tmp_path):
    class FailOnce(SendModel):
        def __init__(self):
            super().__init__()
            self.fail = True

        def _record_session_exchange(self, user, reply):
            self.session.append(user, reply)
            if self.fail:
                self.fail = False
                raise RuntimeError("transient session failure")

    model = FailOnce()
    workflow = _workflow(tmp_path, model)

    calls = []

    def send(payload, feedback=""):
        calls.append(payload)
        identity = payload.split('id="')[1].split('"')[0]
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            "${御坂美琴} smiled.</bbm-unit></bbm-response>"
        )

    assert workflow.translate(["御坂美琴笑了。"], send, lambda *args: None) == [
        "${御坂美琴} smiled."
    ]
    assert len(calls) == 1
    assert workflow.catalogue["御坂美琴"]["count"] == 1


def test_expanded_label_is_repaired_in_a_grouped_followup_request(tmp_path):
    workflow = _workflow(tmp_path)
    calls = []

    def send(payload, feedback=""):
        calls.append((payload, feedback))
        identity = payload.split('id="')[1].split('"')[0]
        label = "白井黑子" if len(calls) == 1 else "黑子"
        return (
            f'<bbm-response><bbm-unit id="{identity}">'
            f"${{{label}}} smiled.</bbm-unit></bbm-response>"
        )

    assert workflow.translate(["黑子笑了。"], send, lambda *args: None) == [
        "${黑子} smiled."
    ]
    assert calls[1][1] == ""
    assert "白井黑子" in next(f["reason"] for f in workflow.state["diagnostics"]["failures"] if f["stage"] == "paragraph-response")
    assert "expected 0" in next(f["reason"] for f in workflow.state["diagnostics"]["failures"] if f["stage"] == "paragraph-response")
    assert workflow.catalogue["黑子"]["count"] == 1
    assert "白井黑子" not in workflow.catalogue
    assert len(calls) == 2


def test_mapping_requests_are_capped_and_never_enter_history(tmp_path):
    model = SendModel()
    workflow = _workflow(tmp_path, model)
    workflow.state["run"]["effective_settings"]["request_max_units"] = 40
    for index in range(45):
        label = f"名{index}"
        workflow.catalogue[label] = {
            "key": marker_key(label),
            "label": label,
            "count": 1,
            "paragraphs": [1],
            "references": [],
            "contexts": [],
            "target": None,
            "mapping_status": "pending",
        }
    assert workflow.process_mappings() == 45
    assert len(model.mapping_requests) == 2
    first = model.mapping_requests[0].rsplit("\n\n", 1)[-1]
    second = model.mapping_requests[1].rsplit("\n\n", 1)[-1]
    assert len(json.loads(first)) == 40
    assert len(json.loads(second)) == 5
    assert model.session.messages() == []
    assert all(entry["target"] for entry in workflow.catalogue.values())


def test_failed_mapping_batch_waits_for_more_labels(tmp_path):
    class Broken(SendModel):
        def marker_mapping_request(self, prompt):
            self.mapping_requests.append(prompt)
            return "not json"

    model = Broken()
    workflow = _workflow(tmp_path, model)
    for label in ("甲", "乙"):
        workflow.catalogue[label] = {
            "key": marker_key(label),
            "label": label,
            "count": 1,
            "paragraphs": [1],
            "references": [],
            "contexts": [],
            "target": None,
            "mapping_status": "pending",
        }
    assert workflow._maybe_request_mappings() == 0
    assert len(model.mapping_requests) == 2  # bounded attempts, not a storm
    assert workflow._maybe_request_mappings() == 0
    assert len(model.mapping_requests) == 2
    for label in ("丙", "丁"):
        workflow.catalogue[label] = {
            "key": marker_key(label),
            "label": label,
            "count": 1,
            "paragraphs": [1],
            "references": [],
            "contexts": [],
            "target": None,
            "mapping_status": "pending",
        }
    workflow._maybe_request_mappings()
    assert len(model.mapping_requests) > 2


def test_session_snapshot_round_trip(tmp_path):
    workflow = _workflow(tmp_path)
    workflow.model.session.append("source", "translated")
    workflow.state["session"]["last_summary"] = "summary"
    snapshot = workflow.snapshot(
        job_ids=[],
        translations=[],
        status="partial",
        settings={"request_max_units": 2, "request_max_tokens": 4000, "run_fingerprint": "fp"},
    )
    restored = MarkerWorkflow(
        SendModel(),
        workflow.book_path,
        state=json.loads(json.dumps(snapshot)),
        settings={},
    )
    restored.restore_session()
    assert restored.model.session.messages() == [
        {"role": "user", "content": "source"},
        {"role": "assistant", "content": "translated"},
    ]


def test_snapshot_preserves_context_repair_fields(tmp_path):
    workflow = _workflow(tmp_path)
    workflow.model.session.append("source", "translated")
    workflow.note_compaction(
        "The story continues.", workflow.model.session.windows
    )
    workflow.mark_context_repair("seeded pending flag")
    snapshot = json.loads(
        json.dumps(
            workflow.snapshot(
                job_ids=[],
                translations=[],
                status="partial",
                settings={
                    "request_max_units": 2,
                    "request_max_tokens": 4000,
                    "run_fingerprint": "fp",
                },
            )
        )
    )
    assert snapshot["session"]["context_repair"]["pending"] is True
    assert snapshot["session"]["context_validation"]["version"] == 1
    assert snapshot["session"]["last_summary"] == "The story continues."
    restored = MarkerWorkflow(
        SendModel(),
        workflow.book_path,
        state=snapshot,
        settings={},
    )
    assert restored.context_repair_pending() is True


# ------------------------------------------------- marker context recovery


PROVIDER_BOILERPLATE = (
    "I'm having a hard time fulfilling your request. "
    "Can I help you with something else instead?"
)
OTHER_PROVIDER_BOILERPLATE = (
    "Sorry, something went wrong. Please try your request again."
)


def _compact_runner(workflow, script):
    """A ChatGPTAPI-shaped stub bound to the real marker compaction."""
    from types import SimpleNamespace

    calls = []
    backoffs = []

    def _compact_request(messages):
        calls.append([message["content"] for message in messages])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=script.pop(0), refusal=None
                    ),
                    finish_reason="stop",
                )
            ],
            model="offline",
        )

    stub = SimpleNamespace(
        _marker_state=workflow,
        session=workflow.model.session,
        max_chunk_attempts=5,
        model="offline",
        _protected_response_metadata=None,
        _compact_request=_compact_request,
        _note_usage=lambda completion: None,
        _compaction_backoff=lambda number: backoffs.append(number),
    )
    return stub, calls, backoffs


def test_marker_compaction_sees_only_translations_and_prior_summary(tmp_path):
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI

    workflow = _workflow(tmp_path)
    workflow.model.session.reset(seed="Previous accepted summary.")
    workflow.model.session.append(
        "SOURCE_ONLY 第七迷雾商城。 TRANSLATION_RULES_ONLY",
        "<bbm-response>Seventh Mist Mall.</bbm-response>",
    )
    stub, calls, _ = _compact_runner(workflow, ["They visited Seventh Mist Mall."])
    ChatGPTAPI._compact_marker_session(stub, 8192)
    assert calls[0][:-1] == [
        "Previous accepted summary.",
        "<bbm-response>Seventh Mist Mall.</bbm-response>",
    ]
    assert "第七迷雾商城" not in "\n".join(calls[0])
    assert "TRANSLATION_RULES_ONLY" not in "\n".join(calls[0])
    assert "DO NOT create new markers" in calls[0][-1]
    assert workflow.model.session.messages()[0]["content"] == "They visited Seventh Mist Mall."


def test_marker_compaction_without_seed_excludes_source(tmp_path):
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI

    workflow = _workflow(tmp_path)
    workflow.model.session.append("ORIGINAL_SOURCE_ONLY", "Accepted English.")
    stub, calls, _ = _compact_runner(workflow, ["Accepted summary."])
    ChatGPTAPI._compact_marker_session(stub, 8192)
    assert calls[0][:-1] == ["Accepted English."]


def test_marker_compaction_retries_provider_error_then_takes_valid_summary(
    tmp_path,
):
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI

    workflow = _workflow(tmp_path)
    script = [PROVIDER_BOILERPLATE, "Summary of accepted events."]
    stub, calls, backoffs = _compact_runner(workflow, script)

    ChatGPTAPI._compact_marker_session(stub, 8192)

    # Sanitized evidence: both attempts ask the same history; the unusable
    # reply is never appended as conversational context.
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert all(
        "hard time" not in content
        for request in calls
        for content in request
    )
    # Only the validated summary became the seed.
    messages = workflow.model.session.messages()
    assert len(messages) == 1
    assert messages[0]["content"] == "Summary of accepted events."
    events = workflow.state["diagnostics"]["compactions"]
    assert [(event["status"], event.get("attempt")) for event in events] == [
        ("failed", 1),
        ("ok", 2),
    ]
    assert events[-1]["category"] == "content"
    assert workflow.state["session"]["context_validation"]["version"] == 1
    assert workflow.context_repair_pending() is False
    assert backoffs == [1]


def test_marker_compaction_exhaustion_marks_repair_pending(tmp_path):
    import copy
    from hashlib import sha256
    from book_maker.marker_workflow import ContextRepairPending
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI

    workflow = _workflow(tmp_path)
    workflow.state["session"]["last_summary"] = "Old accepted summary."
    source = "御坂美琴笑了。"
    workflow._put_recovery(
        {
            "identity": "epub:0:p1:digest",
            "index": 1,
            "source": source,
            "source_sha256": sha256(source.encode("utf-8")).hexdigest(),
            "master": "${御坂美琴} smiled.",
            "request_sha256": "r",
            "kind": "body",
        }
    )

    script = [PROVIDER_BOILERPLATE] * 5
    stub, calls, backoffs = _compact_runner(workflow, script)
    with pytest.raises(ContextRepairPending):
        ChatGPTAPI._compact_marker_session(stub, 8192)
    assert len(calls) == 5
    assert calls.count(calls[0]) == 5
    assert backoffs == [1, 2, 3, 4]
    saved = workflow.state["session"]
    assert saved["seed"] == ""
    # The last independently valid summary survives.
    assert saved["last_summary"] == "Old accepted summary."
    assert saved["context_repair"]["pending"] is True
    assert saved["context_repair"]["attempts"] == 5
    events = workflow.state["diagnostics"]["compactions"]
    assert all(event["status"] == "failed" for event in events)
    assert events[-1]["attempt"] == 5
    assert events[-1]["category"] == "provider-error"
    # Accepted journal rows survive an exhausted compaction.
    assert len(workflow.state["paragraph_recovery"]["entries"]) == 1

    # A reload cannot treat the blank active seed as a successful repair.
    state = copy.deepcopy(workflow.state)
    reloaded = MarkerWorkflow(
        SendModel(),
        workflow.book_path,
        state=state,
        settings={
            "request_max_units": 2,
            "request_max_tokens": 4000,
            "run_fingerprint": "fp",
        },
    )
    reloaded.restore_session()
    with pytest.raises(Exception):
        reloaded.translate(
            ["新的一段。"],
            lambda payload, feedback="": pytest.fail("must not send"),
            lambda *args: None,
        )


# ------------------------------------------- marker request reinforcement

_REMINDER_TAIL = (
    "Natural pronouns and omitted repeated names are allowed; markers are "
    "NOT required for every source occurrence."
)


def _reminder_blocks(content):
    """The exact reminder texts, one per ``MARKER RULES:`` heading."""
    blocks = []
    index = content.index("MARKER RULES:")
    while True:
        end = content.index(_REMINDER_TAIL, index) + len(_REMINDER_TAIL)
        blocks.append(content[index:end])
        try:
            index = content.index("MARKER RULES:", end)
        except ValueError:
            return blocks


def test_marker_request_brackets_data_with_identical_reminders(tmp_path):
    from book_maker.marker_workflow import (
        assemble_marker_request,
        paragraph_request,
    )

    _ids, payload = paragraph_request(["御坂美琴 resumed the story."])
    initial = assemble_marker_request(payload, language="english")
    blocks = _reminder_blocks(initial)
    assert len(blocks) == 2
    assert blocks[0] == blocks[1]
    first, last = blocks
    # The positive-only example sits before source data and never carries an
    # output frame of its own. Avoid showing malformed marker syntax at all.
    assert initial.count("MARKER EXAMPLE —") == 1
    assert initial.index("MARKER EXAMPLE —") < initial.index("<bbm-request>")
    example = initial.split("MARKER EXAMPLE —", 1)[1]
    example = example[: example.index("<bbm-request>")]
    assert "<bbm-unit" not in example and "<bbm-request>" not in example
    assert "Translation: ${小明} raised his hand." in example
    assert "Translation: He raised his hand." in example
    assert "${his}" not in initial
    assert "${He}" not in initial
    assert "WRONG:" not in initial
    # The identical reminder is the final instruction, outside the XML,
    # directly after the closing request frame separated by one newline.
    final_start = initial.index("MARKER RULES:", initial.index("<bbm-request>"))
    assert initial[:final_start].endswith("</bbm-request>\n")
    assert initial.endswith(last + "\n")

    feedback = "Failures from the previous response:\n- id p1: bad marker"
    repaired = assemble_marker_request(
        payload, language="english", feedback=feedback
    )
    repaired_blocks = _reminder_blocks(repaired)
    assert len(repaired_blocks) == 2
    first_repaired, last_repaired = repaired_blocks
    assert first_repaired == last_repaired and first_repaired == first
    assert "This is a repair request for the supplied ids only" in repaired
    assert repaired.index("failures exactly") > repaired.index(
        "MARKER EXAMPLE —"
    )
    assert repaired.index("failures exactly") < repaired.index("<bbm-request>")
    assert feedback in repaired


def test_canonical_history_and_rebuild_evidence_use_the_shared_assembly(tmp_path):
    from book_maker.session_context import estimate_tokens
    from book_maker.marker_workflow import (
        assemble_marker_request,
        canonical_response,
        paragraph_request,
    )

    workflow = _workflow(tmp_path)
    _ids, payload = paragraph_request(["美琴 laughs."], ["epub:x:p1:d"])
    expected = assemble_marker_request(payload, language="english")
    verification = workflow.canonical_evidence_exchanges(
        [("epub:x:p1:d", "美琴 laughs.", "${美琴} laughed.")]
    )
    (user, assistant), = verification
    assert user == expected
    assert assistant == canonical_response(["epub:x:p1:d"], ["${美琴} laughed."])
    # The added reminder/example overhead is part of the message the
    # context-budget estimator counts.
    assert any("MARKER RULES:" in exchange[0] for exchange in verification)
    assert estimate_tokens(expected) > estimate_tokens(payload)


def _canonical_user(workflow):
    """The canonical user message `_commit_round` records for one source."""
    from book_maker.marker_workflow import assemble_marker_request, paragraph_request

    _ids, payload = paragraph_request(["美琴 laughs."])
    return assemble_marker_request(payload, language=workflow._language)


def test_canonical_commit_uses_the_shared_assembly(tmp_path):
    workflow = _workflow(tmp_path)

    def send(payload, feedback=""):
        root = etree.fromstring(payload.encode())
        unit = root[0]
        return (
            f'<bbm-response><bbm-unit id="{unit.get("id")}">'
            "${美琴} laughed.</bbm-unit></bbm-response>"
        )

    workflow.translate(
        ["美琴 laughs."], send, workflow.validate_quality
    )
    user, _assistant = workflow.model.session.messages()[-2:]
    assert user == {
        "role": "user",
        "content": _canonical_user(workflow),
    }
    # Accepted-only history: no repair furniture, exactly one reminder pair.
    assert user["content"].count("MARKER RULES:") == 2
    assert "Failures from" not in user["content"]


def test_non_english_targets_keep_generic_rules_without_english_example(tmp_path):
    from book_maker.marker_workflow import (
        assemble_marker_request,
        marker_example,
        marker_rules,
        paragraph_request,
    )

    zh_rules = marker_rules("chinese")
    assert "NEVER put translated words, including translated pronouns" in zh_rules
    assert "he, she, his, her, him, they, their" not in zh_rules
    assert "translated English pronouns" not in zh_rules
    assert marker_example("chinese") == ""
    _ids, payload = paragraph_request(["美琴在大厅里跑。"])
    content = assemble_marker_request(
        payload, language="chinese", feedback="repair feedback"
    )
    assert "MARKER EXAMPLE ONLY" not in content
    assert content.count("MARKER RULES:") == 2


def test_premarked_instructions_preserve_repeated_marker_occurrences():
    from book_maker.marker_workflow import marker_prompt, marker_rules

    for instructions in (
        marker_prompt(premarked=True),
        marker_rules("english", premarked=True),
    ):
        assert "Preserve the exact count of each" in instructions
        assert "copy every occurrence at its corresponding marked source span" in instructions
        assert "do not collapse repeated names into pronouns or omit them" in instructions


def test_repair_feedback_carries_targeted_corrections_for_right_paragraphs(
    tmp_path,
):
    from book_maker.marker_workflow import canonical_response, paragraph_request

    workflow = _workflow(tmp_path)
    sources = ["美琴 waved.", "小明 cried.", "美琴 waved."]
    ids = paragraph_request(sources)[0]
    sends = []

    def send(payload, feedback=""):
        sends.append((payload, feedback))
        if len(sends) == 1:
            root = etree.fromstring(payload.encode())
            texts = {}
            for node in root:
                index = int(node.get("id").rsplit("_", 1)[1])
                if index == 0:
                    texts[node.get("id")] = "${美琴} raised ${his} hand."
                elif index == 1:
                    texts[node.get("id")] = "${他的} hand was shaking."
                else:
                    texts[node.get("id")] = "${美琴} waved and ${美琴} left."
            return canonical_response(list(texts), [texts[k] for k in texts])
        # Repaired round: one corrected candidate per requested id.
        return canonical_response(
            ids[:3],
            [
                "${美琴} raised his hand.",
                "His hand was shaking hard.",
                "${美琴} waved; he left.",
            ],
        )

    result = workflow.translate(sources, send, workflow.validate_quality)
    assert result == [
        "${美琴} raised his hand.",
        "His hand was shaking hard.",
        "${美琴} waved; he left.",
    ]
    # Only the failed paragraphs were sent again, all inside one repair
    # request, with per-paragraph corrections after each failing line.
    assert len(sends) == 2
    repair_payload, repair_feedback = sends[1]
    assert etree.fromstring(repair_payload.encode()).xpath(
        "//bbm-unit/@id"
    ) == ids[:3]
    assert repair_feedback == ""
    repair_feedback = next(
        f["reason"] for f in workflow.state["diagnostics"]["failures"]
        if f["stage"] == "paragraph-response"
    )
    assert repair_feedback.startswith("Failures from the previous response:")
    id_1 = "id " + ids[0] + " "
    id_2 = "id " + ids[1] + " "
    id_3 = "id " + ids[2] + " "
    segments = repair_feedback.split("Read-only", 1)[0]
    line_1 = segments[segments.index(id_1):segments.index(id_2)]
    line_2 = segments[segments.index(id_2):segments.index(id_3)]
    line_3 = segments[segments.index(id_3):]
    assert 'the English pronoun "his"' in line_1
    assert "Write \"his\" as plain prose" in line_1
    assert "invented source labels are forbidden" in line_2
    assert "ordinary translated prose" in line_2
    assert "may appear in at most 1 marked occurrence(s)" in line_3
    assert "natural plain pronouns" in line_3
    # Corrected candidates were committed exactly once each.
    assert workflow.catalogue["美琴"]["count"] == 2
    assert "小明" not in workflow.catalogue
    assert len(workflow.model.session.messages()) == 2  # one accepted round


def test_excess_marker_feedback_preserves_unmarked_aliases_as_plain_prose():
    from book_maker.marker_workflow import _failure_corrections

    corrections = _failure_corrections(
        [
            {
                "kind": "excess",
                "label": "佐天泪子",
                "available": 1,
                "requested": 2,
            }
        ]
    )

    assert len(corrections) == 1
    assert "keep no more than 1 marked occurrence(s)" in corrections[0]
    assert "unmarked alias, nickname, or honorific" in corrections[0]
    assert "plain prose" in corrections[0]
    assert "do not replace it with this full-name marker" in corrections[0]


def test_pending_repair_blocks_translation_and_display_mappings(tmp_path):
    from book_maker.marker_workflow import ContextRepairPending

    workflow = _workflow(tmp_path)
    workflow.mark_context_repair("quarantine seeded by test")
    with pytest.raises(ContextRepairPending):
        workflow.translate(
            ["御坂美琴笑了。"],
            lambda payload, feedback="": pytest.fail("must not send"),
            lambda *args: None,
        )
    workflow.catalogue["丙"] = {
        "key": marker_key("丙"),
        "label": "丙",
        "count": 1,
        "paragraphs": [1],
        "references": [],
        "contexts": [],
        "target": None,
        "mapping_status": "pending",
    }
    with pytest.raises(ContextRepairPending):
        workflow.process_mappings(force=True)
    assert workflow.model.mapping_requests == []


def test_resume_quarantine_replaces_poisoned_saved_context(tmp_path):
    wrapped = (
        "You are continuing a translation already in progress.\n\n"
        + OTHER_PROVIDER_BOILERPLATE
    )
    workflow = _workflow(tmp_path)
    workflow.state["session"]["seed"] = PROVIDER_BOILERPLATE
    workflow.state["session"]["last_summary"] = wrapped
    workflow.state["session"]["exchanges"] = [
        {"user": "source", "assistant": "translated"}
    ]

    workflow.restore_session()

    saved = workflow.state["session"]
    assert saved["seed"] == ""
    assert saved["last_summary"] == ""
    assert saved["context_repair"]["pending"] is True
    reason = saved["context_repair"]["reason"]
    assert "seed" in reason and "last_summary" in reason
    failures = workflow.state["diagnostics"]["failures"]
    assert any(
        failure["stage"] == "context-restore"
        and "seed" in failure["reason"]
        for failure in failures
    )
    # Existing accepted-only exchanges are pure reuse and still load.
    assert workflow.model.session.messages() == [
        {"role": "user", "content": "source"},
        {"role": "assistant", "content": "translated"},
    ]


def test_provider_error_reply_counts_once_and_history_stays_accepted_only(
    tmp_path,
):
    workflow = _workflow(tmp_path)
    workflow.model.max_chunk_attempts = 5
    sources = ["御坂美琴笑了。", "白井黑子笑了。"]
    labels = {
        "御坂美琴笑了。": "御坂美琴",
        "白井黑子笑了。": "白井黑子",
    }
    replies = iter([PROVIDER_BOILERPLATE, OTHER_PROVIDER_BOILERPLATE, None])
    sends = []

    def send(payload, feedback=""):
        root = etree.fromstring(payload.encode())
        identities = [node.get("id") for node in root]
        sends.append(identities)
        reply = next(replies)
        if reply is None:
            parts = [
                '<bbm-unit id="%s">${%s} smiled.</bbm-unit>'
                % (node.get("id"), labels[node.text or ""])
                for node in root
            ]
            return "<bbm-response>" + "".join(parts) + "</bbm-response>"
        return reply

    workflow.translate(sources, send, workflow.validate_quality)

    # Each unusable reply is charged once per outstanding ID; the grouped
    # repair then accepts both units in a single third send.
    assert len(sends) == 3
    assert len(sends[0]) == 2 and len(sends[1]) == 2 and len(sends[2]) == 2
    failures = workflow.state["diagnostics"]["failures"]
    provider_errors = [
        failure
        for failure in failures
        if failure["stage"] == "paragraph-provider-error"
    ]
    assert len(provider_errors) == 2
    # The accepted-only history must not contain the unusable replies.
    dumped = json.dumps(
        workflow.model.session.messages(), ensure_ascii=False
    )
    assert "hard time" not in dumped and "something went wrong" not in dumped
    assert workflow.catalogue["御坂美琴"]["count"] == 1
    assert workflow.catalogue["白井黑子"]["count"] == 1


# --------------------------------------------------- immutable masters


def _two_name_book(tmp_path):
    book = epub.EpubBook()
    book.set_identifier("marker-localization-two-names")
    book.set_title("Alice")
    book.set_language("zh")
    one = epub.EpubHtml(title="Alice", file_name="one.xhtml", lang="zh")
    one.content = (
        "<html><head><title>Alice</title></head>"
        "<body><p>first chapter</p></body></html>"
    )
    two = epub.EpubHtml(title="Bob", file_name="two.xhtml", lang="zh")
    two.content = (
        "<html><head><title>Bob</title></head>"
        "<body><p>second chapter</p></body></html>"
    )
    book.add_item(one)
    book.add_item(two)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", one, two]
    book.toc = (
        epub.Link("one.xhtml", "Alice", "one"),
        epub.Link("two.xhtml", "Bob", "two"),
    )
    source = tmp_path / "two-names.epub"
    epub.write_epub(str(source), book)
    _insert_head_meta(source, "EPUB/one.xhtml", "npkr-title", "Alice")
    return source


def _insert_head_meta(path, resource, name, content):
    """Add a head meta after ebooklib has written its fixed title head."""
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(path, "w") as archive:
        for info, data in entries:
            if info.filename == resource:
                text = data.decode("utf-8")
                text = text.replace(
                    "</title>",
                    f'</title><meta name="{name}" content="{content}"/>',
                    1,
                )
                data = text.encode("utf-8")
            archive.writestr(info, data)


def _tokenize_epub(path, replacements):
    """Put opaque binding tokens into a test source at explicit addresses."""
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    for resource, changes in replacements.items():
        parsed = None
        doctype = None
        for info, data in entries:
            if info.filename == resource:
                parsed = etree.fromstring(data)
                doctype = getattr(parsed.getroottree().docinfo, "doctype", None)
                break
        assert parsed is not None, resource
        for xpath, attribute, token in changes:
            nodes = parsed.xpath(xpath)
            assert nodes, (resource, xpath)
            for node in nodes:
                if attribute is None:
                    node.text = token
                else:
                    node.set(attribute, token)
        data = etree.tostring(
            parsed.getroottree(),
            encoding="utf-8",
            xml_declaration=True,
            doctype=doctype,
        )
        entries = [
            (info, data if info.filename == resource else old)
            for info, old in entries
        ]
    with zipfile.ZipFile(path, "w") as archive:
        for info, data in entries:
            archive.writestr(info, data)


def _label_bindings(first, second, reverse=False):
    bindings = LocalizationBindings(single=True)
    order = (second, first) if reverse else (first, second)
    ids = {}
    for index, label in enumerate(order, 1):
        name = "first" if label == first else "second"
        resource = "one.xhtml" if name == "first" else "two.xhtml"
        ids[name] = bindings.add(
            f"toc:{resource}#:{label}", label, f"${{{label}}}", label
        )
    ids["dc"] = bindings.add(
        f"dc:title:0:{first}", first, f"${{{first}}}", first
    )
    bindings.nonce = "0123456789abcdef0123456789abcdef"
    return bindings, ids


def _tokenize_label_book(source, first, second, reverse=False):
    bindings, ids = _label_bindings(first, second, reverse=reverse)
    token = bindings.token
    _tokenize_epub(
        source,
        {
            "EPUB/one.xhtml": [
                ("//*[local-name()='title']", None, token(ids["first"])),
                (
                    "//*[local-name()='meta'][@name='npkr-title']",
                    "content",
                    token(ids["first"]),
                ),
            ],
            "EPUB/two.xhtml": [
                ("//*[local-name()='title']", None, token(ids["second"]))
            ],
            "EPUB/nav.xhtml": [
                (
                    "//*[local-name()='a'][@href='one.xhtml']",
                    None,
                    token(ids["first"]),
                ),
                (
                    "//*[local-name()='a'][@href='two.xhtml']",
                    None,
                    token(ids["second"]),
                ),
                (
                    "//*[local-name()='nav']/*[local-name()='h2'][1]",
                    None,
                    token(ids["dc"]),
                ),
            ],
            "EPUB/toc.ncx": [
                (
                    "//*[local-name()='docTitle']/*[local-name()='text']",
                    None,
                    token(ids["dc"]),
                ),
                (
                    "(//*[local-name()='navLabel']/*[local-name()='text'])[1]",
                    None,
                    token(ids["first"]),
                ),
                (
                    "(//*[local-name()='navLabel']/*[local-name()='text'])[2]",
                    None,
                    token(ids["second"]),
                ),
            ],
            "EPUB/content.opf": [
                ("//*[local-name()='title']", None, token(ids["dc"]))
            ],
        },
    )
    return bindings


def _add_catalogue(state, label, target):
    state["catalogue"][label] = {
        "key": marker_key(label),
        "label": label,
        "count": 1,
        "paragraphs": [1],
        "references": [],
        "contexts": [],
        "target": target,
        "mapping_status": "mapped" if target else "pending",
    }


def _two_name_state(tmp_path, source, reverse=False):
    state = _state(tmp_path, source, master="${御坂美琴} smiled.")
    _add_catalogue(state, "Alice", "Alice")
    _add_catalogue(state, "Bob", "Bob")
    state["localization"] = {
        "version": 3,
        "single": True,
        "values": [
            {"key": "toc:one.xhtml#:Alice", "source": "Alice", "master": "${Alice}"},
            {"key": "nav:Alice", "source": "Alice", "master": "${Alice}"},
            {
                "key": "meta:npkr-title:Alice",
                "source": "Alice",
                "master": "${Alice}",
            },
            {
                "key": "dc:title:0:Alice",
                "source": "Alice",
                "master": "${Alice}",
            },
            {"key": "toc:two.xhtml#:Bob", "source": "Bob", "master": "${Bob}"},
            {"key": "nav:Bob", "source": "Bob", "master": "${Bob}"},
        ],
    }
    bindings = _tokenize_label_book(source, "Alice", "Bob", reverse=reverse)
    return state, bindings


def _navigation_text(output):
    with zipfile.ZipFile(output) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode()
        ncx = archive.read("EPUB/toc.ncx").decode()
        opf = archive.read("EPUB/content.opf").decode()
        chapter = archive.read("EPUB/one.xhtml").decode()
    return nav, ncx, opf, chapter


def _nav_anchor_labels(output):
    with zipfile.ZipFile(output) as archive:
        root = etree.fromstring(archive.read("EPUB/nav.xhtml"))
    return [
        "".join(element.itertext())
        for element in root.iter()
        if isinstance(element.tag, str)
        and etree.QName(element).localname == "a"
    ]


def test_catalogue_edits_do_not_cascade_across_displayed_text(tmp_path):
    source = _two_name_book(tmp_path)
    state, bindings = _two_name_state(tmp_path, source)
    output = tmp_path / "out.epub"
    finalize_bundle(source, output, state, bindings=bindings)
    bundled = read_state(output)
    assert bundled["localization"]["version"] == 3
    assert bundled["localization"]["destinations"]

    apply_catalogue_update(
        output,
        {"catalogue": {"Alice": {"target": "Bob"}, "Bob": {"target": "Carol"}}},
    )
    nav, ncx, opf, chapter = _navigation_text(output)
    # The Alice destination becomes Bob exactly once; the Bob destination
    # becomes Carol.  Sequential replacement would turn both into Carol.
    assert _nav_anchor_labels(output) == ["Bob", "Carol"]
    assert "<title>Bob</title>" in chapter
    assert "Bob / Carol" not in nav
    assert "Carol / Carol" not in nav
    assert "Bob" in ncx and "Carol" in ncx
    assert "Bob" in opf and "Carol" not in opf

    # Applying the same catalogue again is semantically idempotent.
    apply_catalogue_update(
        output,
        {"catalogue": {"Alice": {"target": "Bob"}, "Bob": {"target": "Carol"}}},
    )
    again = _navigation_text(output)
    assert again == (nav, ncx, opf, chapter)


def test_reversed_binding_order_keeps_labels_distinct(tmp_path):
    source = _two_name_book(tmp_path)
    state, bindings = _two_name_state(tmp_path, source, reverse=True)
    output = tmp_path / "reversed.epub"
    finalize_bundle(source, output, state, bindings=bindings)
    apply_catalogue_update(
        output,
        {"catalogue": {"Alice": {"target": "Bob"}, "Bob": {"target": "Carol"}}},
    )
    nav, ncx, opf, chapter = _navigation_text(output)
    assert _nav_anchor_labels(output) == ["Bob", "Carol"]
    assert "<title>Bob</title>" in chapter
    assert "Bob" in ncx and "Carol" in ncx
    assert "Bob" in opf and "Carol" not in opf


def _overlap_book(tmp_path):
    book = epub.EpubBook()
    book.set_identifier("marker-localization-overlap")
    book.set_title("Alice")
    book.set_language("zh")
    one = epub.EpubHtml(title="Alice", file_name="one.xhtml", lang="zh")
    one.content = (
        "<html><head><title>Alice</title></head>"
        "<body><p>first chapter</p></body></html>"
    )
    two = epub.EpubHtml(title="Alicia", file_name="two.xhtml", lang="zh")
    two.content = (
        "<html><head><title>Alicia</title></head>"
        "<body><p>second chapter</p></body></html>"
    )
    book.add_item(one)
    book.add_item(two)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", one, two]
    book.toc = (
        epub.Link("one.xhtml", "Alice", "one"),
        epub.Link("two.xhtml", "Alicia", "two"),
    )
    source = tmp_path / "overlap.epub"
    epub.write_epub(str(source), book)
    return source


def _overlap_state(tmp_path, source):
    state = _state(tmp_path, source, master="${Alice} met ${Alicia}.")
    _add_catalogue(state, "Alice", "Alice")
    _add_catalogue(state, "Alicia", "Alicia")
    _insert_head_meta(source, "EPUB/one.xhtml", "npkr-title", "Alice")
    state["localization"] = {
        "version": 3,
        "single": True,
        "values": [
            {"key": "toc:one.xhtml#:Alice", "source": "Alice", "master": "${Alice}"},
            {"key": "nav:Alice", "source": "Alice", "master": "${Alice}"},
            {"key": "dc:title:0:Alice", "source": "Alice", "master": "${Alice}"},
            {
                "key": "toc:two.xhtml#:Alicia",
                "source": "Alicia",
                "master": "${Alicia}",
            },
            {"key": "nav:Alicia", "source": "Alicia", "master": "${Alicia}"},
        ],
    }
    bindings = _tokenize_label_book(source, "Alice", "Alicia")
    return state, bindings


def test_overlapping_names_swap_without_sequential_cascades(tmp_path):
    source = _overlap_book(tmp_path)
    state, bindings = _overlap_state(tmp_path, source)
    output = tmp_path / "overlap-out.epub"
    finalize_bundle(source, output, state, bindings=bindings)

    apply_catalogue_update(
        output,
        {
            "catalogue": {
                "Alice": {"target": "Alicia"},
                "Alicia": {"target": "Alice"},
            }
        },
    )
    nav, ncx, _opf, _chapter = _navigation_text(output)
    assert _nav_anchor_labels(output) == ["Alicia", "Alice"]
    assert "Alicia" in ncx and "Alice" in ncx
    assert "Alice / Alicia" not in nav
    assert "Alicia / Alice" not in nav


def test_same_document_fragments_keep_distinct_labels(tmp_path):
    book = epub.EpubBook()
    book.set_identifier("marker-localization-fragments")
    book.set_title("Alice")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="Alice", file_name="one.xhtml", lang="zh")
    chapter.content = (
        "<html><head><title>Alice</title></head><body>"
        '<h2 id="a">Alice</h2><p>first</p>'
        '<h2 id="b">Bob</h2><p>second</p>'
        "</body></html>"
    )
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    book.toc = (
        epub.Link("one.xhtml#a", "Alice", "a"),
        epub.Link("one.xhtml#b", "Bob", "b"),
    )
    source = tmp_path / "fragments.epub"
    epub.write_epub(str(source), book)

    state = _state(tmp_path, source, master="${Alice} met ${Bob}.")
    _add_catalogue(state, "Alice", "Alice")
    _add_catalogue(state, "Bob", "Bob")
    state["localization"] = {
        "version": 3,
        "single": True,
        "values": [
            {
                "key": "toc:one.xhtml#a:Alice",
                "source": "Alice",
                "master": "${Alice}",
            },
            {
                "key": "toc:one.xhtml#b:Bob",
                "source": "Bob",
                "master": "${Bob}",
            },
        ],
    }
    bindings = LocalizationBindings(single=True)
    first = bindings.add(
        "toc:one.xhtml#a:Alice", "Alice", "${Alice}", "Alice"
    )
    second = bindings.add("toc:one.xhtml#b:Bob", "Bob", "${Bob}", "Bob")
    bindings.nonce = "0123456789abcdef0123456789abcdef"
    token = bindings.token
    _tokenize_epub(
        source,
        {
            "EPUB/nav.xhtml": [
                ("//*[local-name()='a'][@href='one.xhtml#a']", None, token(first)),
                ("//*[local-name()='a'][@href='one.xhtml#b']", None, token(second)),
            ],
            "EPUB/toc.ncx": [
                ("(//*[local-name()='navLabel']/*[local-name()='text'])[1]", None, token(first)),
                ("(//*[local-name()='navLabel']/*[local-name()='text'])[2]", None, token(second)),
            ],
        },
    )

    output = tmp_path / "fragments-out.epub"
    finalize_bundle(source, output, state, bindings=bindings)
    apply_catalogue_update(
        output,
        {"catalogue": {"Alice": {"target": "Alicia"}, "Bob": {"target": "Bobby"}}},
    )
    assert _nav_anchor_labels(output) == ["Alicia", "Bobby"]
    _nav, ncx, _opf, _chapter = _navigation_text(output)
    assert "Alicia" in ncx and "Bobby" in ncx


def test_tail_slot_preserves_surrounding_prose_and_whitespace(tmp_path):
    root = etree.fromstring(
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>'
        '<span class="label">lead</span>placeholder<em>end</em>'
        "</p></body></html>"
    )
    unused = tmp_path / "unused.epub"
    unused.write_bytes(b"unused")
    state = _state(tmp_path, unused)
    _add_catalogue(state, "Alice", "Alice")
    state["localization"] = {
        "version": 3,
        "single": True,
        "values": [
            {
                "key": "nav:Alice",
                "source": "The Alice arrives",
                "master": "The ${Alice} arrives",
            }
        ],
    }
    bindings = LocalizationBindings(single=True)
    binding = bindings.add(
        "nav:Alice", "The Alice arrives", "The ${Alice} arrives", "The Alice arrives"
    )
    bindings.nonce = "0123456789abcdef0123456789abcdef"
    span = root.find(
        ".//{http://www.w3.org/1999/xhtml}span"
    )
    assert span is not None
    span.tail = f"  Before {bindings.token(binding)} after  "

    capture_token_destinations(state, {"EPUB/nav.xhtml": root}, bindings)
    record = state["localization"]["destinations"][0]
    assert record["destination"] == "tail"
    assert record["source"] == "  Before The Alice arrives after  "
    assert record["master"] == "  Before The ${Alice} arrives after  "

    state["catalogue"]["Alice"]["target"] = "Alicia"
    regenerate_localization_destinations(state, {"EPUB/nav.xhtml": root})
    assert span.tail == "  Before The Alicia arrives after  "


def test_repeated_dc_metadata_occurrences_get_their_own_bindings(tmp_path):
    book = epub.EpubBook()
    book.set_identifier("marker-dc-occurrences")
    book.set_title("Book")
    book.set_language("zh")
    book.add_metadata("DC", "subject", "Alice subject")
    book.add_metadata("DC", "subject", "Bob subject")
    chapter = epub.EpubHtml(title="Book", file_name="one.xhtml", lang="zh")
    chapter.content = "<html><body><p>chapter</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    book.toc = (epub.Link("one.xhtml", "Book", "one"),)
    source = tmp_path / "dc.epub"
    epub.write_epub(str(source), book)

    state = _state(tmp_path, source, master="${Alice} met ${Bob}.")
    _add_catalogue(state, "Alice", "Alice")
    _add_catalogue(state, "Bob", "Bob")
    state["localization"] = {
        "version": 3,
        "single": True,
        "values": [
            {
                "key": "dc:subject:0:Alice subject",
                "source": "Alice subject",
                "master": "${Alice} subject",
            },
            {
                "key": "dc:subject:1:Bob subject",
                "source": "Bob subject",
                "master": "${Bob} subject",
            },
        ],
    }
    bindings = LocalizationBindings(single=True)
    first = bindings.add(
        "dc:subject:0:Alice subject",
        "Alice subject",
        "${Alice} subject",
        "Alice subject",
    )
    second = bindings.add(
        "dc:subject:1:Bob subject",
        "Bob subject",
        "${Bob} subject",
        "Bob subject",
    )
    bindings.nonce = "0123456789abcdef0123456789abcdef"
    _tokenize_epub(
        source,
        {
            "EPUB/content.opf": [
                (
                    "(//*[local-name()='subject'])[1]",
                    None,
                    bindings.token(first),
                ),
                (
                    "(//*[local-name()='subject'])[2]",
                    None,
                    bindings.token(second),
                ),
            ]
        },
    )
    output = tmp_path / "dc-out.epub"
    finalize_bundle(source, output, state, bindings=bindings)
    apply_catalogue_update(
        output,
        {"catalogue": {"Alice": {"target": "Alicia"}, "Bob": {"target": "Bobby"}}},
    )
    with zipfile.ZipFile(output) as archive:
        opf = archive.read("EPUB/content.opf").decode()
    assert "Alicia subject" in opf
    assert "Bobby subject" in opf


def test_mixed_master_updates_text_without_dropping_wrappers(tmp_path):
    root = etree.fromstring(
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><nav>'
        '<h1><span class="label">A meeting with Alice</span></h1>'
        "</nav></body></html>"
    )
    unused = tmp_path / "unused.epub"
    unused.write_bytes(b"unused")
    state = _state(tmp_path, unused)
    _add_catalogue(state, "Alice", "Alice")
    state["localization"] = {
        "version": 3,
        "single": True,
        "values": [
            {
                "key": "nav:Alice",
                "source": "A meeting with Alice",
                "master": "A meeting with ${Alice}",
            }
        ],
    }
    bindings = LocalizationBindings(single=True)
    binding = bindings.add(
        "nav:Alice",
        "A meeting with Alice",
        "A meeting with ${Alice}",
        "A meeting with Alice",
    )
    bindings.nonce = "0123456789abcdef0123456789abcdef"
    span = root.find(
        ".//{http://www.w3.org/1999/xhtml}h1"
        "/{http://www.w3.org/1999/xhtml}span"
    )
    assert span is not None
    span.text = bindings.token(binding)

    records = capture_token_destinations(
        state, {"EPUB/nav.xhtml": root}, bindings
    )
    destinations = state["localization"]["destinations"]
    assert len(destinations) == 1
    assert destinations[0]["master"] == "A meeting with ${Alice}"
    assert destinations[0]["path"]

    state["catalogue"]["Alice"]["target"] = "Alicia"
    changed = regenerate_localization_destinations(
        state, {"EPUB/nav.xhtml": root}
    )
    assert changed == {"EPUB/nav.xhtml"}
    heading = root.find(
        ".//{http://www.w3.org/1999/xhtml}h1"
    )
    assert heading is not None
    assert heading.find(
        "{http://www.w3.org/1999/xhtml}span"
    ) is not None
    assert "".join(heading.itertext()) == "A meeting with Alicia"


def test_attribute_destination_is_captured_and_regenerated(tmp_path):
    source = _two_name_book(tmp_path)
    state, bindings = _two_name_state(tmp_path, source)
    output = tmp_path / "out.epub"
    finalize_bundle(source, output, state, bindings=bindings)

    bundled = read_state(output)
    attribute_records = [
        record
        for record in bundled["localization"]["destinations"]
        if record["destination"] == "attribute"
    ]
    assert attribute_records
    assert attribute_records[0]["attribute"] == "content"
    assert attribute_records[0]["element"].endswith("meta")

    apply_catalogue_update(
        output, {"catalogue": {"Alice": {"target": "Alicia"}}}
    )
    with zipfile.ZipFile(output) as archive:
        root = etree.fromstring(archive.read("EPUB/one.xhtml"))
    # Namespace-agnostic on purpose: when this test runs after any test that
    # instantiates EpubLoader, its deliberate process-wide writer patch (the
    # #173 fix) writes raw item bytes, so one.xhtml carries no XHTML
    # namespace. The assertion is about the meta element, not its ns.
    meta = next(
        element
        for element in root.iter()
        if isinstance(element.tag, str)
        and etree.QName(element).localname == "meta"
        and element.get("name") == "npkr-title"
    )
    assert meta.get("content") == "Alicia"


def test_unmapped_edit_restores_the_original_label(tmp_path):
    source = _two_name_book(tmp_path)
    state, bindings = _two_name_state(tmp_path, source)
    output = tmp_path / "out.epub"
    finalize_bundle(source, output, state, bindings=bindings)
    apply_catalogue_update(
        output, {"catalogue": {"Alice": {"target": "Bob"}}}
    )
    apply_catalogue_update(
        output, {"catalogue": {"Alice": {"target": None}}}
    )
    nav, ncx, opf, _chapter = _navigation_text(output)
    assert _nav_anchor_labels(output) == ["Alice", "Bob"]
    assert "Alice" in ncx
    assert "Alice" in opf


def test_destination_text_is_xml_escaped_and_unicode_safe(tmp_path):
    source = _two_name_book(tmp_path)
    state, bindings = _two_name_state(tmp_path, source)
    output = tmp_path / "out.epub"
    finalize_bundle(source, output, state, bindings=bindings)
    target = 'A & B < C "quoted" 御坂'
    apply_catalogue_update(
        output, {"catalogue": {"Alice": {"target": target}}}
    )
    with zipfile.ZipFile(output) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode()
        ncx = archive.read("EPUB/toc.ncx").decode()
        opf = archive.read("EPUB/content.opf").decode()
    for document in (nav, ncx, opf):
        assert "&amp;" in document
        assert "&lt; C" in document
    with zipfile.ZipFile(output) as archive:
        from lxml import etree

        nav_root = etree.fromstring(archive.read("EPUB/nav.xhtml"))
        assert any(
            "".join(element.itertext()) == target for element in nav_root.iter()
        )


def test_broken_destination_fails_before_replacing_the_bundle(tmp_path):
    source = _two_name_book(tmp_path)
    state, bindings = _two_name_state(tmp_path, source)
    output = tmp_path / "out.epub"
    finalize_bundle(source, output, state, bindings=bindings)
    broken = read_state(output)
    broken["localization"]["destinations"][0]["path"] = [999]
    write_bundle(output, output, broken)
    before = output.read_bytes()
    with pytest.raises(BundleError, match="no longer resolves"):
        apply_catalogue_update(
            output, {"catalogue": {"Alice": {"target": "Changed"}}}
        )
    assert output.read_bytes() == before


def test_unknown_token_fails_before_replacing_the_bundle(tmp_path):
    source = _two_name_book(tmp_path)
    state, bindings = _two_name_state(tmp_path, source)
    output = tmp_path / "out.epub"
    finalize_bundle(source, output, state, bindings=bindings)
    _tokenize_epub(
        output,
        {
            "EPUB/one.xhtml": [
                ("//*[local-name()='p'][1]", None, "BBMLOCdeadbeefX1Z")
            ]
        },
    )
    before = output.read_bytes()
    with pytest.raises(BundleError, match="token survived"):
        apply_catalogue_update(
            output, {"catalogue": {"Alice": {"target": "Changed"}}}
        )
    assert output.read_bytes() == before


def test_version_two_destinations_need_source_backed_regeneration(tmp_path):
    source = _two_name_book(tmp_path)
    state, bindings = _two_name_state(tmp_path, source)
    output = tmp_path / "out.epub"
    finalize_bundle(source, output, state, bindings=bindings)
    legacy = read_state(output)
    legacy["localization"]["version"] = 2
    write_bundle(output, output, legacy)
    before = output.read_bytes()
    with pytest.raises(BundleError, match="source-backed regeneration"):
        apply_catalogue_update(
            output, {"catalogue": {"Alice": {"target": "Changed"}}}
        )
    assert output.read_bytes() == before

    # An edit that provably leaves every saved navigational master alone is
    # still a CSS-only change and remains allowed.
    apply_catalogue_update(
        output, {"catalogue": {"御坂美琴": {"target": "Misaka"}}}
    )
    assert read_state(output)["catalogue"]["御坂美琴"]["target"] == "Misaka"


def test_body_spans_and_catalogue_identity_survive_offline_edits(tmp_path):
    source = _book(tmp_path)
    state = _state(tmp_path, source)
    state["localization"] = {"version": 2, "single": True, "values": []}
    output = tmp_path / "out.epub"
    finalize_bundle(source, output, state)
    source.unlink()
    before = read_state(output)["catalogue"]["御坂美琴"]
    apply_catalogue_update(
        output, {"catalogue": {"御坂美琴": {"target": "Misaka"}}}
    )
    after = read_state(output)["catalogue"]["御坂美琴"]
    assert after["key"] == before["key"]
    assert after["label"] == "御坂美琴"
    assert after["count"] == before["count"]
    with zipfile.ZipFile(output) as archive:
        chapter = archive.read("EPUB/chapter.xhtml").decode()
    assert ">御坂美琴</span>" in chapter
    assert marker_key("御坂美琴") in chapter


def test_older_bundle_css_only_edit_is_allowed(tmp_path):
    source = _book(tmp_path)
    state = _state(tmp_path, source)
    _add_catalogue(state, "Alice", "Alice")
    state["localization"] = {
        # Version 1: display strings only, no structural destinations.
        "single": True,
        "values": [
            {"key": "nav:Alice", "source": "Alice", "master": "${Alice}"}
        ],
    }
    output = tmp_path / "old.epub"
    finalize_bundle(source, output, state)
    assert read_state(output)["localization"].get("version") is None
    apply_catalogue_update(
        output, {"catalogue": {"御坂美琴": {"target": "Misaka"}}}
    )
    with zipfile.ZipFile(output) as archive:
        css = archive.read("EPUB/bbm-names.css").decode()
    assert "Misaka" in css


def test_older_bundle_navigation_edit_fails_cleanly(tmp_path):
    source = _book(tmp_path)
    state = _state(tmp_path, source)
    _add_catalogue(state, "Alice", "Alice")
    state["localization"] = {
        "single": True,
        "values": [
            {"key": "nav:Alice", "source": "Alice", "master": "${Alice}"}
        ],
    }
    output = tmp_path / "old.epub"
    finalize_bundle(source, output, state)
    before = output.read_bytes()
    with pytest.raises(BundleError, match="source-backed regeneration"):
        apply_catalogue_update(
            output, {"catalogue": {"Alice": {"target": "Changed"}}}
        )
    assert output.read_bytes() == before


