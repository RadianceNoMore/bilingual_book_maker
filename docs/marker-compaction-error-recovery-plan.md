# Reject provider error text and regenerate contaminated marker context

Status: implemented and validated on 2026-11-03; see "Validation record:
marker-context recovery (2026-11-03)" in `docs/protected-names.md`. The
offline repair helper lives in `book_maker/marker_repair.py`.

## 1. Scope and required outcome

Work in `bilingual_book_maker-protected-names` only. Preserve the original sibling repository, unrelated edits, and existing EPUB content. Implement automatic recovery without manual glossary work or another AI tool.

Prevent standalone provider error/refusal text from becoming a compaction seed. On resume, detect an already contaminated seed, exclude it from every model request, rebuild context from verified accepted material, generate a valid replacement summary, and persist that replacement before continuing translation.

Do not merely clear the seed and continue without regenerating context. Do not restart the book or discard accepted translations. Keep paragraph IDs strict: malformed IDs must not be guessed or repaired by matching translated prose.

## 2. Evidence and limits of the diagnosis

Evidence: `C:\Users\Tina_\AppData\Local\Temp\Ditto\DragFiles\text_177.txt` and embedded state in:

`ongoing/超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub`

- The saved `session.seed` and `session.last_summary` contain:
  `I'm having a hard time fulfilling your request. Can I help you with something else instead?`
- Both `check_provider_refusal` and `validate_summary` currently accept this standalone message. They also accept `Sorry, something went wrong. Please try your request again.`
- Compaction recorded success and later translation requests included the bad seed as a user context message.
- In the final batch, Gemini translated the outstanding paragraph but returned `epub:21:chap_21.xhtml:99674eb05562c6f4` instead of `epub:21:chap_21.xhtml:75:99674eb05562c6f4`. The translated text exists; the ID is invalid.
- The subsequent four repair responses were: hard-time message, something-went-wrong message, hard-time message, hard-time message. The first malformed-ID response plus these four sends exhausted five attempts.
- There are 39 accepted entries in the final partial-batch recovery journal. Preserve and reuse them.
- The bridge returned HTTP 200 and `finish_reason=stop`; the client saw no structured refusal. A successful HTTP response does not establish a valid translation or summary.
- A small live test translated the sentence even with the bad seed present. Therefore neither source-based blocking nor bad-seed causation is proven. Google may have returned a temporary service error; do not label these messages definitively as policy refusals.

## 3. Preserve current workflow invariants

- Body masters retain `${exact source spelling}` markers. Catalogue registration remains deterministic and independent of compaction. English display mappings never feed back into translation context.
- Keep selective grouped paragraph repairs, longest-first source-span accounting, relaxed source-count validation, the default five sends per paragraph, strict duplicate/ambiguous-ID rejection, and provider-truncation salvage.
- Keep accepted-only history and transactional catalogue/journal commits. No new glossary extraction, review voting, or name substitution in body masters.
- Use the canonical recovery EPUB under `ongoing`. Embed all new state and diagnostics; create no default loose sidecars. Complete output remains under `output`.
- A run without resume must not inspect or reuse old generated context.
- Do not alter the anonymous bridge as part of this patch. Error classification in this repository must work when a compatible provider returns HTTP 200 with error text.

## 4. Classify unusable standalone responses

Relevant code: `book_maker/provider_refusal.py`, `marker_names.py`, and the protected completion/compaction paths in `translator/chatgptapi_translator.py`.

Add a shared classification helper that distinguishes:

1. Explicit policy refusal: existing structured refusal, safety finish reasons, and narrowly recognized standalone policy messages. Preserve terminal behavior; no automatic policy-evasion retry or route switching.
2. Standalone provider failure: narrowly recognized whole-response messages such as the two exact examples above. These are unusable, potentially transient replies; they are not proof of a policy refusal.
3. Ordinary content: process through the existing task-specific validation.

Match standalone boilerplate conservatively, tolerating whitespace and typographic apostrophes. Do not reject valid XML/JSON translation content or a book passage quoting these words. Do not use broad rules like any response containing “sorry”, “request”, or “hard time”. Existing structured refusal signals still take precedence regardless of response format.

Apply this classification before parsing/trimming compaction output, before accepting a seed, and when restoring an old seed/last summary. Also validate the final bounded seed after truncation. Enforce the rule at shared summary-validation boundaries so a future caller cannot accidentally bypass it.

A valid summary may contain no names or markers. Do not require a glossary section, a fixed number of characters, or mandatory marker coverage as a proxy for validity. Keep current marker validation. Plain arbitrary nonsense cannot be fully ruled out by a deterministic validator; do not claim this change proves semantic summary accuracy.

## 5. Runtime compaction recovery

Relevant code: `_compact_marker_session`, `compaction_prompt`, `note_compaction`, session serialization.

