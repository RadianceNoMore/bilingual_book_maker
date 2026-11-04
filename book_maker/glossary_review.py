"""Conservative policy and durable review records for model-learned terms.

Categories are hints from notes, not entity recognition. No historical or
mythological correctness is inferred from a familiar-looking spelling.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from book_maker.glossary import Glossary


def category(entry):
    note = entry.note.casefold()
    if re.search(r"\b(city|place|region|country|port|island|river|mountain)\b", note):
        return "place"
    if re.search(r"\b(name|character|person|nickname|deity|goddess|god|commander)\b", note):
        return "name"
    if re.search(r"\b(people|demonym|tribe|ethnic)\b", note):
        return "people"
    if "vocabulary" in note:
        return "vocabulary"
    return "unclassified"


def concern(entry, entries=()):
    """Only flag concrete ambiguities; never guess a canonical replacement."""
    if category(entry) == "place" and re.search(r"\b(?:people|citizens|inhabitants|[a-z]+ians)\b", entry.translation, re.I):
        return "a place is rendered as a group of people"
    # E.g. 色雷斯 -> Thracians alongside 色雷斯人 -> Thracians. Both may
    # occur naturally in prose, but a verbatim global instruction is unsafe.
    for other in entries:
        if other.term == entry.term + "人" and (
            other.translation.casefold() == entry.translation.casefold()
        ):
            return "place/base term and its people form share one rendering"
    return None


def review_path(handoff_path):
    path = Path(handoff_path)
    return path.with_name(path.stem.removesuffix("_handoff") + "_glossary_review.jsonl")


def learn_stable(existing, proposed, pinned):
    """Keep the first rendering; quarantine concrete ambiguities for review."""
    all_entries = existing.entries + proposed.entries
    accepted, events = list(existing.entries), []
    for entry in proposed.entries:
        known = pinned.lookup(entry.term) or existing.lookup(entry.term)
        issue = concern(entry, all_entries)
        if known and known.translation != entry.translation:
            events.append(dict(event="conflict", term=entry.term, kept=known.translation,
                               proposed=entry.translation, category=category(entry),
                               reason="established rendering retained"))
        elif not known:
            if not issue:
                accepted.append(entry)
            events.append(dict(event="quarantined" if issue else "learned", term=entry.term,
                               proposed=entry.translation, note=entry.note,
                               category=category(entry), provisional=True,
                               reason=issue or "review only; not independently verified or enforced"))
        elif existing.lookup(entry.term) and not known.note and entry.note:
            # Enrich a later category observation without changing the rendering.
            accepted = [entry if old.term.lower() == entry.term.lower() else old for old in accepted]
    # Reconsider older entries when the people/base counterpart is first seen.
    safe = []
    for entry in accepted:
        issue = concern(entry, all_entries)
        if issue:
            if existing.lookup(entry.term):
                events.append(dict(event="quarantined", term=entry.term,
                                   proposed=entry.translation, category=category(entry),
                                   provisional=True, reason=issue))
        else:
            safe.append(entry)
    return Glossary(safe), events


def append_review(path, events):
    if not events:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    needs_newline = False
    if path.exists() and path.stat().st_size:
        with path.open("rb") as stream:
            stream.seek(-1, 2)
            needs_newline = stream.read(1) != b"\n"
    with path.open("a", encoding="utf-8") as stream:
        if needs_newline:
            stream.write("\n")
        for event in events:
            stream.write(json.dumps(dict(timestamp=timestamp, **event), ensure_ascii=False) + "\n")


def read_review(path):
    if not path or not Path(path).exists():
        return []
    events = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a truncated last line must not hide the rest of the audit
        if isinstance(event, dict):
            events.append(event)
    return events
