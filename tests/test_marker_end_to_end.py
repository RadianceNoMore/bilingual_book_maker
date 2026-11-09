"""End-to-end marker workflow with an offline fake provider.

The fake model answers the strict XML paragraph protocol with marked Chinese
prose, then supplies display mappings.  The test covers a fresh tiny run,
interruption, resume from ``ongoing``, final CSS rendering, a second fresh run,
and recovery cleanup.
"""

import json
import re
import zipfile
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from ebooklib import epub
from lxml import etree

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.marker_bundle import STATE_PATH, read_state
from book_maker.session_context import SessionHistory
from book_maker.translator.chatgptapi_translator import ChatGPTAPI


TRANSLATIONS = {
    "Alice met Bob.": "${Alice}遇见了${Bob}。",
    "Carol waved.": "${Carol}挥了挥手。",
    "Alice": "${Alice}到了。",
    "Bob": "${Bob}来了。",
    "Tiny marker book": "小标记书",
}


@pytest.fixture(autouse=True)
def _restore_ebooklib_reader():
    """The loader's fallback read patch is process-global; undo it here."""
    original = epub.EpubReader._load_spine
    try:
        yield
    finally:
        epub.EpubReader._load_spine = original


def _write_book(path):
    book = epub.EpubBook()
    book.set_identifier("marker-e2e")
    book.set_title("Tiny marker book")
    book.set_language("en")
    chapters = []
    for index, (file_name, paragraph) in enumerate(
        [("one.xhtml", "Alice met Bob."), ("two.xhtml", "Carol waved.")]
    ):
        item = epub.EpubHtml(
            title=["Alice", "Bob"][index], file_name=file_name, lang="en"
        )
        item.content = f"<html><body><p>{paragraph}</p></body></html>"
        book.add_item(item)
        chapters.append(item)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = tuple(
        epub.Link(item.file_name, item.title, f"chapter-{index}")
        for index, item in enumerate(chapters)
    )
    book.spine = [book.get_item_with_id("nav"), *chapters]
    epub.write_epub(str(path), book)


def _write_three_paragraph_book(path):
    book = epub.EpubBook()
    book.set_identifier("marker-attribution")
    book.set_title("Tiny marker book")
    book.set_language("en")
    item = epub.EpubHtml(title="One", file_name="one.xhtml", lang="en")
    item.content = (
        "<html><body>"
        "<p>Alice met Bob.</p>"
        "<p>Carol waved.</p>"
        "<p>Dave left.</p>"
        "</body></html>"
    )
    book.add_item(item)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", item]
    book.toc = (epub.Link("one.xhtml", "One", "one"),)
    epub.write_epub(str(path), book)


class FakeMarkerModel:
    fail_at_call = None
    calls = 0
    TRANSLATION_ERROR_MARKER = "[Translation unavailable]"
    default_translations = TRANSLATIONS
    default_mapping_targets = None

    def __init__(self, *args, **kwargs):
        self.language = kwargs.get("language") or "zh-hans"
        self.source_language = args[1] if len(args) > 1 else "en"
        self.model = "offline"
        self.max_chunk_attempts = 2
        self.session = SessionHistory()
        self._fatal_error_detected = False
        self._marker_state = None
        self._marker_pending_exchanges = None
        self.compactions = 0
        self.translations = dict(type(self).default_translations)
        self.mapping_targets = dict(type(self).default_mapping_targets or {})
        self.requests = []
        self.stage_calls = []
        self.extraction_summaries = []
        self.translation_contexts = []

    @classmethod
    def reset(cls, fail_at_call=None, mapping_targets=None, translations=None):
        cls.fail_at_call = fail_at_call
        cls.calls = 0
        cls.default_mapping_targets = mapping_targets or {}
        cls.default_translations = translations or TRANSLATIONS

    def _record_session_exchange(self, user, reply):
        self.session.append(user, reply)
        # Trigger the real compaction ordering on every accepted body chunk:
        # the summary may mention this chunk's brand-new marker, so it can only
        # validate if catalogue registration happened first.
        labels = re.findall(r"\$\{([^{}]+)\}", reply)
        mention = f"${{{labels[0]}}}" if labels else "no names"
        summary = f"Summary with {mention}."
        self._marker_state.validate_compaction(summary)
        self._marker_state.note_compaction(summary, self.session.windows)
        self.session.reset(seed=summary)
        self.compactions += 1

    def _translate_source(self, source):
        source = re.sub(r"\$\{([^{}]+)\}", r"\1", source)
        return self.translations[source]

    def standing_instructions(self):
        return "offline test system instructions"

    def extract_window_names(self, prompt):
        data = json.loads(prompt.rsplit("\n", 1)[-1])
        self.stage_calls.append(("extract", data["window_id"]))
        self.extraction_summaries.append(
            data["prior_original_language_summary"]
        )
        candidates = {
            label
            for value in self.translations.values()
            for label in re.findall(r"\$\{([^{}]+)\}", value)
        }
        rows = sorted(
            (label for label in candidates if any(label in source for source in data["source_units"])),
            key=lambda value: (-len(value), value),
        )
        return json.dumps(
            {"window_id": data["window_id"], "names": rows}, ensure_ascii=False
        )

    def summarize_source_window(self, prompt, *, max_tokens):
        data = json.loads(prompt.rsplit("\n", 1)[-1])
        window_id = next(
            (identity for kind, identity in reversed(self.stage_calls) if kind == "extract"),
            "unknown",
        )
        self.stage_calls.append(("summary", window_id))
        candidates = {
            label
            for value in self.translations.values()
            for label in re.findall(r"\$\{([^{}]+)\}", value)
        }
        source = " ".join(
            row["source"] for row in data["completed_current_window_source"]
        )
        for label in sorted(candidates, key=lambda value: (-len(value), value)):
            source = source.replace(label, f"${{{label}}}")
        prior = data.get("prior_original_language_summary") or ""
        return (prior + " " + source).strip() or "The source window contains no events."

    def translate_marker_units(self, payload, feedback="", *, premarked=False, context=None):
        type(self).calls += 1
        self.requests.append(payload)
        self.translation_contexts.append(context)
        if type(self).fail_at_call is not None and type(self).calls > type(self).fail_at_call:
            raise KeyboardInterrupt
        root = etree.fromstring(payload.encode())
        units = []
        for node in root:
            source = node.text or ""
            self.stage_calls.append(("translate", node.get("id")))
            units.append(
                f'<bbm-unit id="{node.get("id")}">'
                f"{self._translate_source(source)}</bbm-unit>"
            )
        return "<bbm-response>\n" + "\n".join(units) + "\n</bbm-response>"

    def marker_mapping_request(self, prompt):
        self.requests.append(prompt)
        requested = json.loads(prompt.rsplit("\n\n", 1)[-1])
        rows = [
            {
                "key": row["key"],
                "label": row["label"],
                "target": self.mapping_targets.get(
                    row["label"], {"Alice": "爱丽丝", "Bob": "鲍勃", "Carol": "卡萝尔", "Dave": "戴夫"}.get(row["label"], "目标")
                ),
            }
            for row in requested
        ]
        return json.dumps({"mappings": rows})


