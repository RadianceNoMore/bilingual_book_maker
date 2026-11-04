# Debug plan: first-window name extraction

Status: fixes implemented; focused checks passed, 2026-11-04.
Scope: the local `bilingual_book_maker-window-names` fork, branch
`source-window-names`. Runtime fixes are applied in this fork; the original fork and saved recovery were not modified.

## Evidence and reproduction

Inspect the original log:
`C:\Users\Tina_\AppData\Local\Temp\Ditto\DragFiles\text_187.txt`.
Inspect the embedded `META-INF/bbm-marker-state.json` in:
`ongoing/超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub`.

Recovery SHA-256 at investigation time:
`55984bbed878e70dfca0e7d110df29ff87acf10a7a3fbf32bcfbe09df82b2bdb`.
Keep this recovery unchanged as diagnostic evidence. Read the ZIP directly;
do not resume it or replay its requests to the provider during offline debugging.

The frozen plan contains 78 windows and 12,774 selected units/fragments. The
first extraction covers 164 units and 8,168 estimated source tokens. The name
catalogue is empty because no extraction was committed. Translation has not
started. The configured summary budget was 10,500; no summary request was made.

All three saved requests have the same user-prompt SHA-256:
`d8c306cf8c54ffa0a311bbadb6cd410747a805ead0b19f710ab81e13b62b4da2`.
All responses are complete fenced JSON objects and report `finish_reason=stop`,
with no explicit refusal. There is no recorded transport or token-limit error.

| Attempt | Response characters | Extracted labels | Result after removing the outer fence |
| --- | ---: | ---: | --- |
| 1 | 4,534 | 14 | Exact-source citation validation fails: 14 invalid label/citation pairs across two labels |
| 2 | 5,851 | 12 | Exact-source citation validation fails: 19 invalid label/citation pairs across four labels |
| 3 | 1,753 | 1 | Schema and grounding pass; only `超电磁炮` is returned |

Attempts 1 and 2 cite paragraphs containing only `美琴` for `御坂美琴`, among
other mismatches. Attempt 3 omits 16 distinct labels seen in earlier responses
that occur literally in the current source, including `御坂美琴`, `一色`,
`食蜂操祈`, and `白井黑子`. Earlier rejected responses are diagnostic evidence,
not an approved name inventory. This comparison exposes undercoverage; it does
not prove that every earlier proposed label belongs in the final catalogue.

## Confirmed causes and diagnostic gaps

1. `source_windows.validate_extraction()` applies `json.loads(raw)` directly.
   Every reply begins with a Markdown fence, so validation stops at character
   zero before reaching schema or citation checks.
2. The extraction prompt asks for unit citations but does not say that one
   exact witness is sufficient. The provider returns long lists of IDs and
   sometimes substitutes entity identity for literal spelling.
3. Retry requests are identical and contain no failure explanation. They pay
   for another full extraction without telling the provider what was wrong.
4. Valid structure and grounding do not establish complete name extraction.
   A fence-only fix would accept attempt 3 despite obvious omissions.
5. The console hides the response prefix, label count, and specific bad unit
   IDs. Raw evidence exists in the EPUB but requires manual ZIP inspection.
   Extraction backoff incorrectly says "Marker paragraph request failed".
6. Extraction preflight reserves 512 estimated output tokens, but the request
   has no corresponding output cap. The three replies estimate to 2,353,
   2,705, and 891 tokens using `num_tokens_from_text`. Thus the reservation is
   insufficient even for the sparse reply. It did not cause this failure;
   the endpoint reports no known context limit and no truncation. It is a
   separate sizing defect to correct.

## Proposed implementation order

### 1. Normalize the response envelope locally

In `book_maker/source_windows.py`, accept either a bare JSON object or one
complete outer Markdown code block with a `json` or empty language label.
Remove only that envelope, then run the existing full schema and grounding
validation. Preserve the raw response for diagnostics.

Reject surrounding commentary, multiple blocks/objects, incomplete fences,
truncated JSON, duplicate JSON keys, wrong window IDs, unknown IDs, and reserved
marker syntax. Do not use the general `extract_json_object()` scanner directly:
it intentionally searches through surrounding text and can select an inner
object, which is broader than this extraction contract.

Both narrative and auxiliary extraction must use the same normalizer. Removing
a harmless wrapper must not consume another provider request.

### 2. Make exact citation work smaller and actionable

Update `source_window_prompt()` to request the complete set of exact source
spellings and one valid witness unit per label. Clarify that the model need not
enumerate every occurrence: local matching already scans all window units.
Explicitly distinguish a full name, surname, and nickname; a paragraph with
`美琴` cannot witness `御坂美琴`.

Keep strict citation validation. Do not silently drop bad rows/citations or
convert a partial response into a successful extraction. Report the label and
the exact offending unit IDs, with bounded source excerpts in verbose output.

For a failed extraction, pass bounded, structured feedback to the next attempt:
wrong citations, malformed fields, and any detected omissions. Keep the full
original window and the same incoming source summary. Feedback is protocol
repair data and must never enter translation context or cumulative summaries.
Retain the three-attempt limit; normal successful windows still use one call.

### 3. Handle incomplete extraction explicitly

Before accepting a retry, compare it against exact-source candidates observed
in earlier attempts and against previously collected labels actually present
in this window. In the recorded case, the one-label third response must be
flagged as conflicting/incomplete rather than automatically treated as enough.

