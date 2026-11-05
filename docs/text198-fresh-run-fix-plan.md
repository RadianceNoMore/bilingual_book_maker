# Fresh-run translation failure: text_198.txt

Status: local fixes implemented and verified on 2026-11-05; the upstream
gateway multi-candidate join is identified and reproduced but not fixed in the
live service.

Implementation handoff, verification, gateway evidence, risks and rollback:
`docs/text198-fresh-run-fix-implementation.md`.
Offline reproductions and the preserved read-only recovery copy live in
`diagnostics/text198/`. The real recovery EPUB digest was unchanged by the
bounded live probe.

Repository: `bilingual_book_maker-window-names`.

## What happened

The launcher explicitly reports `Resume=False`. This is a fresh run using the
text196 implementation (`b454a1f`), including short provider IDs and shrinking
repair batches. It is not the window-11 extraction failure from text197.

Name extraction succeeded for windows 1 and 2. Window 1 completed. The run
stopped in window 2, chunk 3/4, after approximately 41 minutes. That chunk
accepted 37 of its 40 paragraphs.

The three unresolved paragraphs are:

| Permanent ID | Provider alias | Last failure | Attempts used |
| --- | --- | --- | ---: |
| `epub:5:chap_5.xhtml:77:0f1f266cfb3e8c2d` | `u003` | Restarted XML with an ambiguous duplicate | 5/5 |
| `epub:6:chap_6.xhtml:14:8452ed434206873c` | `u021` | Standalone provider failure message | 4/5 |
| `epub:6:chap_6.xhtml:15:363b123b121e911b` | `u022` | Unrecognized provider failure message reported as missing output | 4/5 |

The final singleton response for `u003` contains this structure:

```xml
<bbm-response>
<bbm-unit id="u003">"Don't<bbm-response>
<bbm-unit id="u003">...complete translated paragraph...</bbm-unit>
</bbm-response>
```

An earlier singleton response restarted inside the ID attribute itself:

```xml
<bbm-response>
<bbm-unit id="u00<bbm-response>
<bbm-unit id="u003">...complete translated paragraph...</bbm-unit>
</bbm-response>
```

These are already present in the decoded content received from the provider.
The parser correctly refuses ambiguous attribution. Short IDs and smaller
batches cannot repair this response-assembly problem; it occurs with one unit.
The metadata reports `finish_reason=stop`, no structured refusal, and no
requested translation output cap. A context-limit or policy cause is not proven.

The complete log contains eight initial translation sends and 110 repair sends:
118 logical translation requests, excluding name extraction, summarization,
and any underlying retries. The failing chunk alone made 29 requests. There
are 21 multiple-root warnings. These figures explain the slow progress better
than the printed final attempt total.

## Recovery evidence

The current recovery EPUB passes ZIP integrity and `validate_state`:

- Window 1: 164/164 accepted, summary complete.
- Window 2: 117/132 accepted, summary not yet updated.
- Final checkpoint: 246 IDs, plus 35 additional unique journal entries.
- Total saved: 281 distinct units. Fifteen window-2 units remain, comprising
  three unresolved units in this chunk and twelve not yet processed.

The earlier stop-time message shows 244 checkpointed plus 37 journal entries.
The loader subsequently promotes two contiguous accepted paragraphs into the
checkpoint before saving. Both decompositions total 281; this is not lost work.

This fresh run writes the same recovery pathname shown in the earlier run,
whose checkpoint previously contained 1,603 accepted units. The current file
now represents this fresh run. Availability of an older backup has not been
established; do not claim that all 1,603 remain in the current EPUB.

## Confirmed local defects

1. **Premature queue termination.** At the top of `MarkerWorkflow.translate`,
   `len(eligible) != len(outstanding)` immediately exhausts the whole batch.
   Thus `u003` reaching 5/5 stops `u021` and `u022` at 4/5.
2. **Impossible progress text.** `max(outstanding.attempts) + 1` prints `6/5`
   even though no sixth send occurs. Mixed attempt groups also make other
   next-attempt messages disagree with the actual queued request.
3. **Mislabelled request totals.** `_exhaust_recovery` sums attempts only for
   the remaining paragraphs: `5 + 4 + 4 = 13`. That is neither the chunk's 29
   requests nor all paragraph-send participations.
4. **Counters omitted on one failure path.** The top-of-loop exhaustion call
   does not pass accumulated provider calls or usage, so it prints unavailable
   despite metadata recording individual provider calls and usage.
