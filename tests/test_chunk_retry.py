import pytest

from book_maker.cli import build_parser
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.helper import translate_list_or_singles
from book_maker.translator.base_translator import BatchMismatch


class Model:
    _fatal_error_detected = False
    TRANSLATION_ERROR_MARKER = None

    def __init__(self, failures):
        self.failures = failures
        self.batches = []
        self.singles = []

    def translate_list(self, texts):
        self.batches.append(list(texts))
        if len(self.batches) <= self.failures:
            raise BatchMismatch("bad alignment")
        return [f"T:{text}" for text in texts]

    def translate(self, text):
        self.singles.append(text)
        return f"T:{text}"


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)


@pytest.mark.parametrize("plan", [False, True])
def test_failed_chunk_retries_intact_then_succeeds(plan):
    model = Model(failures=2)
    texts = ["one", "two", "three"]
    if plan:
        loader = EPUBBookLoader.__new__(EPUBBookLoader)
        loader.translate_model = model
        result = loader._translate_texts_aligned(texts)
    else:
        result = translate_list_or_singles(model, texts)
    assert result == [f"T:{text}" for text in texts]
    assert model.batches == [texts, texts, texts]
    assert model.singles == []


@pytest.mark.parametrize("plan", [False, True])
def test_exhaustion_never_silently_retries_paragraphs(plan):
    model = Model(failures=10)
    texts = ["one", "two"]
    with pytest.raises(BatchMismatch):
        if plan:
            loader = EPUBBookLoader.__new__(EPUBBookLoader)
            loader.translate_model = model
            loader._translate_texts_aligned(texts)
        else:
            translate_list_or_singles(model, texts)
    assert model.batches == [texts] * 3
    assert model.singles == []


def test_individual_retries_require_explicit_opt_in():
    model = Model(failures=10)
    model.retry_one_by_one = True
    assert translate_list_or_singles(model, ["one", "two"]) == ["T:one", "T:two"]
    assert model.batches == [["one", "two"]]
    assert model.singles == ["one", "two"]


def test_fatal_errors_are_not_retried():
    model = Model(failures=10)
    def fail(texts):
        model.batches.append(texts)
        raise TypeError("programming error")
    model.translate_list = fail
    with pytest.raises(TypeError):
        translate_list_or_singles(model, ["one", "two"])
    assert len(model.batches) == 1


def test_new_modes_are_opt_in():
    defaults = build_parser().parse_args([])
    assert not defaults.whole_chapter
    assert not defaults.retry_one_by_one
    chosen = build_parser().parse_args(["--whole-chapter", "--retry-one-by-one"])
    assert chosen.whole_chapter and chosen.retry_one_by_one
