"""Independent source-guided paragraph review and exact-pair approvals."""

import json
from hashlib import sha256

from book_maker.marker_names import MarkerError, validate_paragraph
from book_maker.structured import extract_json_object


def digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def review(workflow, item, neighbors, validate_quality):
    """Return reviewed text/delta, or raise without changing accepted state."""
    prompt = (
        "Review the candidate against the ORIGINAL paragraph and nearby source. "
        "Correct wrong identities, speaker changes, first/second-person dialogue, "
        "pronouns, omissions, mistranslations and invalid markers. Names, places "
        "and named terms may retain their exact original spelling inside ${...}. "
        "Every marker label MUST be an exact substring of THIS original paragraph "
        "and must denote a name or named term, never an ordinary pronoun. "
        "Prefer natural plain pronouns. A repeated source-backed name may be "
        "legitimate English expansion: approve extra references ONLY if each "
        "one has the correct referent and preserves dialogue perspective. "
        "Never turn dialogue I/you into a third-person character name. "
        "Nearby source is read-only context, not text to return. If the candidate "
        "is absent, translate the original paragraph. If uncertainty prevents a "
        "reliable result, defer it. Return one JSON object with exactly these fields: "
        '{"id":"supplied id","decision":"approve|rewrite|defer",'
        '"translation":"full paragraph text with markers, no XML wrapper",'
        '"allow_extra_references":false,"reason":"source-grounded explanation"}. '
        "approve must reproduce the candidate unchanged; rewrite must give the "
        "complete corrected paragraph. Set allow_extra_references to true only "
        "when the returned paragraph needs additional valid name references.\n\n"
        + json.dumps({
            "id": item.identity,
            "target_language": workflow._language,
            "original": item.source,
            "nearby_source": neighbors,
            "candidate": item.candidate,
        }, ensure_ascii=False)
    )
    raw = workflow.model.review_marker_paragraph(prompt)
    result = extract_json_object(raw, required_keys={"id", "decision", "translation"})
    if not isinstance(result, dict) or result.get("id") != item.identity:
        raise MarkerError("Paragraph reviewer returned a different or missing id")
    decision = result.get("decision")
    if decision == "defer":
        reason = result.get("reason")
        raise MarkerError("Paragraph reviewer deferred: " +
                          (reason[:1000] if isinstance(reason, str) else "no reason supplied"))
    if decision not in ("approve", "rewrite"):
        raise MarkerError("Paragraph reviewer returned an unknown decision")
    text = result.get("translation")
    reason = result.get("reason")
    extra = result.get("allow_extra_references")
    if not isinstance(text, str) or not text.strip():
        raise MarkerError("Paragraph reviewer returned empty text")
    if not isinstance(extra, bool) or not isinstance(reason, str) or not reason.strip():
        raise MarkerError("Paragraph reviewer omitted its reference decision or reason")
    if decision == "approve" and text != item.candidate:
        raise MarkerError("Paragraph reviewer changed text without a rewrite decision")
    if "<bbm-" in text or "</bbm-" in text:
        raise MarkerError("Paragraph reviewer returned a protocol wrapper as prose")
    delta = validate_paragraph(item.source, text, item.index, allow_excess=extra)
    validate_quality([item.source], [text])
    record = {
        "source_sha256": digest(item.source),
        "translation_sha256": digest(text),
        "candidate_sha256": digest(item.candidate or ""),
        "decision": decision,
        "allow_extra_references": extra,
        "reason": reason[:2000],
    }
    # Stored with the accepted paragraph by the coordinator's transaction.
    item.review = record
    return text, delta