class TruncatingAdapterModel(FakeMarkerModel):
    """Loader plumbing plus the real ``ChatGPTAPI.translate_marker_units``.

    ``_protected_completion`` is scripted so a provider-truncated response can
    be exercised through the real adapter rather than a coordinator-only fake.
    """

    script = []
    sent_requests = []

    @classmethod
    def reset_script(cls, script):
        cls.script = list(script)
        cls.sent_requests = []
        cls.calls = 0

    def standing_instructions(self):
        return "system"

    def _protected_completion(self, messages, stage):
        mode = type(self).script.pop(0)
        content = messages[-1]["content"]
        start = content.rindex("<bbm-request>")
        end = content.index("</bbm-request>", start) + len("</bbm-request>")
        root = etree.fromstring(content[start:end].encode())
        units = [(node.get("id"), node.text or "") for node in root]
        type(self).sent_requests.append(units)
        if mode == "interrupt":
            raise KeyboardInterrupt
        parts = []
        for index, (identity, source) in enumerate(units):
            if mode == "truncate" and index == len(units) - 1 and len(units) > 1:
                parts.append(f'<bbm-unit id="{identity}">partial')
            else:
                parts.append(
                    f'<bbm-unit id="{identity}">{self._translate_source(source)}</bbm-unit>'
                )
        raw = "<bbm-response>" + "".join(parts)
        finish = "length" if mode == "truncate" else "stop"
        self._protected_last_response = raw
        self._protected_response_metadata = {"finish_reason": finish, "model": "fake"}
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=raw, refusal=None),
                    finish_reason=finish,
                )
            ]
        )

    translate_marker_units = ChatGPTAPI.translate_marker_units


PROVIDER_BOILERPLATE = (
    "I'm having a hard time fulfilling your request. "
    "Can I help you with something else instead?"
)
OTHER_PROVIDER_BOILERPLATE = (
    "Sorry, something went wrong. Please try your request again."
)

ATTRIBUTION_TRANSLATIONS = {
    "Alice met Bob.": "${Alice}遇见了${Bob}。",
    "Carol waved.": "${Carol}挥了挥手。",
    "Dave left.": "${Dave}离开了。",
}


class RepairAdapterModel(FakeMarkerModel):
    """Real loader plumbing plus the real ``rebuild_marker_context``.

    The rebuild turn is scripted independently from paragraph sends so the
    poisoned-resume path can be exercised through the production method.
    """

    script = []
    build_script = []
    received_compactions = []
    actions = []
    default_translations = ATTRIBUTION_TRANSLATIONS

    @classmethod
    def reset_script(cls, script):
        cls.script = list(script)
        cls.calls = 0

    @classmethod
    def reset_rebuild(cls, script):
        cls.build_script = list(script)
        cls.received_compactions = []
        cls.actions = []

    def standing_instructions(self):
        return "system"

    def _note_usage(self, completion):
        return None

    def _compact_request(self, messages):
        type(self).actions.append(("rebuild", [m["content"] for m in messages]))
        type(self).received_compactions.append(
            [message["content"] for message in messages]
        )
        content = type(self).build_script.pop(0)
        self._protected_last_request = {"messages": messages}
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content, refusal=None),
                    finish_reason="stop",
                )
            ],
            model="fake",
        )

    def _protected_completion(self, messages, stage):
        mode = type(self).script.pop(0)
        content = messages[-1]["content"]
        start = content.rindex("<bbm-request>")
        end = content.index("</bbm-request>", start) + len("</bbm-request>")
        root = etree.fromstring(content[start:end].encode())
        units = [(node.get("id"), node.text or "") for node in root]
        type(self).actions.append(("send", units))
        if mode == "interrupt":
            raise KeyboardInterrupt
        parts = [
            f'<bbm-unit id="{identity}">{self._translate_source(source)}</bbm-unit>'
            for identity, source in units
        ]
        raw = "<bbm-response>" + "".join(parts) + "</bbm-response>"
        self._protected_last_response = raw
        self._protected_response_metadata = {"finish_reason": "stop", "model": "fake"}
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=raw, refusal=None),
                    finish_reason="stop",
                )
            ]
        )

    translate_marker_units = ChatGPTAPI.translate_marker_units
    rebuild_marker_context = ChatGPTAPI.rebuild_marker_context


