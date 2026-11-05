"""Reproduce both text198 singleton restart shapes against the real parser.

Run from the repository root:

    python diagnostics/text198/reproduce_assembly.py

No provider is called.  Each fixture is the decoded content shape observed at
the client boundary; the parser must reject the ambiguous unit and preserve an
independently safe sibling.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from book_maker.marker_workflow import parse_paragraph_reply
from book_maker.translator.chatgptapi_translator import response_framing_facts

SHAPES = {
    "restart_inside_text": (
        '<bbm-response>\n<bbm-unit id="u003">"Don\'t<bbm-response>\n'
        '<bbm-unit id="u003">complete translated paragraph</bbm-unit>\n'
        "</bbm-response>"
    ),
    "restart_inside_attribute": (
        '<bbm-response>\n<bbm-unit id="u00<bbm-response>\n'
        '<bbm-unit id="u003">complete translated paragraph</bbm-unit>\n'
        "</bbm-response>"
    ),
    "restart_with_safe_sibling": (
        '<bbm-response>\n<bbm-unit id="u004">Safe sibling.</bbm-unit>\n'
        '<bbm-unit id="u003">"Don\'t<bbm-response>\n'
        '<bbm-unit id="u003">complete translated paragraph</bbm-unit>\n'
        "</bbm-response>"
    ),
}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cases = []
    for name, raw in SHAPES.items():
        requested = ["u003", "u004"] if "sibling" in name else ["u003"]
        reply = parse_paragraph_reply(raw, requested)
        cases.append(
            {
                "case": name,
                "requested": requested,
                "accepted": reply.texts,
                "errors": reply.errors,
                "unknown": reply.unknown,
                "diagnostics": reply.diagnostics,
                "framing_error": reply.framing_error,
                "framing": response_framing_facts(raw),
                "ambiguous_unit_rejected": "u003" not in reply.texts,
                "safe_sibling_preserved": (
                    reply.texts.get("u004") == "Safe sibling."
                    if "sibling" in name
                    else None
                ),
            }
        )
    report = {
        "source": "text198 decoded client-boundary shapes",
        "provider_calls": 0,
        "cases": cases,
    }
    path = Path(__file__).with_name("assembly-reproduction.json")
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
