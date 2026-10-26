import pickle
import threading
import zipfile

import pytest
from ebooklib import ITEM_DOCUMENT, epub

from book_maker.loader.epub_loader import EPUBBookLoader


class RecordingModel:
    instances = []
    TRANSLATION_ERROR_MARKER = "[Translation unavailable]"

    def __init__(
        self,
        key,
        language,
        api_base=None,
        context_flag=False,
        context_paragraph_limit=0,
        temperature=1.0,
        source_lang="auto",
        **kwargs,
    ):
        self.calls = []
        self.list_calls = []
        self.contexts_at_call = []
        self.context_flag = context_flag
        self.context_paragraph_limit = context_paragraph_limit or 3
        self.context_list = []
        self.context_translated_list = []
        self._fatal_error_detected = False
        type(self).instances.append(self)

    def translate(self, text):
        self.calls.append(text)
        self.contexts_at_call.append(list(self.context_list))
        translated = f"<T>{text}</T>"
        if self.context_flag:
            self.context_list.append(text)
            self.context_translated_list.append(translated)
            self.context_list = self.context_list[-self.context_paragraph_limit :]
            self.context_translated_list = self.context_translated_list[
                -self.context_paragraph_limit :
            ]
        return translated

    def translate_list(self, texts):
        plain_texts = [str(text) for text in texts]
        self.list_calls.append(plain_texts)
        return [self.translate(text) for text in plain_texts]


class InterruptingModel(RecordingModel):
    def translate(self, text):
        if self.calls:
            raise KeyboardInterrupt
        return super().translate(text)


class OrderedCompletionModel(RecordingModel):
    """Make chapter B finish before chapter A after A obtained index zero."""

    a_started = threading.Event()
    b_finished = threading.Event()

    def translate(self, text):
        self.calls.append(text)
        if text == "chapter A":
            type(self).a_started.set()
            assert type(self).b_finished.wait(timeout=2)
        elif text == "chapter B":
            assert type(self).a_started.wait(timeout=2)
            type(self).b_finished.set()
        return f"<T>{text}</T>"


class ParallelInterruptingModel(RecordingModel):
    first_finished = threading.Event()

    def translate(self, text):
        if text == "chapter A":
            result = super().translate(text)
            type(self).first_finished.set()
            return result
        assert type(self).first_finished.wait(timeout=2)
        raise KeyboardInterrupt


class FailingModel(RecordingModel):
    def translate(self, text):
        raise RuntimeError(f"cannot translate {text}")


def _write_epub(path, chapters):
    book = epub.EpubBook()
    book.set_identifier("correctness-baseline")
    book.set_title("Correctness baseline")
    book.set_language("en")

    items = []
    for index, (file_name, paragraphs) in enumerate(chapters):
        item = epub.EpubHtml(
            title=f"Chapter {index + 1}", file_name=file_name, lang="en"
        )
        body = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
        item.content = f"<html><body>{body}</body></html>"
        book.add_item(item)
        items.append(item)

    book.toc = tuple(items)
    book.spine = items
    epub.write_epub(str(path), book)


def _write_epub_with_toc(path, chapters, metadata=False):
    book = epub.EpubBook()
    book.set_identifier("toc-fixture")
    book.set_title("Correctness baseline")
    book.set_language("en")
    if metadata:
        book.add_metadata("DC", "description", "A source synopsis")
        book.add_metadata("DC", "subject", "Fantasy")
    items = []
    for index, (file_name, paragraphs) in enumerate(chapters):
        item = epub.EpubHtml(
            uid=f"chapter-{index}", title=f"Chapter {index + 1}",
            file_name=file_name, lang="en"
        )
        body = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
        if not body:
            body = f"<h1>Chapter {index + 1}</h1>"
        item.content = (
            "<html><head><title>Old title</title><meta name='npkr-title' "
            "content='Old title'/></head><body>" + body + "</body></html>"
        )
        book.add_item(item)
        items.append(item)
    book.toc = tuple(epub.Link(item.file_name, item.title, item.id) for item in items)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *items]
    epub.write_epub(str(path), book)
    if metadata:
        for item in items:
            member = f"EPUB/{item.file_name}"
            with zipfile.ZipFile(path) as archive:
                raw = archive.read(member).decode("utf-8")
            _replace_epub_member(
                path, member,
                raw.replace("</head>", '<meta name="npkr-title" content="Old title"/></head>'),
            )


def _replace_epub_member(path, member_name, content):
    """Replace one archive member without leaving a duplicate ZIP entry."""
    replacement = path.with_suffix(".replacement.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    content.encode("utf-8")
                    if info.filename == member_name
                    else source.read(info.filename)
                ),
            )
    replacement.replace(path)


def _write_epub_with_custom_nav(path):
    book = epub.EpubBook()
    book.set_identifier("nav-correctness-baseline")
    book.set_title("Navigation correctness baseline")
    book.set_language("en")

    chapter = epub.EpubHtml(
        uid="chapter", title="Chapter one", file_name="chapter.xhtml", lang="en"
    )
    chapter.content = "<html><body><p>Chapter body</p></body></html>"
    nav = epub.EpubNav(uid="nav", file_name="toc.xhtml")
    book.add_item(chapter)
    book.add_item(nav)
    book.toc = (epub.Link("chapter.xhtml", "Chapter one", "chapter-link"),)
    book.spine = [nav, chapter]
    epub.write_epub(str(path), book)

    custom_nav = """<?xml version="1.0" encoding="utf-8"?>
    <html xmlns="http://www.w3.org/1999/xhtml"
          xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
      <head><title>Source navigation title</title></head>
      <body data-source-marker="preserve-me">
        <h1>Source navigation heading</h1>
        <nav epub:type="toc" id="toc" role="doc-toc">
          <h2>Source table of contents</h2>
          <ol><li><a href="chapter.xhtml">Chapter one</a></li></ol>
        </nav>
      </body>
    </html>"""
    _replace_epub_member(path, "EPUB/toc.xhtml", custom_nav)


def _make_loader(
    tmp_path,
    monkeypatch,
    chapters,
    model=RecordingModel,
    *,
    resume=False,
    parallel_workers=1,
    context_flag=False,
    is_test=False,
    test_num=5,
):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "book.epub"
    if not source.exists():
        _write_epub(source, chapters)

    loader = EPUBBookLoader(
        str(source),
        model,
        key="",
        resume=resume,
        language="zh-hans",
        context_flag=context_flag,
        context_paragraph_limit=3,
        parallel_workers=parallel_workers,
        is_test=is_test,
        test_num=test_num,
    )
    # These tests isolate body translation and checkpoint ordering. Metadata
    # localization has its own dedicated EPUB tests.
    loader.translate_metadata = False
    return loader, source.with_name("book_bilingual.epub")


def _document_texts(path):
    book = epub.read_epub(str(path))
    return {
        item.file_name: item.get_body_content().decode("utf-8")
        for item in book.get_items_of_type(ITEM_DOCUMENT)
    }


