# Isolated baseline vs reinforced marker-prompt comparison (2026-11-03).
#
# Read-only against ongoing/超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub.
# The five outstanding chap_25.xhtml paragraph identities, sources, system
# message, rebuilt seed, and accepted exchange history are lifted verbatim
# from the recovery diagnostics so both variants see identical evidence.
# Only the final request assembly differs per variant.
#
# Variants:
#   baseline   = marker_prompt() + payload                     (pre-fix live assembly)
#   reinforced = assemble_marker_request(payload, language="english")
# Repair phase mirrors the pre-fix assembly on the baseline arm:
#   baseline repair   = marker_prompt() + marker_repair_notice(feedback) + payload
#   reinforced repair  = assemble_marker_request(payload, language="english",
#                                                feedback=feedback)
#
# Generation settings match the previous documented live check and both arms:
# temperature 0, max_tokens 4000, gemini-anon @ http://127.0.0.1:4995/v1.
#
# Resume behaviour (added after the 2026-11-03 12:58Z read-timeout crashed
# the first run): completed records in requests.jsonl are replayed and used
# as the trial state, so trials 1-4 are never re-measured. Transport errors
# (no response received) are logged and excluded from the five-send attempt
# budget and from validation evidence; after 3 consecutive transport errors
# a trial is recorded as inconclusive rather than as a prompt failure.

import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests  # noqa: E402

from book_maker.marker_workflow import (  # noqa: E402
    assemble_marker_request,
    marker_prompt,
    marker_repair_notice,
    paragraph_request,
    parse_paragraph_reply,
)
from book_maker.marker_names import MarkerError, validate_paragraph  # noqa: E402

BUNDLE = (
    Path(__file__).resolve().parents[2]
    / "ongoing"
    / "超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub"
)
OUT = Path(__file__).resolve().parent
BASE_URL = "http://127.0.0.1:4995/v1"
MODEL = "gemini-anon"
KEY = "anon"  # literal anonymous key from scripts/run_grey_translation.ps1

IDENTITIES = [
    "epub:25:chap_25.xhtml:31:8d150b9b1713c93a",
    "epub:25:chap_25.xhtml:35:dead30795cbebbec",
    "epub:25:chap_25.xhtml:36:332c12cc085d8e1c",
    "epub:25:chap_25.xhtml:41:29fa0ef66c35376c",
    "epub:25:chap_25.xhtml:53:c41373e498ae14f1",
]
PAR_INDEX = {
    "epub:25:chap_25.xhtml:31:8d150b9b1713c93a": 12,
    "epub:25:chap_25.xhtml:35:dead30795cbebbec": 16,
    "epub:25:chap_25.xhtml:36:332c12cc085d8e1c": 17,
    "epub:25:chap_25.xhtml:41:29fa0ef66c35376c": 22,
    "epub:25:chap_25.xhtml:53:c41373e498ae14f1": 34,
}
SOURCES = dict(zip(IDENTITIES, [
    "“想不出来。”一色摊手，“感觉就是情报完全不足啊，我身上完全没有什么值得女王探究的东西，展示出来的能力也是乱七八糟的没什么可研究性，其他方面的价值我觉得也不至于得到女王这么大宽容。”",
    "一色本来想移开屁股展现绅士风范，但是闻着鼻尖萦绕的淡淡蜂蜜一般的香甜气息，有些心旷神怡呢，软软的身子，很贴心。舍不得离开了呢。",
    "不过身边少女眼睛里闪烁的似乎带有侵略性的光芒让一色有些紧张。",
    "呱！搅、搅什么哇？一色蒙圈了，食蜂操祈对自己的好感度异常的高啊，自己人机托管的时间里到底发生了什么？",
    "一色扭动前进了一下，脑袋顶到了柔软的肚子。",
]))
SOURCE_LIST = [SOURCES[i] for i in IDENTITIES]

