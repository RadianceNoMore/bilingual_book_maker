"""Offline, advisory consistency report. Findings never rewrite book content."""

import json
from collections import Counter
from pathlib import Path

from book_maker.glossary import Glossary, GlossaryEntry, rendering_matcher
from book_maker.glossary_review import category, concern, read_review
from book_maker.translation_quality import untranslated_reason


def audit_translations(pairs, glossary=None, events=(), target=None, source_language=None, pinned=None, approved=None):
    """Pairs contain (chapter, unit identifier, source, accepted translation)."""
    glossary = glossary or Glossary()
    events = tuple(events)
    findings, checked = [], 0
    seen = set()
    locations = {}
    review_terms = Glossary(GlossaryEntry(e["term"], e.get("proposed", ""))
                            for e in events if e.get("term"))
    target_patterns = {entry.term: rendering_matcher(entry)
                       for entry in glossary.entries}
    for event in events:
        if event.get("event") not in {"conflict", "quarantined"}:
            continue
        key = (event.get("event"), event.get("term"), event.get("proposed"))
        if key not in seen:
            seen.add(key)
            findings.append(dict(kind="glossary_" + event["event"], **event))
    for entry in glossary.entries:
        issue = concern(entry, glossary.entries)
        if issue:
            findings.append(dict(kind="suspicious_mapping", term=entry.term,
                                 translation=entry.translation, reason=issue))
    # Term-use checks are advisory: pronouns, inflections, and overlapping
    # names make exact target-string absence insufficient to reject a reply.
    for chapter, unit, source, translated in pairs:
        checked += 1
        location = dict(chapter=chapter, unit=unit)
        reason = untranslated_reason(source, translated, target, source_language, detect_language=False)
        if reason:
            findings.append(dict(kind="translation_guard", reason=reason,
                                 source=source[:180], translation=str(translated)[:180], **location))
        hits = glossary.matches(source)
        for entry in review_terms.matches(source):
            locations.setdefault(entry.term, {}).setdefault(chapter, unit)
        for entry in hits:
            locations.setdefault(entry.term, {}).setdefault(chapter, unit)
            # Prefer a more specific source term; e.g. a person's full name
            # must not also require every shorter alias embedded inside it.
            if any(entry.term != other.term and entry.term in other.term for other in hits):
                continue
            if not target_patterns[entry.term].search(str(translated)):
                findings.append(dict(kind="term_rendering_absent", term=entry.term,
                                     expected=entry.translation, category=category(entry),
                                     reason="advisory: inflection or contextual rendering may be valid",
                                     translation=str(translated)[:180], **location))
    for finding in findings:
        if "chapter" not in finding and finding.get("term") in locations:
            finding["locations"] = [dict(chapter=chapter, unit=unit)
                                    for chapter, unit in locations[finding["term"]].items()]
    return dict(version=1, advisory=True, checked_units=checked,
                scope="Selected target slots; script/format checks and glossary consistency. No semantic correctness guarantee.",
                glossary=[dict(term=e.term, translation=e.translation, note=e.note,
                               category=category(e),
                               status=("pinned" if pinned and pinned.lookup(e.term) else
                                       "review_approved" if approved and approved.lookup(e.term) else "provisional"))
                          for e in glossary.entries],
                counts=dict(Counter(f["kind"] for f in findings)), findings=findings)


def write_audit(path, pairs, glossary=None, review_file=None, target=None, source_language=None, pinned=None, approved=None):
    report = audit_translations(pairs, glossary, read_review(review_file), target, source_language, pinned, approved)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return report
