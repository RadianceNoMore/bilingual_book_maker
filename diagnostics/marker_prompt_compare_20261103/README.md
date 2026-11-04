# Marker prompt comparison: baseline vs reinforced (2026-11-03)

Live, isolated comparison of the five paragraphs that exhausted the
five-send recovery in `chap_25.xhtml` of
`ongoing/超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub`. No other
content was sent, the recovery EPUB was only read, and no files outside
`diagnostics/marker_prompt_compare_20261103/` were touched.

## Method

- Identities, sources, the `system` message, the rebuilt seed, and the
  accepted exchange history are lifted verbatim from the recovery
  diagnostics (`diagnostics.failures` #39 of the embedded
  `META-INF/bbm-marker-state.json`), so both variants see byte-identical
  evidence. The final request itself is the only variable:
  - **baseline**: the pre-fix live assembly, `marker_prompt() + payload`
    (byte-identical to what the incident sent for a first send; the repair
    arm is `marker_prompt() + marker_repair_notice(feedback) + payload`,
    also byte-identical);
  - **reinforced**: `assemble_marker_request(payload, language="english")`
    (repair arm adds the repair notice with the per-paragraph corrections).
- Generation settings for every send: model `gemini-anon`, temperature 0,
  `max_tokens 4000`, anonymous endpoint `http://127.0.0.1:4995/v1` (the
  literal key is `anon`, matching `scripts/run_grey_translation.ps1`).
- Six interleaved trials: baseline, reinforced, baseline, reinforced,
  baseline, reinforced (A B A B A B), one request each (the five units in a
  single `<bbm-request>`).
- Then selective repair with the existing five-attempt limit (the initial
  send counts as attempt 1 per outstanding ID); all outstanding IDs ride
  together in one repair request per attempt, with read-only neighbor
  context identical to the incident's.
- Validation is the repository's own deterministic path:
  `parse_paragraph_reply` followed by `validate_paragraph` (with
  `label_details`). Accept/reject decisions do not depend on this report.
- Records: `requests.jsonl` (one line per request, raw model output
  included, no credentials — the endpoint is anonymous), `trials.json`
  (per-trial rollup).

## Results

Initial send (first-send accepted of 5):

| Trial | Variant | Initial | Sends | Outcome |
|-------|---------|---------|-------|---------|
| 1 | baseline | 1/5 | 2 | completed after 1 repair round |
| 2 | reinforced | 1/5 | 5 | **exhausted** (3 outstanding: 31, 35, 36) |
| 3 | baseline | 1/5 | 5 | completed |
| 4 | reinforced | 0/5 | 5 | **exhausted** (3 outstanding: 31, 35, 36) |
| 5 | baseline | 0/5 | 4 | completed |
| 6 | reinforced | 5/5 | 1 | completed, no repairs |

Totals: baseline 2/15 first-send acceptances, **3/3 completed**;
reinforced 6/15 first-send acceptances, **1/3 completed**.

## Failure modes observed (both arms)

- Invented Chinese label `${其}` (paragraph 16; both arms) — exactly the
  "marker for an absent source label" case; the English-pronoun hint does
  not apply and is not supposed to.
- English pronoun markers (`${his}`, `${he}`, `${she}`, `${him}`, `${He}`,
  `${their}`, `${them}`) in both arms, including under the reinforced
  prompt (trial 4, attempts 2 and 5).
- Chinese pronoun-like markers (`${她的}`, `${他的}`) in the baseline.
- Over-repeated valid names (`${一色}` ×2–3 against the source's single
  occurrence) in both arms.
- One reinforced repair round returned duplicate/ambiguous `bbm-unit` ids
  (trial 4, attempt 3) — a framing failure, not a marker-content failure.

## Interpretation and limitations

- The sample is far too small to rank prompts (n = 6 multi-unit sends,
  high variance: one reinforced trial was a perfect 5/5 on the first
  send). **No improvement from the reinforced prompt is demonstrated, and
  none is claimed.** The deterministic validator remains the gate.
- The reinforced trial that failed was hardest to interpret honestly: its
  outstanding IDs (31, 35, 36) failed with the same English-pronoun mode
  the new feedback explicitly warns about, so the guidance is reaching the
  model in effect only as text; compliance is not guaranteed.
- Context was held identical per the plan; the comparison does not
  measure the rebuilding path or provider-error seeds.
- One transport read-timeout (240 s) hit trial 4's first repair during
  the initial run; it received no response, was excluded from the
  five-send budget, and neither arm was credited or blamed. Trials 1–4
  from that run were preserved; only trial 4's repair phase was re-run.
- Provider errors (non-response, non-JSON) were absent after that fix; had
  any occurred, they would be reported separately from validation
  failures.
- Diagnostics from the 2026-11-02 live check remain under
  `diagnostics/marker_live_20261102/`. Raw responses for every send here
  are in `requests.jsonl`.

## Bottom line

The deterministic validator remains mandatory. Adoption of the reinforced
prompt is reasonable (it is strictly clearer instruction text with no
behavioral downside and identical assembly code paths), but this sample
gives no evidence that it alone fixes marker drift — the model kept
producing pronoun markers under both assemblies, and the five outstanding
paragraphs in the real recovery were not demonstrated to pass here.
The user's ordinary resume command remains
`scripts/run_grey_translation.ps1 -Resume`.