def test_plan_mode_preserves_translated_imported_nav_and_manifest(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "book.epub"
    _write_epub_with_custom_nav(source)
    loader = EPUBBookLoader(
        str(source),
        RecordingModel,
        key="",
        resume=False,
        language="zh-hans",
    )
    loader.plan_mode = True
    loader.plan_classify = "all"
    loader.translate_tags = "auto"
    loader.quiet = True

    loader.make_bilingual_book()

    output = tmp_path / "book_bilingual.epub"
    with zipfile.ZipFile(output) as archive:
        nav = archive.read("EPUB/toc.xhtml").decode("utf-8")
        opf = archive.read("EPUB/content.opf").decode("utf-8")
    assert 'data-source-marker="preserve-me"' in nav
    assert 'id="toc"' in nav
    assert 'id="id"' not in nav
    assert "Source navigation heading" in nav
    assert "&lt;T&gt;Source navigation heading&lt;/T&gt;" in nav
    nav_manifest = next(line for line in opf.splitlines() if 'href="toc.xhtml"' in line)
    assert 'properties="nav"' in nav_manifest


def test_spine_comment_survives_the_rebuild_instead_of_crashing_it(
    tmp_path, monkeypatch
):
    """An XML comment inside <spine> must not become an itemref.

    ebooklib's _load_spine turns every child of <spine> into an
    (idref, linear) tuple, comments included — they come back as
    (None, None), and writing that book dies inside lxml with "Argument
    must be bytes or unicode, got 'NoneType'".
    vertically-scrollable-manga.epub keeps a commented-out
    page-progression-direction exactly there.
    """
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "book.epub"
    _write_epub(source, [("chapter.xhtml", ["Chapter body"])])
    with zipfile.ZipFile(source) as archive:
        opf = archive.read("EPUB/content.opf").decode("utf-8")
    assert "<spine" in opf
    head, _, tail = opf.partition("<spine")
    tag, _, rest = tail.partition(">")
    _replace_epub_member(
        source,
        "EPUB/content.opf",
        f'{head}<spine{tag}><!-- page-progression-direction="rtl" -->{rest}',
    )

    loader = EPUBBookLoader(
        str(source),
        RecordingModel,
        key="",
        resume=False,
        language="zh-hans",
    )
    # Pin the trigger: if ebooklib stops parsing the comment into the spine,
    # this fails and the filter below is dead code to remove.
    assert (None, None) in loader.origin_book.spine

    rebuilt = loader._make_new_book(loader.origin_book)
    assert (None, None) not in rebuilt.spine
    # Every real entry survives. Compared over the tuples only, because a
    # spine entry may also arrive as an item object rather than an idref.
    assert [entry for entry in rebuilt.spine if isinstance(entry, tuple)] == [
        entry for entry in loader.origin_book.spine if entry[0]
    ]
    assert all(
        entry[0] if isinstance(entry, tuple) else entry for entry in rebuilt.spine
    )

    epub.write_epub(str(tmp_path / "rebuilt.epub"), rebuilt)


def test_empty_nav_is_generated_with_rebuilt_book_title(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "book.epub"
    _write_epub(source, [("chapter.xhtml", ["Chapter body"])])
    loader = EPUBBookLoader(
        str(source),
        RecordingModel,
        key="",
        resume=False,
        language="zh-hans",
    )
    rebuilt = loader._make_new_book(loader.origin_book)
    rebuilt.add_item(epub.EpubNav(uid="nav", file_name="generated-nav.xhtml"))

    output = tmp_path / "generated.epub"
    epub.write_epub(str(output), rebuilt)

    with zipfile.ZipFile(output) as archive:
        nav = archive.read("EPUB/generated-nav.xhtml").decode("utf-8")
    assert rebuilt.title == "Correctness baseline"
    # the rebuilt book's language is the language it was made to be read in
    assert rebuilt.language == "zh-hans"
    assert "<h2>Correctness baseline</h2>" in nav


def test_epub_sequential_bilingual_output_preserves_source(tmp_path, monkeypatch):
    loader, output = _make_loader(
        tmp_path, monkeypatch, [("one.xhtml", ["one", "two"])]
    )

    loader.make_bilingual_book()

    content = _document_texts(output)["one.xhtml"]
    assert content.index("one") < content.index("&lt;T&gt;one&lt;/T&gt;")
    assert content.index("two") < content.index("&lt;T&gt;two&lt;/T&gt;")


def test_epub_sequential_single_translate_removes_source(tmp_path, monkeypatch):
    loader, output = _make_loader(
        tmp_path, monkeypatch, [("one.xhtml", ["source only"])]
    )
    loader.single_translate = True

    loader.make_bilingual_book()

    content = _document_texts(output)["one.xhtml"]
    assert content.count("<p>") == 1
    assert "&lt;T&gt;source only&lt;/T&gt;" in content


def test_epub_sequential_honors_excluded_files(tmp_path, monkeypatch):
    loader, output = _make_loader(
        tmp_path,
        monkeypatch,
        [("keep.xhtml", ["translate me"]), ("skip.xhtml", ["do not translate"])],
    )
    loader.exclude_filelist = "skip.xhtml"

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["translate me"]
    documents = _document_texts(output)
    assert "&lt;T&gt;translate me&lt;/T&gt;" in documents["keep.xhtml"]
    assert "&lt;T&gt;do not translate&lt;/T&gt;" not in documents["skip.xhtml"]


def test_epub_sequential_honors_only_filelist(tmp_path, monkeypatch):
    loader, output = _make_loader(
        tmp_path,
        monkeypatch,
        [("keep.xhtml", ["translate me"]), ("skip.xhtml", ["do not translate"])],
    )
    loader.only_filelist = "keep.xhtml"

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["translate me"]
    # --only_filelist narrows the *work*, not the book. Dropping the other
    # documents leaves the copied spine, the nav and the NCX pointing at
    # files the package no longer contains (epubcheck RSC-007).
    documents = _document_texts(output)
    assert set(documents) >= {"keep.xhtml", "skip.xhtml"}
    assert "do not translate" in documents["skip.xhtml"]
    assert "&lt;T&gt;do not translate&lt;/T&gt;" not in documents["skip.xhtml"]


def test_epub_parallel_only_filelist_keeps_the_untranslated_documents(
    tmp_path, monkeypatch
):
    # same invariant on the parallel path, which assembles the output book
    # from a different list
    loader, output = _make_loader(
        tmp_path,
        monkeypatch,
        [
            ("keep.xhtml", ["translate me"]),
            ("skip.xhtml", ["do not translate"]),
            ("also.xhtml", ["nor this"]),
        ],
        parallel_workers=4,
    )
    loader.only_filelist = "keep.xhtml"

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["translate me"]
    documents = _document_texts(output)
    assert set(documents) >= {"keep.xhtml", "skip.xhtml", "also.xhtml"}
    assert "nor this" in documents["also.xhtml"]


def test_epub_sequential_test_num_limits_requests(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one", "two", "three"])],
        is_test=True,
        test_num=2,
    )

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["one", "two"]


def test_epub_sequential_context_follows_document_order(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one", "two", "three"])],
        context_flag=True,
    )

    loader.make_bilingual_book()

    assert loader.translate_model.contexts_at_call == [[], ["one"], ["one", "two"]]


def test_epub_translation_plan_has_stable_document_order_ids(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["one", "two"]), ("b.xhtml", ["three"])],
    )
    document_items = list(loader.origin_book.get_items_of_type(ITEM_DOCUMENT))

    first = loader._build_translation_plan(document_items, ["p"])
    second = loader._build_translation_plan(document_items, ["p"])
    first_jobs = [job for chapter in first for job in chapter.jobs]
    second_jobs = [job for chapter in second for job in chapter.jobs]

    assert [job.global_index for job in first_jobs] == [0, 1, 2]
    assert [job.document_index for job in first_jobs] == [0, 0, 1]
    assert [job.node_index for job in first_jobs] == [0, 1, 0]
    assert [job.context_group for job in first_jobs] == [
        "a.xhtml",
        "a.xhtml",
        "b.xhtml",
    ]
    assert [job.job_id for job in first_jobs] == [job.job_id for job in second_jobs]
    assert len({job.job_id for job in first_jobs}) == 3


