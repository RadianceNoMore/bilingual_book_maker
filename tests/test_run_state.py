"""Fresh runs must not inherit any generated terminology or history."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from ebooklib import epub

from book_maker.glossary import Glossary
from book_maker.glossary_gate import load_approved, scope
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.run_state import prepare_book_run


def test_fresh_run_archives_only_generated_files_and_keeps_every_byte(tmp_path):
    names = [".book.temp.bin", "book_handoff.md", "book_handoff.md.tmp",
             "book_approved_glossary.json", "book_approved_glossary.tmp", "book_glossary_review.jsonl"]
    retained = ["book.epub", "book_plan.json", "my_glossary.tsv", "book_bilingual.epub",
                "other_handoff.md", ".other.temp.bin"]
    for name in names + retained:
        (tmp_path / name).write_bytes(name.encode())
    archive = prepare_book_run(tmp_path / "book.epub", False)
    for name in names:
        assert not (tmp_path / name).exists()
        assert (archive / name).read_bytes() == name.encode()
    for name in retained:
        assert (tmp_path / name).read_bytes() == name.encode()
    assert prepare_book_run(tmp_path / "book.epub", False) is None


def test_resume_does_not_touch_generated_files(tmp_path):
    for name in ["book_handoff.md", "book_approved_glossary.json", ".book.temp.bin"]:
        (tmp_path / name).write_bytes(b"unchanged")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert prepare_book_run(tmp_path / "book.epub", True) is None
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_failed_archive_restores_already_moved_files(tmp_path, monkeypatch):
    for name in [".book.temp.bin", "book_handoff.md"]:
        (tmp_path / name).write_text(name)
    rename = Path.rename

    def locked(self, target):
        if self == tmp_path / "book_handoff.md":
            raise PermissionError("locked handoff")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(PermissionError):
        prepare_book_run(tmp_path / "book.epub", False)
    assert (tmp_path / ".book.temp.bin").read_text() == ".book.temp.bin"
    assert (tmp_path / "book_handoff.md").read_text() == "book_handoff.md"


@pytest.mark.parametrize("resume", [False, True])
def test_epub_constructor_isolates_ledger_before_first_glossary_load(tmp_path, monkeypatch, resume):
    # The loader installs ebooklib compatibility patches. Keep this fixture
    # from changing how later tests create/read their independent EPUBs.
    for cls, name in [(epub.EpubWriter, "_write_items"),
                      (epub.EpubReader, "_check_deprecated"),
                      (epub.EpubReader, "_load_spine")]:
        monkeypatch.setattr(cls, name, getattr(cls, name))
    book = epub.EpubBook()
    book.set_identifier("fresh-run-test")
    book.set_title("Test")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="Test", file_name="chapter.xhtml")
    chapter.content = "<p>测试正文。</p>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.toc = (epub.Link("chapter.xhtml", "Test", "test"),)
    book.spine = [chapter]
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), book)
    model = SimpleNamespace(handoff_path=tmp_path / "book_handoff.md", language="en",
                            source_language="zh", pinned=Glossary.parse("固定 → Pinned"),
                            learned=Glossary(), glossary_source_sha256="source-hash")
    ledger = tmp_path / "book_approved_glossary.json"
    # A pending old decision is enough to demonstrate stale ledger loading.
    ledger.write_text(json.dumps({"version": 2, "scope": scope(model), "decisions": {},
                                 "translation_chunks": {"old": {"sources": ["旧内容"],
                                                                "translations": ["Old text"]}},
                                 "chunks": [], "repairs": {}, "window_chunks": ["old"]}), encoding="utf8")
    (tmp_path / "book_handoff.md").write_text("old summary")
    (tmp_path / ".book.temp.bin").write_text("old checkpoint")
    # Checkpoint-format parsing is independent; verify that resume invokes it.
    loaded = []
    monkeypatch.setattr(EPUBBookLoader, "load_state", lambda self: loaded.append(self.bin_path))
    EPUBBookLoader(str(source), lambda *a, **kw: model, "unused", resume, "en")
    load_approved(model)
    assert model._glossary_chunks == ({"old": {"sources": ["旧内容"], "translations": ["Old text"]}} if resume else {})
    assert bool(loaded) == resume
    assert ledger.exists() == resume
    assert (tmp_path / "book_handoff.md").exists() == resume
    assert model.pinned.lookup("固定").translation == "Pinned"
