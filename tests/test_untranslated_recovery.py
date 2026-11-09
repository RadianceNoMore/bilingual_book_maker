"""Untranslated replies retry with feedback, then divide without accepting source."""

from types import SimpleNamespace

import pytest

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.helper import translate_list_or_singles
from book_maker.translation_quality import UntranslatedTextError


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)


def loader_for(model):
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = model
    loader.language = "en"
    loader.source_lang = "zh"
    loader.max_chunk_attempts = 2
    return loader


def test_unchanged_chunk_retries_then_splits_with_feedback_and_keeps_order():
    calls = []
    model = SimpleNamespace()

    def translate(texts):
        calls.append((list(texts), getattr(model, "_translation_retry_feedback", "")))
        return list(texts) if len(texts) > 2 else ["English sentence " + t[-1] for t in texts]

    model.translate_list = translate
    # Long Chinese sentences ensure unchanged-source validation fires.
    texts = [f"这是需要翻译的一段中文原文，不应该原样返回。{i}" for i in range(4)]
    result = loader_for(model)._translate_cleaned_texts_aligned(texts)
    assert result == ["English sentence " + t[-1] for t in texts]
    assert [len(c[0]) for c in calls] == [4, 4, 2, 2]
    assert not calls[0][1]
    assert all("previous response copied" in c[1] for c in calls[1:])
    assert not hasattr(model, "_translation_retry_feedback")


def test_persistent_source_reply_stops_at_two_units_without_singles():
    calls = []
    model = SimpleNamespace()

    def translate(texts):
        calls.append(len(texts))
        return list(texts)

    model.translate_list = translate
    texts = ["这是一段应该翻译为英文的中文原文。"] * 4
    with pytest.raises(UntranslatedTextError):
        loader_for(model)._translate_cleaned_texts_aligned(texts)
    assert calls == [4, 4, 2, 2]
    assert not hasattr(model, "_translation_retry_feedback")


def test_feedback_restores_previous_value_after_retry():
    model = SimpleNamespace(_translation_retry_feedback="existing instructions")
    calls = []

    def translate(texts):
        calls.append(model._translation_retry_feedback)
        return texts if len(calls) == 1 else ["The translated sentence."]

    model.translate_list = translate
    assert translate_list_or_singles(
        model, ["这是一段必须翻译的中文原文。"], language="en", max_attempts=2
    ) == ["The translated sentence."]
    assert calls[0] == "existing instructions"
    assert "previous response copied" in calls[1]
    assert model._translation_retry_feedback == "existing instructions"