# Verbatim read-only neighbor context from the 2026-11-03T12:56:09 repair
# attempt (identical for both arms).
READ_ONLY_CONTEXT = (
    "Read-only original source immediately before/after the failed paragraphs. "
    "Do not return units for this context:\n"
    "Source before paragraph 11: “哦？那说说看是什么目的呢？”食蜂操祈美眸频闪，一色这家伙说的确实没问题呢。\n"
    "Source after paragraph 13: “呼~一色同学确实是情报不足呢，如果我不提供一点情报，你这样根本无法得知我不可——告人的目的哦。”食蜂操祈依然笑意盈盈。\n"
    "Source before paragraph 15: “嗯哼~万一我的要求，就是很过分呢？”食蜂操祈走到一色的床前，贴着身子坐在了一色身边。\n"
    "Source after paragraph 18: “操祈同学……”\n"
    "Source before paragraph 21: 少女轻轻凑上来，藕臂环绕过一色的脖颈，将嘴唇凑到一色耳边轻柔吐息：“你知道人家等了多久嘛？”\n"
    "Source after paragraph 23: 还是说其实女王大人有什么不为人知的神秘癖好？\n"
    "Source before paragraph 33: 看着还是没有记忆觉醒迹象的一色，食蜂操祈咬咬牙：“你再往上来一点。”\n"
    "Source after paragraph 35: “再来一点……”\n"
)


def load_context():
    with zipfile.ZipFile(BUNDLE) as z:
        state = json.loads(z.read("META-INF/bbm-marker-state.json").decode("utf-8"))
    failure = state["diagnostics"]["failures"][39]
    messages = failure["request"]["messages"]
    context = messages[:-1]
    assert context[0]["role"] == "system"
    assert context[1]["role"] == "user" and context[1]["content"].startswith("${一色} woke up")
    assert context[-1]["role"] == "assistant"
    assert len(context) == 8  # system + seed + 3 accepted exchanges
    return context


def build_payload(ids, sources):
    _ids, payload = paragraph_request(sources, ids)
    assert _ids == list(ids)
    return payload


def validate_reply(raw, ids):
    results = {}
    reply = parse_paragraph_reply(raw, list(ids))
    for identity in ids:
        outcome = {"id": identity, "stage": None, "error": None, "label_details": None}
        if identity not in reply.texts:
            error = reply.errors.get(identity) or "missing from response"
            outcome.update(stage="parse", error=error)
            results[identity] = outcome
            continue
        text = reply.texts[identity]
        try:
            validate_paragraph(SOURCES[identity], text, PAR_INDEX[identity])
            outcome.update(stage="accepted", error=None)
        except MarkerError as error:
            outcome.update(
                stage="validation",
                error=str(error),
                label_details=getattr(error, "label_details", None),
            )
        results[identity] = outcome
    unknown = list(reply.unknown)
    return results, unknown


def _failure_feedback(failed, identity_order):
    lines = ["Failures from the previous response:"]
    for identity in identity_order:
        failure = failed.get(identity)
        if failure is None:
            continue
        index = PAR_INDEX[identity]
        lines.append(f"- id {identity} (original paragraph {index}): {failure['error']}")
        details = failure.get("label_details") or []
        if details:
            from book_maker.marker_workflow import _failure_corrections

            for correction in _failure_corrections(details):
                lines.append(f"  Correction: {correction}")
    lines.append(READ_ONLY_CONTEXT.rstrip("\n"))
    return "\n".join(lines) + "\n"


def send(messages, timeout=240):
    started = time.time()
    try:
        response = requests.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 4000,
            },
            timeout=timeout,
        )
    except requests.exceptions.RequestException as error:
        return None, {"error": f"transport error: {error}"}, time.time() - started
    latency = time.time() - started
    try:
        body = response.json()
    except Exception:
        body = {"error": f"non-JSON response (HTTP {response.status_code})"}
    return response.status_code, body, latency