- Build compaction requests only from accepted canonical history and an independently validated previous seed. Never append rejected summaries or provider error replies to history.
- Classify a standalone provider failure as a failed compaction attempt, not a successful summary. Record its category and retry with bounded backoff; use the configured `max_chunk_attempts` (default five) as the compaction-call limit.
- Keep this budget separate from paragraph-send counters. A failed compaction must not consume the next paragraph's send budget, and nested retry wrappers must not multiply compaction calls.
- Retries use sanitized evidence, not the failed response as conversational context. Do not repeatedly ask the model to summarize its error message.
- Before clearing live session exchanges, retain bounded rebuild evidence inside the bundle. A failed attempt must not overwrite the last independently valid summary or remove accepted evidence.
- Only a validated result may replace `seed`/`last_summary` and receive a compaction-success event. Record a validation-version field for new summaries; legacy summaries without it remain loadable but must pass current checks.
- If all compaction attempts fail, save a partial recovery with context repair pending and stop cleanly. For marker mode, replace the current silent empty-window continuation on exhausted compaction. Leave ordinary non-marker behavior unchanged.
- A repair failure must not erase catalogue entries, accepted translations, journal rows, or previously accepted exchanges.

## 6. Detect contaminated state during resume

Relevant code: `MarkerWorkflow.restore_session`, `session_snapshot`, `_begin_marker_run`, recovery discovery, and bundle validation.

Validate saved `seed` and `last_summary` independently before installing either in the live session. Never restore a recognized error message, even when its old compaction event says `status=ok`. Old success flags are not trustworthy for this defect.

If either field contains recognized provider boilerplate, mark context repair pending. Quarantine bounded bad text in diagnostics only; clear the contaminated active fields and reject them as rebuild inputs. Keep a valid field only if it independently passes current validation and its use is consistent with the saved history boundary; otherwise regenerate without assuming its ordering.

Use an explicit pending-repair state so interruption cannot turn “seed was cleared” into apparent successful repair on the next resume. `session_snapshot` and recovery saves must preserve this state. New validation metadata must not make existing compatible marker bundles unreadable or change the source/run fingerprint.

Defer network regeneration until the translator, source document, stable job identities, and checkpoint reconciliation are available. `restore_session` currently runs early in `_begin_marker_run`; do not fabricate source text there from unavailable chapter plans. A pending-repair gate must block the first new translation/localization/filename request until rebuilding succeeds. Pure reuse of already accepted records is allowed.

Do not broadly swallow corrupt bundle errors or source-hash mismatches as if they were this repairable context problem.

## 7. Rebuild context from accepted evidence and regenerate

Rebuilding is automatic and source-backed:

1. Resolve accepted checkpoint job IDs against this run's verified input EPUB using the same filters, cleanup, and canonical source representation as translation. Pair full source paragraphs with their accepted marker masters.
2. Merge accepted `paragraph_recovery` rows, verifying identity and source digest. Deduplicate checkpoint/journal overlap by stable identity. Do not use the journal's batch-local `index` as global book order. Preserve accepted siblings even when the checkpoint prefix has not advanced past a hole.
3. Prefer recent body evidence relevant to the next outstanding body unit. Order selected pairs by actual source/spine order; do not mix unrelated filename/navigation jobs into narrative order. Pending/untranslated source paragraphs may not masquerade as accepted translation history.
4. Select a recent bounded suffix using the existing token estimator and approximately the configured 8192-token context budget, accounting for prompts and both source and master text. Bound oversize evidence deterministically at paragraph boundaries; do not cut a marker or XML unit in half. Handle an individually oversized paragraph explicitly rather than looping forever or sending unbounded evidence.
5. If a separately verified earlier summary with a known boundary exists, it may precede the evidence. Never reuse the contaminated seed, unvalidated diagnostic text, CSS display mappings, rejected model output, or an old sidecar handoff. For older bundles lacking a valid earlier summary, recent accepted source/master evidence is sufficient for a new local continuity summary; do not pretend to reconstruct the entire prior plot.
6. Generate a replacement using the normal marker-preserving, no-glossary compaction task. Supply only evidence and fixed instructions; no instructions to evade Google restrictions. Validate/classify the result through the same boundary as runtime compaction.
7. Install the valid seed, clear the pending-repair state, and persist a verified recovery EPUB atomically before making any further translation request. Preserve the accepted checkpoint, journal, catalogue, mappings, run identity, and localization masters. No catalogue registration/count increment occurs merely from summarizing existing markers.
8. Resume normal processing. Already accepted final-batch siblings must not be sent again. The unaccepted malformed-ID paragraph remains outstanding and is translated with the corrected context.

If there is no accepted material, clear the bad summary and initialize a clean empty session, with a diagnostic explaining that regeneration had no evidence. This exception is for a genuinely empty run, not the affected book with thousands of accepted units.

