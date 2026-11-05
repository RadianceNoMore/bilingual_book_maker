# text_196 translation recovery: implementation handoff

Status: implemented and verified deterministically on 2026-11-05.
Target: `bilingual_book_maker-window-names`, branch `source-window-names`.

This document records what the recovery plan changed, how to verify it, what
was deliberately not run, and how to roll it back.

## 1. What changed, by phase

### Phase 1: response-boundary diagnostics

- `book_maker/marker_trace.py` (new) writes an opt-in JSONL trace when
  `BBM_MARKER_TRACE_DIR` is set. Each record contains the sanitized request,
  the full decoded completion, and the same metadata stored in run state.
  Authentication headers and API keys are dropped. Tracing is off by default;
  trace files can contain source text and translations and must be handled as
  sensitive.
- `ChatGPTAPI._protected_completion` now records, per logical attempt:
  `attempt_id`, `stage`, `window_id`, `chunk`, `chunk_count`,
  `requested_units`, `provider_calls`, `elapsed_seconds`, `finish_reason`,
  `usage`, `response_characters`, `response_sha256`, `output_cap`
  (requested and accepted) and `framing` (root counts, `multiple_roots`).
- Estimated output allowance is recorded separately from an endpoint output
  cap. Translation requests do not send a cap today, so `output_cap.requested`
  is `null` while `estimated_output_allowance` reflects the preflight estimate.
- Failure records now carry the trace location and the same response metadata,
  so every failure links to its request and received response.
- Multiple `<bbm-response>` roots are counted in the content received from the
  OpenAI-compatible client. When present, the console says the corruption is
  upstream of the parser. Distinguishing gateway assembly from model output
  still needs gateway-side evidence; this change does not claim to have it.

### Phase 2: short provider aliases

- `book_maker/loader/epub_loader.py` builds `u001`, `u002`, ... from the frozen
  chunk (before accepted units are filtered out). The same chunk therefore
  yields the same aliases after an interruption.
- `MarkerWorkflow.translate(..., provider_aliases=...)` sends aliases in the
  XML payload and in source-derived repair feedback, parses replies against the
  requested alias set, then maps accepted aliases back to permanent IDs before
  validation, journaling, window acceptance and checkpointing.
- Permanent IDs remain the only persisted identity in checkpoints, journals,
  hashes, ordering and diagnostics. Aliases are operation-scoped and are not
  written to the bundle, so no migration is needed.
- A damaged alias is never repaired by position, fuzzy match or hash suffix.
  Similarity is printed only as an explanation and explicitly cannot authorize
  acceptance.

### Phase 3: shrinking repair batches

- `retry_unit_cap` implements: attempt 1 = the caller's existing chunk size,
  attempt 2 = at most 8 units, attempt 3 = at most 2, attempt 4+ = 1.
- `_next_retry_batch` walks outstanding units in source order, groups units
  with the same attempt count, and applies both the unit cap and the original
  chunk token budget. Units waiting in the queue do not consume attempts.
- Accepted subsets are committed and persisted before the next request.
  Accepted siblings are never resent. Raw rejected output and raw error text
  never enter provider context; prepared-window repairs use source-derived
  marker counts only.
- Provider refusal and terminal transport handling are unchanged: splitting
  cannot turn a terminal failure into a retry.

### Phase 4: console diagnostics

- Per-unit categories: `wrong-or-unknown-id`, `duplicate-id`,
  `malformed-xml`, `incomplete-response`, `malformed-unit`, `ambiguous-frame`,
  `marker-mismatch`, `missing-unit`, `provider-boilerplate`,
  `explicit-refusal`, `transport-error`.
- A missing unit with a similar unknown ID prints one bounded expected/observed
  example and states that the candidate was rejected.
- Malformed unfinished openings explain that otherwise complete-looking units
  are ambiguous and are never accepted by position.
- Retry prints show the shrinking request size and each paragraph's remaining
  attempts.
- At exhaustion the console reports window/chunk, outstanding IDs, checkpoint
  count, additional unique journal count, total durable units, logical
  paragraph attempts, observed provider calls, usage availability, and trace
  location. It also says resume reuses prepared names, retries unresolved work,
  then continues the unprocessed remainder.

### Phase 5: summary marking and sizing

- New source-window summaries are no longer rewritten with
  `bracket_known_labels`. Plain original-language name mentions are valid.
- Marker syntax that the model actually emits is still validated against the
  catalogue; malformed, unknown, non-empty, provider-failure, language and
  budget checks remain.
- The summary prompt now says markers are optional and must not be required at
  every mention. `catalogue_labels` remains available as reference.
- New summaries record `summary_validation.policy =
  "plain-labels-allowed-v1"`. Historical summaries are read with the same
  marker checks and are not rewritten; the policy field is optional for old
  bundles.
- Sizing continues to show estimated summary size against its configured cap.
  The 10,500-token setting and frozen window plan are never changed silently.
- Extraction categories and accepted names are unchanged. Reviewing broad
  labels such as `??` against the intended terminology policy remains a
  separate decision: exact source presence alone does not establish that a term
  deserves protection. No extraction policy was changed in this work.

## 2. File map