def test_epub_translation_plan_assigns_stable_batch_indexes(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one", "two", "three", "four", "five"])],
    )
    loader.block_size = 2
    document_items = list(loader.origin_book.get_items_of_type(ITEM_DOCUMENT))

    [chapter] = loader._build_translation_plan(document_items, ["p"])

    assert [job.batch_index for job in chapter.jobs] == [0, 0, 1, 1, 2]


def test_epub_sequential_accumulated_num_batches_in_document_order(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("book_maker.loader.epub_loader.num_tokens_from_text", len)
    loader, output = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one", "two", "three"])],
    )
    loader.accumulated_num = 100

    loader.make_bilingual_book()

    assert loader.translate_model.list_calls == [["one", "two", "three"]]
    content = _document_texts(output)["one.xhtml"]
    for text in ("one", "two", "three"):
        assert content.count(f"&lt;T&gt;{text}&lt;/T&gt;") == 1


def test_cross_chapter_batch_includes_each_toc_title(tmp_path, monkeypatch):
    monkeypatch.setattr("book_maker.loader.epub_loader.num_tokens_from_text", len)
    _write_epub_with_toc(tmp_path / "book.epub", [("one.xhtml", ["one"]), ("two.xhtml", ["two"])])
    loader, output = _make_loader(
        tmp_path, monkeypatch, [("one.xhtml", ["one"]), ("two.xhtml", ["two"])]
    )
    loader.translate_metadata = True
    loader.accumulated_num = 100
    loader.accumulated_num_given = True
    loader.accumulate_across_chapters = True
    loader.single_translate = True

    loader.make_bilingual_book()

    assert ["Chapter 1", "one", "Chapter 2", "two"] in loader.translate_model.list_calls
    translated = epub.read_epub(str(output))
    assert [entry.title for entry in translated.toc] == [
        "<T>Chapter 1</T>", "<T>Chapter 2</T>"
    ]
    assert translated.get_metadata("DC", "title")[0][0] == "<T>Correctness baseline</T>"
    assert "&lt;T&gt;one&lt;/T&gt;" in _document_texts(output)["one.xhtml"]


def test_default_accumulation_groups_toc_title_with_its_chapter(tmp_path, monkeypatch):
    monkeypatch.setattr("book_maker.loader.epub_loader.num_tokens_from_text", len)
    chapters = [("one.xhtml", ["one"]), ("two.xhtml", ["two"])]
    _write_epub_with_toc(tmp_path / "book.epub", chapters)
    loader, _ = _make_loader(tmp_path, monkeypatch, chapters)
    loader.translate_metadata = True
    loader.accumulated_num = 100
    loader.accumulated_num_given = True
    loader.make_bilingual_book()
    assert ["Chapter 1", "one"] in loader.translate_model.list_calls
    assert ["Chapter 2", "two"] in loader.translate_model.list_calls
    assert ["Chapter 1", "one", "Chapter 2", "two"] not in loader.translate_model.list_calls


@pytest.mark.parametrize("plan_mode", [False, True])
def test_whole_chapter_ignores_budgets_and_keeps_chapters_separate(
    tmp_path, monkeypatch, plan_mode
):
    chapters = [("one.xhtml", ["one", "two", "three"]),
                ("two.xhtml", ["four", "five"])]
    _write_epub_with_toc(tmp_path / "book.epub", chapters)
    loader, output = _make_loader(tmp_path, monkeypatch, chapters)
    loader.whole_chapter = True
    loader.translate_metadata = True
    loader.accumulated_num = 2
    loader.accumulated_num_given = True
    loader.batch_units = 1
    if plan_mode:
        loader.plan_mode = True
        loader.plan_classify = "all"
        loader.translate_tags = "auto"
    loader.make_bilingual_book()
    assert ["Chapter 1", "one", "two", "three"] in loader.translate_model.list_calls
    assert ["Chapter 2", "four", "five"] in loader.translate_model.list_calls
    # Plan mode also selects the navigation XHTML as its own document.
    assert len(loader.translate_model.list_calls) == (3 if plan_mode else 2)
    assert output.is_file()


@pytest.mark.parametrize("eventually_translates", [False, True])
def test_english_whole_chapter_does_not_accept_echoed_chinese(
    tmp_path, monkeypatch, capsys, eventually_translates
):
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    class EchoChapter(RecordingModel):
        def translate_list(self, texts):
            self.list_calls.append(list(texts))
            if eventually_translates and len(self.list_calls) == 3:
                return ["Section Three: A Mysterious Voice", "The sound came from ahead."]
            return list(texts)

    source = ["第三节  神秘的声音", "声音是从前面传来的"]
    loader, output = _make_loader(
        tmp_path, monkeypatch, [("chap_5.xhtml", source)], model=EchoChapter
    )
    loader.language = "English"
    loader.language_tag = "en"
    loader.whole_chapter = True
    if eventually_translates:
        loader.make_bilingual_book()
        assert output.exists()
        assert "Section Three: A Mysterious Voice" in _document_texts(output)["chap_5.xhtml"]
    else:
        with pytest.raises(SystemExit) as failure:
            loader.make_bilingual_book()
        assert failure.value.code == 1
        assert not output.exists()
        assert loader.p_to_save == []
        stdout = capsys.readouterr().out
        assert "chap_5.xhtml" in stdout
        assert "Bilingual book saved:" not in stdout
    assert loader.translate_model.list_calls == [source] * 3
    assert loader.translate_model.calls == []


@pytest.mark.parametrize("previous_cross", [False, True])
def test_whole_chapter_failure_saves_completed_chapters_for_resume(tmp_path, monkeypatch, previous_cross):
    from book_maker.translator.base_translator import BatchMismatch

    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    class FailingChapter(RecordingModel):
        def translate_list(self, texts):
            if "six" in texts:
                self.list_calls.append(list(texts))
                raise BatchMismatch("incomplete chapter response")
            return super().translate_list(texts)

    chapters = [("one.xhtml", ["one", "two"]), ("two.xhtml", ["six", "ten"])]
    loader, output = _make_loader(tmp_path, monkeypatch, chapters, model=FailingChapter)
    loader.whole_chapter = not previous_cross
    if previous_cross:
        monkeypatch.setattr("book_maker.loader.epub_loader.num_tokens_from_text", len)
        loader.accumulated_num = 6
        loader.accumulate_across_chapters = True
    with pytest.raises(SystemExit) as failure:
        loader.make_bilingual_book()
    assert failure.value.code == 1
    assert not output.exists()
    assert loader.translate_model.list_calls == [["one", "two"]] + [["six", "ten"]] * 3
    with open(loader.bin_path, "rb") as checkpoint:
        saved = pickle.load(checkpoint)
    assert saved["translations"] == ["<T>one</T>", "<T>two</T>"]

    resumed, _ = _make_loader(tmp_path, monkeypatch, chapters, resume=True)
    resumed.whole_chapter = True
    resumed.make_bilingual_book()
    assert resumed.translate_model.list_calls == [["six", "ten"]]
    assert output.exists()


