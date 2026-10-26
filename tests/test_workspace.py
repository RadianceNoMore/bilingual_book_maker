import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest
from ebooklib import epub

from book_maker.workspace import prepare_source


ROOT = Path(__file__).resolve().parents[1]


def test_resume_copies_checkpoint_and_plan_without_overwriting_progress(tmp_path):
    source = tmp_path / "external" / "book.epub"
    source.parent.mkdir()
    source.write_bytes(b"source")
    checkpoint = source.with_name(".book.temp.bin")
    checkpoint.write_bytes(b"old progress")
    source.with_name("book_plan.json").write_bytes(b"plan")
    staged = prepare_source(source, tmp_path / "input", resume=True)
    saved = staged.with_name(".book.temp.bin")
    assert saved.read_bytes() == b"old progress"
    assert staged.with_name("book_plan.json").read_bytes() == b"plan"
    saved.write_bytes(b"new progress")
    prepare_source(source, tmp_path / "input", resume=True)
    assert saved.read_bytes() == b"new progress"
    source.unlink()
    assert prepare_source(source, tmp_path / "input", resume=True) == staged
    assert staged.read_bytes() == b"source"


def test_same_named_different_books_never_overwrite_sources_or_progress(tmp_path):
    one = tmp_path / "one" / "book.epub"
    two = tmp_path / "two" / "book.epub"
    for source, content in [(one, b"one"), (two, b"two")]:
        source.parent.mkdir()
        source.write_bytes(content)
    first = prepare_source(one, tmp_path / "input")
    second = prepare_source(two, tmp_path / "input")
    assert first != second
    assert first.read_bytes() == b"one"
    assert second.read_bytes() == b"two"
    assert prepare_source(two, tmp_path / "input") == second
    two.unlink()
    with pytest.raises(ValueError, match="Several working copies"):
        prepare_source(two, tmp_path / "input", resume=True)
    assert prepare_source(second, tmp_path / "input", resume=True) == second


def _run(directory, source, *args):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join([
        str(ROOT / "tests" / "hermetic"), str(ROOT)
    ])
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "make_book.py"),
         "--book_name", str(source), "--api_format", "google", *args],
        cwd=directory, env=environment, capture_output=True, encoding="utf-8",
        timeout=60,
    )


@pytest.mark.parametrize("extension,whole", [("txt", False), ("epub", False), ("epub", True)])
def test_cli_default_workspace_and_custom_output(tmp_path, extension, whole):
    external = tmp_path / "external"
    external.mkdir()
    source = external / f"book.{extension}"
    if extension == "txt":
        source.write_text("Hello world.\nAnother sentence.", encoding="utf-8")
    else:
        book = epub.EpubBook()
        book.set_identifier("workspace-test")
        book.set_title("Book")
        book.set_language("en")
        chapter = epub.EpubHtml(title="Chapter", file_name="chapter.xhtml")
        chapter.content = "<h1>Chapter</h1><p>Hello world.</p>"
        book.add_item(chapter)
        book.add_item(epub.EpubNav())
        book.toc = (chapter,)
        book.spine = ["nav", chapter]
        epub.write_epub(str(source), book)
    working = tmp_path / "working"
    working.mkdir()
    whole_flags = ["--api_format", "openai", "--key", "offline", "--whole-chapter"] if whole else []
    result = _run(working, source, "--test", "--test_num", "1", "--plan-classify", "none", *whole_flags)
    assert result.returncode == 0, result.stdout + result.stderr
    saved = working / "output" / f"book_bilingual.{extension}"
    assert saved.is_file()
    assert (working / "input" / source.name).read_bytes() == source.read_bytes()
    assert not (external / saved.name).exists()
    assert f"Bilingual book saved: {saved}" in result.stdout
    source.unlink()
    # Translate from the working copy after the original has disappeared.
    result = _run(working, working / "input" / source.name,
                  "--output-dir", "custom", "--test", "--test_num", "1",
                  "--plan-classify", "none")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (working / "custom" / saved.name).is_file()
    checkpoint = working / "input" / ".book.temp.bin"
    if extension == "epub":
        checkpoint.write_bytes(pickle.dumps({
            "version": 4, "order": "document", "translations": [],
            "job_ids": [], "cross_chapters": False,
        }))
    else:
        checkpoint.write_text("[]", encoding="utf-8")
    # The old command still works when its external path no longer exists.
    result = _run(working, source, "--resume", "--test", "--test_num", "1",
                  "--plan-classify", "none")
    assert result.returncode == 0, result.stdout + result.stderr
    assert saved.is_file()