REBUILD_SUMMARY = (
    "The story so far: a small cast meets, waves, and leaves; "
    "names are recorded exactly as in the sources."
)


def _poison_recovery(recovery):
    """Simulate the historical defect inside a saved recovery bundle."""
    from book_maker.marker_bundle import write_bundle

    state = read_state(recovery)
    state["session"]["seed"] = (
        "You are continuing a translation already in progress.\n\n"
        + PROVIDER_BOILERPLATE
    )
    state["session"]["last_summary"] = OTHER_PROVIDER_BOILERPLATE
    # An old `ok` event is preserved as history; content decides, not the flag.
    state["diagnostics"]["compactions"].append(
        {"time": "2026-01-01T00:00:00", "window": 1, "status": "ok"}
    )
    write_bundle(recovery, recovery, state)
    poisoned = read_state(recovery)
    assert PROVIDER_BOILERPLATE in poisoned["session"]["seed"]
    assert poisoned["session"]["last_summary"] == OTHER_PROVIDER_BOILERPLATE
    return poisoned


class AttributionAdapterModel(FakeMarkerModel):
    """Real adapter responses that make two IDs ambiguous before a repair.

    The first scripted response hides a nested duplicate for the first two
    paragraphs while still returning a valid third paragraph, then interrupts
    the grouped repair.  Resume must reuse the accepted third paragraph.
    """

    script = []
    sent_requests = []
    default_translations = ATTRIBUTION_TRANSLATIONS

    @classmethod
    def reset_script(cls, script):
        cls.script = list(script)
        cls.sent_requests = []
        cls.calls = 0

    def standing_instructions(self):
        return "system"

    def _protected_completion(self, messages, stage):
        mode = type(self).script.pop(0)
        content = messages[-1]["content"]
        start = content.rindex("<bbm-request>")
        end = content.index("</bbm-request>", start) + len("</bbm-request>")
        root = etree.fromstring(content[start:end].encode())
        units = [(node.get("id"), node.text or "") for node in root]
        type(self).sent_requests.append(units)
        if mode == "interrupt":
            raise KeyboardInterrupt
        if mode == "ambiguous":
            (first_id, _first), (second_id, _second), (third_id, third) = units
            raw = (
                "<bbm-response>"
                f'<bbm-unit id="{first_id}">${{Alice}} BAD outer'
                f'<bbm-unit id="{second_id}">${{Bob}} BAD first</bbm-unit>'
                "</bbm-unit>"
                f'<bbm-unit id="{second_id}">${{Bob}} BAD second</bbm-unit>'
                f'<bbm-unit id="{third_id}">{self._translate_source(third)}</bbm-unit>'
                "</bbm-response>"
            )
        else:
            parts = [
            f'<bbm-unit id="{identity}">{self._translate_source(source)}</bbm-unit>'
                for identity, source in units
            ]
            raw = "<bbm-response>" + "".join(parts) + "</bbm-response>"
        finish = "stop"
        self._protected_last_response = raw
        self._protected_response_metadata = {"finish_reason": finish, "model": "fake"}
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=raw, refusal=None),
                    finish_reason=finish,
                )
            ]
        )

    translate_marker_units = ChatGPTAPI.translate_marker_units


def _loader(path, resume, translate_metadata=False, model=FakeMarkerModel):
    loader = EPUBBookLoader(
        str(path),
        model,
        key="",
        resume=resume,
        language="zh-hans",
        source_lang="en",
        protected_names=True,
    )
    loader.translate_metadata = translate_metadata
    loader.single_translate = True
    loader.output_dir = str(path.parent / "output")
    loader.request_max_units = 1
    return loader


def test_tiny_marker_run_interrupts_resumes_and_finishes(tmp_path):
    source = tmp_path / "book.epub"
    _write_book(source)
    output = tmp_path / "output" / "book_bilingual.epub"
    ongoing = tmp_path / "ongoing"

    FakeMarkerModel.reset(fail_at_call=1)
    loader = _loader(source, resume=False)
    with pytest.raises(SystemExit) as interrupted:
        loader.make_bilingual_book()
    assert interrupted.value.code == 130
    recoveries = list(ongoing.glob("book-*_bilingual_temp.epub"))
    assert len(recoveries) == 1
    recovery = recoveries[0]
    assert not output.exists()
    _assert_no_tokens(recovery, loader.translate_model)
    first_state = read_state(recovery)
    assert first_state["run"]["status"] == "partial"
    first_run_id = first_state["run"]["run_id"]
    assert len(first_run_id) >= 32
    assert first_state["session"]["last_summary"].startswith("${Alice}")
    assert first_state["source_windows"]["windows"][0]["status"] == "complete"
    assert first_state["source_windows"]["windows"][1]["status"] == "prepared"
    assert first_state["diagnostics"]["compactions"] == []
    assert not (tmp_path / ".book.temp.bin").exists()

    FakeMarkerModel.reset(fail_at_call=None)
    resumed = _loader(source, resume=True)
    resumed.make_bilingual_book()
    assert output.exists()
    assert not recovery.exists()
    _assert_no_tokens(output, resumed.translate_model)

    state = read_state(output)
    assert state["run"]["status"] == "complete"
    assert state["run"]["run_id"] == first_run_id
    assert set(state["catalogue"]) == {"Alice", "Bob", "Carol"}
    assert all(entry["target"] for entry in state["catalogue"].values())
    # One accepted occurrence per label: resume must not recount a replayed
    # checkpoint or a compacted exchange.
    assert {entry["count"] for entry in state["catalogue"].values()} == {1}

    with zipfile.ZipFile(output) as archive:
        chapter = archive.read("EPUB/one.xhtml").decode()
        css = archive.read("EPUB/bbm-names.css").decode()
        assert STATE_PATH in archive.namelist()
    assert 'class="bbm-name"' in chapter
    assert 'content: "爱丽丝"' in css

    # A second fresh run must ignore the completed output.  Interrupt it, then
    # prove resume continues *that* recovery rather than reporting the old book
    # as complete.
    FakeMarkerModel.reset(fail_at_call=1)
    with pytest.raises(SystemExit):
        _loader(source, resume=False).make_bilingual_book()
    second_recovery = list(ongoing.glob("book-*_bilingual_temp.epub"))
    assert len(second_recovery) == 1
    second_state = read_state(second_recovery[0])
    assert second_state["run"]["status"] == "partial"
    assert second_state["run"]["run_id"] != first_run_id

    FakeMarkerModel.reset(fail_at_call=None)
    _loader(source, resume=True).make_bilingual_book()
    finished = read_state(output)
    assert finished["run"]["status"] == "complete"
    assert finished["run"]["run_id"] == second_state["run"]["run_id"]
    assert list(ongoing.glob("*.epub")) == []

    leftovers = [
        path.name
        for path in tmp_path.iterdir()
        if path.name.endswith(
            ("_protected_names.json", "_handoff.md", "_glossary.json", ".temp.bin")
        )
    ]
    assert leftovers == []