def test_cross_chapter_budget_exact_fit_and_oversized_unit(tmp_path, monkeypatch):
    monkeypatch.setattr("book_maker.loader.epub_loader.num_tokens_from_text", len)
    loader, output = _make_loader(
        tmp_path, monkeypatch,
        [("one.xhtml", ["aa"]), ("two.xhtml", ["bbb", "oversized", "cc"])],
    )
    loader.accumulated_num = 5
    loader.accumulate_across_chapters = True
    loader.make_bilingual_book()
    assert loader.translate_model.list_calls == [["aa", "bbb"]]
    assert loader.translate_model.calls == ["aa", "bbb", "oversized", "cc"]
    contents = _document_texts(output)
    assert "&lt;T&gt;aa&lt;/T&gt;" in contents["one.xhtml"]
    assert "&lt;T&gt;bbb&lt;/T&gt;" not in contents["one.xhtml"]
    assert "&lt;T&gt;oversized&lt;/T&gt;" in contents["two.xhtml"]


def test_cross_chapter_stream_uses_spine_order(tmp_path, monkeypatch):
    chapters = [("one.xhtml", ["one"]), ("two.xhtml", ["two"])]
    _write_epub_with_toc(tmp_path / "book.epub", chapters)
    loader, _ = _make_loader(tmp_path, monkeypatch, chapters)
    loader.origin_book.spine = list(reversed(loader.origin_book.spine))
    loader.translate_metadata = True
    loader.accumulated_num = 100
    loader.accumulate_across_chapters = True
    loader.make_bilingual_book()
    assert loader.translate_model.list_calls[0] == [
        "Chapter 2", "two", "Chapter 1", "one"
    ]


@pytest.mark.parametrize("cross_chapters", [False, True])
def test_distinct_toc_label_keeps_its_own_translation(tmp_path, monkeypatch, cross_chapters):
    chapters = [("one.xhtml", ["body"])]
    source = tmp_path / "book.epub"
    _write_epub_with_toc(source, chapters)
    with zipfile.ZipFile(source) as archive:
        content = archive.read("EPUB/one.xhtml").decode("utf-8")
    _replace_epub_member(source, "EPUB/one.xhtml", content.replace(
        "<p>body</p>", "<h1>Visible heading</h1><p>body</p>"
    ))
    loader, output = _make_loader(tmp_path, monkeypatch, chapters)
    loader.translate_metadata = True
    loader.translate_tags = "h1,p"
    loader.single_translate = True
    loader.accumulated_num = 100
    loader.accumulate_across_chapters = cross_chapters
    loader.make_bilingual_book()
    assert epub.read_epub(str(output)).toc[0].title == "<T>Chapter 1</T>"
    assert loader.translate_model.calls.count("Chapter 1") == 1


def test_metadata_localization_preserves_excluded_document_language(tmp_path, monkeypatch):
    chapters = [("one.xhtml", ["one"]), ("skip.xhtml", ["source"])]
    _write_epub_with_toc(tmp_path / "book.epub", chapters)
    loader, output = _make_loader(tmp_path, monkeypatch, chapters)
    loader.translate_metadata = True
    loader.single_translate = True
    loader.exclude_filelist = "skip.xhtml"
    original = loader.origin_book.get_item_with_href("skip.xhtml").content
    loader.make_bilingual_book()
    with zipfile.ZipFile(output) as archive:
        assert archive.read("EPUB/skip.xhtml") == original


@pytest.mark.parametrize("single", [False, True])
def test_nav_heading_translation_is_not_sent_for_translation_again(tmp_path, monkeypatch, single):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "book.epub"
    _write_epub_with_custom_nav(source)
    loader = EPUBBookLoader(str(source), RecordingModel, "", False, "zh-hans")
    loader.translate_tags = "h2,p"
    loader.single_translate = single
    loader.make_bilingual_book()
    assert not any(text.startswith("<T>") for text in loader.translate_model.calls)
    from bs4 import BeautifulSoup
    with zipfile.ZipFile(tmp_path / "book_bilingual.epub") as archive:
        nav = BeautifulSoup(archive.read("EPUB/toc.xhtml"), "html.parser")
    expected = "<T>Source table of contents</T>"
    if not single:
        expected = f"Source table of contents / {expected}"
    assert nav.select_one("nav h2").get_text(" ", strip=True) == expected


def test_documents_without_toc_get_localized_head_titles(tmp_path, monkeypatch):
    source = tmp_path / "book.epub"
    _write_epub(source, [("one.xhtml", ["body"])])
    with zipfile.ZipFile(source) as archive:
        content = archive.read("EPUB/one.xhtml").decode("utf-8")
    _replace_epub_member(source, "EPUB/one.xhtml", content.replace(
        "<body>", "<head><title>Chapter 1</title></head><body>"
    ))
    loader, output = _make_loader(tmp_path, monkeypatch, [("one.xhtml", ["body"])])
    loader.translate_metadata = True
    loader.single_translate = True
    assert not loader.origin_book.toc
    loader.make_bilingual_book()
    with zipfile.ZipFile(output) as archive:
        content = archive.read("EPUB/one.xhtml").decode("utf-8")
    assert "<title>&lt;T&gt;Chapter 1&lt;/T&gt;</title>" in content


def test_metadata_only_resume_checks_language_prompt_and_model(tmp_path, monkeypatch):
    loader, _ = _make_loader(tmp_path, monkeypatch, [("one.xhtml", ["body"])])
    loader.resume = True
    loader._localized_cache = {"title": "cached translation"}
    loader._resume_run_fingerprint = "another-language"
    with pytest.raises(SystemExit):
        loader._check_resume_run_fingerprint()


@pytest.mark.parametrize("cross_chapters", [False, True])
def test_empty_title_reply_does_not_produce_finished_book(tmp_path, monkeypatch, cross_chapters):
    chapters = [("one.xhtml", ["body"])]
    _write_epub_with_toc(tmp_path / "book.epub", chapters)

    class EmptyTitle(RecordingModel):
        def translate(self, text):
            return "" if text == "Chapter 1" else super().translate(text)

    loader, output = _make_loader(tmp_path, monkeypatch, chapters, model=EmptyTitle)
    loader.translate_metadata = True
    loader.accumulate_across_chapters = cross_chapters
    if cross_chapters:
        loader.accumulated_num = 100
    with pytest.raises(SystemExit) as error:
        loader.make_bilingual_book()
    assert error.value.code == 1
    assert not output.exists()


