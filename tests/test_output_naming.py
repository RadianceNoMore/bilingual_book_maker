from pathlib import Path
from types import SimpleNamespace

import pytest

from book_maker.cli import parse_args
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.output_naming import safe_title_stem
from book_maker.translation_quality import UntranslatedTextError


@pytest.mark.parametrize("title,expected", [
    ('Grey: A / Case? "Book"', "Grey A Case Book"),
    ("../outside\\book", "outside book"),
    ("  A\nBook. ", "A Book"),
    ("CON", "_CON"),
    ("nul.txt", "_nul.txt"),
    ("LPT9", "_LPT9"),
    ("Étoile 星", "Étoile 星"),
    ("a" * 150, "a" * 120),
])
def test_filename_is_one_portable_component(title, expected):
    assert safe_title_stem(title) == expected


def test_unusable_translation_is_rejected():
    with pytest.raises(ValueError):
        safe_title_stem("...\x00")


def loader_for(tmp_path):
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.output_dir = tmp_path / "output"
    loader.output_dir.mkdir()
    loader.translate_filename = True
    loader.is_test = False
    loader.language = "en"
    loader.source_lang = "zh"
    loader.translate_model = SimpleNamespace(TRANSLATION_ERROR_MARKER=None)
    loader._localized_cache = {}
    loader._save_progress = lambda: None
    return loader


def test_filename_translation_is_cached_and_checkpoint_identity_is_unchanged(tmp_path):
    loader = loader_for(tmp_path)
    loader.epub_name = str(tmp_path / "input" / "格蕾事件簿.epub")
    loader.bin_path = str(tmp_path / "input" / ".格蕾事件簿.temp.bin")
    calls = []
    saves = []
    loader._save_progress = lambda: saves.append(dict(loader._localized_cache))

    def translate(texts):
        calls.append(texts)
        return ["Grey: Casebook"]

    loader._translate_texts_aligned = translate
    name = str(Path(loader.epub_name).with_suffix(""))
    for _ in range(2):
        final = loader._final_output_name(name)
        assert loader.output_path(f"{final}_bilingual.epub") == str(
            loader.output_dir / "Grey Casebook_bilingual.epub"
        )
    assert calls == [["格蕾事件簿"]]
    assert saves == [{"filename:格蕾事件簿": "Grey: Casebook"}]
    assert loader.epub_name.endswith("格蕾事件簿.epub")
    assert loader.bin_path.endswith(".格蕾事件簿.temp.bin")


@pytest.mark.parametrize("enabled,preview", [(False, False), (True, True)])
def test_default_and_preview_do_not_request_filename_translation(tmp_path, enabled, preview):
    loader = loader_for(tmp_path)
    loader.translate_filename = enabled
    loader.is_test = preview
    loader._translate_texts_aligned = lambda _: pytest.fail("unexpected API request")
    assert loader._final_output_name("original") == "original"


def test_collision_preserves_existing_output(tmp_path):
    loader = loader_for(tmp_path)
    loader._translate_texts_aligned = lambda _: ["Grey Casebook"]
    existing = loader.output_dir / "Grey Casebook_bilingual.epub"
    existing.write_bytes(b"existing book")
    with pytest.raises(FileExistsError, match="--output-dir"):
        loader._final_output_name("格蕾事件簿")
    assert existing.read_bytes() == b"existing book"


def test_untranslated_filename_uses_same_validation_as_book(tmp_path):
    loader = loader_for(tmp_path)
    loader._translate_texts_aligned = lambda texts: texts
    with pytest.raises(UntranslatedTextError):
        loader._final_output_name("格蕾事件簿")
    assert not loader._localized_cache


def test_cli_filename_translation_is_opt_in():
    assert not parse_args(["--book_name", "book.epub"]).translate_filename
    assert parse_args(["--book_name", "book.epub", "--translate-filename"]).translate_filename
