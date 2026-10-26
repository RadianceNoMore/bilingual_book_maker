"""Narrow removal of confirmed, obfuscated contact-code signatures.

This is deliberately not an advertisement classifier. Add signatures only
after confirming them in source material; mixed scripts alone prove nothing.
"""

import re
import unicodedata

from bs4 import NavigableString


RULE_VERSION = 1
# Confirmed contact watermark. NFKC covers circled/fullwidth digits and T.
# The endpoint boundary prevents matching the beginning of a longer identifier.
_CONTACT = re.compile(r"(?<![A-Za-z0-9])T2就起六揪翼叁捌遛(?![\w])")


def code_spans(text):
    """Return original-text offsets, even when normalization expands a char."""
    normalized, offsets = [], []
    for index, char in enumerate(text):
        expanded = unicodedata.normalize("NFKC", char)
        normalized.append(expanded)
        offsets.extend([index] * len(expanded))
    return [(offsets[m.start()], offsets[m.end() - 1] + 1)
            for m in _CONTACT.finditer("".join(normalized))]


def strip_codes(text):
    if not isinstance(text, str):
        return text
    spans = code_spans(text)
    for start, end in reversed(spans):
        text = text[:start] + text[end:]
    return text.strip() if spans else text


def clean_document(soup):
    """Remove codes across inline tags without flattening surrounding markup."""
    removals = []
    for owner in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li"]):
        # Process each text run once, through its innermost block owner.
        if owner.find(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li"]):
            continue
        nodes = [n for n in owner.descendants if isinstance(n, NavigableString)
                 and not any(parent.name in {"script", "style", "code", "pre"}
                             for parent in n.parents)]
        text = "".join(map(str, nodes))
        spans = code_spans(text)
        if not spans:
            continue
        removals.extend(text[a:b] for a, b in spans)
        offset = 0
        for node in nodes:
            value = str(node)
            kept = "".join(char for i, char in enumerate(value, offset)
                           if not any(a <= i < b for a, b in spans))
            offset += len(value)
            node.replace_with(kept)
        if not owner.get_text().strip() and not owner.find(["img", "svg", "audio", "video", "br"]):
            # Retain anchors referenced from navigation or elsewhere.
            if owner.get("id") or owner.find(attrs={"id": True}):
                continue
            owner.decompose()
    return removals