def test_parallel_accumulation_reuses_heading_for_toc(tmp_path, monkeypatch):
    source = tmp_path / "book.epub"
    chapters = [("one.xhtml", ["body one"]), ("two.xhtml", ["body two"])]
    _write_epub_with_toc(source, chapters)
    for index, (name, _) in enumerate(chapters, 1):
        with zipfile.ZipFile(source) as archive:
            raw = archive.read(f"EPUB/{name}").decode("utf-8")
        _replace_epub_member(source, f"EPUB/{name}", raw.replace(
            "<body>", f"<body><h1>Chapter {index}</h1>"
        ))
    loader, output = _make_loader(tmp_path, monkeypatch, chapters, parallel_workers=2)
    loader.translate_metadata = True
    loader.translate_tags = "h1,p"
    loader.accumulated_num = 100
    loader.single_translate = True
    loader.make_bilingual_book()
    assert loader.translate_model.calls.count("Chapter 1") == 1
    assert loader.translate_model.calls.count("Chapter 2") == 1
    assert [entry.title for entry in epub.read_epub(str(output)).toc] == [
        "<T>Chapter 1</T>", "<T>Chapter 2</T>"
    ]


def test_alias_toc_labels_for_same_target_remain_distinct(tmp_path, monkeypatch):
    from bs4 import BeautifulSoup
    chapters = [("one.xhtml", ["body"])]
    _write_epub_with_toc(tmp_path / "book.epub", chapters)
    loader, output = _make_loader(tmp_path, monkeypatch, chapters)
    loader.origin_book.toc = (
        *loader.origin_book.toc, epub.Link("one.xhtml", "Alternate label", "alias")
    )
    nav = next(item for item in loader.origin_book.get_items() if isinstance(item, epub.EpubNav))
    soup = BeautifulSoup(nav.content, "html.parser")
    alias = soup.new_tag("a", href="one.xhtml")
    alias.string = "Alternate label"
    row = soup.new_tag("li")
    row.append(alias)
    soup.select_one("nav ol").append(row)
    nav.content = soup.encode("utf-8")
    loader.translate_metadata = True
    loader.accumulated_num = 100
    loader.single_translate = True
    loader.make_bilingual_book()
    with zipfile.ZipFile(output) as archive:
        result = BeautifulSoup(archive.read("EPUB/nav.xhtml"), "html.parser")
    assert [link.get_text() for link in result.select("nav a")] == [
        "<T>Chapter 1</T>", "<T>Alternate label</T>"
    ]


def test_title_only_chapter_is_localized_when_selected(tmp_path, monkeypatch):
    chapters = [("one.xhtml", [])]
    _write_epub_with_toc(tmp_path / "book.epub", chapters)
    loader, output = _make_loader(tmp_path, monkeypatch, chapters)
    loader.translate_metadata = True
    loader.accumulated_num = 100
    loader.accumulated_num_given = True
    loader.make_bilingual_book()
    assert epub.read_epub(str(output)).toc[0].title == (
        "Chapter 1 / <T>Chapter 1</T>"
    )
    assert "&lt;T&gt;Chapter 1&lt;/T&gt;" in _document_texts(output)["one.xhtml"]


def test_metadata_opt_out_leaves_source_toc(tmp_path, monkeypatch):
    _write_epub_with_toc(tmp_path / "book.epub", [("one.xhtml", ["one"])])
    loader, output = _make_loader(
        tmp_path, monkeypatch, [("one.xhtml", ["one"])]
    )
    loader.accumulated_num = 100
    loader.accumulate_across_chapters = True
    loader.make_bilingual_book()
    assert [entry.title for entry in epub.read_epub(str(output)).toc] == ["Chapter 1"]


def test_default_localizes_toc_and_document_title(tmp_path, monkeypatch):
    _write_epub_with_toc(tmp_path / "book.epub", [("one.xhtml", ["one"])], metadata=True)
    loader, output = _make_loader(
        tmp_path, monkeypatch, [("one.xhtml", ["one"])]
    )
    loader.translate_metadata = True
    loader.single_translate = True
    loader.make_bilingual_book()

    result = epub.read_epub(str(output))
    assert result.toc[0].title == "<T>Chapter 1</T>"
    assert result.get_metadata("DC", "description")[0][0] == "<T>A source synopsis</T>"
    assert result.get_metadata("DC", "subject")[0][0] == "<T>Fantasy</T>"
    assert result.get_metadata("DC", "language")[0][0] == "zh-hans"
    with zipfile.ZipFile(output) as archive:
        chapter = archive.read("EPUB/one.xhtml").decode("utf-8")
        ncx = archive.read("EPUB/toc.ncx").decode("utf-8")
        nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
    assert "<title>&lt;T&gt;Chapter 1&lt;/T&gt;</title>" in chapter
    assert 'content="&lt;T&gt;Chapter 1&lt;/T&gt;"' in chapter
    assert 'lang="zh-hans"' in chapter
    assert "&lt;T&gt;Chapter 1&lt;/T&gt;" in ncx
    assert "&lt;T&gt;Chapter 1&lt;/T&gt;" in nav


def test_plan_mode_cross_chapter_batch(tmp_path, monkeypatch):
    monkeypatch.setattr("book_maker.loader.epub_loader.num_tokens_from_text", len)
    _write_epub_with_toc(tmp_path / "book.epub", [("one.xhtml", ["one"]), ("two.xhtml", ["two"])])
    loader, output = _make_loader(
        tmp_path, monkeypatch, [("one.xhtml", ["one"]), ("two.xhtml", ["two"])]
    )
    loader.translate_metadata = True
    loader.plan_mode = True
    loader.plan_classify = "all"
    loader.translate_tags = "auto"
    loader.accumulated_num = 100
    loader.accumulated_num_given = True
    loader.accumulate_across_chapters = True
    loader.make_bilingual_book()
    assert any("one" in call and "two" in call for call in loader.translate_model.list_calls)
    assert [entry.title for entry in epub.read_epub(str(output)).toc] == [
        "Chapter 1 / <T>Chapter 1</T>",
        "Chapter 2 / <T>Chapter 2</T>",
    ]


def test_fragment_toc_titles_reuse_body_heading_translations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    book = epub.EpubBook()
    book.set_identifier("fragment-fixture")
    book.set_title("Book")
    book.set_language("en")
    chapter = epub.EpubHtml(uid="chapter", file_name="one.xhtml", title="Alpha")
    chapter.content = (
        '<html><head><title>Old title</title></head><body>'
        '<h1 id="a">Alpha</h1><p>first</p>'
        '<h1 id="b">Beta</h1><p>second</p></body></html>'
    )
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (
        epub.Link("one.xhtml#a", "Alpha", "one"),
        epub.Link("one.xhtml#b", "Beta", "two"),
    )
    book.spine = ["nav", chapter]
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), book)
    loader = EPUBBookLoader(str(source), RecordingModel, "", False, "zh-hans")
    loader.translate_tags = "h1,p"
    loader.accumulated_num = 100
    loader.accumulated_num_given = True
    loader.accumulate_across_chapters = True
    loader.single_translate = True
    loader.make_bilingual_book()

    assert loader.translate_model.calls.count("Alpha") == 1
    assert loader.translate_model.calls.count("Beta") == 1
    output = source.with_name("book_bilingual.epub")
    assert [entry.title for entry in epub.read_epub(str(output)).toc] == [
        "<T>Alpha</T>", "<T>Beta</T>"
    ]


