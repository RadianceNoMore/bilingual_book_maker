"""Conservative guards against untranslated or contaminated translations.

Script proportions catch source text left in a different writing system.
Language identification catches long replies that use the same alphabet as
the target (for example French left in an English translation). These checks
detect likely failures; they do not judge translation quality or meaning.
"""

import html
import re
import unicodedata
from functools import lru_cache

from book_maker.translator.base_translator import BatchMismatch
from book_maker.utils import language_code


_MARKERS = re.compile(r"⟦[^⟦⟧]*⟧")
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_HEADING_MARKDOWN = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_FENCED_CODE = re.compile(r"(?m)^\s{0,3}```")
_BACKTICK = re.compile(r"`")
_PROMPT_LEAKS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bplease\s+(?:help\s+(?:me\s+)?(?:to\s+)?)?translate\b",
        r"\btranslate\s+(?:the following|this text|the above)\b",
        r"\bhere(?:'s| is)\s+(?:the\s+)?translation\b",
        r"\bas an ai(?:\s+language\s+model)?\b",
        r"\b(?:system|developer)\s+prompt\b",
    )
)
_ERROR_REPLY_LEAKS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Some compatible endpoints return user-facing error copy as a 200
        # completion. It looks like valid English to language/script checks,
        # but it is not a translation and must go through the retry path.
        r"\bi seem to be encountering an error\b",
        r"\bi encountered an error doing what you asked\b",
        r"\bi encountered an error while processing your request\b",
        r"\bcan i try something else for you\b",
    )
)
_TRANSLATOR_ASIDES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # A model may explain an ambiguous sound instead of translating it.
        r"\(\s*depending on (?:the )?context\b[^)]*\)",
        r"\bdepending on (?:the )?context,?\s+(?:such as|for example)\b",
    )
)

# Language codes whose usual output scripts can be checked directly. Languages
# with more than one standard script list all of them so valid names and loan
# words do not trip the source-script guard.
_LATIN = set(
    "af an az bs br ca cs cy da de en eo es et eu fi fo fr ga gl hr hu id "
    "is it jv la lb lt lv ms mt nb nl nn no nso oc pl pt qu ro se sk sl sq "
    "sr sv sw tl tr uz vi wa xh zu fil".split()
)
_TARGET_SCRIPTS = {code: {"LATIN"} for code in _LATIN}
_TARGET_SCRIPTS.update(
    {
        "ar": {"ARABIC"}, "fa": {"ARABIC"}, "ur": {"ARABIC"},
        "ps": {"ARABIC"}, "he": {"HEBREW"}, "yi": {"HEBREW"},
        "ru": {"CYRILLIC"}, "uk": {"CYRILLIC"}, "bg": {"CYRILLIC"},
        "mk": {"CYRILLIC"}, "be": {"CYRILLIC"}, "kk": {"CYRILLIC"},
        "ky": {"CYRILLIC"}, "mn": {"CYRILLIC"},
        "el": {"GREEK"}, "zh": {"HAN"}, "yue": {"HAN"}, "wuu": {"HAN"},
        "ja": {"HAN", "HIRAGANA", "KATAKANA"},
        # Korean prose is written in Hangul; Chinese copied into a Korean
        # target should not pass merely because Hanja exists.
        "ko": {"HANGUL"}, "th": {"THAI"}, "lo": {"LAO"},
        "my": {"MYANMAR"}, "km": {"KHMER"}, "hi": {"DEVANAGARI"},
        "mr": {"DEVANAGARI"}, "ne": {"DEVANAGARI"},
        "bn": {"BENGALI"}, "as": {"BENGALI"}, "ta": {"TAMIL"},
        "te": {"TELUGU"}, "kn": {"KANNADA"}, "ml": {"MALAYALAM"},
        "gu": {"GUJARATI"}, "pa": {"GURMUKHI"}, "or": {"ORIYA"},
        "ka": {"GEORGIAN"}, "hy": {"ARMENIAN"}, "am": {"ETHIOPIC"},
        "si": {"SINHALA"}, "bo": {"TIBETAN"},
        # Serbian is routinely written in both scripts.
        "sr": {"LATIN", "CYRILLIC"},
    }
)

_SCRIPT_MARKERS = (
    ("CJK", "HAN"), ("IDEOGRAPH", "HAN"), ("HIRAGANA", "HIRAGANA"),
    ("KATAKANA", "KATAKANA"), ("HANGUL", "HANGUL"), ("LATIN", "LATIN"),
    ("CYRILLIC", "CYRILLIC"), ("ARABIC", "ARABIC"), ("HEBREW", "HEBREW"),
    ("GREEK", "GREEK"), ("THAI", "THAI"), ("LAO", "LAO"),
    ("MYANMAR", "MYANMAR"), ("KHMER", "KHMER"), ("DEVANAGARI", "DEVANAGARI"),
    ("BENGALI", "BENGALI"), ("TAMIL", "TAMIL"), ("TELUGU", "TELUGU"),
    ("KANNADA", "KANNADA"), ("MALAYALAM", "MALAYALAM"),
    ("GUJARATI", "GUJARATI"), ("GURMUKHI", "GURMUKHI"),
    ("ORIYA", "ORIYA"), ("GEORGIAN", "GEORGIAN"), ("ARMENIAN", "ARMENIAN"),
    ("ETHIOPIC", "ETHIOPIC"), ("SINHALA", "SINHALA"), ("TIBETAN", "TIBETAN"),
)


