# Implementation instructions: recovery storage, catalogue commits, and safe EPUB regeneration

Status: implemented and validated 2026-11-03; see the validation record in
[`protected-names.md`](protected-names.md#validation-record-2026-11-03-repair).

Repository: `C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker-protected-names`

## 1. Objective and boundaries

Repair the existing `source-markers-css-v1` workflow without redesigning translation.

Implement three changes:

1. Store unfinished EPUBs in `ongoing`, and resume the newest compatible unfinished run.
2. Register validated marker labels before session recording can trigger compaction.
3. Generate navigation and metadata from immutable marked masters, avoiding replacements against previously displayed English text.

Do not modify the original `bilingual_book_maker` repository. Preserve unrelated working-tree changes. Do not reset, clean, commit, or push.

Read parent `AGENTS.md` and any nearer instructions before editing. Parent instructions request Claude consultation for architectural work. The previous reviewer found no available `claude.ask_claude` tool; do not claim consultation unless it actually happens.

Keep existing defaults for marker syntax, translation prompts, body batching, retry limits, cross-chapter grouping, filename translation, CSS body rendering, and independent display-mapping requests.

## 2. Current problems and integration points

Inspect current files before editing: another harness may have changed them after this handoff was written. Symbol names below describe the reviewed implementation, not immutable line numbers.

### 2.1 Recovery selection

`EPUBBookLoader._discover_marker_bundle` currently scans output EPUBs and can prefer a completed book over a partial recovery.

`MarkerWorkflow.new_state` also assigns a deterministic run ID derived from source/settings, so separate fresh runs can appear to be the same run.

Relevant loader integration points include `_begin_marker_run`, `_write_protected_book`, `_save_progress`, `_save_temp_book`, `_write_partial_recovery`, and final success cleanup. `BaseBookLoader.output_path` redirects basenames into the configured output directory; do not accidentally redirect recovery files there.

### 2.2 Catalogue ordering

`MarkerWorkflow.translate` calls `_record_session_exchange` before `_record_delta`.

Session recording can immediately trigger compaction. Consequently, a correctly preserved marker introduced by the current chunk is present in history but absent from the catalogue. Summary validation rejects it as unknown.

This is an ordering bug. It does not justify an additional AI extraction or review request.

### 2.3 Navigation replacement

`marker_bundle._localized_replacements` constructs old/new displayed strings. `_replace_text_node_text` and related regeneration paths then apply sequential `str.replace` operations.

Corrections can cascade:

```text
Requested:
Alice -> Bob
Bob   -> Carol

Incorrect processing:
Alice -> Bob -> Carol
```

Protected body spans retain their original labels. The demonstrated bug concerns derived navigation and metadata text, especially during `apply-catalogue`.

## 3. Recovery storage and lifecycle

### 3.1 Directory resolution

Use two generated-output directories:

```text
input/
    source.epub

ongoing/
    <source-stem>-<source-hash12>_bilingual_temp.epub

output/
    <translated-filename>_bilingual.epub
```

Resolve `ongoing` as a sibling of the configured output directory:

```text
--output-dir ./output
-> ./ongoing

--output-dir D:\Translations\finished
-> D:\Translations\ongoing
```

For direct loader calls without `output_dir`, use an `ongoing` subdirectory beside the source.

Create the directory only when writing a checkpoint. Keep the existing completed-output location.

Use the SHA-256 of the source EPUB bytes for the filename suffix. This prevents unrelated books with identical filenames from sharing a recovery path.

Do not add a mandatory CLI flag or require changes to existing PowerShell commands.

### 3.2 One recovery per book

The user explicitly chose one recovery per book, not one directory per fresh run. A source identity has one normal recovery path, regardless of translation settings.

Starting without `-Resume` must:

- Initialize empty catalogue, history, translations, and localization state.
- Generate a fresh random run ID, such as UUID4.
- Ignore previous generated state.
- Leave the existing recovery untouched until a new valid checkpoint has been assembled and verified.
- Replace that recovery atomically when the new checkpoint is ready.

Do not delete an older recovery before a replacement exists.

Preserve the run ID across resumed checkpoints and final publication. Keep the compatibility fingerprint separate from the unique run ID.

### 3.3 Resume discovery

Search only the resolved `ongoing` directory for new-format unfinished EPUB bundles.

A compatible candidate must have:

- Supported state schema and marker workflow.
- Matching source SHA-256.
- Matching existing run fingerprint.
- `run.status == "partial"`.
- Valid checkpoint structure.

If several compatible candidates exist, select the greatest filesystem modification time using `st_mtime_ns`. Break ties deterministically by normalized absolute path.

Print the chosen path and accepted-unit count.

Completed files in `output` must never block or satisfy this resume lookup.

If no compatible candidate exists:

- Clearly announce that a fresh run is starting. The user explicitly chose this behavior over stopping or checking completed outputs.
- Do not import external handoff, glossary, pickle, or legacy protected-name state.
- Leave incompatible and corrupt candidates untouched.
- Report incompatible or unreadable files encountered, without silently treating them as valid checkpoints.

Do not deserialize legacy pickle files during discovery.

### 3.4 Existing recovery compatibility

Do not automatically move existing user EPUBs from `output`.

Document that an existing new-format partial EPUB can be moved into `ongoing` before using `-Resume`. Discovery must accept its existing filename; normal subsequent checkpoint saves use the canonical recovery filename.

After a successful canonical checkpoint save, an alternate resumed recovery file may be removed only after verifying that its embedded source identity and run ID match the active run.

### 3.5 Completion and cleanup

On successful translation:

1. Finish ordinary mapping/localization work.
2. Assemble the completed bundle.
3. Verify its ZIP, EPUB, and embedded state.
4. Atomically publish it in `output`.
5. Remove this run's recovery file.

If any publication step fails, keep the recovery.

Before deleting a recovery, confirm its source identity and run ID still match the active run. Preserve unrelated files and legacy sidecars.

Route every protected-mode checkpoint and fallback recovery path through the same location helper. Avoid passing an `ongoing` path through `output_path`, which currently redirects basenames into the output directory.

Keep atomic replacement and cleanup of ephemeral assembly files. On Windows, use safe native path operations and verify targets before any directory-level removal. This work should only need deletion of explicitly identified recovery files, not recursive directory deletion.

## 4. Catalogue registration and compaction

### 4.1 Required chunk sequence

For each body chunk:

1. Send the existing paragraph-framed translation request.
2. Parse paragraph identities and text.
3. Validate marker syntax and exact source occurrence counts.
4. Validate surrounding translation quality.
5. Apply the validated catalogue delta using Python.
6. Record the accepted canonical exchange in session history.
7. Allow normal compaction if the context threshold is reached.
8. Schedule independent display mappings under the existing batching policy.
9. Return the accepted translations to the loader.

No failed structural or quality response may reach the catalogue or accepted history.

### 4.2 Catalogue operations require no AI

Registration consists only of deterministic work:

- Deduplicate exact original labels.
- Compute stable keys.
- Update occurrence counts.
- Attach source references and bounded representative contexts.
- Initialize new labels with a nullable target.

Do not add extraction, voting, review, or identity-resolution calls.

An entry with `target: null` is valid catalogue state. It is already registered even though an English display mapping is not yet available.

### 4.3 Compaction independence

Compaction uses the previous validated summary and accepted exchanges.

It must:

- Preserve names as exact `${original label}` expressions.
- Work when every catalogue target is null.
- Avoid reading or injecting English display mappings.
- Avoid scheduling mapping requests.
- Retain existing safeguards against malformed and unknown markers.

The current chunk's validated labels must be visible before compaction validates its summary.

Do not loosen marker validation to hide the ordering bug. Catalogue membership checks concern exact original labels, not whether their English targets exist.

### 4.4 Transaction behavior

Treat catalogue registration and accepted-history recording as one transaction.

Capture the state required to restore catalogue and session before applying the accepted update. If commit unexpectedly fails, restore both before propagating the failure. Include session token accounting, window counter, active seed, and associated summary state in rollback where those fields can change during recording.

Keep optional compaction failure distinct from chunk failure:

- A refused or exhausted compaction follows the existing empty-context fallback.
- The valid body translation and catalogue additions remain accepted.
- Keep the last valid summary for diagnostics.
- Never record refusal text as a summary.

Display-mapping failure must continue to leave accepted body translations intact and retain pending labels.

## 5. Immutable masters for navigation and metadata

### 5.1 Representation

Keep ordinary text in reader navigation, NCX labels, and OPF metadata. These reader interfaces generally do not apply chapter CSS.

Continue using protected spans and shared CSS in the body.

For each localized navigation or metadata destination, embed a record containing:

- EPUB resource path.
- Structural destination address.
- Whether the destination is element text or an attribute.
- Expected element name, including namespace.
- Original source value.
- Immutable marked master value.
- Existing bilingual/single-language presentation information.

A structural address must identify the destination without depending on its current English wording. Use an element-index path with an expected expanded element name, plus the attribute name when relevant. Define element indices over element children consistently in both capture and resolution, so comments and processing instructions do not shift addresses unexpectedly.

Store these records in the existing embedded state. Do not create loose master files by default.

### 5.2 Capturing masters

Capture the master before converting its markers into displayed names.

For example:

```text
Immutable master:
A meeting with ${御坂美琴}

Current mapping:
御坂美琴 -> Mikoto Misaka

Generated navigation:
A meeting with Mikoto Misaka
```

Never overwrite the immutable master with generated display text.

Capture destinations after the document structure is finalized for serialization, so recorded addresses match the written resource. When the loader reconstructs documents from source on resume, recapture addresses for the reconstructed resources while retaining the corresponding accepted marked master values.

### 5.3 Regeneration algorithm

For each recorded destination:

1. Parse the current EPUB resource.
2. Resolve its structural address.
3. Verify the expected element and destination type.
4. Render the immutable marked master using the current catalogue.
5. Substitute target text when mapped, otherwise original label text.
6. Apply the existing bilingual presentation rule.
7. Assign the resulting text or attribute value directly.
8. Serialize the resource.

Do not run replacements against existing displayed English text.

Do not use raw `sed` over serialized XML. Parsed assignment provides escaping and preserves document structure. The user's request for substitution from an original saved value is fulfilled by deterministic marker substitution into that master, not replacement through already rendered output.

Update only recorded destinations. Preserve unrelated text, attributes, body spans, and resources.

### 5.4 Offline edits

`python -m book_maker.tools.marker_epub BOOK.epub apply-catalogue names.json` must:

- Continue allowing target-only changes.
- Preserve original labels, stable keys, accepted masters, counts, and references.
- Regenerate shared CSS from the updated catalogue.
- Regenerate navigation and metadata from immutable masters.
- Verify the complete candidate before atomic replacement.
- Make no provider request.

Applying the same catalogue twice must produce the same displayed values without further substitutions. Byte-identical ZIP output is not required; semantic idempotence is required.

### 5.5 Existing bundles

Keep existing marker bundles readable and resumable.

Add an explicit version for destination-aware localization records without changing the marker workflow identifier.

During a source-backed resumed save, rebuild destination records from the normal loader localization process.

For offline edits to older bundles lacking reliable destinations:

- Proceed with CSS-only edits when no changed mapping affects navigation or metadata.
- If affected navigation or metadata cannot be safely located, fail before replacing the EPUB.
- Explain that the bundle needs a source-backed regeneration.
- Never fall back to sequential English replacements.

## 6. Tests and acceptance criteria

These regression tests are necessary because the existing focused tests passed while missing the three reviewed defects.

### 6.1 Recovery tests

Cover:

- Completed output plus unfinished recovery: resume the recovery.
- Several compatible recoveries: newest modification time wins; ties are deterministic.
- No compatible recovery: start fresh and report it.
- Different source/settings: never reuse incompatible state.
- Distinct fresh runs receive different run IDs.
- Same source filenames with different contents do not collide.
- Fresh-run initialization does not delete an old recovery before a new checkpoint succeeds.
- Failed checkpoint replacement preserves the prior recovery.
- Failed final publication preserves the recovery.
- Successful final publication removes only the matching recovery.
- A moved existing marker recovery resumes from `ongoing`.
- A mismatching file at a cleanup path is preserved.
- Direct loader and custom output-directory paths follow the specified resolution rules.

### 6.2 Catalogue and compaction tests

Exercise the real session-recording path, not a fake method that only appends history.

Force compaction on a chunk introducing a new marker and verify:

- Its catalogue entry exists before summary validation.
- A valid summary containing that marker is accepted.
- Null English targets do not affect compaction.
- No mapping request is made by compaction.
- Failed validation changes neither catalogue nor history.
- Unexpected commit failure rolls back both.
- Optional compaction failure retains accepted chunk state.
- Accepted occurrence counts are not doubled on retry or resume.

### 6.3 Regeneration tests

Cover:

- `Alice -> Bob` and `Bob -> Carol` produce distinct intended results.
- Overlapping names and repeated visible labels.
- Quotes, ampersands, angle brackets, and Unicode.
- NCX labels, XHTML navigation, document titles, and localized metadata.
- Mapped-to-unmapped edits restore original labels.
- Repeated edits always render from the same immutable master.
- Missing or mismatched structural destinations fail atomically.
- Body span labels and catalogue identities remain unchanged.
- Offline edits require neither the source EPUB nor network access for new destination-aware bundles.
- Older bundles follow the explicit compatibility behavior.

### 6.4 End-to-end test

Use a tiny temporary EPUB:

1. Start fresh.
2. Accept a chunk containing a new name and trigger compaction.
3. Interrupt.
4. Verify one recovery exists in `ongoing`, with embedded state.
5. Resume and finish.
6. Verify the final EPUB exists in `output`.
7. Verify the matching recovery is gone.
8. Edit display mappings offline.
9. Confirm body CSS, navigation, and metadata show the intended values.

Also start a second fresh run while the completed first output exists, interrupt it, and prove that resume continues the new recovery rather than reporting the old output as complete.

Run the focused marker suite and relevant session, loader, output, and offline-tool regressions. Report inherited failures separately.

The prior review reported 108 focused tests passing, plus 340 passing and 7 failing in its selected broader suite. The seven failures involved fake translations rejected by the language guard. Re-run the applicable checks; these historical numbers are not proof of the repaired implementation.

Do not run a whole novel as validation or alter user checkpoints. If a bounded live provider check is performed, use temporary test books and report it separately from offline tests.

## 7. Documentation and completion report

Update the workflow documentation and launcher help to explain:

- `ongoing` versus `output`.
- Newest-compatible recovery selection.
- Starting fresh when `-Resume` finds nothing.
- Moving an existing partial EPUB into `ongoing`.
- Deterministic catalogue registration versus independent AI display mapping.
- Immutable-master regeneration.

Keep existing commands usable:

```powershell
./run_grey_translation.ps1 -BookPath 'C:\Books\novel.epub'
./run_grey_translation.ps1 -BookPath 'C:\Books\novel.epub' -Resume
```

The completion report must state what changed, exact test outcomes, compatibility limitations, and whether any live provider testing occurred. Do not describe mocked tests as live validation. No commit or push is required.
