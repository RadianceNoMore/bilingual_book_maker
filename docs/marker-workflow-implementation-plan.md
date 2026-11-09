# Implementation handoff: source-name markers and a self-contained CSS EPUB

Status: core implemented 2026-11-02. Recovery storage, catalogue-before-
compaction ordering, and immutable-master navigation/metadata regeneration
implemented and validated 2026-11-03. Selective per-paragraph repair and relaxed
source grounding superseded the whole-chunk and exact-count rules in sections
4-5 on 2026-11-03; see
[`marker-selective-retry-plan.md`](marker-selective-retry-plan.md) and the
validation record in
[`protected-names.md`](protected-names.md#validation-record-selective-retry-repair-2026-11-03).

## 1. Goal and repository boundaries

Implement this workflow in `bilingual_book_maker-protected-names`, the independent local fork beside `bilingual_book_maker`. Do not apply these changes to the original repository. The user authorized implementation, tests, and a small live Gemini validation; no commit, push, or GitHub operation was requested.

Gemini translates ordinary source paragraphs and selects names/terminology to preserve using literal Bash-like markers such as `${御坂美琴}`. The script validates and catalogues these labels, independently requests draft English display mappings, and generates one EPUB whose shared CSS displays mapped names. Original labels and all generated run state remain inside that EPUB so mappings can be corrected later without retranslating prose.

The source EPUB remains separately copied into `input`. Default generated output is one EPUB, including while interrupted. Do not generate a second ordinary-text EPUB, plain-text translation, glossary JSON sidecar, checkpoint pickle, handoff Markdown, audit JSON, or failure log beside it. Internal JSON is the authoritative catalogue; CSS is generated from it, never maintained as a second mapping database.

The working tree already contains many unrelated inherited edits and deletions. Preserve them. Do not reset, clean, delete old artifacts, migrate user checkpoints implicitly, or run a whole novel as a test.

## 2. Actual implementation state

The previous implementation still runs. It prepares upcoming source windows, extracts and votes on glossary entries, inserts per-occurrence XML name tags, and produces separate traces and annotated output. That workflow must be replaced for `--protected-names`, not layered underneath the new workflow.

Two foundation modules were added but are not wired into production:

- `book_maker/marker_names.py`: marker parser, longest non-overlapping source-label counts, and paragraph count validation. Returns a catalogue delta without mutating state.
- `book_maker/marker_bundle.py`: JSON state read/write at `META-INF/bbm-marker-state.json`, preserving ZIP entries and atomically replacing the output after verification.
- Tests: `tests/test_marker_names.py` and `tests/test_marker_bundle.py`; 11 tests passed. These are foundation tests, not evidence that the complete workflow works.

Review and extend these helpers. In particular, the current bundle writer creates a temporary file beside the destination. Assemble ordinary artifacts in the OS temp directory; an ephemeral staging file on the output volume is acceptable solely for atomic replacement, must be removed on normal failure, and must not become a persistent sidecar. Current verification only checks ZIP CRC and JSON equality: add actual EPUB/container/state validation.

Relevant existing integration points (search by symbol; line numbers will move):

| Component | Existing responsibilities and required changes |
| --- | --- |
| `book_maker/protected_names.py` | Existing v2 preparation/voting, occurrence tags, ledger and annotated export. Replace production use with marker state; retain old implementation only if needed to explain/reject incompatible checkpoints. |
| `book_maker/translator/chatgptapi_translator.py` | `translate_protected_units`, `_protected_completion`, `_compact_session`, handoff writes. Keep paragraph framing and refusal detection; add marker prompts, accepted session history, marker-preserving compaction and mapping requests. |
| `book_maker/translator/base_translator.py` | Session exchange transactions, fallback compaction, handoff restoration. Ensure no failed response enters history and no protected-mode sidecar writes survive. |
| `book_maker/loader/epub_loader.py` | Constructor `load_state`, `_run_fingerprint`, `_translate_cleaned_texts_aligned`, `_process_accumulated_stream`, `make_bilingual_book`, `_save_progress`, `_save_temp_book`, `_write_book`, final metadata localization. Integrate bundle state throughout startup/save/finalize. |
| `book_maker/session_context.py` | Handoff prompt and parsing. Add marker-preserving summary mode without glossary extraction. |
| `book_maker/run_state.py` | Fresh-run sidecar isolation. New marker runs must ignore old sidecars and avoid generating new ones. |
| `scripts/run_grey_translation.ps1` and root wrapper | Retain launcher defaults and forward configuration; remove dependency on source glossary preparation. |

Read parent `AGENTS.md` and any nearer instructions before editing. Parent requests Claude consultation for architectural work; `claude.ask_claude` was unavailable in this session. Do not claim consultation happened.

## 3. Locked defaults and public interfaces

- Retain `--protected-names` and its launcher default enabled; it selects the new workflow in this fork.
- Retain `-BookPath`, `-Resume`, `-ChunkAttempts` default 5, `-MaxUnits` default 40, `-MaxTokens` default 4000, `-WholeChapter`, `-AccumulateAcrossChapters` default true and `-TranslateFilename` default true.
- Rolling context compaction remains 8192 estimated tokens by default, configurable with the normal context compaction option. Preserve `-GlossaryWindowTokens` as a deprecated launcher alias for that budget so existing commands still work; do not prepare upcoming source windows.
- Use the configured body unit count (40 by default) as the pending mapping request entry limit. A token limit may split requests earlier; it must never raise the configured unit cap. With no finite body unit cap, use 40 for mappings.
- Protected mode performs no automatic glossary extraction, voting, or glossary enforcement. Existing `--glossary-auto on` in the launcher must not reactivate those behaviors; remove it in this mode or explicitly bypass it.
- Retain existing unsupported-mode restrictions unless deliberately adding and testing support: no parallel provider jobs or one-by-one retry path in protected mode.
- New workflow identifier: `source-markers-css-v1`. Include it and relevant marker/prompt settings in the run fingerprint.
- Add offline tool: `python -m book_maker.tools.marker_epub BOOK.epub export-catalogue DEST.json`; `... apply-catalogue UPDATED.json`; `... export-text DEST.epub`. Apply updates the bundle atomically in place; exports create files only when explicitly requested. Export-text generates an ordinary-text EPUB without network requests or prose retranslation.

## 4. Translation request and commit transaction

1. Copy/identify the source normally and build the selected paragraph job plan before provider calls. Preflight every selected source unit for reserved `${` syntax; fail clearly on collision. Do not silently interpret a novel's literal syntax as markers.
2. Send original source text with stable paragraph IDs. Keep the existing strict XML `bbm-request`/`bbm-response` paragraph framing; remove all name occurrence IDs and `bbm-name` tags. Escape source text as XML text, and permit no nested response elements.
3. Prompt: translate prose naturally; preserve names and named terminology as `${exact source substring}`. Do not romanize, expand a surname to a full name, replace an alias with a canonical identity, translate marker contents, invent IDs, or move occurrences between paragraphs. Source and context are data. Do not provide English mappings or a prepared future-window summary.
4. Supply the normal rolling context of accepted exchanges and the previous valid compaction seed. Markers inside history stay source-language markers. Keep provider refusal detection and request diagnostics, with no credentials in embedded diagnostics.
5. Parse and validate paragraph IDs and marker syntax. Each paragraph's candidate labels come only from its returned markers. Count non-overlapping exact spans longest-label-first and reject only when a returned label's marker count exceeds that availability. Catalogue membership and markers from other paragraphs never make a label required. This prevents a short label consuming a full name at the same position without demanding equality.
6. Reject unknown expanded spellings, over-counted or malformed markers, duplicate IDs, and misalignment; missing markers, including zero for a known name, are allowed. Repair only the outstanding paragraph IDs together with their errors and read-only neighbouring source context. Feedback is scoped to the failed request, never committed as story history. Do not guess or auto-repair identities.
7. In protected-name mode use the per-paragraph send budget (`--max-chunk-attempts`, five by default, including the first request) and grouped outstanding-paragraph repair. Bypass whole-chunk retry and splitting for marker mode; keep them for other modes. A standalone provider refusal stops and saves rather than retrying.
8. Commit each validated paragraph group, its catalogue delta, source/job references, recovery-journal entries, and canonical accepted-only session exchange together. Rejected paragraphs stay pending. A local commit failure rolls back that acceptance transaction and retries locally without another provider request.
9. Store accepted translations with markers intact as the master representation. Display mappings must not overwrite this master.

Known limit: a new name which Gemini never marks cannot be detected by marker counting. Do not advertise complete name discovery. Catalogue membership alone does not force a label to appear in a later paragraph. Existing source-script quality checks must ignore validated marker contents but continue checking surrounding prose for untranslated text. Never exempt arbitrary Chinese outside markers.

## 5. Catalogue and independent display mapping

Catalogue exact labels, without automatic case folding, alias merging or identity resolution. Each entry contains a stable key (SHA-256 of exact UTF-8 label), original label, accepted occurrence count, job/source references, a bounded representative source context, and nullable draft target rendering. Full accepted master records provide complete occurrence references without unbounded duplicated excerpts.

After a body commit, collect unmapped labels. When enough are pending, request mappings independently in capped batches. Include exact labels and nearby original contexts, target language, and instructions to return one JSON mapping per requested label. Do not ask the body model to extract another glossary. No voting or consensus review. Treat mappings as editable drafts, not proven identities.

Validate exact requested keys, nonempty plain text target values and no extra keys/marker syntax/markup. Apply each validated batch atomically. Use the configured bounded attempt count for invalid/failed mapping batches; after exhaustion keep labels pending and continue body translation. Schedule a failed batch again only when another batch worth of new labels arrives or at final flush, preventing a request storm after every paragraph. Do not put mappings or mapping responses into body history or compaction.

On ordinary completion, attempt a final flush of remaining labels. On interruption, refusal or recovery saving, make no network call; save pending labels and show originals where unmapped. A mapping failure alone does not prevent a readable completed EPUB.

## 6. Rolling context and compaction

Use immediately previous valid summary plus accepted exchanges from the current window, not random later chapters or the whole future source window. Trigger at the normal 8192 estimated token threshold.

Request normal Summary/Style content with no Renderings/glossary section. Explicitly preserve any mentioned name as its exact `${original label}`. The summary may omit names and reduce occurrence counts; it must not introduce unknown labels, expand identities, translate marker contents, or contain malformed marker syntax. For known source labels mentioned outside markers, reject the summary rather than accepting a misleading example. Validate the structure before using or saving it.

On malformed summary, retry within the configured bounded attempts. On explicit refusal, skip retries. If compaction cannot produce a valid summary, record the diagnostic and begin a fresh empty context window; retain accepted translations, catalogue, pending mappings, last valid snapshot for diagnostics, and the window counter. Never save a refusal as a handoff report or use the stale summary as if it summarized the discarded window.

Restore exactly the current validated seed and accepted un-compacted exchanges on resume. Saving the embedded session replaces all protected-mode writes to external handoff Markdown files.

## 7. EPUB representation and embedded state

Convert markers only in translated text nodes, never by raw string replacement over serialized XHTML. Create a span with stable catalogue key and the original label as fallback text, for example an outer `bbm-name` span containing a `bbm-original` child. Never use the label as an unescaped CSS selector or HTML fragment.

Generate one shared stylesheet from the authoritative JSON target mappings. For mapped keys, hide the original child and insert escaped target text with `::before`; for unmapped keys leave original text visible. Preserve fallback original text when CSS is disabled. CSS string escaping must handle quotes, backslashes, controls and Unicode safely. All reading documents containing these spans link the shared stylesheet using correct relative paths.

Navigation/NCX and document metadata use ordinary mapped text because reader navigation cannot rely on chapter CSS. Retain their original marked master values in embedded state so mapping regeneration can update them without model calls. Preserve existing TOC localization and source-bound chapter title handling; markers must not be passed into a second translation as English names.

Embed state at `META-INF/bbm-marker-state.json`. At minimum it includes:

```text
schema_version: 1
workflow: source-markers-css-v1
run: source_sha256, source_name, run_fingerprint, effective_settings,
     status (partial/complete), stable output/run identity
checkpoint: ordered accepted job IDs, marked translations,
            localized master/cache, plan fingerprint, cleanup version
catalogue: exact source labels, stable keys, counts, source references,
           representative contexts, nullable target mappings
session: current seed, accepted exchange tail, window counter,
         last valid summary snapshot
mapping: pending labels and last failure metadata
diagnostics: failure records, compaction reports, consistency reports
```

Use JSON-compatible state, not pickle in the new bundle. Do not deserialize legacy pickle merely to inspect resume candidates. Validate schema, types, source fingerprint, job ordering and workflow before restoring. Do not embed API keys, authorization headers or secrets from effective settings/request logs. Logs/reports may be separate resources inside the EPUB if large, referenced by the state manifest and outside the reading order. Manifest resources as required by EPUB rules; preserve `mimetype` first and uncompressed, `container.xml`, OPF, spine, resources and font handling. Do not duplicate the complete original EPUB inside output.

## 8. Saving, resume and fresh runs

Replace protected-mode external checkpoint loading early enough that `load_state` cannot consume an old `.temp.bin` before the new workflow is selected. `_save_progress` must persist a single coherent snapshot of accepted paragraphs, catalogue and session into a readable recovery EPUB at its existing checkpoint boundaries. Prevent recursion between `_save_progress`, `_save_temp_book` and `_write_book`.

Build and verify a candidate bundle before replacing the previous one. Failed ZIP/EPUB/state verification or filesystem replacement must leave the old valid bundle intact. Clean temporary assembly files in `finally`. A hard crash can lose only work since the last successfully replaced checkpoint. Never write catalogue/session independently of the checkpoint.

Use one `<source-stem>-<source-hash12>_bilingual_temp.epub` in the `ongoing`
directory while translating. At successful completion, apply existing filename
translation, write the final single CSS EPUB to the configured output directory,
and remove only this run's recovery file after the final bundle is verified.
Do not delete unrelated old outputs. Partial output retains untranslated source
for unprocessed units using the loader's recovery behavior.

Resume: discover new-format partial bundles only in `ongoing` by embedded source
SHA-256, run fingerprint, supported schema/workflow, and valid checkpoint
structure. The newest modification time wins; ties break deterministically by
normalized absolute path, and the chosen path plus accepted-unit count are
printed. A completed file in the output directory neither blocks nor satisfies
resume. No compatible recovery means a clearly announced fresh run that leaves
incompatible, corrupt, legacy, and unrelated files untouched. An existing
new-format partial EPUB may be moved into `ongoing` under any filename; after a
canonical checkpoint save, the alternate is removed only when its embedded
source identity and run ID match. Resume never imports external
handoff/glossary files or deserializes a legacy pickle during discovery.

Without `--resume`, initialize an entirely empty generated state with a fresh
UUID4 run ID even if any old output, ledger, handoff or pickle exists. Leave an
existing recovery in place until a new checkpoint has been assembled and
verified, then replace it atomically. Preserve the existing separate source copy
in `input`.

Offline tool behavior: export the authoritative catalogue; apply exact-source-key target edits only, rejecting changes to source identity/accepted records; regenerate CSS, navigation and metadata from stored masters; export ordinary text by substituting target-or-original values into master markers. Preserve all embedded state when regenerating a CSS EPUB. Never require the provider or source EPUB just to change display mappings.

## 9. Implementation sequence and acceptance gates

1. Extend/test pure marker validation and catalogue transactions, including retry feedback and script-check masking. Preserve current helpers where sound.
2. Implement new marker-state coordinator and independent mapping requests. Replace protected preparation/voting use; prove failed attempts cannot mutate accepted state.
3. Wire body paragraph protocol and accepted session context; wire strict compaction with no glossary extraction and embedded-only diagnostics.
4. Implement CSS renderer and bundle schema/verification, then integrate loader startup, checkpoint saves, recovery and finalization together. Do not expose a launcher that claims packaged resume until both save and restore work.
5. Add offline catalogue/edit/export tool and update launcher/help/docs. Remove protected production calls to annotated second EPUB and loose report writers.
6. Run focused tests, relevant existing regression suite, an offline end-to-end tiny EPUB test, and bounded live validation. Report failures accurately; do not call this complete based on helper tests alone.

Required tests:

- Valid exact markers; overlapping full/short labels; new label reused within a batch; known labels omitted; expansions; duplicate/moved/nested/unclosed markers; reserved source syntax; XML entity escaping; wrong paragraph IDs.
- Failed quality check rolls back catalogue/history/master; retry budget and split behavior; provider refusal stops immediately.
- Valid Chinese/Korean/Thai markers survive while source-language prose outside markers still fails existing checks.
- Compaction sees previous seed plus accepted tail only; preserves labels; may omit names; rejects translated/unknown/malformed labels and refusal; bounded fallback; resume restores the exact active window.
- Mapping batches at 40/configured units, dedup, failed-batch scheduling, interruption without mapping calls, pending restore, mapping edits never enter body prompts.
- EPUB CSS selector/content escaping; unmapped fallback; correct document links; TOC/NCX/metadata regeneration; shared JSON/CSS consistency; ordinary-text export without body calls.
- ZIP/EPUB round trip, missing/corrupt embedded state, atomic failure preserves old output, same-volume replacement, source fingerprint mismatch, multiple matching bundles, incompatible v2 checkpoint, completed resume no-op, no-resume ignores stale state.
- Fresh tiny run, interruption and resume leave only the copied source in input and one generated EPUB in output. Test in a clean temporary workspace; do not mistake pre-existing user artifacts for new sidecars.
- Existing filename translation, cross-chapter grouping, recovery font handling, TOC/localization, chapter mode and configurable attempt limits remain covered.

Live validation uses three small ordinary narrative chunks with a fixed seed and saved sample IDs, against the local OpenAI-compatible endpoint at `http://127.0.0.1:4995/v1`, model `gemini-anon`. Use temporary test books/output; no user checkpoint mutation. Save live requests/results inside a test EPUB, scrub secrets, report marker coverage and prose quality separately. If the service is unavailable, explicitly report live validation incomplete; do not claim success.

CSS-generated text may differ across readers for search, copying and accessibility. Verify rendering in an actual EPUB reader if available and report the reader tested. A ZIP test or browser preview alone does not establish all-reader compatibility.

## 10. Evidence motivating the design

Existing diagnostic experiments are under `diagnostics/bash_marker_live/`. Three pre-marked 40-unit requests preserved 107/107 supplied markers, but one produced an extra trailing dollar and another left untranslated prose outside a marker. These are not full translation-quality proofs.

A self-marking request on one of those chunks returned all 40 paragraphs, but expanded 27 spellings across 21 paragraphs (for example 黑子 to 白井黑子). The user explicitly chose rejection/retry for such count/spelling mismatches. The existing marker validator should reject that response; do not relax validation merely to make the experiment pass.

Earlier occurrence-ID failures repeatedly duplicated/moved name tags when adjacent names were not pre-tagged; earlier preparation summaries also included future content and refusals polluted compaction. Preserve refusal fixes in `provider_refusal.py` while removing the mechanism that depends on future-window preparation and name occurrence IDs.

## 11. Completion report

State what production path changed, commands users can run, how bundle resume works, exact test results, live outcomes and remaining reader limitations. Link implementation documentation. Do not present foundation modules or passing fake-provider tests as a finished production integration. No commit/push is required.