def test_cross_chapter_resume_reuses_title_and_body_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr("book_maker.loader.epub_loader.num_tokens_from_text", len)
    chapters = [("one.xhtml", ["one"]), ("two.xhtml", ["two"])]
    _write_epub_with_toc(tmp_path / "book.epub", chapters)

    class StopAfterFirstBatch(RecordingModel):
        def translate_list(self, texts):
            if self.list_calls:
                raise KeyboardInterrupt
            return super().translate_list(texts)

    interrupted, _ = _make_loader(
        tmp_path, monkeypatch, chapters, model=StopAfterFirstBatch
    )
    interrupted.translate_metadata = True
    interrupted.accumulated_num = 12
    interrupted.accumulated_num_given = True
    interrupted.accumulate_across_chapters = True
    with pytest.raises(SystemExit) as exc:
        interrupted.make_bilingual_book()
    assert exc.value.code == 130
    assert interrupted.p_to_save == ["<T>one</T>"]
    recovery = tmp_path / "book_bilingual_temp.epub"
    assert [entry.title for entry in epub.read_epub(str(recovery)).toc] == [
        "Chapter 1 / <T>Chapter 1</T>", "Chapter 2"
    ]

    wrong_setting, _ = _make_loader(
        tmp_path, monkeypatch, chapters, resume=True
    )
    with pytest.raises(ValueError, match="cross-chapter setting changed"):
        wrong_setting.make_bilingual_book()

    resumed, output = _make_loader(
        tmp_path, monkeypatch, chapters, resume=True
    )
    resumed.translate_metadata = True
    resumed.accumulated_num = 12
    resumed.accumulated_num_given = True
    resumed.accumulate_across_chapters = True
    resumed.make_bilingual_book()
    assert "Chapter 1" not in resumed.translate_model.calls
    assert "one" not in resumed.translate_model.calls
    assert [entry.title for entry in epub.read_epub(str(output)).toc] == [
        "Chapter 1 / <T>Chapter 1</T>",
        "Chapter 2 / <T>Chapter 2</T>",
    ]


def test_title_only_checkpoint_preserves_untranslated_body_language(tmp_path, monkeypatch):
    from bs4 import BeautifulSoup
    monkeypatch.setattr("book_maker.loader.epub_loader.num_tokens_from_text", len)
    chapters = [("one.xhtml", ["one"])]
    _write_epub_with_toc(tmp_path / "book.epub", chapters)

    class StopOnBody(RecordingModel):
        def translate(self, text):
            if text == "one":
                raise KeyboardInterrupt
            return super().translate(text)

    loader, _ = _make_loader(tmp_path, monkeypatch, chapters, model=StopOnBody)
    original = BeautifulSoup(
        loader.origin_book.get_item_with_href("one.xhtml").content, "html.parser"
    ).html.get("lang")
    loader.translate_metadata = True
    loader.accumulated_num = 9
    loader.accumulate_across_chapters = True
    loader.single_translate = True
    with pytest.raises(SystemExit):
        loader.make_bilingual_book()
    assert loader.p_to_save == []
    assert loader._localized_cache
    with zipfile.ZipFile(tmp_path / "book_bilingual_temp.epub") as archive:
        recovery = BeautifulSoup(archive.read("EPUB/one.xhtml"), "html.parser")
    assert recovery.title.get_text() == "<T>Chapter 1</T>"
    assert recovery.p.get_text() == "one"
    assert recovery.html.get("lang") == original


def test_epub_sequential_sentence_mode_preserves_sentence_order(tmp_path, monkeypatch):
    loader, output = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["First sentence. Second sentence."])],
    )
    loader.sentence_mode = True

    loader.make_bilingual_book()

    assert loader.translate_model.list_calls == [
        ["First sentence.", "Second sentence."]
    ]
    content = _document_texts(output)["one.xhtml"]
    assert content.index("First sentence.") < content.index("Second sentence.")
    assert content.index("&lt;T&gt;First sentence.&lt;/T&gt;") < content.index(
        "&lt;T&gt;Second sentence.&lt;/T&gt;"
    )


def test_epub_sequential_interrupt_and_resume_uses_completed_prefix(
    tmp_path, monkeypatch
):
    chapters = [("one.xhtml", ["one", "two", "three"])]
    loader, _ = _make_loader(tmp_path, monkeypatch, chapters, model=InterruptingModel)

    with pytest.raises(SystemExit) as exc:
        loader.make_bilingual_book()
    # an interrupted run has no finished book, and says so (P9)
    assert exc.value.code == 130
    assert loader.p_to_save == ["<T>one</T>"]

    resumed, output = _make_loader(
        tmp_path, monkeypatch, chapters, model=RecordingModel, resume=True
    )
    resumed.make_bilingual_book()

    assert resumed.translate_model.calls == ["two", "three"]
    content = _document_texts(output)["one.xhtml"]
    for text in ("one", "two", "three"):
        assert content.count(f"&lt;T&gt;{text}&lt;/T&gt;") == 1


def test_plan_mode_interrupt_saves_progress_despite_accumulated_num(
    tmp_path, monkeypatch
):
    # The `accumulated_num == 1` guard on the interrupt save is tag-mode
    # shaped: its positional slots are unreliable mid-batch. Plan-mode
    # checkpoints record finished units only, so a batched plan run must
    # save exactly like an unbatched one.
    chapters = [("one.xhtml", ["one", "two", "three"])]
    loader, _ = _make_loader(tmp_path, monkeypatch, chapters, model=InterruptingModel)
    loader.plan_mode = True
    loader.plan_classify = "all"
    loader.accumulated_num = 800
    saved = []
    monkeypatch.setattr(loader, "_save_progress", lambda: saved.append(True))
    monkeypatch.setattr(loader, "_save_temp_book", lambda: None)

    with pytest.raises(SystemExit) as exc:
        loader.make_bilingual_book()

    assert exc.value.code == 130
    assert saved == [True]


def test_epub_sequential_batch_excludes_inline_code_content(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["before <code>secret()</code> after"])],
    )

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["before  after"]


def test_epub_sequential_translation_failure_exits_nonzero(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one"])],
        model=FailingModel,
    )

    with pytest.raises(SystemExit) as exc:
        loader.make_bilingual_book()
    assert exc.value.code != 0


def test_epub_parallel_checkpoint_order_is_deterministic(tmp_path, monkeypatch):
    OrderedCompletionModel.a_started.clear()
    OrderedCompletionModel.b_finished.clear()
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["chapter A"]), ("b.xhtml", ["chapter B"])],
        model=OrderedCompletionModel,
        parallel_workers=2,
    )

    loader.make_bilingual_book()

    assert loader.p_to_save == ["<T>chapter A</T>", "<T>chapter B</T>"]


def test_epub_parallel_rejects_legacy_completion_order_checkpoint(
    tmp_path, monkeypatch
):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["chapter A"]), ("b.xhtml", ["chapter B"])],
        parallel_workers=2,
    )
    with open(loader.bin_path, "wb") as checkpoint:
        pickle.dump(["<T>chapter B</T>", "<T>chapter A</T>"], checkpoint)

    with pytest.raises(ValueError, match="Legacy EPUB resume checkpoints"):
        _make_loader(
            tmp_path,
            monkeypatch,
            [("a.xhtml", ["chapter A"]), ("b.xhtml", ["chapter B"])],
            resume=True,
            parallel_workers=2,
        )