If evidence cannot be reconciled or regeneration exhausts its budget, retain a valid partial recovery and report a specific reason. Do not silently resume with the poisoned message, an empty replacement, or an unverified older summary.

## 8. Paragraph errors and retry accounting

Classify whole-response transient provider boilerplate before the XML parser turns it into “all IDs missing”. Keep pending IDs pending, accepted IDs committed, and charge each actual paragraph-generation send once to its outstanding IDs. Report `provider reply unusable` rather than misleadingly reporting only XML alignment.

Do not automatically regenerate context for every malformed paragraph, and do not reset a healthy session on a single model mistake. Rebuild when context is known invalid or repair is explicitly pending. Genuine malformed IDs still follow strict selective repair, without fuzzy matching or inferred attribution.

Preserve terminal handling for explicit refusals. Repeated generic HTTP-200 failures should exhaust a bounded budget and save progress, not loop indefinitely or trigger an unlimited context-rebuild cycle.

## 9. Embedded state, diagnostics, and operator messages

Use small backward-compatible optional fields under session state for context validation and repair status/evidence. Specify the fields and lifecycle in the implementation documentation, and update bundle validators. Keep embedded evidence bounded; do not duplicate the whole novel/history per compaction.

Record compaction purpose (`normal` or `resume-rebuild`), window, attempt, response classification, response finish reason, elapsed time, bounded bad reply, evidence identities/digest, and replacement outcome. Do not log keys or private proxy credentials. Preserve old events as historical evidence; do not rewrite old `ok` records to imply the original code detected the defect.

Required clear messages include:

```text
Resume: saved context contains a provider error message; rebuilding from accepted units.
Context rebuild: selected N accepted units; attempt 1/5.
Context rebuild: replacement validated and recovery saved; continuing translation.
Compaction: unusable provider reply; retrying 2/5.
Context rebuild failed after 5 attempts. Accepted translations saved; resume will retry context repair.
```

Print the category/reason of existing summary marker failures too. Do not expose raw book passages by default merely to explain retries.

## 10. Required regression coverage

- Both exact error messages are rejected as seeds in raw/wrapped forms, including typographic apostrophes. A normal summary with no markers remains allowed.
- Those phrases inside valid XML translation prose or quoted narrative do not trigger the standalone-error rule. Existing explicit-policy refusal tests remain terminal.
- Compaction returns an error once then a valid summary: only the valid result becomes seed; the old valid history stays intact; backoff/call budget is bounded.
- Five compaction failures preserve accepted work and persist repair-pending state. Reload cannot bypass repair because the active seed is now blank.
- Legacy bundle has poisoned `seed`, poisoned `last_summary`, or one poisoned field: no outgoing request includes that text. Repair uses full accepted source/master pairs, then persists before translation.
- Actual EPUB fixture with a checkpoint prefix, 39 accepted journal siblings, and one missing/malformed-ID paragraph: rebuild deduplicates identities, preserves all 39, and translation sends only the outstanding paragraph when reaching that batch.
- Checkpoint/journal duplicate rows do not increment catalogue counts or duplicate exchanges. Bounded evidence is source ordered, full length when fitting, and contains no display-name substitutions.
- Simulate interruption before regeneration, after successful generation but before durable save, and after atomic replacement. Resume always either repairs again safely or uses the verified saved seed; no accepted work is lost.
- Hash mismatch, missing source, empty run, oversized paragraph, valid seed, non-resume run, and explicit provider refusal have separate expected outcomes.
- Tests exercise real translator compaction and loader recovery integration with mocked HTTP completions, not only isolated validator calls. Preserve earlier duplicate-ID/truncation tests.

Run the affected marker, session, bundle, resume, end-to-end, refusal, and chunk-retry suites. Report exact commands/results and distinguish selected tests from the full repository suite.

## 11. Repair the user's existing recovery after implementation

Provide an offline repair mode/helper that marks the named compatible recovery for rebuild and removes poisoned active context fields without calling Gemini or changing accepted book text. Keep any backup inside a dedicated backup subdirectory under `ongoing`, excluded from automatic candidate selection; do not scatter files into input/output. Use verified atomic EPUB replacement and retain the original until replacement verification succeeds.

Then exercise the normal `-Resume` path to regenerate the summary before further translation. Validate the new persisted seed and the absence of the error message from active session context. Stop the validation run after successful context repair if necessary; do not launch the full remaining novel simply to demonstrate the fix. The user's ordinary resume command must work thereafter without an additional flag.

No code or recovery archive is changed merely by creating this plan. The implementing harness must report separately whether it repaired the existing recovery, regenerated a summary live, or only validated mocked fixtures. Never claim the cause of Google's original error is known from these tests.
