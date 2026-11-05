"""Reproduce the local gateway's multi-candidate concatenation offline.

The gateway at AI-BRIDGE/gemini-pool/anonymous/server.py is read-only input.
No HTTP request is made and no service is restarted.  Gemini's web payload can
carry more than one candidate; the gateway currently joins every candidate with
two newlines into one OpenAI completion.  That is the observed restart shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

GATEWAY_DIR = Path(
    r"C:\Users\Tina_\Documents\PlayingWithRepo\AI-BRIDGE\gemini-pool\anonymous"
)
sys.path.insert(0, str(GATEWAY_DIR))
import server  # noqa: E402


def raw_for(payload):
    frame = json.dumps([["wrb.fr", None, json.dumps(payload)]])
    return (server.XSSI_GUARD + "\n" + str(len(frame)) + "\n" + frame).encode()


def raw_for_many(payloads):
    frames = [
        json.dumps([["wrb.fr", None, json.dumps(payload)]])
        for payload in payloads
    ]
    return (server.XSSI_GUARD + "\n" + "\n".join(frames)).encode()


def payload_with(candidates):
    payload = [None] * 47
    payload[4] = candidates
    payload[42] = "3.5 Flash-Lite"
    return payload


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    partial = '<bbm-response><bbm-unit id="u003">"Don\'t'
    complete = (
        '<bbm-response><bbm-unit id="u003">'
        "complete translated paragraph</bbm-unit></bbm-response>"
    )

    distinct = server._parse_generation(
        raw_for(
            payload_with(
                [
                    ["rc_1", [partial]],
                    ["rc_2", [complete]],
                ]
            )
        )
    )
    same_candidate_snapshots = server._parse_generation(
        raw_for_many(
            [
                payload_with([["rc_1", [partial]]]),
                payload_with([["rc_1", [complete]]]),
            ]
        )
    )

    report = {
        "gateway_file": str(GATEWAY_DIR / "server.py"),
        "http_requests": 0,
        "distinct_candidates": {
            "candidate_ids": ["rc_1", "rc_2"],
            "returned_text": distinct.text,
            "contains_two_roots": distinct.text.count("<bbm-response>") == 2,
            "joined_with_two_newlines": "\n\n" in distinct.text,
        },
        "same_candidate_snapshots": {
            "candidate_ids": ["rc_1", "rc_1"],
            "returned_text": same_candidate_snapshots.text,
            "snapshot_replaced_not_joined": same_candidate_snapshots.text == complete,
        },
        "finding": (
            "Distinct Gemini candidates are concatenated by _parse_generation; "
            "repeated snapshots of one candidate are correctly replaced. "
            "This reproduces the text198 response restart at the local gateway "
            "boundary without calling a provider."
        ),
    }
    path = Path(__file__).with_name("gateway-join.json")
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