def record(event):
    with (OUT / "requests.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in event.items() if k != "raw"}, ensure_ascii=False))


def load_records():
    path = OUT / "requests.jsonl"
    if not path.exists():
        return {}
    progress = {}
    for line in path.open(encoding="utf-8"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        progress.setdefault(rec.get("order"), []).append(rec)
    return progress


def rebuild_state(records):
    attempts = {i: 1 for i in IDENTITIES}
    accepted = set()
    failed = {}
    outstanding = list(IDENTITIES)
    terminal = None
    for rec in records:
        phase = rec.get("phase")
        if phase == "initial":
            accepted = set(rec.get("accepted", []))
            failed = dict(rec.get("failed", {}) or {})
            outstanding = [i for i in IDENTITIES if i not in accepted]
        elif phase == "repair":
            for identity, value in (rec.get("attempt") or {}).items():
                attempts[identity] = value
            accepted |= set(rec.get("accepted", []))
            failed = dict(rec.get("failed", {}) or {})
            outstanding = [i for i in outstanding if i not in accepted]
        elif phase == "exhausted":
            terminal = "exhausted"
        elif phase == "transport-error":
            pass  # not counted as an attempt (no response received)
    if terminal is None:
        terminal = "completed" if not outstanding else None
    return attempts, outstanding, failed, terminal


def repair_loop(label, variant, context, order, attempts, outstanding, failed):
    rounds = []
    consecutive_transport = 0
    while outstanding:
        remaining = [i for i in outstanding if attempts[i] < 5]
        if len(remaining) != len(outstanding):
            record({
                "variant": variant,
                "phase": "exhausted",
                "order": order,
                "outstanding": list(outstanding),
                "attempts": attempts,
            })
            rounds.append({"phase": "exhausted", "outstanding": list(outstanding)})
            return {"label": label, "variant": variant, "order": order,
                    "rounds": rounds, "attempts": attempts, "completed": False}
        feedback = _failure_feedback(failed, remaining)
        sub_ids = list(remaining)
        sub_sources = [SOURCES[i] for i in sub_ids]
        sub_payload = build_payload(sub_ids, sub_sources)
        if variant == "baseline":
            repair = marker_prompt() + marker_repair_notice(feedback) + sub_payload
        else:
            repair = assemble_marker_request(
                sub_payload, language="english", feedback=feedback
            )
        repair_messages = list(context) + [{"role": "user", "content": repair}]
        status, body, latency = send(repair_messages)
        if status is None:
            # Transport error: no response; not counted as an attempt.
            consecutive_transport += 1
            record({
                "variant": variant,
                "phase": "transport-error",
                "order": order,
                "error": body.get("error"),
                "latency_s": round(latency, 2),
            })
            rounds.append({"phase": "transport-error",
                           "latency_s": round(latency, 2)})
            if consecutive_transport >= 3:
                record({
                    "variant": variant,
                    "phase": "inconclusive",
                    "order": order,
                    "reason": "three consecutive transport errors",
                })
                rounds.append({"phase": "inconclusive",
                               "reason": "three consecutive transport errors"})
                return {"label": label, "variant": variant, "order": order,
                        "rounds": rounds, "attempts": attempts,
                        "completed": False, "inconclusive": True}
            continue
        consecutive_transport = 0
        for identity in remaining:
            attempts[identity] += 1
        try:
            raw = body["choices"][0]["message"]["content"] or ""
        except Exception:
            record({
                "variant": variant,
                "phase": "provider-error",
                "order": order,
                "http_status": status,
                "error": body.get("error", body) if isinstance(body, dict) else body,
                "latency_s": round(latency, 2),
                "attempts": attempts,
            })
            rounds.append({"phase": "provider-error",
                           "latency_s": round(latency, 2)})
            outstanding = [i for i in remaining if True]
            failed = {i: {"id": i, "stage": "transport", "error": "provider error"} for i in remaining}
            continue
        results, unknown = validate_reply(raw, remaining)
        newly_accepted = [i for i in remaining if results[i]["stage"] == "accepted"]
        still_failed = {i: results[i] for i in remaining if results[i]["stage"] != "accepted"}
        record({
            "variant": variant,
            "phase": "repair",
            "order": order,
            "http_status": status,
            "latency_s": round(latency, 2),
            "attempt": {i: attempts[i] for i in remaining},
            "accepted": newly_accepted,
            "failed": still_failed,
            "unknown_units": unknown,
            "raw": raw,
        })
        rounds.append({"phase": "repair", "latency_s": round(latency, 2),
                       "accepted": newly_accepted, "failed": still_failed})
        outstanding = [i for i in remaining if results[i]["stage"] != "accepted"]
        failed = still_failed

    return {"label": label, "variant": variant, "order": order,
            "rounds": rounds, "attempts": attempts, "completed": True}


def run_trial(label, variant, context, order, records):
    ids = IDENTITIES
    sources = SOURCE_LIST
    attempts = {i: 1 for i in ids}
    outstanding = list(ids)
    failed = {}

    initial_records = [r for r in records if r.get("phase") == "initial"]
    if initial_records:
        attempts, outstanding, failed, terminal = rebuild_state(records)
        if terminal:
            print(f"=== {label} ({variant}) already terminal ({terminal}); keeping recorded result")
            return {"label": label, "variant": variant, "order": order,
                    "rounds": [], "attempts": attempts,
                    "completed": terminal == "completed", "replayed": True}
        print(f"=== {label} ({variant}) resuming; outstanding: {len(outstanding)}")
        return repair_loop(label, variant, context, order,
                           attempts, outstanding, failed)

    payload = build_payload(ids, sources)
    if variant == "baseline":
        final = marker_prompt() + payload
    else:
        final = assemble_marker_request(payload, language="english")
    messages = list(context) + [{"role": "user", "content": final}]

    status, body, latency = send(messages)
    if status is None:
        record({
            "variant": variant,
            "phase": "transport-error",
            "order": order,
            "error": body.get("error"),
            "latency_s": round(latency, 2),
        })
        return {"label": label, "variant": variant, "order": order,
                "rounds": [{"phase": "transport-error"}],
                "attempts": attempts, "completed": False, "inconclusive": True}
    try:
        raw = body["choices"][0]["message"]["content"] or ""
    except Exception:
        raw = ""
        record({
            "variant": variant,
            "phase": "provider-error",
            "order": order,
            "http_status": status,
            "error": body.get("error", body) if isinstance(body, dict) else body,
            "latency_s": round(latency, 2),
            "attempts": attempts,
        })
        return {"label": label, "variant": variant, "order": order,
                "rounds": [{"phase": "provider-error"}],
                "attempts": attempts, "completed": False, "inconclusive": True}

    results, unknown = validate_reply(raw, outstanding)
    accepted = [i for i in outstanding if results[i]["stage"] == "accepted"]
    failed = {i: results[i] for i in outstanding if results[i]["stage"] != "accepted"}
    record({
        "variant": variant,
        "phase": "initial",
        "order": order,
        "http_status": status,
        "latency_s": round(latency, 2),
        "accepted": accepted,
        "failed": failed,
        "unknown_units": unknown,
        "raw": raw,
    })
    outstanding = [i for i in outstanding if i not in set(accepted)]
    print(f"=== {label} ({variant}) initial: {len(accepted)}/5 accepted, "
          f"{len(outstanding)} outstanding")
    return repair_loop(label, variant, context, order, attempts, outstanding, failed)


def main():
    context = load_context()
    order = ["baseline", "reinforced", "baseline", "reinforced", "baseline", "reinforced"]
    progress = load_records()
    trials = []
    for index, variant in enumerate(order, 1):
        label = f"trial-{index}"
        records = progress.get(index, [])
        trials.append(run_trial(label, variant, context, index, records))
    (OUT / "trials.json").write_text(
        json.dumps(trials, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for variant, items in (
        ("baseline", [t for t in trials if t["variant"] == "baseline"]),
        ("reinforced", [t for t in trials if t["variant"] == "reinforced"]),
    ):
        accepted = [t["completed"] for t in items]
        print(variant, "completed:", accepted)


if __name__ == "__main__":
    main()
