"""Deterministic offline reproduction of the text_196 failure categories.

Run from the repository root:

    python diagnostics/text196/reproduce_recovery.py

The script never calls a provider and never opens or modifies a recovery EPUB.
It writes reproduction.json beside itself and prints the same object.
"""

from __future__ import annotations

import json
from pathlib import Path

from book_maker.marker_workflow import (
    classify_unit_failure,
    closest_unknown,
    parse_paragraph_reply,
    retry_unit_cap,
)
from book_maker.translator.chatgptapi_translator import response_framing_facts

DAMAGED_ID = (
    '<bbm-response><bbm-unit id="u01">Indeed.</bbm-unit></bbm-response>'
)
DUPLICATE = (
    '<bbm-response><bbm-unit id="u001">A.</bbm-unit>'
    '<bbm-unit id="u001">B.</bbm-unit></bbm-response>'
)
TRUNCATED = (
    '<bbm-response><bbm-unit id="u001">${Alice} left.</bbm-unit>'
    '<bbm-unit id="u002">${Bob} arr'
)
UNFINISHED_ATTRIBUTE = (
    '<bbm-response><bbm-unit id="u001">${Alice} left.</bbm-unit>'
    '<bbm-unit id="u002" data-state="unfinished'
)
MULTIPLE_ROOTS = (
    '<bbm-response><bbm-unit id="u001">A.</bbm-unit></bbm-response>'
    '<bbm-response><bbm-unit id="u002">B.</bbm-unit></bbm-response>'
)


def case(name, raw, requested):
    reply = parse_paragraph_reply(raw, requested)
    row = {
        "case": name,
        "requested": requested,
        "accepted_ids": sorted(reply.texts),
        "framing_error": reply.framing_error,
        "salvaged": reply.salvaged,
        "unknown_ids": reply.unknown,
        "diagnostics": reply.diagnostics,
        "per_id": {},
        "framing": response_framing_facts(raw),
    }
    for identity in requested:
        if identity in reply.texts:
            row["per_id"][identity] = {"status": "accepted"}
            continue
        error = reply.errors.get(identity) or "missing from response"
        category = classify_unit_failure(
            error, salvaged=reply.salvaged, framing_error=reply.framing_error or ""
        )
        observed, ratio = closest_unknown(identity, reply.unknown)
        if category in ("missing-unit", "ambiguous-frame") and observed and ratio >= 0.6:
            category = "wrong-or-unknown-id"
        row["per_id"][identity] = {
            "status": "pending",
            "category": category,
            "error": error,
            "closest_observed": observed,
            "similarity": round(ratio, 3),
            "acceptance_rule": "similarity never authorizes acceptance",
        }
    return row


def main():
    report = {
        "source": "text_196 offline fixtures",
        "provider_calls": 0,
        "recovery_epub_touched": False,
        "retry_schedule": [
            {"attempt": attempt, "max_units": retry_unit_cap(attempt, 40)}
            for attempt in range(1, 6)
        ],
        "cases": [
            case("damaged-id", DAMAGED_ID, ["u001"]),
            case("duplicate-id", DUPLICATE, ["u001"]),
            case("truncated-final-unit", TRUNCATED, ["u001", "u002"]),
            case(
                "unfinished-attribute-restart",
                UNFINISHED_ATTRIBUTE,
                ["u001", "u002"],
            ),
            case("multiple-roots", MULTIPLE_ROOTS, ["u001", "u002"]),
        ],
    }
    path = Path(__file__).with_name("reproduction.json")
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