def _downgrade_checkpoint_to_v3(loader):
    loader._save_progress()
    with open(loader.bin_path, "rb") as checkpoint:
        state = pickle.load(checkpoint)
    state["version"] = 3
    state.pop("localized_cache", None)
    state.pop("cross_chapters", None)
    original = pickle.dumps(state)
    with open(loader.bin_path, "wb") as checkpoint:
        checkpoint.write(original)
    return original


def _tag_checkpoint_v3(tmp_path, monkeypatch):
    chapters = [("one.xhtml", ["one", "two"])]
    loader, _ = _make_loader(tmp_path, monkeypatch, chapters)
    plans = loader._build_translation_plan(
        list(loader.origin_book.get_items_of_type(ITEM_DOCUMENT)), ["p"]
    )
    loader._planned_job_ids = [job.job_id for plan in plans for job in plan.jobs]
    loader.p_to_save = ["<T>one</T>"]
    return chapters, _downgrade_checkpoint_to_v3(loader)


@pytest.mark.parametrize("budget", [1, 100])
def test_v3_resume_preserves_translations_and_backs_up_before_upgrade(
    tmp_path, monkeypatch, budget
):
    chapters, original = _tag_checkpoint_v3(tmp_path, monkeypatch)
    resumed, output = _make_loader(tmp_path, monkeypatch, chapters, resume=True)
    resumed.accumulated_num = budget
    resumed.translate_metadata = True
    assert resumed.p_to_save == ["<T>one</T>"]
    assert resumed._localized_cache == {}
    with open(resumed.bin_path, "rb") as checkpoint:
        assert checkpoint.read() == original

    resumed.make_bilingual_book()

    assert "one" not in resumed.translate_model.calls
    assert resumed.translate_model.calls.count("two") == 1
    assert resumed._loaded_checkpoint_version == resumed.CHECKPOINT_VERSION
    with open(f"{resumed.bin_path}.v3.bak", "rb") as backup:
        assert backup.read() == original
    assert "&lt;T&gt;one&lt;/T&gt;" in _document_texts(output)["one.xhtml"]


@pytest.mark.parametrize("mismatch", ["source", "language"])
def test_v3_resume_keeps_identity_checks_and_leaves_rejected_cache_untouched(
    tmp_path, monkeypatch, mismatch
):
    chapters, original = _tag_checkpoint_v3(tmp_path, monkeypatch)
    resumed, _ = _make_loader(tmp_path, monkeypatch, chapters, resume=True)
    if mismatch == "source":
        item = resumed.origin_book.get_item_with_href("one.xhtml")
        item.content = item.content.replace(b"<p>one</p>", b"<p>changed</p>")
        with pytest.raises(ValueError, match="filters changed"):
            resumed.make_bilingual_book()
    else:
        resumed.language = "fr"
        with pytest.raises(SystemExit):
            resumed.make_bilingual_book()
    assert resumed.translate_model.calls == []
    with open(resumed.bin_path, "rb") as checkpoint:
        assert checkpoint.read() == original


def test_v3_plan_resume_retains_classification_fingerprint(tmp_path, monkeypatch):
    chapters = [("one.xhtml", ["one", "two"])]
    first, _ = _make_loader(tmp_path, monkeypatch, chapters)
    first.plan_mode = True
    first.translate_tags = "auto"
    first.plan_classify = "all"
    first.make_bilingual_book()
    _downgrade_checkpoint_to_v3(first)

    resumed, output = _make_loader(tmp_path, monkeypatch, chapters, resume=True)
    resumed.plan_mode = True
    resumed.translate_tags = "auto"
    resumed.plan_classify = "all"
    assert resumed._resume_plan_fingerprint == first._plan_fingerprint
    resumed.make_bilingual_book()
    assert resumed.translate_model.calls == []
    assert "&lt;T&gt;one&lt;/T&gt;" in _document_texts(output)["one.xhtml"]


def test_epub_parallel_saves_when_committed_prefix_crosses_interval(
    tmp_path, monkeypatch
):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one"])],
        parallel_workers=2,
    )
    saved_lengths = []
    monkeypatch.setattr(
        loader, "_save_progress", lambda: saved_lengths.append(len(loader.p_to_save))
    )

    for index in range(1, 22):
        loader._record_translation_result(index, str(index))
    assert saved_lengths == []

    loader._record_translation_result(0, "0")

    assert len(loader.p_to_save) == 22
    assert saved_lengths == [22]


def test_epub_resume_rejects_changed_source_job_identity(tmp_path, monkeypatch):
    chapters = [("one.xhtml", ["one", "two"])]
    loader, _ = _make_loader(tmp_path, monkeypatch, chapters)
    document_items = list(loader.origin_book.get_items_of_type(ITEM_DOCUMENT))
    plans = loader._build_translation_plan(document_items, ["p"])
    loader._planned_job_ids = [job.job_id for plan in plans for job in plan.jobs]
    loader.p_to_save = ["<T>one</T>"]
    loader._save_progress()

    _write_epub(tmp_path / "book.epub", [("one.xhtml", ["changed", "two"])])
    resumed, _ = _make_loader(tmp_path, monkeypatch, chapters, resume=True)

    with pytest.raises(ValueError, match="EPUB or translation filters changed"):
        resumed.make_bilingual_book()


def test_epub_temp_book_replays_the_filtered_translation_plan(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("skip.xhtml", ["skip me"]), ("keep.xhtml", ["translate me"])],
        parallel_workers=2,
    )
    loader.exclude_filelist = "skip.xhtml"
    loader.p_to_save = ["<T>translate me</T>"]

    loader._save_temp_book()

    documents = _document_texts(tmp_path / "book_bilingual_temp.epub")
    assert "&lt;T&gt;translate me&lt;/T&gt;" not in documents["skip.xhtml"]
    assert "&lt;T&gt;translate me&lt;/T&gt;" in documents["keep.xhtml"]


def test_epub_parallel_interrupt_resumes_from_contiguous_prefix(tmp_path, monkeypatch):
    ParallelInterruptingModel.first_finished.clear()
    chapters = [("a.xhtml", ["chapter A"]), ("b.xhtml", ["chapter B"])]
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        chapters,
        model=ParallelInterruptingModel,
        parallel_workers=2,
    )

    with pytest.raises(SystemExit) as exc:
        loader.make_bilingual_book()
    assert exc.value.code == 130
    assert loader.p_to_save == ["<T>chapter A</T>"]

    resumed, output = _make_loader(
        tmp_path,
        monkeypatch,
        chapters,
        model=RecordingModel,
        resume=True,
        parallel_workers=2,
    )
    resumed.make_bilingual_book()

    assert resumed.translate_model.calls == ["chapter B"]
    documents = _document_texts(output)
    assert documents["a.xhtml"].count("&lt;T&gt;chapter A&lt;/T&gt;") == 1
    assert documents["b.xhtml"].count("&lt;T&gt;chapter B&lt;/T&gt;") == 1


