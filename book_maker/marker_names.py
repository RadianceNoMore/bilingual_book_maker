"""Validation and catalogue primitives for model-selected source-name markers.

This module has no provider or filesystem side effects. A caller commits each
validated paragraph immediately and retries only the failures; the catalogue
delta must still be applied in the same local transaction as the accepted text.

The wire marker is literal ``${exact source label}``.  Labels are kept exactly
as the model wrote them: no case folding, alias merging, or identity
resolution.  A catalogue key is the SHA-256 of the exact UTF-8 label, which is
also the only value the generated CSS ever uses as a selector.
"""

import hashlib
import re
from collections import Counter


MARKER = re.compile(r"\$\{([^{}\r\n]+)\}")
WORKFLOW = "source-markers-css-v1"


class MarkerError(ValueError):
    """A response cannot safely be committed to the name catalogue."""


def marker_key(label):
    """Stable catalogue key for an exact source label."""
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def marker_expression(label):
    return "${" + label + "}"


def markers(text):
    """Return marker labels, rejecting incomplete or nested marker syntax."""
    if not isinstance(text, str):
        raise MarkerError("Marker text is not a string")
    labels = []
    cursor = 0
    for match in MARKER.finditer(text):
        if "${" in text[cursor:match.start()]:
            raise MarkerError("Malformed or nested name marker")
        label = match.group(1)
        if label != label.strip() or not label.strip() or "$" in label:
            raise MarkerError("Invalid name marker label")
        labels.append(label)
        cursor = match.end()
    if "${" in text[cursor:]:
        raise MarkerError("Unclosed or nested name marker")
    return labels


def has_reserved_syntax(text):
    """Whether a source string contains syntax this workflow reserves."""
    return "${" in (text or "")


def require_no_reserved_syntax(texts):
    """Preflight selected source units before any provider request."""
    for index, text in enumerate(texts, 1):
        if has_reserved_syntax(text):
            raise MarkerError(
                f"Source unit {index} contains reserved marker syntax '${{'. "
                "Protected-name mode cannot tell a literal sequence from a "
                "name marker; refusing to interpret it."
            )


def strip_markers(text):
    """Remove validated marker expressions, keeping surrounding prose.

    Used only to make script/language quality checks ignore the source-script
    label inside a marker.  Prose outside markers is never exempted.
    """
    if not isinstance(text, str):
        return ""
    # Remove syntactically complete markers only. A malformed unclosed marker
    # stays visible so the quality checks can see (and reject) it.
    return MARKER.sub("", text)


def marker_parts(text):
    """Split text into ``('text', value)`` / ``('marker', label)`` parts."""
    parts = []
    cursor = 0
    for match in MARKER.finditer(text):
        if match.start() > cursor:
            parts.append(("text", text[cursor:match.start()]))
        parts.append(("marker", match.group(1)))
        cursor = match.end()
    if cursor < len(text):
        parts.append(("text", text[cursor:]))
    return parts


def ordered_labels(labels):
    """Deterministic longest-first, then lexical order."""
    return sorted({label for label in labels if label}, key=lambda value: (-len(value), value))


def source_spans(source, labels):
    """Map each label to its accepted, non-overlapping source spans.

    Labels are considered longest-first.  A match is accepted only when none
    of its source positions were reserved by an earlier accepted match.
    Searching advances one character at a time so overlapping candidates are
    enumerated, but an overlapping candidate never consumes positions.  This
    is span bookkeeping, not string replacement: removing accepted text could
    join neighbours and manufacture matches that never existed in the source.
    """
    ordered = ordered_labels(labels)
    spans = {label: [] for label in ordered}
    reserved = bytearray(len(source))
    for label in ordered:
        length = len(label)
        for start in range(0, len(source) - length + 1):
            if not source.startswith(label, start):
                continue
            end = start + length
            if any(reserved[start:end]):
                continue
            spans[label].append((start, end))
            reserved[start:end] = b"\x01" * length
    return spans


