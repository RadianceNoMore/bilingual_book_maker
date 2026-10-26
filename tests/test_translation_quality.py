from types import SimpleNamespace

import pytest

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.helper import translate_list_or_singles
from book_maker.translation_quality import (
    UntranslatedTextError,
    untranslated_reason,
)


@pytest.mark.parametrize("source", [
    "第三节  神秘的声音",
    "四目相对，我并不害怕",
    "我们从村子的中央出发，一路向北",
])
def test_real_book_source_echoes_are_rejected(source):
    assert untranslated_reason(source, source, "English")


def test_whitespace_changes_do_not_hide_a_source_echo():
    assert untranslated_reason("第三节  神秘的声音", "第三节 神秘的声音", "en")


def test_chinese_reply_is_rejected_even_if_it_is_not_an_exact_echo():
    assert untranslated_reason("四目相对，我并不害怕", "双目对视，我并没有恐惧", "en")


@pytest.mark.parametrize("source,reply,language", [
    ("作者:smally小小", "Author: smally小小", "en"),
    ("亚德", "亚德", "en"),
    ("……", "……", "en"),
    ("Chapter 2", "Chapter 2", "en"),
    ("四目相对，我并不害怕", "四目相對，我並不害怕", "zh-hant"),
    ("森の狼人", "森の狼人", "ja"),
    ("我看着他", "I looked at him (小明).", "en"),
])
def test_names_punctuation_and_han_target_languages_are_allowed(source, reply, language):
    assert untranslated_reason(source, reply, language) is None


def test_english_target_echo_retries_the_entire_chunk(monkeypatch):
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    source = ["第三节  神秘的声音", "声音是从前面传来的"]
    calls = []
    def translate(texts):
        calls.append(list(texts))
        # Correct counts and nonempty text, but an untranslated heading.
        return ([texts[0], "The sound came from ahead."] if len(calls) < 3
                else ["Section Three: A Mysterious Voice", "The sound came from ahead."])
    model = SimpleNamespace(language="English", translate_list=translate)
    result = translate_list_or_singles(model, source)
    assert calls == [source] * 3
    assert result[0] == "Section Three: A Mysterious Voice"


def test_cached_untranslated_text_cannot_be_published():
    source = "我们从村子的中央出发，一路向北"
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.language = "English"
    loader.p_to_save = [source]
    loader._parallel_translations = {}
    job = SimpleNamespace(source_text=source, global_index=0, node_index=0)
    plan = SimpleNamespace(item=SimpleNamespace(file_name="chap_14.xhtml"), jobs=[job])
    with pytest.raises(UntranslatedTextError, match="chap_14.xhtml, unit 1"):
        loader._validate_completed_translations([plan])
    assert loader.p_to_save == [source]
