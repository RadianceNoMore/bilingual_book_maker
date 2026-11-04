"""Isolated live probe; never edits translator code or EPUBs."""
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from book_maker.vendor import deeplx


def send(name, text, options=None):
    request_id = deeplx.get_random_number()
    params = {
        "texts": [{"text": text, "requestAlternatives": 0}],
        "splitting": "newlines",
        "lang": {"source_lang_user_selected": "ZH", "target_lang": "EN"},
        "timestamp": deeplx.get_timestamp(deeplx.get_i_count(text)),
        "commonJobParams": {"wasSpoken": False, "transcribe_as": ""},
    }
    params.update(options or {})
    payload = {"jsonrpc": "2.0", "method": "LMT_handle_texts", "id": request_id, "params": params}
    body = json.dumps(payload, ensure_ascii=False)
    if (request_id + 5) % 29 == 0 or (request_id + 3) % 13 == 0:
        body = body.replace('"method":"', '"method" : "')
    else:
        body = body.replace('"method":"', '"method": "')
    row = {"case": name, "source": text, "options": options or {}}
    started = time.monotonic()
    try:
        response = requests.post(deeplx.DEEPL_API, data=body.encode("utf-8"), headers=deeplx.HEADERS, timeout=(10, 25))
        row.update(status=response.status_code, body=response.text[:12000])
    except requests.RequestException as exc:
        row.update(error=type(exc).__name__)
    row["elapsed_seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


if __name__ == "__main__":
    cases = [
        ("plain_control", "御坂美琴抬起手。", None),
        ("xml_no_options", "<p><keep>御坂美琴</keep>抬起手。</p>", None),
        ("xml_ignore_options", "<p><keep>御坂美琴</keep>抬起手。</p>", {"tag_handling": "xml", "ignore_tags": ["keep"]}),
        ("bash_marker_control", "${御坂美琴}抬起手。", None),
    ]
    rows = []
    for case in cases:
        row = send(*case)
        rows.append(row)
        if row.get("status") == 429:
            break
        time.sleep(1)
    Path(__file__).with_suffix(".results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
