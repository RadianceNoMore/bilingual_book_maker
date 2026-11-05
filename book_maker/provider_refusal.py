"""Explicit provider refusals are terminal, not paragraph alignment errors."""

import re

# Whole-response provider boilerplate. A reply consisting of one of these is
# unusable and possibly transient, but it is not proof of a policy refusal, so
# it must not raise the terminal ProviderRefusal path. Matched conservatively:
# the whole reply, after our fence and handoff-wrapper stripping, has to equal
# one message, so a book passage quoting the words can never match.
_PROVIDER_FAILURE_MESSAGES = (
    "I'm having a hard time fulfilling your request. "
    "Can I help you with something else instead?",
    "Sorry, something went wrong. Please try your request again.",
    "I encountered an error doing what you asked. Could you try again?",
    "I seem to be encountering an error. Can I try something else for you?",
)

# The marker workflow saves summaries under this fixed introduction; a saved
# seed must be classified with the wrapper removed, never with it attached.
_SEED_WRAPPER = "You are continuing a translation already in progress."

# Matched case-insensitively after typographic apostrophes are normalized, so
# a provider that re-quotes its own boilerplate with ’ instead of ' still
# matches. Deliberately only these two messages: broad rules like "any reply
# containing 'sorry'" would reject ordinary book prose.
_APOSTROPHES = ((0x2019, "'"), (0x2018, "'"))


class ProviderRefusal(Exception):
    """An explicit provider refusal; terminal, never retried or split."""

    retryable = False
    user_facing = True

    def __init__(self, message):
        self.refusal = message
        super().__init__(
            f"Provider refused this request: {message}. Progress will be saved; "
            "this request will not be retried or split automatically."
        )


class ProviderResponseUnusable(Exception):
    """A whole-response provider failure message; retryable, not a refusal.

    The reply carries no translation and no summary, so it must be reported
    and retried as an unusable provider response rather than fed to a parser,
    a validator, or a compaction seed.
    """

    retryable = True
    user_facing = False

    def __init__(self, response):
        self.response = response or ""
        self.category = classify_provider_reply(self.response)[0]
        message = repr(_normalize_error_text(self.response)[:160])
        super().__init__(f"Provider reply unusable ({self.category}): {message}")


def _normalize_error_text(text):
    for codepoint, replacement in _APOSTROPHES:
        text = text.replace(chr(codepoint), replacement)
    return re.sub(r"\s+", " ", text).strip().lower()


def _retry_text(content):
    """The comparable text of a reply, ignoring our fence and seed wrapper.

    A fenced seed or wrapper-prefixed seed is one whole message, so the
    furniture we add ourselves is removed before the equality test. Anything
    that carries markup, JSON, or a fence first is left for the parser: XML or
    JSON content that happens to quote these words is ordinary content.
    """
    if not isinstance(content, str):
        return ""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and lines[-1].strip() == "```":
            lines.pop()
        text = "\n".join(lines).strip()
    if text.startswith(_SEED_WRAPPER):
        remainder = text[len(_SEED_WRAPPER):]
        if "\n\n" in remainder:
            remainder = remainder.split("\n\n", 1)[1]
        text = remainder.strip()
    return text


def standalone_provider_error(content):
    """The boilerplate message a whole reply consists of, or ``""``.

    Only a standalone message can match: the reply must be bounded, must not
    open with markup/JSON/fences, and must equal exactly one recognized
    message, so valid translation content or narrative quoting the same
    words stays ordinary content.
    """
    candidate = _retry_text(content)
    if not candidate:
        return ""
    if len(candidate) > 2000 or candidate.startswith(("<", "{", "[", "```")):
        return ""
    normalized = _normalize_error_text(candidate)
    for message in _PROVIDER_FAILURE_MESSAGES:
        if normalized == _normalize_error_text(message):
            return message
    return ""


def _explicit_policy_refusal(content):
    """Whether the text alone matches our narrowly recognized refusals.

    Structured refusal attributes and safety finish reasons are decided by the
    caller through :func:`check_provider_refusal`; this only classifies the
    same standalone policy text that function recognizes, so classification
    and refusal behavior cannot drift apart.
    """
    if not isinstance(content, str):
        return ""
    text = content.strip()
    if len(text) > 2000 or text.startswith(("<", "{", "[", "```")):
        return ""
    normalized = text.replace("’", "'")
    if re.match(
        r"^(?:I'm sorry|I am sorry|I can help with (?:all sorts of things|many kinds of requests)|Your request may go against my)",
        normalized,
        re.I,
    ) and (
        "gemini.google/policy-guidelines" in text
        or re.search(
            r"\b(?:can't|cannot|can not) (?:help|assist|comply)|\b(?:against|violate) my (?:guidelines|polic)",
            normalized,
            re.I,
        )
    ):
        return text
    return ""


def classify_provider_reply(content):
    """One of ``"policy-refusal"``, ``"provider-error"``, or ``"content"``.

    Explicit refusals stay terminal at their existing call sites; the
    classification exists so callers can route a whole standalone provider
    failure before a parser or validator turns it into misleading output.
    """
    matched = _explicit_policy_refusal(content)
    if matched:
        return "policy-refusal", matched
    matched = standalone_provider_error(content)
    if matched:
        return "provider-error", matched
    return "content", ""


def check_provider_refusal(content, refusal=None, finish_reason=None):
    if refusal:
        raise ProviderRefusal(str(refusal))
    if finish_reason in ('content_filter', 'safety', 'blocked'):
        raise ProviderRefusal(content or f'Provider finish reason: {finish_reason}')
    # Match only a standalone refusal, never those words quoted inside a book's
    # XML/JSON translation. A policy link on its own is not a refusal either.
    matched = _explicit_policy_refusal(content or '')
    if matched:
        raise ProviderRefusal(matched)


def check_handoff_refusal(seed):
    """Check both a raw summary and the summary inside our saved seed wrapper."""
    if seed.startswith('You are continuing a translation already in progress.'):
        seed = seed.split('\n\n', 1)[-1]
    check_provider_refusal(seed)