5. **Retry metadata describes the original chunk.** A singleton send still
   reports `requested_units=40` and the original output estimate because
   `_protected_completion` trusts the unchanged loader diagnostic context.
6. **Provider failure classification is incomplete.** The standalone message
   `I encountered an error doing what you asked. Could you try again?` goes to
   XML parsing and becomes a missing-unit error. Recognized whole-response
   boilerplate is retried without the transport branch's delay.
7. **Full tracing was disabled.** Embedded diagnostic excerpts help identify
   corruption but cannot reveal the gateway's intermediate stream assembly.

## Fix 1: trace and correct response assembly at its source

Relevant client files: `book_maker/translator/chatgptapi_translator.py` and
`book_maker/marker_trace.py`. The actual gateway implementation must be located
before assigning it a patch; it is not established by this log.

1. Preserve a read-only diagnostic copy of this recovery file with a digest and
   manifest before any live replay. Enable `BBM_MARKER_TRACE_DIR` for the replay.
   Use separate recovery/output paths; do not advance the real book.
2. Replay one failing singleton with the same source markers and original-
   language context. Capture the sanitized request, full decoded response,
   attempt ID, finish reason, and digest at the client boundary. Never capture
   authentication secrets in diagnostic files.
3. Identify the configured OpenAI-compatible endpoint and its locally accessible
   implementation/logs. Correlate this one request through its upstream calls.
   Compare upstream events, the gateway's final response, and the client's
   decoded completion. Use existing authorized local access; do not change
   remote infrastructure merely to obtain a trace.
4. Investigate concrete assembly mechanisms: cumulative snapshots appended as
   deltas, a restarted generation appended to an earlier partial response,
   multiple candidates joined together, or retry accumulators reused across
   upstream calls. These are hypotheses until traced.
5. Patch the first proven faulty boundary. Delta streams append only deltas;
   snapshot streams replace the current snapshot; retry/new-generation state
   resets its buffer; candidates remain distinct. Determine the actual protocol
   from its events and implementation before choosing a rule.
6. Return one coherent selected completion and preserve available finish/error
   metadata. If the upstream provider itself emits corrupt content, report that
   finding and retain bounded recovery. Do not claim a local gateway fix without
   evidence or automatically switch the user's provider.

Keep the current conservative parser: do not accept the last root, strip an
arbitrary prefix, select the prettiest duplicate, or attribute by position.
Choosing a coherent upstream generation is different from guessing which
fragment of an already ambiguous final reply to trust.

Acceptance: either the traced faulty boundary is fixed and tested, or the
remaining upstream fault is identified with reproducible evidence and clearly
reported as unresolved. A successful client retry alone is not a transport fix.

## Fix 2: finish eligible repairs before reporting exhausted units

Relevant code: `MarkerWorkflow.translate`, `_next_retry_batch`, and
`_exhaust_recovery` in `book_maker/marker_workflow.py`.

- Partition pending items into eligible and exhausted sets. Park exhausted
  items; exclude them from future sends while keeping their last errors.
- Continue scheduling eligible items using the existing attempt-group caps
  and source order. Increment counters only for items actually sent.
- Stop successfully when every item is accepted. Raise the recovery exception
  when no eligible item remains and unresolved items still exist.
- In the text198 case, `u021` and `u022` must each receive their fifth attempt
  after `u003` exhausts. Never send a sixth request for `u003`.
- Persist each accepted subset immediately. Preserve the marker multiset,
  permanent IDs, stable aliases, source-only context, and recovery journal.
- Keep explicit refusals and terminal transport errors terminal. Queue draining
  applies to recoverable paragraph failures, not to refusal bypass.
- State the actual selected next group's attempt, size, and remaining queued
  units. If no eligible group remains, print exhaustion instead of `6/5`.

## Fix 3: count requests and paragraph attempts accurately

- Increment a chunk-level logical-request counter exactly once per `send`.
- Separately count paragraph participations across all items. One request with
  eight units is one logical request and eight paragraph participations.
- Accumulate observed underlying provider calls and available provider-reported
  usage on success and exception paths. Mark partial/unavailable measurements
  explicitly rather than treating missing measurements as zero.
- Pass one structured statistics object to every terminal reporting path.
  Include successes as well as failed replies in the totals.
- At each actual request, derive `requested_units` and the output estimate from
  that payload. Keep `original_chunk_units` separate from retry-subset size.
- Keep extraction and summary stage counts separate. State whether a printed
  total covers this process, this chunk invocation, or persisted earlier work.
- Report both exhausted IDs and any eligible work interrupted by a terminal
  provider exception. Do not say all pending paragraphs exhausted five attempts
  when their counters differ.