def test_epub_parallel_honors_excluded_files(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("keep.xhtml", ["translate me"]), ("skip.xhtml", ["do not translate"])],
        parallel_workers=2,
    )
    loader.exclude_filelist = "skip.xhtml"

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["translate me"]


def test_epub_parallel_test_num_limits_requests(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["one", "two"]), ("b.xhtml", ["three", "four"])],
        parallel_workers=2,
        is_test=True,
        test_num=1,
    )

    loader.make_bilingual_book()

    assert len(loader.translate_model.calls) == 1


@pytest.mark.xfail(
    strict=True,
    reason="EPUB chapters currently share the same mutable translator instance",
)
def test_epub_parallel_creates_an_isolated_translator_per_chapter(
    tmp_path, monkeypatch
):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["one"]), ("b.xhtml", ["two"])],
        parallel_workers=2,
        context_flag=True,
    )

    first = loader._create_chapter_translator()
    second = loader._create_chapter_translator()

    assert first is not loader.translate_model
    assert second is not loader.translate_model
    assert first is not second


@pytest.mark.xfail(
    strict=True,
    reason="parallel chapter errors are swallowed and the original chapter is emitted",
)
def test_epub_parallel_propagates_chapter_failures(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["one"]), ("b.xhtml", ["two"])],
        model=FailingModel,
        parallel_workers=2,
    )

    with pytest.raises(RuntimeError, match="cannot translate"):
        loader.make_bilingual_book()


@pytest.mark.xfail(
    strict=True,
    reason="parallel EPUB processing still bypasses block_size batch translation",
)
def test_epub_parallel_honors_block_size(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["one", "two"]), ("b.xhtml", ["three", "four"])],
        parallel_workers=2,
    )
    loader.block_size = 2

    loader.make_bilingual_book()

    assert sorted(loader.translate_model.list_calls) == [
        ["one", "two"],
        ["three", "four"],
    ]
    assert sorted(loader.translate_model.calls) == ["four", "one", "three", "two"]


# --------------------------------------------- EPUB validity of the output
# Three defects the 45-book corpus gate caught with epubcheck. Pinned here
# too: the corpus gate is slow and opt-in, and these are cheap to check.


def _bilingual_soup(html, **loader_kwargs):
    from bs4 import BeautifulSoup as bs

    from book_maker.loader.epub_loader import EPUBBookLoader
    from book_maker.loader.helper import EPUBBookLoaderHelper
    from book_maker.loader.plan import DisplayResolver, partition_soup

    soup = bs(html, "html.parser")
    fp = partition_soup(soup, DisplayResolver([]), "x.xhtml")
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = type(
        "M", (), {"TRANSLATION_ERROR_MARKER": None, "_fatal_error_detected": False}
    )()
    loader.exclude_translate_tags = "sup,code"
    loader.helper = EPUBBookLoaderHelper(loader.translate_model, 1, "", False)
    for unit in fp.units:
        loader._insert_plan_translation(unit, f"T[{unit.text}]", "", False)
    return soup


def test_bilingual_copy_does_not_duplicate_ids():
    # epubcheck RSC-005: two elements answering to one fragment identifier,
    # and an internal link that may land on the translation instead of the
    # passage it cites
    soup = _bilingual_soup(
        '<html><body><p id="p1">Hello <span id="s1">world</span></p></body></html>'
    )
    for attr in ("p1", "s1"):
        assert len(soup.find_all(id=attr)) == 1, f"duplicate id {attr}"
    assert "T[Hello world]" in soup.get_text()


def test_nav_translations_go_inside_their_element():
    # an EPUB 3 nav document allows one heading before its <ol> and nothing
    # but <a>/<span> inside an <li>; a translated sibling is invalid there
    soup = _bilingual_soup(
        "<html><body><nav epub:type='toc'><h2>Contents</h2>"
        "<ol><li><a href='c1.xhtml'>Chapter 1</a></li></ol></nav></body></html>"
    )
    nav = soup.find("nav")
    assert len(nav.find_all("h2")) == 1, "a second heading breaks nav grammar"
    assert len(nav.find_all("li")) == 1
    assert nav.find("li").find_all(True, recursive=False) == nav.find("li").find_all(
        ["a", "span"], recursive=False
    )
    assert "T[Contents]" in nav.h2.get_text()
    assert "T[Chapter 1]" in nav.find("a").get_text()


def test_figcaption_translation_stays_inside_the_caption():
    # <figure> accepts exactly one <figcaption>
    soup = _bilingual_soup(
        "<html><body><figure><img src='x.png'/>"
        "<figcaption>A tree</figcaption></figure></body></html>"
    )
    assert len(soup.find_all("figcaption")) == 1
    assert "T[A tree]" in soup.find("figcaption").get_text()


# The NCX-item and OPF-<link> defects this file used to pin are covered by
# test_epub_output_validity.py, whose `_rebuilder` gives `_make_new_book` the
# source book, language and mode its derived identity now needs.


def test_a_failed_final_write_exits_nonzero(tmp_path, monkeypatch):
    """A run whose output cannot be written has failed; exiting 0 tells
    every caller the opposite, with no artifact to contradict it."""
    loader, output = _make_loader(
        tmp_path, monkeypatch, [("chapter.xhtml", ["Chapter body"])]
    )
    writes = []

    def failing_write(name, book, options=None):
        writes.append(name)
        raise OSError("synthetic write failure")

    monkeypatch.setattr("book_maker.loader.epub_loader.epub.write_epub", failing_write)

    with pytest.raises(SystemExit) as excinfo:
        loader.make_bilingual_book()

    assert writes, "the failure must come from the final write, not earlier"
    assert excinfo.value.code == 1
    assert not output.exists()


def test_the_bar_shows_usage_once_a_request_reported_it():
    # the meter replaces the old "no cached token after 10 requests" guard:
    # the operator watches cached= on the bar instead of being warned
    from book_maker.loader.epub_loader import EPUBBookLoader

    loader = EPUBBookLoader.__new__(EPUBBookLoader)

    class Bar:
        postfix = None

        def set_postfix(self, postfix, refresh=True):
            self.postfix = postfix

    class Silent:
        pass

    loader.translate_model = Silent()  # a translator without a meter
    bar = Bar()
    loader._show_usage(bar)
    assert bar.postfix is None and loader._usage_suffix() == ""

    class Metered:
        def usage_postfix(self):
            return {"in": "2.0k", "out": "400", "cached": "1.0k"}

    loader.translate_model = Metered()
    loader._show_usage(bar)
    assert bar.postfix == {"in": "2.0k", "out": "400", "cached": "1.0k"}
    assert loader._usage_suffix() == " in=2.0k out=400 cached=1.0k"


def test_a_broken_meter_never_stops_the_bar():
    """The bar hooks decorate; a meter that raises must not end a paid run."""
    from book_maker.loader.epub_loader import EPUBBookLoader

    loader = EPUBBookLoader.__new__(EPUBBookLoader)

    class Broken:
        def usage_postfix(self):
            raise RuntimeError("meter")

        def usage_summary(self):
            raise RuntimeError("meter")

    class Bar:
        def set_postfix(self, postfix, refresh=True):
            raise RuntimeError("bar")

    loader.translate_model = Broken()
    loader._show_usage(Bar())
    assert loader._usage_suffix() == ""
    loader._print_usage()
