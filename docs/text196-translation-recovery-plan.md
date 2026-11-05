# Translation recovery and diagnostics plan: text_196.txt

Status: implemented and verified deterministically on 2026-11-05.

Implementation handoff, file map, verification commands, risks and rollback:
`docs/text196-translation-recovery-implementation.md`.
Offline reproduction: `diagnostics/text196/reproduce_recovery.py` and its
`reproduction.json` output. No live provider replay was performed and the real
recovery EPUB was not modified.

Target repository: `bilingual_book_maker-window-names`.

## Goal

Finish recoverable translation batches despite provider formatting errors, reduce
unproductive retries, and explain failures using concrete evidence. Preserve
accepted translations and exact paragraph attribution throughout resume.

## Observed failure

Evidence comes from `text_196.txt`, the current source, and the recovery EPUB
`ongoing/超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub`.

- The previous retroactive summary-validation problem did not recur. Window 5
  completed, including its cumulative original-language summary.
- Window 6 stopped in chunk 3 after five attempts. Its last response contained
  the translation `"Indeed."`, but under a damaged paragraph ID:

  ```text
  expected: epub:11:chap_11.xhtml:77:87e5f92fc6a4e547
  returned: epub:11:chap_11.xhtml:87e5f92fc6a4e547
  ```

- Other replies contain truncated tags, changed IDs, repeated units, and an
  unfinished response followed by another `<bbm-response>`. One request returned
  provider failure boilerplate. These are distinct failure categories.
- The visible run made 23 logical translation attempts for eight chunks, versus
  eight attempts if each chunk succeeded immediately. This excludes extraction,
  summary, and any retries hidden inside the provider or gateway.
- The recovery EPUB passes ZIP integrity and bundle validation. It holds 890
  checkpointed translations and four additional journal entries: 894 distinct
  durable units. Windows 1–5 are complete; window 6 has 119/168 accepted units.
- The cumulative summary through window 5 contains 2,601 estimated tokens.
  The configured 10,500-token summary budget is a ceiling, not its actual size.

The saved evidence does not establish whether restarted responses originated
in Gemini or in an upstream service. It also does not prove a context-limit
failure. Embedded failure responses are bounded to 8,000 characters and the
failure list is bounded, so those records alone are not full transport captures.

## Required behavior

1. Keep permanent source IDs in checkpoints, journals, hashes, ordering, and
   diagnostics. Provider-facing aliases must never become persisted identities.
2. Continue extracting names once per source window. Bracket that window's
   source using only its extracted labels, longest first.
3. Supply translation chunks with the incoming cumulative source-language
   summary and bounded original-source prefix. Update the summary only after
   every unit in the window has been accepted.
4. Reuse accepted translations and prepared windows on resume. Do not rewrite
   accepted work to accommodate a new request format.
5. Preserve strict marker counts and paragraph attribution. Unknown, duplicate,
   nested, or ambiguous response units cannot silently replace missing units.
6. Retain the recent change that avoids retroactive plain-label checks against
   older summaries. Historical syntax, hashes, and chain checks still apply.

## Phase 1: reproduce failures and trace the provider boundary

Primary code: `book_maker/translator/chatgptapi_translator.py`, especially
`_protected_completion` and `translate_marker_units`; failure recording in
`book_maker/marker_workflow.py`.

- Create small offline fixtures reproducing the damaged ID, a response restart
  inside an unfinished attribute, duplicate units, and a truncated final unit.
  Derive fixtures from the evidence without copying unrelated novel passages.
- Extend the existing request and response metadata rather than introducing a
  second diagnostics system. Record a local attempt ID, stage, window/chunk,
  requested unit count, actual provider-call count, elapsed time, finish reason,
  available usage, response character count, and response digest.
- Record requested output caps and whether the endpoint accepted them. Keep
  estimated allowances separate from actual request parameters and provider
  usage. In particular, the translation preflight allowance currently does not
  by itself establish an output cap sent to the endpoint.
- Add an opt-in local trace that captures the full decoded completion content
  at the client boundary before parsing, with the exact sanitized request and
  metadata. Keep secrets and authentication headers out of artifacts. Mark
  bounded embedded excerpts explicitly as excerpts.