def test_provider_refusal_on_later_source_chunk_saves_prior_and_stops(tmp_path):
    from book_maker.provider_refusal import ProviderRefusal

    class RefuseSecondChunkModel(FakeMarkerModel):
        default_translations = {
            **TRANSLATIONS,
            "Dave left.": "${Dave}离开了。",
        }

        def translate_marker_units(
            self, payload, feedback="", *, premarked=False, context=None
        ):
            if type(self).calls == 0:
                return super().translate_marker_units(
                    payload, feedback, premarked=premarked, context=context
                )
            type(self).calls += 1
            self.requests.append(payload)
            self._protected_last_response = "Simulated policy refusal."
            self._protected_response_metadata = {
                "window_id": "w000001",
                "chunk": 2,
                "chunk_count": 3,
            }
            raise ProviderRefusal("simulated provider refusal")

    source = tmp_path / "book.epub"
    _write_three_paragraph_book(source)
    ongoing = tmp_path / "ongoing"
    RefuseSecondChunkModel.reset()
    loader = _loader(source, resume=False, model=RefuseSecondChunkModel)

    with pytest.raises(SystemExit) as stopped:
        loader.make_bilingual_book()

    assert stopped.value.code == 1
    assert RefuseSecondChunkModel.calls == 2
    assert len(loader.translate_model.requests) == 2
    recovery = next(ongoing.glob("book-*_bilingual_temp.epub"))
    state = read_state(recovery)
    window = state["source_windows"]["windows"][0]

    assert window["status"] == "translating"
    assert len(window["units"]) == 3
    assert window["accepted_unit_ids"] == [window["units"][0]["unit_id"]]
    assert window["units"][0]["translation"] == "${Alice}遇见了${Bob}。"
    assert window["units"][1].get("translation") is None
    assert window["units"][2].get("translation") is None
    assert state["source_windows"]["current_summary"] == ""
    assert state["source_windows"]["summary_pending_window"] is None
    recovery_entries = state["paragraph_recovery"]["entries"]
    assert len(recovery_entries) == 1
    assert recovery_entries[0]["identity"] == window["units"][0]["unit_id"]
    assert recovery_entries[0]["master"] == "${Alice}遇见了${Bob}。"

    failure = state["diagnostics"]["failures"][-1]
    assert failure["stage"] == "window-translation"
    assert "w000001, chunk 2/3" in failure["reason"]
    assert "simulated provider refusal" in failure["reason"]
    assert failure["response_metadata"]["chunk"] == 2
    assert failure["response_metadata"]["chunk_count"] == 3


def test_each_window_extracts_translates_then_updates_cumulative_summary(tmp_path):
    source = tmp_path / "book.epub"
    _write_book(source)
    output = tmp_path / "output" / "book_bilingual.epub"
    FakeMarkerModel.reset(fail_at_call=None)

    loader = _loader(source, resume=False)
    loader.make_bilingual_book()

    calls = loader.translate_model.stage_calls
    assert [kind for kind, _identity in calls] == [
        "extract", "translate", "summary", "extract", "translate", "summary"
    ]
    assert [calls[index][1] for index in (0, 2, 3, 5)] == [
        "w000001", "w000001", "w000002", "w000002"
    ]
    assert loader.translate_model.extraction_summaries[0] == ""
    assert loader.translate_model.extraction_summaries[1]
    assert "${Alice}" in loader.translate_model.extraction_summaries[1]
    assert "${Bob}" in loader.translate_model.extraction_summaries[1]
    assert loader.translate_model.translation_contexts[0]["summary"] == ""
    assert (
        loader.translate_model.translation_contexts[1]["summary"]
        == loader.translate_model.extraction_summaries[1]
    )
    assert read_state(output)["run"]["status"] == "complete"


def test_source_backed_navigation_is_captured_and_editable(tmp_path):
    source = tmp_path / "book.epub"
    _write_book(source)
    output = tmp_path / "output" / "book_bilingual.epub"

    FakeMarkerModel.reset(fail_at_call=None)
    _loader(source, resume=False, translate_metadata=True).make_bilingual_book()
    assert output.exists()
    state = read_state(output)
    assert state["localization"]["version"] == 3
    destinations = state["localization"]["destinations"]
    assert destinations
    assert {record["resource"] for record in destinations} >= {
        "EPUB/nav.xhtml",
        "EPUB/toc.ncx",
        "EPUB/content.opf",
    }

    with zipfile.ZipFile(output) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode()
    assert "爱丽丝" in nav
    assert "鲍勃" in nav

    from book_maker.marker_bundle import apply_catalogue_update

    apply_catalogue_update(
        output, {"catalogue": {"Alice": {"target": "Alicia"}}}
    )
    with zipfile.ZipFile(output) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode()
        ncx = archive.read("EPUB/toc.ncx").decode()
        chapter = archive.read("EPUB/one.xhtml").decode()
        css = archive.read("EPUB/bbm-names.css").decode()
    assert "Alicia" in nav
    assert "Alicia" in ncx
    assert "<title>Alicia" in chapter
    assert 'class="bbm-name"' in chapter
    assert "爱丽丝" not in chapter
    assert "Alicia" in css
    assert "爱丽丝" not in css
    assert "爱丽丝" not in nav