class UntranslatedTextError(BatchMismatch):
    """A structurally valid reply still contains untranslated or leaked text."""

    user_facing = True


def _clean(text):
    text = _MARKERS.sub("", _TAGS.sub("", str(text or "")))
    return html.unescape(text)


def _code(value):
    code = language_code(value)
    if not code and isinstance(value, str):
        value = value.strip()
        code = value if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value) else None
    return code.lower().split("-")[0] if code else None


def _script(char):
    name = unicodedata.name(char, "")
    for marker, script in _SCRIPT_MARKERS:
        if marker in name:
            return script
    return None


def _script_counts(text):
    counts = {}
    for char in text:
        if char.isalpha():
            script = _script(char)
            if script:
                counts[script] = counts.get(script, 0) + 1
    return counts


@lru_cache(maxsize=1)
def _language_detector():
    """Load the optional statistical detector once, with repeatable results."""
    try:
        from langdetect import DetectorFactory, detect_langs
    except ImportError:
        return None
    DetectorFactory.seed = 0
    return detect_langs


def _detected_language(text):
    # Language identification is noisy on headings and short names. Restrict
    # it to substantial prose and require a high-confidence result.
    if sum(char.isalpha() for char in text) < 80:
        return None
    detect_langs = _language_detector()
    if detect_langs is None:
        return None
    try:
        ranked = detect_langs(text)
    except Exception:
        return None
    if not ranked or ranked[0].prob < 0.90:
        return None
    return ranked[0].lang.lower()


def untranslated_reason(source, reply, target_language, source_language=None):
    """Return a high-confidence reason to retry, or None when it passes.

    Source-only writing systems are rejected when they make up at least half
    of the reply's letters. When source and target share an alphabet, a
    deterministic language identifier checks only substantial prose.
    """
    if not isinstance(reply, str):
        return None

    original, translated = _clean(source), _clean(reply)
    original_normalized = _SPACE.sub(" ", original).strip().casefold()
    translated_normalized = _SPACE.sub(" ", translated).strip().casefold()

    for pattern in _PROMPT_LEAKS:
        if pattern.search(translated) and not pattern.search(original):
            return "reply contains translation instructions or model boilerplate"
    for pattern in _ERROR_REPLY_LEAKS:
        if pattern.search(translated) and not pattern.search(original):
            return "reply contains a provider/model error response instead of a translation"
    for pattern in _TRANSLATOR_ASIDES:
        if pattern.search(translated) and not pattern.search(original):
            return "reply contains translator commentary instead of book text"
    if _HEADING_MARKDOWN.search(translated) and not _HEADING_MARKDOWN.search(original):
        return "reply added Markdown heading syntax absent from the source"
    if _FENCED_CODE.search(translated) and not _FENCED_CODE.search(original):
        return "reply added a Markdown code fence absent from the source"
    if _BACKTICK.search(translated) and not _BACKTICK.search(original):
        return "reply added Markdown code punctuation absent from the source"

    target = _code(target_language)
    if not target:
        return None

    if not translated_normalized:
        return None

    target_scripts = _TARGET_SCRIPTS.get(target, set())
    reply_scripts = _script_counts(translated)
    letter_count = sum(reply_scripts.values())
    source_scripts = set(_script_counts(original))
    source_only = source_scripts - target_scripts
    source_only_count = sum(reply_scripts.get(script, 0) for script in source_only)
    if letter_count >= 4 and source_only_count / letter_count >= 0.50:
        names = ", ".join(sorted(source_only))
        if original_normalized == translated_normalized:
            return f"source text was returned unchanged for target {target} ({names} script)"
        return f"reply is still mostly in the source writing system ({names}) for target {target}"

    if (
        target == "ja"
        and reply_scripts.get("HAN", 0) >= 16
        and not {"HIRAGANA", "KATAKANA"}.intersection(reply_scripts)
    ):
        return "reply is Han-only and lacks the kana normally present in Japanese prose"

    detected = _detected_language(translated)
    same_language = detected == target or (
        target in {"no", "nb", "nn"} and detected in {"no", "nb", "nn"}
    ) or (target == "zh" and detected in {"zh", "zh-cn", "zh-tw"})
    same_language = same_language or (
        target == "fil" and detected == "tl"
    ) or (
        target in {"yue", "wuu"} and detected in {"zh", "zh-cn", "zh-tw"}
    )
    if detected and not same_language:
        if original_normalized == translated_normalized:
            return f"source text was returned unchanged; reply appears to be {detected}, target is {target}"
        return f"reply appears to be {detected}, target is {target}"

    # An exact short echo in a shared writing system cannot be classified
    # safely. Allow it rather than treating names or short quotations as prose.
    source = _code(source_language)
    if (
        original_normalized
        and original_normalized == translated_normalized
        and source
        and source != target
        and sum(char.isalpha() for char in translated) >= 24
        and len(translated.split()) >= 3
    ):
        return f"source text was returned unchanged for target {target}"
    return None


def validate_translation_texts(sources, replies, target_language, source_language=None):
    """Reject a complete chunk when any slot is untranslated or contaminated."""
    failures = []
    for index, (source, reply) in enumerate(zip(sources, replies), 1):
        reason = untranslated_reason(source, reply, target_language, source_language)
        if reason:
            failures.append(f"slot {index}: {reason} ({str(reply)[:70]!r})")
    if failures:
        examples = "; ".join(failures[:3])
        raise UntranslatedTextError(
            f"Translation validation failed in {len(failures)} slot(s): {examples}. "
            "The complete chunk will be retried."
        )