- Check whether multiple roots already exist in the content received from the
  OpenAI-compatible client. If they do, report the corruption as arriving from
  upstream; distinguishing gateway assembly from model output then requires
  gateway-side evidence. Inspect any accessible local gateway configuration
  read-only before proposing a transport change.
- If live confirmation is needed, replay one bounded failing batch in an
  isolated diagnostic run. Do not advance or modify the real recovery EPUB.

Acceptance: each failure can be linked to its request and received response;
reports distinguish observed corruption from an unverified upstream cause.

## Phase 2: use short IDs in translation requests

Primary code: `paragraph_request`, `parse_paragraph_reply`, `translate`, and
`_window_repair_feedback` in `book_maker/marker_workflow.py`; request assembly in
`book_maker/translator/chatgptapi_translator.py`.

- At the prepared-window translation boundary, map the original chunk's
  permanent IDs to short aliases such as `u001`, `u002`, and `u003`.
- Keep that map stable across retries within the chunk. Requests for subsets
  retain their aliases; do not renumber the outstanding units on every attempt.
- Send aliases consistently in XML and source-derived repair constraints.
  Keep permanent IDs out of provider-facing paragraph identifiers.
- Parse strict and salvaged replies against only the requested alias set.
  Map accepted aliases back to permanent IDs before candidate validation,
  journaling, window acceptance, or checkpoint updates.
- Keep aliases scoped to this translation operation. Resume can reconstruct
  them deterministically from the frozen chunk; no bundle migration is needed.
- Never recover an ID by paragraph position, fuzzy matching, or a hash suffix.
  A damaged legacy ID is useful diagnostic evidence, not sufficient attribution.
- Preserve legacy translation behavior unless a shared helper requires an
  explicitly tested compatible change.

Acceptance: valid alias responses produce the existing permanent-ID recovery
state. Unknown aliases and ambiguous duplicates remain pending. Existing
recovery EPUBs resume without repeating extraction or accepted translations.

## Phase 3: reduce retry batch sizes as failures persist

Primary code: the pending-paragraph scheduler in `MarkerWorkflow.translate`.

- Preserve the initial chunk's configured unit and token limits.
- Queue outstanding units in source order. Cap each subsequent repair request
  by the attempt number of the included paragraphs:

  | Attempt, including initial send | Maximum units per request |
  | --- | ---: |
  | 1 | Existing initial chunk limit |
  | 2 | 8 |
  | 3 | 2 |
  | 4 and later | 1 |

- Apply the existing token limit as well as these unit caps. Keep the schedule
  internal initially; avoid adding configuration until evidence requires it.
- Group units with compatible attempt counts. Increment a paragraph's counter
  only when that paragraph is actually sent. Waiting in the queue consumes no
  attempts. Keep the current five-attempt default and finite exhaustion.
- Persist every accepted subset before proceeding. Never resend accepted
  siblings. Retain the same original-language context during a chunk's repairs;
  rejected output and raw error messages must not enter provider context.
- Preserve existing handling of explicit refusals and terminal transport
  failures. Batch splitting must not turn terminal failures into retries.
- When a unit exhausts its budget, stop with its permanent ID and final cause.
  Accepted siblings remain recoverable even when later queued work was not sent.

Tradeoff: splitting creates more requests when a whole response fails. It aims
to reduce repeated malformed replies and give stubborn units isolated attempts;
measure total calls and completion rate rather than promising lower request use.

## Phase 4: make console diagnostics explain the failure

- Report categories separately: wrong/unknown ID, duplicate ID, malformed XML,
  incomplete response, missing unit, marker mismatch, provider boilerplate,
  explicit refusal, and transport error.
- For a missing unit accompanied by a structurally similar unknown ID, print
  a bounded expected/observed example and say that the candidate was rejected.
  Similarity is explanatory only and must never authorize acceptance.
- Explain when a malformed unfinished opening makes otherwise complete-looking
  units ambiguous. Do not claim every affected paragraph was literally duplicated.
- Print shrinking retry sizes and each pending paragraph's remaining budget.
- At stopping time report the window/chunk, outstanding IDs, checkpoint count,
  additional unique journal count, total durable count, and trace location.
- Print that resume will reuse prepared names, retry unresolved work, and then
  continue the unprocessed remainder. Avoid implying the entire book has only
  one paragraph left.
- Count logical translation attempts separately from underlying provider calls
  and name-extraction/summary requests. Show unavailable usage as unavailable.