Acceptance: replaying this schedule yields 29 logical requests for the failing
chunk, rather than the misleading total 13, with each item's own attempt count.
After the scheduling fix, totals reflect the additional permitted sends.

## Fix 4: classify provider failures and pace retries

Relevant code: `book_maker/provider_refusal.py` and the translation retry loop.

- Add the observed standalone error message to the narrow boilerplate matcher.
  Review the related text197 message `I seem to be encountering an error. Can I
  try something else for you?` as a second exact standalone variant.
- Preserve whole-response matching. XML/JSON or novel prose quoting an error
  message must remain content. Do not match any occurrence of words such as
  `sorry` or `error`.
- Classify these failures before XML parsing and print `provider-boilerplate`,
  not `missing-unit`. Never store failure text as accepted prose or context.
- Apply bounded backoff to consecutive provider-error responses, including
  responses returned as HTTP success. Reuse the existing backoff helper with
  a capped delay and the existing per-paragraph budget; no unlimited retries.
- Reset the consecutive provider-error streak after a usable reply. Do not
  automatically treat XML formatting mistakes as proof of a service outage.
- Keep structured safety/refusal signals terminal under existing behavior.

## Fix 5: protect distinct fresh runs and improve recovery reporting

- Keep exact run provenance with the recovery path: source digest, run identity,
  fresh/resume choice, and selected checkpoint.
- When a fresh run collides with an existing recovery pathname, preserve the
  old file through a unique run path or a verified archival copy before the
  first replacement. Keep normal compatible resume selection deterministic.
- Explain the preserved old path and new active path in the console. Do not
  add an interactive confirmation to routine fresh runs.
- Report stop-time and final-save checkpoint/journal counts with the same
  definition. If contiguous journal entries are promoted during final save,
  explain that the durable total is unchanged.
- If final-save validation or archival preservation fails, show the exact path
  and error before replacing any existing valid recovery file.

## Verification

Use small offline fixtures and the real coordinator/parser before live testing:

1. Reproduce both singleton restart shapes from this document. The ambiguous
   unit remains rejected; a separate independently safe unit still follows
   existing salvage rules. Retain all strict and salvage attribution tests.
2. Reproduce an item at 5/5 with two items at 4/5. Verify the two receive their
   fifth sends, the exhausted item is not resent, accepted results are saved,
   and only unresolved items appear in the final error. Also cover all-success,
   all-exhausted, terminal refusal, and transport-exception paths.
3. Verify mixed attempt groups never print an impossible next attempt. Check
   request counts, paragraph participations, observed provider calls, partial
   usage, and actual retry-subset metadata against the fake provider's calls.
4. Check exact boilerplate variants, typographic apostrophes, permitted wrappers,
   quoted narrative/XML/JSON false positives, and bounded backoff using a fake
   clock or mocked sleep.
5. Interrupt after sibling acceptance and resume from a real recovery EPUB.
   Verify accepted units are reused, prepared names are not re-extracted, and
   checkpoint promotion preserves the unique durable total and source order.
6. Start two fresh runs for the same source/settings in temporary directories.
   Verify the first recovery survives and the launcher identifies the second
   correctly. Test archive/write failure without destroying the first file.
7. If a gateway assembly bug is proven, add protocol-level fixtures for its
   actual snapshot/delta/retry behavior, including separate candidates and
   interrupted generations. Test the boundary fix independently of parser repair.

Run the affected selective-retry, marker-workflow, provider-refusal, source-window,
bundle, resume, end-to-end, tracing, and recovery-path suites. Use an isolated
pytest temporary directory and report exact commands/results. Do not call an
offline test pass proof that Gemini's upstream behavior has been repaired.

Finally run one bounded live probe against the copied recovery and compare
client/upstream traces. Confirm the real recovery digest remains unchanged.
Only a subsequent normal user run should advance the actual book.

## Scope and completion

This plan does not change the 8,192-token source windows, cumulative-summary
timing, window-local name protection, historical summary rules, or accepted
translations. The separate text197 extraction compatibility issue remains a
separate task; it is not the cause of this fresh-run stop.

Implement in this order: preserve evidence; correct queue termination and
statistics; improve provider-error handling; trace and fix any proven upstream
assembly defect; preserve fresh-run recovery history; verify recovery end to end.

Completion requires every local defect above to have regression coverage,
accurate self-explaining diagnostics, unchanged accepted-work integrity, and an
explicit evidence-backed status for the upstream corruption. Do not declare
the recurring response corruption solved solely by adding more retries.