| File | Purpose |
| --- | --- |
| `book_maker/marker_workflow.py` | Alias mapping, retry scheduler, failure categories, durable-stop report, summary policy |
| `book_maker/loader/epub_loader.py` | Frozen-chunk alias construction, diagnostic context, estimated output allowance |
| `book_maker/translator/chatgptapi_translator.py` | Per-attempt metadata, output-cap facts, framing facts, trace emission |
| `book_maker/marker_trace.py` | Opt-in JSONL trace writer |
| `book_maker/source_windows.py` | Revised cumulative-summary prompt |
| `book_maker/marker_bundle.py` | Accepts the optional summary policy field |
| `tests/test_marker_diagnostics.py` | Metadata, trace, framing, failure-linkage tests |
| `tests/test_marker_selective_retry.py` | Alias, damaged-ID, shrinking-schedule, feedback-alias tests |
| `tests/test_summary_recovery.py` | Plain-label acceptance and malformed-marker rejection tests |
| `tests/test_marker_end_to_end.py` | Resume assertions now use permanent IDs |
| `diagnostics/text196/reproduce_recovery.py` | Offline reproduction of all failure categories |

## 3. Verification

Run from the repository root with the project interpreter (the repository's
dependencies are installed in the Miniconda environment used for these runs):

```powershell
& 'C:\Users\Tina_\Miniconda3\python.exe' -m pytest `
  tests/test_source_windows.py tests/test_marker_selective_retry.py `
  tests/test_marker_workflow.py tests/test_marker_bundle.py `
  tests/test_marker_resume.py tests/test_marker_end_to_end.py `
  tests/test_provider_refusal.py tests/test_summary_recovery.py `
  tests/test_marker_diagnostics.py tests/test_text194_extraction_recovery.py `
  tests/test_translation_metadata.py tests/test_chatgptapi_translator.py -q
```

Recorded result: see `diagnostics/text196/verification.txt`.

Offline reproduction:

```powershell
$env:PYTHONPATH = (Get-Location).Path
& 'C:\Users\Tina_\Miniconda3\python.exe' diagnostics/text196/reproduce_recovery.py
```

It makes zero provider calls and does not open or modify the real recovery
EPUB. `diagnostics/text196/reproduction.json` contains the captured categories,
including the damaged-ID case: requested `u001`, observed `u01`, similarity
0.857, category `wrong-or-unknown-id`, and the explicit rule that similarity
never authorizes acceptance.

## 4. Assumptions and intentionally unrun work

- "Execute this plan" was treated as implementing all five phases in the
  repository and verifying them deterministically, not as launching a live
  book run.
- No live provider replay was performed: no credentials were supplied, and the
  plan says a live replay is only needed if offline evidence is insufficient.
  The offline fixtures reproduce the damaged ID, duplicate ID, truncated final
  unit, unfinished-attribute restart and multiple-root cases.
- The real recovery EPUB and `ongoing/` artifacts were not opened, advanced or
  modified by this work.
- Pre-existing uncommitted summary-validation edits and `diagnostics/text194/`
  were preserved.
- The repository uses a 40-record bounded failure list; the trace, when
  enabled, is the full local capture for deeper analysis.

## 5. Risks

1. **Provider-facing identifiers changed.** Aliases are now `u001`-style in the
   prepared-window translation path. Any external tool that inspects raw
   provider requests and assumes permanent IDs must be updated. Persisted state
   is unaffected.
2. **More requests on whole-batch failures.** Shrinking batches increase the
   number of provider calls when a large response is wholly malformed. The
   per-paragraph budget remains five attempts; measure completion rate and
   total calls rather than assuming lower request use.
3. **Trace sensitivity.** `BBM_MARKER_TRACE_DIR` writes source text and
   translations to disk. Keep it off in production unless diagnosing, and
   delete or protect the trace afterwards.
4. **Multiple roots can still yield independently valid units.** The parser
   reports upstream corruption, but two roots containing distinct, complete,
   uniquely requested units are accepted. Duplicate or ambiguous units remain
   pending. This is a deliberate conservative-attribution choice, not proof
   that the upstream response was well formed.
5. **No live measurement.** The text_196 attempt count after this change is not
   measured against the provider. The deterministic suite proves the schedule
   and attribution rules, not a guaranteed whole-book failure rate.

## 6. Rollback and review

- No schema migration or bundle-version bump is involved. The only new bundle
  field is optional (`summary_validation.policy`), so older code can read new
  bundles and new code can read old bundles.
- To roll back code, revert the commit(s) containing these changes or restore
  the six source files listed above from the previous revision. Accepted
  translations and recovery bundles remain valid because aliases are never
  persisted.
- To disable tracing without a code change, unset `BBM_MARKER_TRACE_DIR`.
- To restore the previous summary policy, revert the `summarize_source_window`
  block and the `source_summary_prompt` wording together; do not revert only
  one side, or prompt and validator will disagree.
- Review focus: `MarkerWorkflow.translate`, `_next_retry_batch`,
  `_exhaust_recovery`, `ChatGPTAPI._protected_completion`, and the alias map in
  `epub_loader.py`.

## 7. How to resume the real book

1. Keep a copy of the current recovery EPUB and record its SHA-256 first.
2. Optionally set `BBM_MARKER_TRACE_DIR` to a private directory.
3. Run the existing resume command with the copied recovery EPUB and separate
   output paths.
4. Compare accepted units, outstanding IDs, categories, attempt counts and
   elapsed time against the pre-change run.
5. Only after a successful bounded retry, replace the production recovery
   artifact. One successful retry demonstrates recovery, not a guaranteed
   whole-book failure rate.