Acceptance: the text_196 reproduction explains that a translation arrived under
the wrong ID, rather than reporting only an unexplained missing paragraph.

## Phase 5: address summary marking and request sizing separately

This is a distinct behavior change with separate tests and review. It must not
be bundled invisibly into the ID or retry fix.

### Summary marker scope

Current code calls `bracket_known_labels(summary, self.known_labels)` before
validating each newly generated summary. Longest-first matching only resolves
overlaps among known terms; it cannot recognize a longer expression that has
not been extracted. Broad labels such as `姐姐` make this significant.

Proposed window-summary contract:

- Treat summary prose as original-language context. Permit plain name mentions
  and remove automatic cumulative-catalogue substring bracketing for newly
  generated source-window summaries.
- Keep existing valid markers and validate any emitted marker against the
  allowed catalogue. Keep malformed-marker, nonempty, provider-failure,
  language, and summary-budget checks.
- Align the window-summary prompt with this rule so it no longer requires every
  occurrence of every catalogue substring to be bracketed. Preserve earlier
  facts through cumulative summarization; do not start using all historical
  summaries as parallel inputs.
- Keep body-source protection per window. Do not automatically promote broad
  role words or historical labels into a later window's protected source list.
- Record the revised summary policy/version for new summaries. Reuse previously
  accepted summaries as saved; do not rewrite historical checkpoints.

Review extraction examples such as `姐姐` against the intended terminology
policy. Exact source presence does not establish that a term deserves protection.
Document the policy before changing extraction categories or accepted names.

### Sizing

- Continue showing actual estimated summary size alongside its configured cap.
  Explain that a larger cap permits more context but does not require a model
  to fill it or guarantee retention of every earlier fact.
- Account for instructions, XML/alias overhead, source, summary, repair feedback,
  source prefix, and an explicit translation-output estimate in preflight.
- Enforce a verified endpoint limit when available. When unavailable, state that
  sizing is estimated; do not label a malformed response as proven overflow.
- Do not silently truncate the cumulative summary, change the user's 10,500
  setting, or modify the frozen window plan to get a request through.

## Verification

Use meaningful deterministic tests before a live retry:

1. Alias round trips with out-of-order replies, changed/unknown aliases,
   duplicates, nested units, truncated tags, and response restarts. Existing
   conservative attribution tests must continue passing.
2. A fake provider that fails large batches and succeeds on smaller ones.
   Assert the attempt schedule, actual per-unit counts, finite exhaustion,
   accepted-only context, and no retransmission of accepted siblings.
3. Interrupt after partial acceptance and resume from an actual recovery EPUB.
   Verify stable permanent identities, journal reuse, final ordering, no
   duplicated counts, and no repeated name extraction.
4. Diagnostics for the exact damaged-ID case, truncated excerpts, absent finish
   metadata, provider failures, and overlapping checkpoint/journal IDs.
5. For the separate summary change, cover a short known term inside a longer
   unextracted expression, preserved valid markers, unknown/malformed markers,
   language/budget enforcement, and old-summary compatibility.

Run the affected source-window, selective-retry, workflow, bundle, resume,
end-to-end, provider-refusal, and summary suites using an isolated pytest
temporary directory. Report the exact command and result. Broaden checks only
for changes that touch additional behavior.

For live validation, first use a copied recovery EPUB and separate output paths.
Confirm the original file's digest is unchanged. Compare attempts, accepted
units, ID failures, malformed-response counts, and elapsed time. One successful
retry demonstrates recovery, not a guaranteed whole-book failure rate.

## Implementation order and completion criteria

1. Preserve evidence and add response-boundary diagnostics and reproductions.
2. Introduce short translation aliases and verify recovery compatibility.
3. Add the shrinking retry scheduler and actionable console reporting.
4. Review and implement the separate summary policy and sizing improvements.
5. Run focused verification and document any unresolved upstream corruption.

The work is complete when the damaged-ID failure is reproducible and clearly
explained, short aliases preserve strict attribution, persistent failures reach
isolated attempts within their budget, accepted work survives interruption, and
the summary policy no longer performs blind cumulative substring repair.

Existing uncommitted summary-validation edits and `diagnostics/text194/` are
pre-existing work. Preserve them and keep unrelated artifacts out of commits.
