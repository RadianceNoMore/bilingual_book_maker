"""One bounded live probe of the text198 singleton; no recovery file is written.

Run from the repository root:

    python diagnostics/text198/live_probe.py

It reads the preserved copy only, sends one request through the local
OpenAI-compatible gateway, and records the decoded client-boundary response.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TRACE = Path(__file__).with_name("live-trace")
os.environ["BBM_MARKER_TRACE_DIR"] = str(TRACE)

from book_maker.marker_bundle import read_state  # noqa: E402
from book_maker.marker_workflow import paragraph_request, parse_paragraph_reply  # noqa: E402
from book_maker.translator.chatgptapi_translator import ChatGPTAPI  # noqa: E402

UNIT_ID = "epub:5:chap_5.xhtml:77:0f1f266cfb3e8c2d"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    state = read_state(Path(__file__).with_name("recovery-preserved.epub"))
    record = state["source_windows"]["windows"][1]
    unit = next(row for row in record["units"] if row["unit_id"] == UNIT_ID)
    translator = ChatGPTAPI(
        key="anon",
        language="th",
        api_base="http://127.0.0.1:4995/v1",
        prompt_sys_msg=(
            "You are a professional literary translator. Follow the requested "
            "XML paragraph protocol exactly."
        ),
        temperature=0,
    )
    translator.set_model_list(["gemini-anon"])
    translator.max_chunk_attempts = 1
    _ids, payload = paragraph_request([unit["prepared"]], ["u003"])
    context = {
        "summary": record.get("incoming_summary") or "",
        "accepted_source_prefix": "",
    }
    raw = translator.translate_marker_units(
        payload, "", premarked=True, context=context
    )
    reply = parse_paragraph_reply(raw, ["u003"])
    report = {
        "unit_id": UNIT_ID,
        "provider": "local gateway http://127.0.0.1:4995/v1",
        "model": "gemini-anon",
        "response_characters": len(raw),
        "response_starts": raw[:200],
        "accepted": "u003" in reply.texts,
        "error": reply.errors.get("u003"),
        "framing_error": reply.framing_error,
        "unknown": reply.unknown,
        "metadata": translator._protected_response_metadata,
        "trace_directory": str(TRACE),
    }
    path = Path(__file__).with_name("live-probe.json")
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4000])
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