COLLISION_TRANSLATIONS = {
    "Alice arrived.": "${Alice}登场。",
    "Bob waited.": "${Bob}等待。",
    "Alice": "${Alice}",
    "Bob": "${Bob}",
}


def _write_collision_book(path):
    book = epub.EpubBook()
    book.set_identifier("marker-collision")
    book.set_title("Alice")
    book.set_language("en")
    one = epub.EpubHtml(title="Alice", file_name="one.xhtml", lang="en")
    one.content = (
        "<html><head><title>Alice</title></head>"
        "<body><p>Alice arrived.</p></body></html>"
    )
    two = epub.EpubHtml(title="Bob", file_name="two.xhtml", lang="en")
    two.content = (
        "<html><head><title>Bob</title></head>"
        "<body><p>Bob waited.</p>"
        '<p><code><a href="https://example.com">Alice</a></code></p>'
        "</body></html>"
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
    epub.write_epub(str(path), book)


def _assert_no_tokens(output, model):
    from book_maker.marker_bundle import STATE_PATH

    with zipfile.ZipFile(output) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            assert b"BBMLOC" not in data, name
        assert STATE_PATH in archive.namelist()
    for request in model.requests:
        assert "BBMLOC" not in request
    session = getattr(model, "session", None)
    if session is not None:
        assert "BBMLOC" not in json.dumps(session.messages(), ensure_ascii=False)


def test_colliding_draft_targets_keep_distinct_navigation_identity(tmp_path):
    source = tmp_path / "collision.epub"
    _write_collision_book(source)
    output = tmp_path / "output" / "collision_bilingual.epub"

    FakeMarkerModel.reset(
        fail_at_call=None,
        mapping_targets={"Alice": "Same", "Bob": "Same"},
        translations=COLLISION_TRANSLATIONS,
    )
    model = FakeMarkerModel
    loader = _loader(source, resume=False, translate_metadata=True, model=model)
    loader.make_bilingual_book()
    assert output.exists()

    state = read_state(output)
    assert state["localization"]["version"] == 3
    destinations = state["localization"]["destinations"]
    masters = {record["master"] for record in destinations}
    assert "${Alice}" in masters
    assert "${Bob}" in masters

    with zipfile.ZipFile(output) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode()
        chapter = archive.read("EPUB/one.xhtml").decode()
    nav_root = etree.fromstring(nav.encode())
    assert [
        "".join(element.itertext())
        for element in nav_root.iter()
        if isinstance(element.tag, str)
        and etree.QName(element).localname == "a"
    ] == ["Same", "Same"]
    assert "<h2>Same</h2>" in nav
    assert 'class="bbm-name"' in chapter
    assert "Alice" in chapter

    # Offline edits must not need the source EPUB any more.
    source.unlink()
    from book_maker.marker_bundle import apply_catalogue_update

    apply_catalogue_update(
        output,
        {
            "catalogue": {
                "Alice": {"target": "Correct A"},
                "Bob": {"target": "Correct B"},
            }
        },
    )

    with zipfile.ZipFile(output) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode()
        ncx = archive.read("EPUB/toc.ncx").decode()
        opf = archive.read("EPUB/content.opf").decode()
        one = archive.read("EPUB/one.xhtml").decode()
        two = archive.read("EPUB/two.xhtml").decode()
    assert nav.count("Correct A") == 3
    assert nav.count("Correct B") == 1
    assert "Same" not in nav
    assert "Correct A" in ncx and "Correct B" in ncx
    assert "Correct A" in opf
    assert "<title>Correct A</title>" in one
    assert "<title>Correct B</title>" in two
    assert 'class="bbm-name"' in one
    one_root = etree.fromstring(one.encode())
    two_root = etree.fromstring(two.encode())
    assert [
        "".join(element.itertext())
        for element in one_root.iter()
        if isinstance(element.tag, str)
        and "bbm-original" in (element.get("class") or "").split()
    ] == ["Alice"]
    assert [
        "".join(element.itertext())
        for element in two_root.iter()
        if isinstance(element.tag, str)
        and "bbm-original" in (element.get("class") or "").split()
    ] == ["Bob"]
    assert "Correct A" not in one.split("<body", 1)[-1]
    # The unrelated body hyperlink was never a localization destination.
    assert '<a href="https://example.com">Alice</a>' in two
    assert "data-bbm-loc" not in nav + ncx + opf + one + two
    _assert_no_tokens(output, loader.translate_model)


def test_resume_promotes_paragraph_journal_before_the_chunk_returns(tmp_path):
    source = tmp_path / "book.epub"
    _write_book(source)
    output = tmp_path / "output" / "book_bilingual.epub"
    ongoing = tmp_path / "ongoing"

    class JournalModel(FakeMarkerModel):
        default_translations = {
            "Alice met Bob.": "${Alice}遇见了${Bob}。",
            "Carol waved.": "${Carol}挥了挥手。",
            "Alice": "${Alice}到了。",
            "Bob": "${Bob}来了。",
            "Tiny marker book": "小标记书",
        }
        fail_repair = True

        def translate_marker_units(self, payload, feedback="", *, premarked=False, context=None):
            type(self).calls += 1
            self.requests.append(payload)
            root = etree.fromstring(payload.encode())
            nodes = list(root)
            if not getattr(type(self), "first_request_done", False):
                type(self).first_request_done = True
                units = []
                for index, node in enumerate(nodes):
                    source_text = node.text or ""
                    if index == 1:
                        text = "${白井黑子}挥了挥手。"
                    else:
                        text = self._translate_source(source_text)
                    units.append(
                        f'<bbm-unit id="{node.get("id")}">{text}</bbm-unit>'
                    )
                return (
                    "<bbm-response>\n" + "\n".join(units) + "\n</bbm-response>"
                )
            raise KeyboardInterrupt

    def chunk_loader(resume, model):
        loader = _loader(source, resume=resume, model=model)
        loader.request_max_units = 40
        loader.request_max_tokens = 8000
        loader.accumulated_num = 1000
        loader.accumulate_across_chapters = True
        return loader

    JournalModel.reset()
    JournalModel.first_request_done = False
    with pytest.raises(SystemExit) as interrupted:
        chunk_loader(False, JournalModel).make_bilingual_book()
    assert interrupted.value.code == 130
    recovery = next(ongoing.glob("book-*_bilingual_temp.epub"))
    state = read_state(recovery)
    journal = state["paragraph_recovery"]["entries"]
    assert journal == []
    assert state["checkpoint"]["translations"] == ["${Alice}遇见了${Bob}。"]
    first_window = state["source_windows"]["windows"][0]
    assert first_window["accepted_unit_ids"] == [first_window["units"][0]["unit_id"]]
    assert first_window["units"][0]["translation"] == "${Alice}遇见了${Bob}。"
    assert set(state["catalogue"]) == {"Alice", "Bob", "Carol"}
    assert state["catalogue"]["Carol"]["count"] == 0
    assert all("白井黑子" not in item["assistant"]
               for item in state["session"]["exchanges"])
    with zipfile.ZipFile(recovery) as archive:
        first = archive.read("EPUB/one.xhtml").decode()
        second = archive.read("EPUB/two.xhtml").decode()
    assert "遇见了" in first
    assert 'class="bbm-name"' in first
    assert "挥了挥手" not in second

    class ResumeModel(FakeMarkerModel):
        default_translations = JournalModel.default_translations

        def translate_marker_units(self, payload, feedback="", *, premarked=False, context=None):
            type(self).calls += 1
            self.requests.append(payload)
            root = etree.fromstring(payload.encode())
            assert len(root) == 1
            node = root[0]
            assert "Alice met Bob." not in (node.text or "")
            return (
                f'<bbm-response><bbm-unit id="{node.get("id")}">'
                f"{self._translate_source(node.text or '')}</bbm-unit></bbm-response>"
            )

    ResumeModel.reset()
    resumed = chunk_loader(True, ResumeModel)
    resumed.make_bilingual_book()
    assert output.exists()
    state = read_state(output)
    assert state["paragraph_recovery"]["entries"] == []
    assert {entry["count"] for entry in state["catalogue"].values()} == {1}
    assert ResumeModel.calls == 1
    with zipfile.ZipFile(output) as archive:
        first = archive.read("EPUB/one.xhtml").decode()
        second = archive.read("EPUB/two.xhtml").decode()
    assert "遇见了" in first
    assert "挥了挥手" in second


def test_resume_upgrades_version_two_localization_without_body_retranslation(
    tmp_path,
):
    from book_maker.marker_bundle import write_bundle

    source = tmp_path / "book.epub"
    _write_book(source)
    output = tmp_path / "output" / "book_bilingual.epub"
    ongoing = tmp_path / "ongoing"

    FakeMarkerModel.reset(fail_at_call=1)
    with pytest.raises(SystemExit):
        _loader(source, resume=False).make_bilingual_book()
    recovery = next(ongoing.glob("book-*_bilingual_temp.epub"))
    accepted = read_state(recovery)["checkpoint"]["translations"]
    assert accepted

    legacy = read_state(recovery)
    legacy["localization"]["version"] = 2
    legacy["localization"]["destinations"] = []
    write_bundle(recovery, recovery, legacy)
    assert read_state(recovery)["localization"]["version"] == 2

    FakeMarkerModel.reset(fail_at_call=None)
    resumed = _loader(source, resume=True, translate_metadata=True)
    resumed.make_bilingual_book()
    assert output.exists()
    state = read_state(output)
    assert state["localization"]["version"] == 3
    assert state["localization"]["destinations"]
    assert state["checkpoint"]["translations"][: len(accepted)] == accepted
    body_requests = [
        request for request in resumed.translate_model.requests
        if "<bbm-request>" in request
    ]
    assert body_requests
    assert all(":one.xhtml:" not in request for request in body_requests)
    _assert_no_tokens(output, resumed.translate_model)


def test_real_adapter_truncation_journals_then_resumes_without_resending(tmp_path):
    source = tmp_path / "book.epub"
    _write_book(source)
    output = tmp_path / "output" / "book_bilingual.epub"
    ongoing = tmp_path / "ongoing"

    TruncatingAdapterModel.reset_script(["truncate", "interrupt"])
    loader = _loader(source, resume=False, model=TruncatingAdapterModel)
    loader.request_max_units = 40
    loader.request_max_tokens = 8000
    loader.accumulated_num = 1000
    loader.accumulate_across_chapters = True
    with pytest.raises(SystemExit) as interrupted:
        loader.make_bilingual_book()
    assert interrupted.value.code == 130

    recovery = next(ongoing.glob("book-*_bilingual_temp.epub"))
    state = read_state(recovery)
    journal = state["paragraph_recovery"]["entries"]
    assert journal == []
    assert state["checkpoint"]["translations"] == [
        TRANSLATIONS["Alice met Bob."]
    ]
    assert state["source_windows"]["windows"][0]["accepted_unit_ids"] == [
        state["source_windows"]["windows"][0]["units"][0]["unit_id"]
    ]
    first_ids = [unit[0] for unit in TruncatingAdapterModel.sent_requests[0]]
    assert len(first_ids) == 2
    assert first_ids == ["u001", "u002"]
    unit_ids = [
        unit["unit_id"]
        for unit in state["source_windows"]["windows"][0]["units"]
    ]
    assert state["checkpoint"]["accepted_job_ids"] == [unit_ids[0]]
    with zipfile.ZipFile(recovery) as archive:
        first_chapter = archive.read("EPUB/one.xhtml").decode()
    assert "遇见了" in first_chapter

    TruncatingAdapterModel.reset_script(["complete"])
    resumed = _loader(source, resume=True, model=TruncatingAdapterModel)
    resumed.request_max_units = 40
    resumed.request_max_tokens = 8000
    resumed.accumulated_num = 1000
    resumed.accumulate_across_chapters = True
    resumed.make_bilingual_book()
    assert output.exists()
    assert len(TruncatingAdapterModel.sent_requests) == 1
    assert [unit[0] for unit in TruncatingAdapterModel.sent_requests[0]] == [
        first_ids[1]
    ]
    state = read_state(output)
    assert state["paragraph_recovery"]["entries"] == []
    assert {entry["count"] for entry in state["catalogue"].values()} == {1}
    with zipfile.ZipFile(output) as archive:
        first = archive.read("EPUB/one.xhtml").decode()
        second = archive.read("EPUB/two.xhtml").decode()
    assert "遇见了" in first
    assert "挥了挥手" in second


def test_resume_after_ambiguous_ids_reuses_the_safe_sibling(tmp_path):
    source = tmp_path / "book.epub"
    _write_three_paragraph_book(source)
    output = tmp_path / "output" / "book_bilingual.epub"
    ongoing = tmp_path / "ongoing"

    def chunk_loader(resume, model):
        loader = _loader(source, resume=resume, model=model)
        loader.request_max_units = 40
        loader.request_max_tokens = 8000
        loader.accumulated_num = 1000
        loader.accumulate_across_chapters = True
        return loader

    AttributionAdapterModel.reset_script(["ambiguous", "interrupt"])
    with pytest.raises(SystemExit) as interrupted:
        chunk_loader(False, AttributionAdapterModel).make_bilingual_book()
    assert interrupted.value.code == 130

    first_send = AttributionAdapterModel.sent_requests[0]
    first_ids = [unit[0] for unit in first_send]
    assert len(first_ids) == 3
    repair = AttributionAdapterModel.sent_requests[1]
    assert [unit[0] for unit in repair] == first_ids[:2]

    recovery = next(ongoing.glob("book-*_bilingual_temp.epub"))
    state = read_state(recovery)
    journal = state["paragraph_recovery"]["entries"]
    unit_ids = [
        unit["unit_id"]
        for unit in state["source_windows"]["windows"][0]["units"]
    ]
    assert [entry["identity"] for entry in journal] == [unit_ids[2]]
    assert journal[0]["master"] == ATTRIBUTION_TRANSLATIONS["Dave left."]
    assert state["checkpoint"]["translations"] == []
    assert set(state["catalogue"]) == {"Alice", "Bob", "Carol", "Dave"}
    assert state["catalogue"]["Dave"]["count"] == 1
    assert all(
        state["catalogue"][label]["count"] == 0
        for label in ("Alice", "Bob", "Carol")
    )
    with zipfile.ZipFile(recovery) as archive:
        chapter = archive.read("EPUB/one.xhtml").decode()
    assert "离开了" in chapter
    assert "BAD" not in chapter
    assert "遇见了" not in chapter
    assert "挥了挥手" not in chapter

    AttributionAdapterModel.reset_script(["complete"])
    resumed = chunk_loader(True, AttributionAdapterModel)
    resumed.make_bilingual_book()
    assert output.exists()
    assert len(AttributionAdapterModel.sent_requests) == 1
    assert [
        unit[0] for unit in AttributionAdapterModel.sent_requests[0]
    ] == first_ids[:2]
    state = read_state(output)
    assert state["paragraph_recovery"]["entries"] == []
    assert set(state["catalogue"]) == {"Alice", "Bob", "Carol", "Dave"}
    assert {entry["count"] for entry in state["catalogue"].values()} == {1}
    with zipfile.ZipFile(output) as archive:
        chapter = archive.read("EPUB/one.xhtml").decode()
    assert "BAD" not in chapter
    positions = [
        chapter.index(fragment)
        for fragment in ("遇见了", "挥了挥手", "离开了")
    ]
    assert positions == sorted(positions)


def _recovery_chunk_loader(source, resume, model):
    loader = _loader(source, resume=resume, model=model)
    loader.request_max_units = 40
    loader.request_max_tokens = 8000
    loader.accumulated_num = 1000
    loader.accumulate_across_chapters = True
    return loader


def test_window_resume_ignores_legacy_target_history_and_uses_source_summary(tmp_path):
    source = tmp_path / "book.epub"
    _write_three_paragraph_book(source)
    output = tmp_path / "output" / "book_bilingual.epub"
    ongoing = tmp_path / "ongoing"

    AttributionAdapterModel.reset_script(["ambiguous", "interrupt"])
    with pytest.raises(SystemExit) as interrupted:
        _recovery_chunk_loader(
            source, False, AttributionAdapterModel
        ).make_bilingual_book()
    assert interrupted.value.code == 130
    recovery = next(ongoing.glob("book-*_bilingual_temp.epub"))
    accepted_journal = read_state(recovery)["paragraph_recovery"]["entries"]
    assert len(accepted_journal) == 1
    outstanding_siblings = [
        unit[0] for unit in AttributionAdapterModel.sent_requests[0]
    ][:2]
    _poison_recovery(recovery)

    RepairAdapterModel.reset(translations=ATTRIBUTION_TRANSLATIONS)
    RepairAdapterModel.reset_script(["complete"])
    resumed = _recovery_chunk_loader(source, True, RepairAdapterModel)
    resumed.make_bilingual_book()
    assert output.exists()

    # V2 ignores legacy target-language session history. It resumes from the
    # validated source summary already stored with the window, with no rebuild.
    modes = [action[0] for action in RepairAdapterModel.actions]
    assert modes == ["send"]
    assert RepairAdapterModel.received_compactions == []

    # Only the two outstanding paragraphs were translated; the accepted
    # journal sibling is reused without a resend.
    send_actions = [action[1] for action in RepairAdapterModel.actions
                    if action[0] == "send"]
    assert len(send_actions) == 1
    assert [unit[0] for unit in send_actions[0]] == outstanding_siblings

    state = read_state(output)
    assert state["run"]["status"] == "complete"
    assert state["paragraph_recovery"]["entries"] == []
    # No catalogue recount with a duplicate rebuild registration.
    assert {entry["count"] for entry in state["catalogue"].values()} == {1}
    session = state["session"]
    assert session["seed"] == ""
    assert session["exchanges"] == []
    assert session["last_summary"] == state["source_windows"]["current_summary"]
    assert PROVIDER_BOILERPLATE not in json.dumps(
        RepairAdapterModel.actions, ensure_ascii=False
    )
    assert OTHER_PROVIDER_BOILERPLATE not in json.dumps(
        RepairAdapterModel.actions, ensure_ascii=False
    )


def test_window_resume_does_not_use_legacy_context_repair_state(tmp_path):
    source = tmp_path / "book.epub"
    _write_three_paragraph_book(source)
    output = tmp_path / "output" / "book_bilingual.epub"
    ongoing = tmp_path / "ongoing"

    AttributionAdapterModel.reset_script(["ambiguous", "interrupt"])
    with pytest.raises(SystemExit):
        _recovery_chunk_loader(
            source, False, AttributionAdapterModel
        ).make_bilingual_book()
    recovery = next(ongoing.glob("book-*_bilingual_temp.epub"))
    _poison_recovery(recovery)

    # V2 ignores the poisoned target-language history and continues with its
    # validated original-source window state.
    AttributionAdapterModel.reset_script(["complete"])
    resumed = _recovery_chunk_loader(source, True, AttributionAdapterModel)
    resumed.make_bilingual_book()
    assert output.exists()
    assert len(AttributionAdapterModel.sent_requests) == 1
    request_data = json.dumps(
        AttributionAdapterModel.sent_requests, ensure_ascii=False
    )
    assert PROVIDER_BOILERPLATE not in request_data
    assert OTHER_PROVIDER_BOILERPLATE not in request_data
    state = read_state(output)
    assert state["run"]["status"] == "complete"
    assert state["session"]["seed"] == ""
    assert state["session"]["exchanges"] == []
    assert state["session"]["last_summary"] == state["source_windows"]["current_summary"]
    assert state["session"]["seed"] == ""



def test_second_fresh_run_preserves_the_first_recovery(tmp_path):
    source = tmp_path / "book.epub"
    _write_book(source)
    ongoing = tmp_path / "ongoing"

    FakeMarkerModel.reset(fail_at_call=1)
    with pytest.raises(SystemExit):
        _loader(source, resume=False).make_bilingual_book()
    first = next(ongoing.glob("book-*_bilingual_temp.epub"))
    first_digest = sha256(first.read_bytes()).hexdigest()
    first_state = read_state(first)
    first_run_id = first_state["run"]["run_id"]
    assert first_state["run"]["mode"] == "fresh"

    FakeMarkerModel.reset(fail_at_call=1)
    with pytest.raises(SystemExit):
        _loader(source, resume=False).make_bilingual_book()

    archives = list((ongoing / "archive").glob("*prior*.epub"))
    assert len(archives) == 1
    assert sha256(archives[0].read_bytes()).hexdigest() == first_digest
    current = next(ongoing.glob("book-*_bilingual_temp.epub"))
    current_state = read_state(current)
    assert sha256(current.read_bytes()).hexdigest() != first_digest
    assert current_state["run"]["run_id"] != first_run_id
    assert current_state["run"]["mode"] == "fresh"

    FakeMarkerModel.reset(fail_at_call=None)
    _loader(source, resume=True).make_bilingual_book()
    assert archives[0].exists()
    assert sha256(archives[0].read_bytes()).hexdigest() == first_digest


def test_archive_failure_leaves_the_prior_recovery_untouched(tmp_path, monkeypatch):
    source = tmp_path / "book.epub"
    _write_book(source)
    ongoing = tmp_path / "ongoing"

    FakeMarkerModel.reset(fail_at_call=1)
    with pytest.raises(SystemExit):
        _loader(source, resume=False).make_bilingual_book()
    first = next(ongoing.glob("book-*_bilingual_temp.epub"))
    first_digest = sha256(first.read_bytes()).hexdigest()

    def fail_copy(*args, **kwargs):
        raise OSError("simulated archive failure")

    monkeypatch.setattr(
        "book_maker.loader.epub_loader.shutil.copy2", fail_copy
    )
    FakeMarkerModel.reset(fail_at_call=1)
    with pytest.raises((OSError, SystemExit)):
        _loader(source, resume=False).make_bilingual_book()

    assert sha256(first.read_bytes()).hexdigest() == first_digest
    assert not list((ongoing / "archive").glob("*.epub"))