def source_occurrences(source, labels):
    """Accepted exact source spans for ``labels``, in ascending start order.

    A short label cannot consume the positions of a longer label accepted
    earlier, which keeps ``美琴`` from hiding inside ``御坂美琴``.
    """
    found = [
        (start, end, label)
        for label, accepted in source_spans(source, labels).items()
        for start, end in accepted
    ]
    return sorted(found)


def source_counts(source, labels):
    """Count accepted exact source spans, longest label first."""
    counts = Counter()
    for _start, _end, label in source_occurrences(source, labels):
        counts[label] += 1
    return counts


def _context(source, start, end, limit=400):
    """A bounded, readable excerpt around one source occurrence."""
    if len(source) <= limit:
        return source
    half = max(40, (limit - (end - start)) // 2)
    left = max(0, start - half)
    right = min(len(source), end + half)
    return source[left:right]


ENGLISH_PRONOUN_MARKERS = frozenset(
    {
        "he",
        "she",
        "it",
        "him",
        "her",
        "his",
        "hers",
        "they",
        "them",
        "their",
        "theirs",
    }
)


def english_pronoun_label(label):
    """Whether a returned label is a plain English pronoun.

    Feedback tailoring only: this never feeds the accept/reject decision and
    never bans those strings globally. A legitimate source label that reads
    like a pronoun in another language must stay acceptable.
    """
    if not isinstance(label, str):
        return False
    return label.strip().casefold() in ENGLISH_PRONOUN_MARKERS


def validate_paragraph(source, translation, index=1, *, allow_excess=False):
    """Validate one paragraph and return its catalogue delta.

    Only labels returned in *this* paragraph are required.  Catalogue
    membership and markers returned by another paragraph never make an
    unmarked name fail here.  A returned label is accepted when its marker
    count does not exceed its non-overlapping exact availability in the
    source; equality is not required. ``allow_excess`` is reserved for an
    independently reviewed, exact source/output pair. It permits additional
    references but never a label absent from the source.  Occurrence examples and catalogue
    counts come from the returned markers, not every available source hit.

    A raised :class:`MarkerError` also carries a narrowly scoped
    structured list in ``label_details`` (per offending label: ``label``,
    ``requested``, ``available``, and a ``kind`` of ``absent``, ``excess``,
    or ``pronoun-marker``) so repair feedback can be actionable. The
    human-readable message is unchanged by this metadata.
    """
    parsed = markers(translation)
    actual = Counter(parsed)
    spans = source_spans(source, actual)
    details = []
    for label in ordered_labels(actual):
        available = len(spans.get(label, ()))
        if actual[label] > available and (not allow_excess or label not in source):
            details.append(
                f"{label!r}: expected {available}, received {actual[label]}"
            )
    if details:
        excerpt = source[:180].replace("\n", " ")
        hint = ""
        structured = []
        for label in ordered_labels(actual):
            available = len(spans.get(label, ()))
            requested = actual[label]
            if requested <= available:
                continue
            if english_pronoun_label(label):
                kind = "pronoun-marker"
            elif available == 0:
                kind = "absent"
            else:
                kind = "excess"
            structured.append(
                {
                    "label": label,
                    "requested": requested,
                    "available": available,
                    "kind": kind,
                }
            )
        if any(
            len(spans.get(label, ())) == 0 for label in actual
        ):
            hint = " Markers were added for text not present as an exact source label."
        error = MarkerError(
            f"Paragraph {index}: marker counts exceed exact source availability. "
            + "; ".join(details)
            + "."
            + hint
            + f" Source: {excerpt!r}"
        )
        error.label_details = structured
        raise error
    delta = {}
    for label in ordered_labels(actual):
        count = actual[label]
        selected = spans.get(label, ())[:count]
        if allow_excess and not selected and label in source:
            start = source.index(label)
            selected = [(start, start + len(label))]
        entry = delta.setdefault(
            label, {"count": 0, "paragraphs": [], "occurrences": []}
        )
        entry["count"] += count
        if index not in entry["paragraphs"]:
            entry["paragraphs"].append(index)
        entry["occurrences"].extend(
            {
                "start": start,
                "end": end,
                "context": _context(source, start, end),
                "paragraph": index,
            }
            for start, end in selected
        )
    return delta


def validate_batch(sources, translations, known_labels=()):
    """Validate a complete batch, returning one merged catalogue delta.

    ``known_labels`` is accepted for callers that still pass the catalogue;
    it deliberately does not make a label required.  Each paragraph is
    validated against its own returned markers only.
    """
    if len(sources) != len(translations):
        raise MarkerError(
            f"Source and translated paragraph counts differ: {len(sources)} source, "
            f"{len(translations)} translated"
        )
    delta = {}
    for index, (source, translated) in enumerate(zip(sources, translations), 1):
        if has_reserved_syntax(source):
            raise MarkerError(
                f"Paragraph {index}: source contains reserved marker syntax '${{'"
            )
        paragraph_delta = validate_paragraph(source, translated, index)
        for label, row in paragraph_delta.items():
            entry = delta.setdefault(
                label, {"count": 0, "paragraphs": [], "occurrences": []}
            )
            entry["count"] += row["count"]
            entry["paragraphs"].extend(
                value for value in row["paragraphs"] if value not in entry["paragraphs"]
            )
            entry["occurrences"].extend(row["occurrences"])
    return delta


def validate_display_target(value):
    """Validate one editable display mapping (plain text, no markup/markers)."""
    if not isinstance(value, str):
        raise MarkerError("Display mapping must be text")
    value = value.strip()
    if not value:
        raise MarkerError("Display mapping must not be empty")
    if "${" in value or "}" in value:
        raise MarkerError("Display mapping must not contain marker syntax")
    if re.search(r"</?[A-Za-z][^>]*>", value):
        raise MarkerError("Display mapping must not contain markup")
    if any(ord(char) < 0x20 and char not in "\t" for char in value):
        raise MarkerError("Display mapping contains a control character")
    return value


def validate_summary(summary, known_labels):
    """Validate a marker-preserving compaction summary.

    The summary may omit names and reduce occurrence counts. Explicit markers
    must use catalogue labels. Plain prose is allowed even when it contains a
    catalogue string: source labels can also be ordinary target-language words
    or substrings, so string matching cannot identify an unprotected name.
    A whole recognized standalone provider failure message is rejected at this
    shared boundary, so no caller can accept one as a seed.
    """
    if not isinstance(summary, str) or not summary.strip():
        raise MarkerError("Summary is empty")
    from book_maker.provider_refusal import standalone_provider_error

    boilerplate = standalone_provider_error(summary)
    if boilerplate:
        raise MarkerError(
            "Summary is a standalone provider failure message: "
            + repr(boilerplate[:160])
        )
    labels = markers(summary)
    known = set(known_labels)
    unknown = [label for label in labels if label and label not in known]
    if unknown:
        raise MarkerError(
            "Summary introduced marker label(s) outside the catalogue: "
            + ", ".join(repr(label) for label in sorted(set(unknown)))
        )
    return summary


def display_text(text, mapping):
    """Substitute target-or-original values into master marker text.

    ``mapping`` is keyed by :func:`marker_key`.  Unmapped labels remain
    visible as their original source spelling.
    """
    if not isinstance(text, str) or "${" not in text:
        return text
    parts = []
    for kind, value in marker_parts(text):
        if kind == "text":
            parts.append(value)
        else:
            parts.append(mapping.get(marker_key(value)) or value)
    return "".join(parts)


def catalogue_from_state(state):
    """The label-keyed catalogue without the surrounding run state."""
    return (state or {}).get("catalogue") or {}


def mapping_from_state(state):
    """``key -> target`` for every catalogue entry with a draft rendering."""
    mapping = {}
    for label, entry in catalogue_from_state(state).items():
        if not isinstance(entry, dict) or not entry.get("target"):
            continue
        mapping[entry.get("key") or marker_key(label)] = entry["target"]
    return mapping
