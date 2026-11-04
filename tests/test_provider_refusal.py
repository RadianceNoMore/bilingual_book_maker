import pytest

from book_maker.provider_refusal import ProviderRefusal, check_provider_refusal
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.helper import _is_retryable


REFUSALS = [
    "I'm sorry, it appears I can't help with this particular request, as it may go against my [guidelines](https://gemini.google/policy-guidelines/).",
    "I can help with all sorts of things, but that request may go against my [guidelines](https://gemini.google/policy-guidelines/).",
    "Your request may go against my [guidelines](https://gemini.google/policy-guidelines/). Would you like to try something else?",
    "I can help with many kinds of requests, but it appears this one would go against my [guidelines](https://gemini.google/policy-guidelines/). Is there something else I can try for you?",
]


@pytest.mark.parametrize('text', REFUSALS)
def test_logged_gemini_refusals_are_terminal(text):
    with pytest.raises(ProviderRefusal) as error:
        check_provider_refusal(text)
    assert text in str(error.value)
    assert not _is_retryable(error.value)


@pytest.mark.parametrize('text', [
    '<bbm-response><bbm-unit id="p0">' + REFUSALS[0] + '</bbm-unit></bbm-response>',
    '{"translation": "' + REFUSALS[1] + '"}',
    'The character visited https://gemini.google/policy-guidelines/ yesterday.',
    'Ordinary translated prose.',
])
def test_quoted_policy_or_refusal_is_not_itself_a_refusal(text):
    check_provider_refusal(text)


@pytest.mark.parametrize('kwargs', [{'refusal': 'Provider declined'}, {'finish_reason': 'content_filter'}])
def test_structured_refusals_and_filtered_finishes_are_terminal(kwargs):
    with pytest.raises(ProviderRefusal):
        check_provider_refusal('', **kwargs)


def test_epub_does_not_retry_split_or_fall_back_to_singles_on_refusal():
    class Model:
        _fatal_error_detected = False
        TRANSLATION_ERROR_MARKER = None
        def __init__(self):
            self.calls = []
        def translate_list(self, texts):
            self.calls.append(list(texts))
            raise ProviderRefusal('Provider declined')
        def translate(self, text):
            pytest.fail('Refused chunk must not fall back to single paragraphs')
    model = Model()
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = model
    with pytest.raises(ProviderRefusal):
        loader._translate_texts_aligned(['one', 'two', 'three', 'four', 'five', 'six'])
    assert len(model.calls) == 1


# ---------------------------------------------- standalone provider failure

PROVIDER_BOILERPLATE = (
    "I'm having a hard time fulfilling your request. "
    "Can I help you with something else instead?"
)
OTHER_PROVIDER_BOILERPLATE = (
    "Sorry, something went wrong. Please try your request again."
)
SEED_WRAPPER = "You are continuing a translation already in progress."


@pytest.mark.parametrize('raw', [
    PROVIDER_BOILERPLATE,
    OTHER_PROVIDER_BOILERPLATE,
    PROVIDER_BOILERPLATE.replace(chr(0x27), chr(0x2019)),
    SEED_WRAPPER + "\n\n" + OTHER_PROVIDER_BOILERPLATE,
    "```\n" + PROVIDER_BOILERPLATE + "\n```",
])
def test_standalone_provider_failure_is_classified_not_refused(raw):
    from book_maker.provider_refusal import classify_provider_reply

    category, matched = classify_provider_reply(raw)
    assert category == "provider-error"
    assert matched.replace(chr(0x2019), chr(0x27)) in (
        PROVIDER_BOILERPLATE, OTHER_PROVIDER_BOILERPLATE,
    )
    # Not an explicit policy refusal: it never raises the terminal path.
    check_provider_refusal(raw)


def test_provider_error_reply_is_retryable_and_not_dragged_through():
    from book_maker.provider_refusal import ProviderResponseUnusable

    error = ProviderResponseUnusable(OTHER_PROVIDER_BOILERPLATE)
    assert error.retryable and not error.user_facing
    assert str(error).startswith("Provider reply unusable")


@pytest.mark.parametrize('text', [
    '<bbm-response><bbm-unit id="p0">She remembered the hard time '
    'fulfilling your request. Can I help you with something else '
    'instead?</bbm-unit></bbm-response>',
    '{"translation": "Sorry, something went wrong. Please try your request again."}',
    'The novel quotes "I\'m having a hard time fulfilling your request."',
    'Ordinary translated prose.',
])
def test_quoted_provider_failure_inside_content_stays_ordinary(text):
    from book_maker.provider_refusal import classify_provider_reply

    assert classify_provider_reply(text) == ("content", "")
