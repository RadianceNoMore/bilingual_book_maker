"""End-to-end marker workflow with an offline fake provider.

The fake model answers the strict XML paragraph protocol with marked Chinese
prose, then supplies display mappings.  The test covers a fresh tiny run,
interruption, resume from ``ongoing``, final CSS rendering, a second fresh run,
and recovery cleanup.
"""

import json
import re
import zipfile
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

    def translate_marker_units(self, payload, feedback=""):
        type(self).calls += 1
        self.requests.append(payload)
        if type(self).fail_at_call is not None and type(self).calls > type(self).fail_at_call:
            raise KeyboardInterrupt
        root = etree.fromstring(payload.encode())
        units = []
        for node in root:
            source = node.text or ""
            units.append(
                f'<bbm-unit id="{node.get("id")}">'
                f"{self.translations[source]}</bbm-unit>"
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
                    row["label"], f"目标{row['label']}"
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
                    f'<bbm-unit id="{identity}">{self.translations[source]}</bbm-unit>'
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
            f'<bbm-unit id="{identity}">{self.translations[source]}</bbm-unit>'
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
                f'<bbm-unit id="{third_id}">{self.translations[third]}</bbm-unit>'
                "</bbm-response>"
            )
        else:
            parts = [
                f'<bbm-unit id="{identity}">{self.translations[source]}</bbm-unit>'
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


class QueuedReviewModel(FakeMarkerModel):
    allow_final_review = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.marker_review_enabled = True
        self.max_chunk_attempts = 1
        self.sources_sent = []
        self.review_sources = []
        self.translations.update(ATTRIBUTION_TRANSLATIONS)
        self.translations["Alice met Bob."] = "${Invented}遇见了${Bob}。"

    def translate_marker_units(self, payload, feedback=""):
        self.sources_sent.extend(node.text for node in etree.fromstring(payload.encode()))
        return super().translate_marker_units(payload, feedback)

    def review_marker_paragraph(self, prompt):
        data = json.loads(prompt.split("\n\n", 1)[1])
        self.review_sources.append(data["original"])
        assert data["nearby_source"] == ["Carol waved."]
        # Refuse to repair the first paragraph until later chunks ran. This
        # exercises the actual persistent gap, journal and final replay path.
        ready = type(self).allow_final_review and (
            "Dave left." in self.sources_sent or bool(self._marker_state.recovery_entries())
        )
        return json.dumps({
            "id": data["id"], "decision": "rewrite" if ready else "defer",
            "translation": ATTRIBUTION_TRANSLATIONS["Alice met Bob."],
            "allow_extra_references": False,
            "reason": "Both named source characters are preserved.",
        })


def test_review_queue_continues_later_chunks_then_assembles_in_order(tmp_path):
    source = tmp_path / "book.epub"
    _write_three_paragraph_book(source)
    QueuedReviewModel.reset()
    QueuedReviewModel.allow_final_review = True
    loader = _loader(source, False, model=QueuedReviewModel)
    loader.make_bilingual_book()
    assert loader.translate_model.sources_sent == ["Alice met Bob.", "Carol waved.", "Dave left."]
    output = tmp_path / "output" / "book_bilingual.epub"
    state = read_state(output)
    assert state["run"]["status"] == "complete"
    assert state["checkpoint"]["translations"] == list(ATTRIBUTION_TRANSLATIONS.values())
    assert state["paragraph_queue"]["entries"] == []
    assert state["paragraph_recovery"]["entries"] == []
    assert "Invented" not in state["catalogue"]
    assert all(row["count"] == 1 for row in state["catalogue"].values())
    with zipfile.ZipFile(output) as archive:
        chapter = archive.read("EPUB/one.xhtml").decode()
    assert chapter.index("遇见了") < chapter.index("挥了挥手") < chapter.index("离开了")


def test_unresolved_review_queue_blocks_publication_and_resume_reuses_siblings(tmp_path):
    source = tmp_path / "book.epub"
    _write_three_paragraph_book(source)
    QueuedReviewModel.reset()
    QueuedReviewModel.allow_final_review = False
    first = _loader(source, False, model=QueuedReviewModel)
    with pytest.raises(SystemExit) as stopped:
        first.make_bilingual_book()
    assert stopped.value.code == 1
    output = tmp_path / "output" / "book_bilingual.epub"
    assert not output.exists()
    recovery = next((tmp_path / "ongoing").glob("*.epub"))
    saved = read_state(recovery)
    assert len(saved["paragraph_queue"]["entries"]) == 1
    assert saved["checkpoint"]["translations"] == []
    assert [entry["master"] for entry in saved["paragraph_recovery"]["entries"]] == [
        ATTRIBUTION_TRANSLATIONS["Carol waved."], ATTRIBUTION_TRANSLATIONS["Dave left."]]

    QueuedReviewModel.allow_final_review = True
    resumed = _loader(source, True, model=QueuedReviewModel)
    resumed.make_bilingual_book()
    assert resumed.translate_model.sources_sent == ["Alice met Bob."]
    finished = read_state(output)
    assert finished["paragraph_queue"]["entries"] == []
    assert finished["checkpoint"]["translations"] == list(ATTRIBUTION_TRANSLATIONS.values())


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
    assert first_state["session"]["last_summary"].startswith("Summary with ${")
    assert first_state["diagnostics"]["compactions"]
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
    assert 'content: "目标Alice"' in css

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


def test_resume_accepts_retry_budget_change_without_repeating_accepted_work(tmp_path):
    source = tmp_path / "book.epub"
    _write_book(source)
    FakeMarkerModel.reset(fail_at_call=1)
    first = _loader(source, False)
    first.max_chunk_attempts = 5
    with pytest.raises(SystemExit):
        first.make_bilingual_book()
    recovery = next((tmp_path / "ongoing").glob("*.epub"))
    original = read_state(recovery)

    FakeMarkerModel.reset()
    resumed = _loader(source, True)
    assert resumed.max_chunk_attempts == 10
    resumed.make_bilingual_book()
    finished = read_state(tmp_path / "output" / "book_bilingual.epub")
    assert finished["run"]["run_id"] == original["run"]["run_id"]
    assert finished["run"]["effective_settings"]["max_chunk_attempts"] == 10
    body_requests = [request for request in resumed.translate_model.requests
                     if request.startswith("<bbm-request>")]
    assert len(body_requests) == 1
    assert "Carol waved." in body_requests[0]
    assert "Alice met Bob." not in body_requests[0]


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
    assert "目标Alice" in nav
    assert "目标Bob" in nav

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
    assert "目标Alice" not in chapter
    assert "Alicia" in css
    assert "目标Alice" not in css
    assert "目标Alice" not in nav


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

        def translate_marker_units(self, payload, feedback=""):
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
                        text = self.translations[source_text]
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
    assert len(journal) == 1
    assert journal[0]["master"] == "${Alice}遇见了${Bob}。"
    assert state["checkpoint"]["translations"] == []
    assert set(state["catalogue"]) == {"Alice", "Bob"}
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

        def translate_marker_units(self, payload, feedback=""):
            type(self).calls += 1
            self.requests.append(payload)
            root = etree.fromstring(payload.encode())
            assert len(root) == 1
            node = root[0]
            assert "Alice met Bob." not in (node.text or "")
            return (
                f'<bbm-response><bbm-unit id="{node.get("id")}">'
                f"{self.translations[node.text]}</bbm-unit></bbm-response>"
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
    assert not any(
        "Alice met Bob." in request
        for request in resumed.translate_model.requests
    )
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
    assert len(journal) == 1
    assert journal[0]["master"] == TRANSLATIONS["Alice met Bob."]
    assert state["checkpoint"]["translations"] == []
    first_ids = [unit[0] for unit in TruncatingAdapterModel.sent_requests[0]]
    assert len(first_ids) == 2
    assert journal[0]["identity"] == first_ids[0]
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
    assert [entry["identity"] for entry in journal] == [first_ids[2]]
    assert journal[0]["master"] == ATTRIBUTION_TRANSLATIONS["Dave left."]
    assert state["checkpoint"]["translations"] == []
    assert set(state["catalogue"]) == {"Dave"}
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


def test_poisoned_context_is_quarantined_rebuilt_and_persisted(tmp_path):
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
    RepairAdapterModel.reset_rebuild([REBUILD_SUMMARY])
    RepairAdapterModel.reset_script(["complete"])
    resumed = _recovery_chunk_loader(source, True, RepairAdapterModel)
    resumed.make_bilingual_book()
    assert output.exists()

    # The rebuild ran once, before any paragraph send (the workflow gate
    # only opens after the verified replacement is installed and saved).
    modes = [action[0] for action in RepairAdapterModel.actions]
    assert modes == ["rebuild", "send"]
    compaction_request = "\n".join(
        RepairAdapterModel.received_compactions[0]
    )
    # The bad seed/summary never reaches a request ...
    for fragment in ("hard time", "something went wrong", "目标"):
        assert fragment not in compaction_request
    # Only accepted translated masters reach compaction, not source requests.
    # This fixture translates English source into Chinese.
    assert "Dave left." not in compaction_request
    assert "${Dave}离开了。" in compaction_request

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
    assert PROVIDER_BOILERPLATE not in (session["seed"] or "")
    assert OTHER_PROVIDER_BOILERPLATE not in (session["last_summary"] or "")
    assert session["context_repair"]["pending"] is False
    # A later on-run compaction may legitimately supersede the rebuild seed,
    # but it must stay a validated summary, consistent with last_summary.
    assert session["seed"] and session["seed"] == session["last_summary"]
    assert session["context_validation"]["version"] == 1
    rebuild_events = [
        event
        for event in state["diagnostics"]["compactions"]
        if event.get("purpose") == "resume-auto-rebuild"
    ]
    assert rebuild_events and rebuild_events[-1]["status"] == "ok"
    assert any(
        failure["stage"] == "context-restore"
        for failure in state["diagnostics"]["failures"]
    )


def test_pending_repair_stops_run_without_new_requests(tmp_path):
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

    # The plain adapter cannot rebuild context; the pending-repair gate must
    # stop the run before the first new provider request, still saving.
    AttributionAdapterModel.reset_script([])
    resumed = _recovery_chunk_loader(source, True, AttributionAdapterModel)
    with pytest.raises(SystemExit) as stopped:
        resumed.make_bilingual_book()
    assert stopped.value.code == 1
    assert not output.exists()
    assert AttributionAdapterModel.sent_requests == []
    assert "目标" not in json.dumps(
        resumed.translate_model.requests, ensure_ascii=False
    )
    state = read_state(recovery)
    assert state["run"]["status"] == "partial"
    assert state["session"]["context_repair"]["pending"] is True
    # The quarantine survives the save, so no later resume can blink past it.
    assert state["session"]["seed"] == ""