Use the remaining extraction budget to request a complete corrected result and
identify omitted candidates for consideration. Never union rejected responses
into the catalogue. If a material coverage conflict remains unresolved when
the budget ends, save an extraction-pending recovery and stop before translation.
Record which candidates caused the conflict so resume can diagnose it.

Define the conflict rule and how an unsupported earlier proposal can be
explicitly dismissed before implementing this gate; candidate absence alone
must not permanently force a false positive into the inventory. Do not invent
a universal minimum name count. An empty or one-name window can be legitimate.
One model extraction cannot prove semantic completeness; state that limitation
and expose coverage diagnostics without adding permanent voting/review calls.

### 4. Improve progress and failure reporting

In `marker_workflow.py` and the provider diagnostic path, report the stage,
window, attempt, elapsed time, finish reason, response length, detected wrapper,
parsed label count, and validation category. Print a bounded escaped response
prefix on parsing failure, plus the recovery path for full diagnostic evidence.
Keep credentials and HTTP headers out of diagnostics.

Make backoff text stage-specific: extraction must say extraction, not paragraph
repair. Keep extraction feedback and semantic coverage conflicts separate from
transport failures, refusal, and provider truncation. Preserve raw responses and
their metadata even if normalization succeeds and later grounding fails.

### 5. Correct sizing and preserve resume behavior

Replace the hardcoded 512-token extraction reservation with a documented
extraction output budget consistent with the actual request. Include system
instructions and framing in sizing. If the endpoint supports an output cap,
use the existing capability fallback and detect truncation; otherwise print
that the reservation is advisory. Size the allowance using the reduced
one-witness response contract and validate it against the recorded replies.

Do not present reducing `SourceSummaryTokens` as the fix for this failure.
The user's 10,500-token summary budget affects later windows and is part of
the frozen plan; changing it can invalidate resume compatibility.

Version changed extraction prompts/contracts and record the version used for
each committed preparation. Never relabel an existing preparation as if it
came from a new prompt or regenerate accepted chunks. Check resume fingerprint
rules before changing settings: this specific run has no prepared windows,
but other runs may. Preserve incompatible recoveries and explain any required
fresh run rather than resetting state silently.

## Validation and completion criteria

1. Offline-replay all three saved responses against their saved source units.
   Normalization must expose attempts 1/2's citation failures; attempt 3 must
   expose the coverage conflict. No catalogue mutation or network call occurs.
2. Add small synthetic fixtures for fenced/bare JSON, malformed/ambiguous
   envelopes, duplicate keys, full/short-name citation mismatches, legitimate
   empty/single-name windows, and sparse retries that omit grounded candidates.
   Do not commit the full copyrighted source or raw provider request payloads.
3. Verify a fenced, complete, grounded response prepares the whole window in
   one call; names enter the persistent catalogue before translation and
   literal replacement remains longest-first throughout all units.
4. Verify bounded corrective extraction retries, exhaustion, stage-specific
   logs, and recovery/resume. A committed window must not be extracted again;
   pending extraction must not allow translation or summary calls.
5. Verify summary/extraction budgets and cap fallback with a fake provider,
   including unknown provider limits and truncated responses.
6. Run the focused source-window, marker-bundle, marker-end-to-end, and CLI
   documentation tests, plus compile and launcher syntax checks if touched.
   Avoid repeating the previously stalled full suite without a specific need.
7. After offline checks, validate one real window through the configured local
   provider as a separately observable smoke run. Report actual labels,
   occurrence counts, request counts, and any retries. A whole-book success
   claim requires a completed run; syntax/grounding checks alone do not prove
   semantic extraction coverage or translation quality.

## Implementation and validation record

Implemented in the fork:

- Extraction now accepts one complete bare JSON object or one complete
  `json`/unlabeled Markdown fence. It still rejects surrounding prose and
  malformed/multiple objects.
- Each name must cite exactly one literal witness unit. Citation failures name
  the invalid unit IDs.
- A retry receives the validation error and source-grounded candidate labels
  from failed responses. It must include each candidate with one witness or
  explicitly list it in `excluded_candidates`; unreviewed candidates block
  acceptance. Rejected attempts are never merged into the name catalogue.
- The extractor requests at most 2,048 output tokens; preflight uses the same
  allowance. The existing provider fallback handles rejected output-cap
  parameters, and the response's finish reason remains checked for truncation.
- Extraction failure logs show response length, finish reason, candidate count,
  and an escaped 240-character prefix. Backoff identifies the extraction stage.
- The manifest records extraction prompt version 2. The failed EPUB has no
  committed extraction or translations; its run fingerprint settings remain
  compatible, so resume can retry the unfinished window while reading prior
  failure diagnostics as candidate evidence.

Validation on 2026-11-04:

- `python -m pytest tests/test_source_windows.py tests/test_marker_end_to_end.py tests/test_marker_bundle.py tests/test_cli_documentation.py -q --tb=short` — **29 passed**.
- `python -m compileall -q book_maker` — passed.
- `git diff --check` — passed; Git reported only LF/CRLF normalization notices.
- The original recovery EPUB's SHA-256 remains
  `55984bbed878e70dfca0e7d110df29ff87acf10a7a3fbf32bcfbe09df82b2bdb`.
- No live provider smoke call has been made. A first response that is
  syntactically valid and contains no previously observed candidates still
  cannot be proven semantically complete by schema validation alone; that
  limitation remains visible in the plan and requires live sampling to assess.
