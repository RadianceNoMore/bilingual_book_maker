Retries send only outstanding paragraphs using the normal translation prompt and the context captured before the batch. Validation errors remain local; partial batch results and retry instructions are not sent to the model. Accepted results are still saved immediately.

# Protected names: source windows, exact markers, and CSS

Fresh protected-name EPUB runs use `source-window-markers-css-v2`. The earlier
`source-markers-css-v1` archives remain readable by the offline marker tools,
but are not resumable as v2 because they used model-selected markers and a
different context contract. The older v1 protocol notes below are retained as
recovery history only.

## Current v2 workflow

The novel is processed sequentially in source windows with a default budget of
8,192 estimated tokens. Each window follows this order:

1. Send the complete cleaned original-language window and the preceding
   cumulative source-language summary in one name-extraction request.
2. Validate exact source spellings and cited unit IDs, add every label to the
   persistent novel catalogue, and insert `${name}` locally. Matches reserve
   spans longest-first, with a lexical tie-break; overlapping short names do
   not split a longer name, while separate short-name occurrences still match.
3. Translate bounded chunks from that prepared window. Each request receives
   the same frozen prior summary and a bounded accepted original-source prefix.
   Every prepared marker must appear exactly once in its own returned unit.
4. After all window units are accepted, summarize the original source together
   with the previous summary. Save the cumulative summary before preparing the
   next window, including the final partial window.

Extraction contains names only; it does not summarize upcoming events. The
translation chunks do not receive target-language history. Extraction and
summary are independent provider stages, and token-triggered session
compaction is disabled in this workflow.

## Run

```powershell
Set-Location C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker-window-names
./run_grey_translation.ps1 -BookPath 'C:\Books\novel.epub'
./run_grey_translation.ps1 -BookPath 'C:\Books\novel.epub' -Resume
```

The launcher enables `--protected-names`, filename translation, and
cross-chapter grouping. `-ProtectedNames:$false` selects the ordinary
inherited workflow.

Useful launcher values:

- `-MaxUnits` (default 40) caps body request units and pending mapping entries.
- `-MaxTokens` (default 4000) caps body and mapping request tokens.
- `-ChunkAttempts` (default 5) bounds each paragraph's marker-repair sends in
  protected-name mode (including its first request) and bounds display-mapping
  attempts. `--retry-one-by-one` is ignored there: marker mode always repairs
  the outstanding failed paragraphs together.
- `-NameWindowTokens` (default 8192) bounds original-source extraction
  windows.
- `-SourceSummaryTokens` (default 1500) bounds the cumulative source-language
  summary.
- `--request-max-units` and `--request-max-tokens` bound translation chunks;
  they never combine different source windows.
- `-WholeChapter` and `-AccumulateAcrossChapters` retain their usual meaning.

Direct Python runs add `--protected-names`. The route must be
OpenAI-compatible and provide paragraph, name-extraction, source-summary, and
mapping requests. Parallel, sentence, provider batch-job, and retranslate
modes are rejected. `--glossary-window-tokens` is rejected in this mode; use
`--name-window-tokens`. Explicit `--context-compact-at` and
`--no-context-compact` are rejected because the source summary advances only
after a whole window is translated.

The launcher prints each stage, window ID, source and context token estimates,
chunk and unit IDs, retry count, and accepted/checkpointed progress. On failure
it states whether the request was sent, what failed validation, which work is
saved, and which stage resume will retry. Provider requests are bounded by the
configured output allowance where the endpoint supports a token cap; exact
input-limit preflight is available only if the route exposes its limit.

## v2 extraction and catalogue

The extraction response is strict JSON with one `window_id` and a `names`
array. Each row contains an exact `source` spelling and current-window
`unit_ids`; unknown units, ungrounded spellings, extra keys, and malformed JSON
stop before body translation. An explicit empty array means no names were
found. A malformed response may retry within the bounded extraction budget.

Once prepared, each exact label is added to the embedded catalogue before the
first translation chunk. Existing labels reuse their stable SHA-256 key.
Extraction provenance records the window, cited units, and matched spans;
accepted translation occurrence counts remain separate. Later summaries may
omit a label without removing it from the catalogue. Checkpoint publication
keeps extraction, catalogue changes, and prepared bracket spans together.

## v2 translation and summary validation

The translator is told to copy every supplied marker exactly once in the same
unit and to translate only surrounding prose. Missing, extra, renamed,
translated, duplicated, or cross-unit markers fail that unit. Valid siblings
stay saved; retries send only outstanding units using the same prepared source
and frozen context. A resume reuses committed extraction and accepted work.

The cumulative summary is generated from the original-language source, not
translated prose. It must retain earlier consequential facts while adding the
current window, fit `--source-summary-tokens`, use the source language, and
contain only known exact labels inside `${...}`. If summary generation fails,
the accepted translations and previous summary remain saved, and the next
window does not start. Resume retries the pending summary only.

Exact local matching cannot guarantee that extraction found every name or
distinguished an ordinary-word homonym. It guarantees only that every selected
source span is inserted longest-first and preserved exactly by accepted
translation units.

## Previous v1 translation protocol (archived)

Model-selected names are written as literal markers:

```text
${御坂美琴}
```

The model is asked to copy the exact original source substring inside the
braces. It must not romanize, expand a surname to a full name, translate marker
contents, invent IDs, or move occurrences between paragraphs.

Every request is a strict XML `bbm-request` containing one `bbm-unit` per
paragraph. The response should be one `bbm-response` with one `bbm-unit` for
each requested ID and no nested elements. A missing ID fails only that
paragraph; it is retried rather than assigned another paragraph's text. Source
prose is XML-escaped.

Markers are a source-grounding check, not proof of name completeness or
semantic identity. A paragraph is judged only by the labels it returns; the
catalogue and markers from other paragraphs never make an unmarked name
required. Labels are matched longest-first, without reusing source characters,
and a returned label's marker count must not exceed its non-overlapping exact
source availability. Equality is not required: `美琴…美琴…美琴` may be marked
twice, while `${美琴}` twice for a single `美琴` is rejected. An expanded
spelling such as `${白井黑子}` cannot stand in for `黑子`, and a short label
cannot consume the characters of a longer one accepted first. Missing markers,
including zero markers for a known name, are allowed. Surrounding prose still
has to pass the ordinary untranslated-text, contamination, and Markdown checks;
only syntactically valid marker contents are exempt from the source-script
check.

The initial chunk is translated once. Every valid paragraph is committed
immediately and kept byte-for-byte unchanged. All outstanding failed paragraphs
are then sent together in one repair request with their individual errors and
the original source immediately before/after them as read-only context. A
provider refusal is terminal. Otherwise the repair repeats up to the
`--max-chunk-attempts` per-paragraph budget, counting the initial request and
any failed transport request. Marker mode bypasses the ordinary whole-chunk
retry and recursive split policy, so a chunk is never retried or split around
the selective coordinator. If the budget is exhausted, the run stops with the
outstanding IDs, preserves accepted work, and resume retries only unfinished
paragraphs.

The model is instructed to copy exact source spelling, avoid markers on ordinary
words, and prefer a natural pronoun or reduced repetition when the translation
does. It is not told to force one marker per source occurrence.

A response is parsed strictly by ID. Missing, duplicated, unknown, reordered,
nested, or malformed units are handled per paragraph; one bad paragraph does not
discard valid siblings, and no unit is ever assigned by document order. Before
accepting anything, the strict path counts expected IDs across the whole parsed
tree, so an occurrence nested under an unknown wrapper or inside another unit
still makes a second occurrence ambiguous; only direct `bbm-unit` children of
`bbm-response` can supply text. If the outer XML is malformed, a bounded lexical
scanner may retain only complete, independently parseable, non-nested `bbm-unit`
fragments with exact expected IDs and a single unambiguous opening. Ordinary
text, tags, quoted attribute values, comments, CDATA sections, and processing
instructions are distinguished, so markup-looking text inside any of them never
opens a unit; duplicate, nested, or unfinished openings leave that ID pending.
An unfinished unit opening is scanned through every attribute, so each complete
or partial ID it contains taints that identity instead of the scanner stopping
at the first match. Units are salvaged only at document level or as direct
children of a `bbm-response` frame, and an unknown wrapper never promotes its
descendants. Entities and DTDs are rejected, network access is disabled, and an
ambiguous or truncated frame contributes nothing. Unknown units and surrounding
commentary are discarded with diagnostics.

A provider finish reason of `length` is not itself fatal for marker paragraph
requests. The available raw response is returned so strict parsing and safe
salvage can accept its complete units; incomplete, ambiguous, or missing units
remain pending for the grouped repair. The finish reason stays in the provider
response metadata and a bounded truncation diagnostic is recorded. Truncation
is still rejected for display mappings, compaction, and the older protected-name
workflow.

Accepted work is committed before the original chunk returns. Catalogue labels
are registered before history recording can trigger compaction. Accepted
paragraphs, the catalogue delta, and the canonical accepted-only exchange are
one rollback-protected local transaction; failed raw responses and repair
instructions never enter history. The accepted journal also survives an error
or interruption in the embedded recovery EPUB. Resume verifies identity and
source digest, skips those model requests, and does not double-count catalogue
occurrences or append duplicate exchanges.

The accepted translation is the master representation. Markers stay in the
master text; display mappings only affect presentation.

Known limit: a name the model never marks cannot be discovered or edited by
CSS, and an unmarked English rendering or a name replaced by a pronoun can
still pass. The source-grounding check does not prove name completeness or
semantic identity.

## Display mappings and CSS

After body translation, unmapped catalogue labels are requested independently
of story history in bounded batches (configured body unit cap, default 40; a
token cap may split earlier). Mapping replies are JSON with exactly one row per
requested key. Mappings are editable drafts, not identity proofs. No voting or
consensus pass runs.

At EPUB assembly, markers only in translated text nodes become:

```html
<span class="bbm-name" data-bbm-key="sha256-key">
  <span class="bbm-original">御坂美琴</span>
</span>
```

One shared stylesheet is generated from the embedded catalogue. A mapped key
hides the original child and inserts the escaped target through `::before`; an
unmapped key has no rule, so its original text remains visible. The stylesheet
is linked from each reading document that contains one of these spans.

Navigation, NCX, and document metadata use ordinary mapped text because reader
navigation cannot rely on chapter CSS. Their marked masters remain in the
embedded state as immutable records, so the offline tool can regenerate them
without network calls.

## Recovery layout and resume

Protected mode separates unfinished work from finished books:

```text
input/
    source.epub
ongoing/
    <source-stem>-<source-hash12>_bilingual_temp.epub
output/
    <translated-filename>_bilingual.epub
```

`ongoing` is a sibling of the configured output directory. For example,
`--output-dir D:\Translations\finished` writes recovery to
`D:\Translations\ongoing`. Direct loader calls without `--output-dir` use an
`ongoing` directory beside the source EPUB. The source-hash suffix keeps two
different books with the same filename from sharing a recovery path. The
directory is created only when a checkpoint is written; the completed book
still goes to the configured output directory.

`-Resume` searches only `ongoing`. A candidate must have supported marker state,
the same source SHA-256 and compatibility fingerprint, `status: partial`, and a
valid checkpoint. If several qualify, the greatest modification time wins and
an exact tie is broken by normalized absolute path; the chosen path and accepted
unit count are printed. Completed books in `output` never block or satisfy this
lookup. If nothing compatible exists, the run announces that it is starting
fresh and leaves every incompatible, corrupt, legacy, or unrelated file
untouched.

Starting without `-Resume` ignores generated state and gives the run a fresh
UUID4 run id. The existing recovery is not deleted first: it is atomically
replaced only after a new checkpoint has been assembled and verified. On
successful publication the final EPUB is verified and atomically written to
`output`, then only a recovery whose embedded source identity and run id match
the active run is removed. A failed checkpoint or publication leaves the prior
recovery in place.

An existing new-format partial EPUB can be moved into `ongoing` before using
`-Resume`; discovery accepts its current filename. After the next canonical
checkpoint save, that alternate file is removed only if its embedded identity
matches the active run.

CSS-generated text may behave differently for search, copying, and
accessibility. A ZIP test or browser preview does not prove compatibility in
every reading system.

## Embedded state

The finished EPUB contains the authoritative catalogue and resumable state at
`META-INF/bbm-marker-state.json`:

- run identity: source SHA-256, source name, compatibility fingerprint, safe
  settings, status, and a unique run id;
- checkpoint: ordered accepted job IDs, marked translations, localized
  masters, plan fingerprint, and cleanup version;
- source-window recovery in v2: frozen ordered windows and source digests,
  exact extracted labels and selected spans, extraction request fingerprints,
  accepted unit translations, incoming and outgoing cumulative summaries, and
  summary validation metadata;
- paragraph recovery journal (optional): versioned accepted marked text keyed
  by the stable source-unit/localization identity plus source digest; entries
  are promoted into the checkpoint and removed when the batch completes;
- catalogue: exact labels, stable SHA-256 keys, accepted counts, job/source
  references, bounded representative contexts, and nullable draft mappings;
- session: validated seed, accepted exchange tail, window counter, last
  valid summary, an optional `context_validation` record (version, window,
  recorded time) stamped with every new summary, and an optional
  `context_repair` record (`pending` flag, purpose, reason, bounded
  evidence counts/digest, resolution) that survives every snapshot;
- mapping: pending labels and bounded failure metadata;
- diagnostics: failures, provider-error/compaction reports, and consistency
  reports; a recognized provider boilerplate reply is quarantined here with
  its bounded text, never reinstalled as context;
- destination-aware localization records (version 3): resource path,
  structural element address, destination type and attribute, expected element
  name, original source, immutable marked master, and bilingual/single
  presentation mode. Version 3 records were captured from explicit
  source-to-master bindings carried through serialization, so two labels that
  happened to share the same English draft remain distinct destinations.

No pickles or external sidecars are used in this mode. Fresh runs ignore old
`.temp.bin`, handoff, approved-glossary, and old protected-name ledgers; they do
not migrate or delete them. A resume without a compatible recovery leaves
legacy state where it is and starts a fresh run.

## Rolling context in legacy v1 recoveries

Accepted exchanges are replayed as source-language marker text. Compaction
at 8192 estimated tokens asks for the normal Summary/Style content, with no
Renderings/glossary section and no display mappings, and requires any mentioned
name to use its exact `${original label}`. Summaries may omit names and reduce
counts but cannot introduce unknown labels, translate marker contents, or
mention a known catalogue label outside a marker. A label whose display target
is still null is already registered and participates normally.

A malformed summary is retried within the attempt budget, sharing the
compaction-call limit with paragraph repairs but a separate budget so a failed
compaction never consumes a paragraph's sends. A whole standalone provider
failure message (for example the hard-time and something-went-wrong
boilerplate) is classified before parsing/trimming, charged as a failed
attempt, and retried against the same sanitized history; an explicit policy
refusal is not retried. The bounded seed is validated again after truncation.

If compaction cannot produce a valid summary, accepted translations, the
catalogue, journal rows, and previously accepted exchanges stay untouched, the
last independently valid summary stays in the bundle, and the run saves a
partial recovery with `context_repair.pending` set before stopping cleanly
(replacing the old silent empty-window continuation, which stays in force for
ordinary non-marker runs). On resume this explicit pending state blocks every
new request until context is rebuilt; pure reuse of already accepted records
keeps working. Saved `seed`/`last_summary` are validated independently before
they are restored, and recognized provider boilerplate is quarantined into
diagnostics with a pending-repair flag even when an old compaction event says
`ok`. Interrupted before regeneration, a pending resume either rebuilds again
or uses nothing poisoned.

Resuming into a pending repair rebuilds the context automatically: body
checkpoint identities are resolved against the current verified input EPUB
with the same filters/cleanup, digest-verified journal siblings are merged and
deduplicated by identity, navigation/metadata jobs never enter the narrative
order, and a budgeted recent suffix (default ~8192 tokens, never cutting a
marker or unit) is sent as canonical accepted source/master exchanges
preceded only by an independently validated earlier summary, if one exists.
The regenerated summary passes the normal compaction boundary before the
pending flag clears, and a verified recovery EPUB is written atomically before
any new translation request. If no accepted material exists, the run
initializes a clean empty session with a diagnostic; unreconcilable evidence
or an exhausted rebuild retries on later resumes with the accepted work
retained. Paragraph IDs stay strict throughout: malformed IDs are neither
guessed nor matched against translated prose, and the malformed-ID paragraph
of the historical defect is simply one more outstanding unit.

## Offline catalogue tool

```powershell
python -m book_maker.tools.marker_epub BOOK.epub export-catalogue names.json
python -m book_maker.tools.marker_epub BOOK.epub apply-catalogue names.json
python -m book_maker.tools.marker_epub BOOK.epub export-text plain.epub
```

- `export-catalogue` writes the authoritative label-keyed catalogue.
- `apply-catalogue` accepts target-only edits. Source labels, keys, counts,
  references, contexts, and immutable masters are preserved. It regenerates
  CSS from the updated catalogue and regenerates navigation, NCX, document
  titles, and OPF metadata from their recorded structural destinations and
  marked masters. It never replaces text against previously displayed English
  strings, so `Alice -> Bob` followed by `Bob -> Carol` remains two distinct
  destinations. Applying the same catalogue twice is semantically idempotent.
  The complete candidate is verified before atomic replacement; a missing or
  mismatched destination fails without touching the original bundle. No
  provider or source EPUB is needed.
- Version 3 destinations are bound to their original labels before ebooklib
  renders navigation, NCX, document titles, or OPF metadata. Distinct labels
  that started with the same displayed English target still update separately.
  The binding is internal serialization transport: it never enters provider
  requests, accepted translations, session history, published XHTML, metadata,
  or embedded state.

## Offline context repair

When a recovery bundle's saved context was seeded with provider boilerplate
(the 2026-11-03 defect), the next resume repairs it automatically. The same
cleanup is available offline, without any provider call and without touching
accepted book text, the catalogue, the checkpoint, or the journal:

```powershell
python -m book_maker.marker_repair "ongoing\<recovery>_bilingual_temp.epub"
```

The helper classifies the saved `seed`/`last_summary` with the shared
standalone-provider-error rules, clears recognized boilerplate from the active
context fields, records the bounded poisoned text in diagnostics, sets
`context_repair.pending = true`, backs the original up under `ongoing/backup`
(a directory resume discovery never scans), embeds the cleaned state, and
verifies the replacement bundle before the atomic swap. A bundle without
recognized contamination is reported as needing no repair. The following
normal `-Resume` run then rebuilds context from accepted translations exactly
as after a runtime repair, with no extra flag.
- Bundles written before trusted destinations (localization versions 1 and 2)
  still open and resume. A source-backed resume rebuilds version-3 bindings from
  the source and accepted marked masters without retranslating accepted body
  paragraphs. Their old destination records are never copied or relabelled
  merely because displayed English happens to match.
- For an offline `apply-catalogue` edit on version 1 or 2, a change that does
  not affect any saved navigation or metadata master is applied CSS-only. A
  change that does affect them, or whose relationship cannot be established,
  fails before replacement and says a source-backed regeneration is required
  rather than guessing identities from displayed text. The same failure applies
  to a malformed or missing destination: the original EPUB is left untouched.
- `export-text` writes an ordinary-text EPUB with target-or-original values and
  no name spans or stylesheet, without prose retranslation.

## Output and limitations

The run writes one `<title>_bilingual.epub` in the configured output directory.
No annotated second EPUB, glossary JSON sidecar, checkpoint pickle, handoff
Markdown, audit JSON, or failure log is written beside it. Partial recovery is a
readable EPUB bundle in `ongoing` and keeps untranslated source for unprocessed
units.

Marker checks enforce names and structure, not overall semantic fidelity.
Mappings are editable drafts and can be wrong. A name never selected by the
model is not protected. Verify rendering in an actual EPUB reader before
relying on the CSS presentation.

## Validation record: ID attribution follow-up (2026-11-03)

Both remaining attribution reproductions now reject their affected IDs:

- strict XML `bbm-unit id="a"` containing a nested `id="b"` followed by a
  second direct `id="b"` accepts neither unit; reversing the two occurrences
  gives the same result, and a safe direct sibling `c` still survives;
- an unfinished `<bbm-unit id="a" id="b"` after a complete `id="b"` keeps both
  `a` and `b` pending instead of honoring only the first attribute.

The strict prepass now counts expected IDs across the whole parsed tree,
including descendants of unknown elements, and rejects an ID that appears more
than once or only in an ineligible position before any text is accepted.
Comments, CDATA, processing instructions, and escaped markup examples contribute
no phantom IDs. The unfinished-opening scanner now reads every attribute to the
available end, respects quoted values, unions complete and partial IDs, ignores
`data-id` lookalikes, and conservatively marks every expected ID ambiguous when
the opening cannot be bounded.

The focused marker suite passed: **198 tests** across the nine marker and
protected-name files. It includes the two reproductions, reversed duplicates,
nested-under-unknown duplicates, nested-only units, CDATA/escaped examples,
reversed and repeated unfinished attributes, partial prefixes, unrelated
attributes between IDs, entity-encoded IDs, bounded safe siblings, unbounded
openings, and `data-id`/quoted-value lookalikes. Two coordinator tests run
through the real `translate_marker_units` adapter: each first response commits
and journals only `c`, persists before the repair request, and then requests
exactly `a` and `b`; rejected text never enters accepted history, catalogue,
journal, or session. An ongoing-EPUB interrupt/resume test proves the accepted
`c` paragraph is reused without another provider request and the final EPUB
keeps source order with one catalogue count per label.

The selected review regression suites (protected names, translation quality,
glossary gate, session handoff/session mode, translation policy, untranslated
recovery, run state, output naming, CLI documentation, repository layout, EPUB
metadata/output validity, provider refusal, and chunk retry) finished
**383 passed, 0 failed**. The provider-truncation adapter tests still pass.

No live provider request was made; deterministic scripted completions cover
these parsing defects.

## Validation record: salvage and truncation repair (2026-11-03)

The focused marker suite passed in this checkout: **186 tests** across the same
nine files. The repair adds:

- the three exact malformed-XML reproductions (nested duplicate, CDATA example,
  unfinished duplicate): `b` is never accepted, and the enclosing/nested IDs are
  tainted;
- wrapper-free nested and unfinished duplicates, and ambiguous examples mixed
  with safe standalone siblings that survive while only the ambiguous IDs are
  repaired;
- units nested under unknown parents, duplicate IDs in reversed order, complete
  and truncated duplicate openings, missing ID attributes, partial ID prefixes,
  and openings too incomplete to attribute safely;
- markup-looking fake units inside CDATA, complete and incomplete comments,
  quoted attributes, and processing instructions, plus valid CDATA inside a real
  unit, escaped attribute references, `data-id` lookalikes, duplicate
  attributes, and `>` inside a quoted attribute;
- provider-to-workflow tests through the real `translate_marker_units` adapter
  with a scripted `_protected_completion`: a length-finished complete unit plus
  unfinished sibling keeps the complete unit and repairs only the rest; empty
  and repeated truncation consume the per-paragraph budget; a length-finished
  all-valid response is accepted once; refusal stays terminal; and an actual
  ongoing-EPUB interrupt/resume keeps the accepted unit translated and sends
  only the outstanding ID again.

The selected review regression suites (protected names, translation quality,
glossary gate, session handoff/session mode, translation policy, untranslated
recovery, run state, output naming, CLI documentation, repository layout, EPUB
metadata/output validity, provider refusal, and chunk retry) finished
**383 passed, 0 failed**.

No live provider request was made; scripted deterministic completions cover the
truncation repair.

## Validation record: selective retry repair (2026-11-03)

The focused marker suite passed in this checkout: **169 tests** across
`tests/test_marker_names.py`, `tests/test_marker_workflow.py`,
`tests/test_marker_bundle.py`, `tests/test_marker_end_to_end.py`,
`tests/test_marker_resume.py`, `tests/test_marker_selective_retry.py`,
`tests/test_protected_names.py`, `tests/test_translation_quality.py`, and
`tests/test_glossary_gate.py`.

The selective-retry coverage includes:

- a 40-paragraph reply with three failures: only those three IDs are requested
  together next, 37 accepted responses stay byte-for-byte unchanged, and a
  second-round single failure requests exactly its own ID;
- the section-2 grounding examples, zero markers for a known name, ordinary
  Chinese substrings that catalogue membership does not make mandatory,
  isolated/internal punctuation labels, expansion rejection, overlapping and
  repeated labels, longest-first priority, and replacement-created matches;
- one bad marker not rejecting valid siblings, a newly selected repair label
  not invalidating accepted paragraphs, five-send and one-send budgets,
  transport failures with bounded waits, and terminal provider refusals;
- missing, duplicate, unknown, reordered, nested, malformed-outer, truncated,
  nested-frame, entity/DTD, and commentary cases, with no invented output for a
  missing ID;
- per-paragraph untranslated-text and Markdown checks, including unmarked
  Chinese outside markers;
- local commit rollback and retry without an extra provider request, catalogue
  registration before compaction validation, failed candidates absent from
  history, identical source paragraphs retaining distinct identities, and a
  recovery-checkpoint callback after each successful acceptance round; journal
  entries are promoted only by a checkpoint snapshot that contains the same
  identity, so an interruption between coordinator return and loader recording
  cannot lose accepted work;
- an actual ongoing-EPUB interruption where only one paragraph was accepted
  before the chunk failed: the recovery bundle embeds and renders that accepted
  paragraph, resume issues one request for the remaining paragraph, and the
  final catalogue counts each label once;
- journal schema validation, source-digest verification, and the existing
  final EPUB navigation/NCX/metadata/CSS/offline-edit checks.

A targeted regression run passed **882 tests** across glossary
gate/trust/wiring, session handoff and mode, translation
policy/recovery/refusal, run state, output naming, CLI documentation, repository
layout, EPUB metadata/output validity/rights, loader batching, and the ChatGPT
translator. **50 failed**, all in the inherited categories listed below:
42 `test_cli` cases dominated by fake-English validation and `cp1252` plan-JSON
reads, all seven marker-alignment cases in
`tests/test_batch_groups_and_markers.py` (fake-English validation), and one
handoff-prompt wording assertion. A focused
`tests/test_epub_loader_correctness.py -k "resume or checkpoint or temp"` run
finished **7 passed, 9 failed**; those failures were the same inherited
fake-English checks and the existing whole-chapter retry-count assertion, not
marker-workflow regressions.

## Validation record: identity repair (2026-11-03)

The focused marker suite passed in this checkout: **142 tests** across
`tests/test_marker_names.py`, `tests/test_marker_workflow.py`,
`tests/test_marker_bundle.py`, `tests/test_marker_end_to_end.py`,
`tests/test_marker_resume.py`, `tests/test_protected_names.py`,
`tests/test_translation_quality.py`, and `tests/test_glossary_gate.py`.

The new coverage includes:

- `ongoing` versus `output` resolution, custom output directories, direct
  loader calls, source-hash filenames, newest-mtime selection, deterministic
  ties, completed-output exclusion, fresh-run run ids, moved recoveries, and
  identity-checked cleanup;
- failed checkpoint replacement and failed final publication preserving the
  prior recovery;
- catalogue registration before real session-recording compaction, null-target
  summaries, rollback of catalogue/session/summary state, optional compaction
  failure, and no doubled counts after a failed commit retry;
- `Alice -> Bob` followed by `Bob -> Carol` without cascade, overlapping-name
  swaps without sequential substitution, mixed masters with prefix text,
  wrapped text preservation, unmapped restoration, XML escaping, idempotent
  repeat edits, broken-destination and unknown-token atomic failure,
  body/catalogue preservation, `meta` content attribute regeneration, and the
  explicit version-1/version-2 compatibility paths;
- explicit-binding identity collisions: two distinct labels whose draft
  English target is identical, reversed binding insertion order, two TOC
  entries for different fragments of one document, repeated copies of one
  master, repeated DC metadata occurrences, and an unrelated body hyperlink
  that is never enrolled;
- tail-slot capture with surrounding prose and whitespace preserved, plus
  wrapped text and prefix text regeneration;
- an offline tiny-EPUB run covering interruption, `ongoing` resume, final CSS
  output, a second fresh run, matching-recovery removal, and no serialization
  tokens in requests, session history, recovery files, or published resources;
- a source-backed run with metadata enabled that captures navigation, NCX,
  document titles, and OPF destinations and edits them offline without the
  source EPUB;
- a source-backed resume that upgrades older localization records to version 3
  without retranslating accepted body paragraphs.

A targeted regression run passed **571 tests** across glossary gate/trust/
wiring, session handoff and mode, translation policy/recovery/retry/refusal,
run state, output naming, CLI documentation, repository layout, EPUB
metadata/output validity/rights, loader batching, and the ChatGPT translator.
The identity-repair re-run of a 369-test subset finished **355 passed, 14
failed**; all 14 were the inherited fake-English, handoff-wording, or
locale-decoding failures listed below, not marker-workflow regressions.

The inherited full suite is still not green. Failures observed outside marker
mode in this checkout are:

- fake `[offline]...`/`<T>...` English replies rejected for a `zh` target in
  `test_epub_loader_correctness`, `test_flag_compat`, `test_workspace`,
  `test_language_tag_and_name`, and seven marker-alignment cases in
  `test_batch_groups_and_markers`;
- a whole-chapter retry-count assertion,
  `test_whole_chapter_failure_saves_completed_chapters_for_resume`, which sees
  two extra `["six", "ten"]` attempts;
- a handoff-prompt wording assertion in `test_session_context`; and
- `cp1252` reads of UTF-8 plan JSON in `test_translation_plan`/`test_cli`.

A focused `test_epub_loader_correctness.py -k "resume or checkpoint or temp"`
run finished **7 passed, 9 failed, 52 deselected**; the full file exceeded ten
minutes because failing chunks retry with exponential backoff. A combined
`test_workspace`/`test_session_context`/`test_language_tag_and_name` run
finished **72 passed, 7 failed**. These paths do not enter the marker workflow.

No live provider request was made for the identity or selective-retry
repairs. The earlier 2026-11-02 `gemini-anon` check below predates these
repairs and is retained as historical evidence only.

## Validation record: marker-context recovery (2026-11-03)

The focused suite passed in this checkout: **417 tests (416 passed)** across
`tests/test_provider_refusal.py`, `tests/test_marker_names.py`,
`tests/test_marker_workflow.py`, `tests/test_marker_selective_retry.py`,
`tests/test_marker_bundle.py`, `tests/test_marker_repair.py`,
`tests/test_marker_resume.py`, `tests/test_marker_end_to_end.py`,
`tests/test_chunk_retry.py`, `tests/test_session_context.py`,
`tests/test_session_mode_translator.py`,
`tests/test_session_handoff_snapshot.py`, and
`tests/test_glossary_gate.py`. The only failure is the inherited
handoff-prompt wording assertion in `tests/test_session_context`
(`test_a_user_style_is_not_asked_for`), which the earlier validation records
already listed; nothing else failed.

The new coverage includes:

- both the hard-time and the something-went-wrong messages rejected as
  compaction seeds in raw, typographic-apostrophe, seed-wrapper, and fenced
  forms, while a marker-free summary, quoted narrative, and XML/JSON content
  that merely contains the words stay ordinary content;
- `classify_provider_reply` deciding policy-refusal over provider-error, and
  the structured refusal/finish-reason behavior staying terminal;
- real `ChatGPTAPI._compact_marker_session` behavior: an unusable reply is
  charged as a failed compaction attempt and retried from identical sanitized
  history (the first send and the second are byte-identical requests), only
  the validated summary becomes the seed with a `context_validation` record,
  and five failures retain the last independently valid summary, blank the
  live seed, mark `context_repair.pending` with the attempt count, and leave
  journal rows untouched — after which a reloaded workflow cannot treat the
  blank seed as a successful repair and every new request is refused;
- paragraph-level classification before XML framing: two boilerplate replies
  keep both outstanding IDs pending, charge exactly one send per ID each, and
  the accepted-only history plus never the unusable replies;
- pending-repair gates refusing translation and display-mapping requests
  while pure reuse of accepted checkpoints keeps working;
- resume quarantine of a poisoned seed plus poisoned/wrapped last summary,
  with valid saved exchanges still replayed;
- snapshot round-trips carrying `context_validation`/`context_repair` fields,
  bundle validation accepting them and refusing a malformed `pending` value,
  and resume discovery selecting such a bundle unchanged (backups living in
  `ongoing/backup` are invisible to discovery);
- two full-loader integration runs on the real three-paragraph EPUB: a
  poisoned bundle with `ok` history quarantined, `_protected_completion`
  never seeing the boilerplate, one rebuild request carrying digest-verified
  accepted source/master evidence (reported as "Context rebuild: selected 1
  accepted units; attempt 1/5") and no display mappings, the pending flag
  cleared after "Context rebuild: replacement validated and recovery saved;
  continuing translation.", the accepted sibling never resent, catalogue
  counts still one per label, and the resumed run completing normally; plus
  a run with no repair capability stopping with exit code 1 before any new
  request and persisting the pending flag for the next resume;
- `repair_state` clearing only the poisoned field(s), the seed-wrapper and
  fenced variants, clean bundles reported as needing no repair, and
  `repair_bundle` rewriting only the embedded state with an atomic, verified
  swap and a backup kept under `ongoing/backup`.

The user's real recovery (`ongoing/超炮，某不科学的恋爱喜剧
-c00de9cde483_bilingual_temp.epub` — 2000 accepted checkpoint units, 39
journal entries, 51 catalogue labels) was repaired offline with
`python -m book_maker.marker_repair`: both poisoned fields cleared, the
bounded boilerplate quarantined into diagnostics,
`context_repair.pending = true` (purpose `offline-cleanup`), the original
backed up under `ongoing/backup/
超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp-20261103T122429Z.epub`,
and the replacement verified by re-reading its embedded state. Its seed,
summary, provisional acceptance, journal, catalogue, localization, and run
identity are otherwise untouched. No live provider request was made for this
validation; the automatic rebuild path was exercised with offline completions
only, and the cause of Google's original refusal is not claimed by these
tests. The user's ordinary resume command is expected to regenerate the
summary from the 2000 accepted units before further translation.

### Previous live check (2026-11-02)

Live validation used `http://127.0.0.1:4995/v1` with `gemini-anon`,
temperature 0, and three ordinary Chinese narrative paragraphs:

- 3/3 body chunks accepted; 5/5 source name occurrences marked exactly
  (`御坂美琴` ×3, `白井黑子` ×2).
- 2 catalogue labels, both mapped; no untranslated Chinese prose survived
  outside markers.
- Six provider requests total (three body chunks, two title/metadata labels,
  one display-mapping batch).
- Calibre 9.17.0 rendered `::before` content and hid `.bbm-original`: extracted
  PDF text showed `Mikoto Misaka` and `Kuroko Shirai` in the translated
  paragraphs. This is a Calibre rendering check, not proof for every reader;
  search/copy/accessibility behavior for CSS-generated text remains
  reader-dependent.

Evidence is stored under
[`diagnostics/marker_live_20261102`](../diagnostics/marker_live_20261102/README.md).

## Marker request assembly (2026-11-03)

The marker translation path uses one shared user-message assembly,
`assemble_marker_request(payload, *, language="english", feedback="")` in
`book_maker/marker_workflow.py`. Its layout is fixed:

1. the existing translation and XML output instructions (`marker_prompt()`),
2. the shared marker reminder (heading `MARKER RULES:`; only critical
   prohibitions are capitalized),
3. the labeled `MARKER EXAMPLE ONLY — DO NOT RETURN THIS EXAMPLE:` block
   (English targets only, exactly once, before the source data; it never
   carries an output ID),
4. when repairing: the repair-only instruction plus the specific feedback
   (failing IDs, counts, and read-only neighboring source),
5. the actual `<bbm-request>...</bbm-request>` payload, byte-preserved in its
   escaped form,
6. the identical marker reminder as the final instruction, outside the XML.

The reminder is seven lines (only English-target lines 3 and 4 are adapted
for other languages using generic wording; the core rule — marker contents
copy the original source, never the translation — is language-independent).
Non-English targets do not receive the English example and are never told to
output English; there is no language whitelist and a name that resembles a
pronoun in another language is not banned. The parser still requires only a
`bbm-response` with correctly escaped units, and no invented ID is ever
added.

The same assembly feeds the live adapter (`translate_marker_units`), the
canonical accepted-only history recorded by `_commit_round`, and the
rebuild-context evidence (`canonical_evidence_exchanges`), so replayed,
reconstructed, and live requests cannot drift apart;
`marker_unit_request` exposes the same assembly for one-shot requests and
rejects repair misuse. The context-budget estimator counts the reminder and
example overhead, exactly as with the rest of the message. Display mapping,
name extraction, and source-window summary requests use their own prompts and
source-only contexts. Historical saved exchanges are not rewritten; v2
prepared translation requests carry the frozen prior source-language summary
explicitly and do not append target-language exchanges.

Failed paragraphs are resent together in one repair request. Repair feedback
is tailored to the actual failure type, using narrowly scoped structured
metadata on the raised `MarkerError` (`label_details`: label, requested,
available, and a `kind` of `pronoun-marker`, `absent`, or `excess`; the
human-readable message is unchanged and the metadata is additive):

- English pronoun markers (a closed, case-insensitive English list used
  only to tailor instructions, never as a global validator ban): write the
  pronoun as plain prose and do not substitute another marker;
- an invented source label absent from the paragraph: invented source
  labels are forbidden, the translated pronoun belongs in ordinary prose,
  and the actual original spelling must be copied exactly if it was meant
  as a name (do not remove valid name markers);
- a valid name over its source count: keep no more marked occurrences than
  the source permits and use natural plain pronouns for the rest (do not
  remove the other name markers).

XML/ID failures keep the existing recovery guidance and are never labeled as
pronoun errors. All five-send defaults, per-paragraph attempt accounting,
atomic commits, and resume behavior are unchanged.

## Validation record: prompt reinforcement and focused repairs (2026-11-03)

Local validation in this checkout passed. The focused eight-file suite
(`tests/test_marker_selective_retry.py`, `tests/test_marker_end_to_end.py`,
`tests/test_marker_resume.py`, `tests/test_marker_repair.py`,
`tests/test_marker_bundle.py`, `tests/test_provider_refusal.py`,
`tests/test_marker_names.py`, `tests/test_marker_workflow.py`) went from
**175 passed, 1 failed** to **176 passed, 0 failed** after the investigation
below; no marker test regressed. The `${his}` / `${he}` / `${him}` /
`${hids}` / `${他的}` failure modes remain rejected; plain pronouns and
legitimate Chinese name markers pass; the 40-unit/five-failure case still
accepts 35 once, resends only the failures, and commits corrected candidates
once.

The one failure in the combined run was
`test_marker_workflow.py::test_attribute_destination_is_captured_and_regenerated`,
a metadata-destination assertion unrelated to this work. Root cause
established: `EpubLoader.__init__` deliberately monkey-patches ebooklib's
writer process-wide (the #173 fix that preserves imported nav documents'
translated content) to emit raw `item.content` bytes. Any test instantiating
`EpubLoader` before this test (here `tests/test_marker_end_to_end.py`)
strips the XHTML namespace from `EPUB/one.xhtml`, so the test's namespaced
XPath matched nothing and a correct element appeared missing. The failure
reproduces identically with every new test from this change deselected, and
passes whenever the attribute test runs before a loader is instantiated —
this is the pre-existing combined-suite flake the plan asked to be
distinguished, not a regression introduced here. The assertion was made
namespace-agnostic (local-name match, like the file's other helpers); it
still requires the `npkr-title` meta element to exist and to carry
`Alicia`. No loader or ebooklib patch was touched.

The new coverage includes:

- identical reminder blocks before and after `<bbm-request>` (verified
  against the two headings), the final reminder immediately after the
  closing tag, the example appearing once before the payload and never
  inside a unit, and the repaired assembly carrying the same reminder pair
  around the repair notice;
- XML escaping and stable identities unchanged, including sources with
  `${...}` sequences, XML characters, and instruction-like text (round-trip
  through `bbm-unit` verified);
- structured `label_details` (`pronoun-marker` / `absent` / `excess`) with
  unchanged human-readable messages; `english_pronoun_label` applies only
  to feedback tailoring and never bans a string globally;
- repair feedback carrying the per-paragraph corrections for the failing
  paragraphs (English pronoun hint, invented-marker prohibition with
  copy-the-original-spelling instruction, at-most-N occurrences with
  natural plain pronouns), sent in one repair request listing only the
  failed IDs, with corrected candidates committed exactly once each;
- canonical commit and rebuild evidence both using the shared assembly
  (`_commit_round` and `canonical_evidence_exchanges` byte-identical to a
  hand-assembled reference), the reminder/example overhead counted by
  `estimate_tokens`, and non-English targets keeping generic pronoun wording
  with no English example and no English-output instruction.

The live comparison required by the plan was run on 2026-11-03 with
`gemini-anon` (anonymous endpoint, temperature 0, `max_tokens` 4000) on
exactly the five outstanding paragraphs, with byte-identical system
message, rebuilt seed, and accepted exchange history for both variants;
three trials per variant, interleaved A B A B A B, selective repair with
the existing five-attempt limit. Results: baseline completed 3/3 (2/15
first-send acceptances; all finishes needed repair rounds), reinforced
completed 1/3 (6/15 first-send acceptances — one trial accepted 5/5 on
the first send; the other two exhausted or stalled at the same
English-pronoun and invented/excess-marker failure modes under both
assemblies). The sample is too small to rank prompts and no improvement
is claimed; the deterministic validator remains the acceptance gate.
One transport read-timeout occurred during the initial run and was
re-run; it is documented and excluded from the attempt accounting and
from both arms' evidence. Full method, per-request records (raw model
output included), and interpretation are under
[`diagnostics/marker_prompt_compare_20261103`](../diagnostics/marker_prompt_compare_20261103/README.md).
The five outstanding paragraphs will receive the reinforced prompt on
their next actual send; saved accepted paragraphs and display mappings
remain reusable, and the ordinary resume command is unchanged
(`scripts/run_grey_translation.ps1 -Resume`).
# Translation-only compaction in legacy v1 recoveries (2026-11-04)

Marker compaction receives accepted translated responses and the previous
validated summary, not original source requests, translation examples, or
repair instructions. Resume context rebuilding uses the same translation-only
boundary, with its evidence selection budget counting the translated messages.
Source verification remains local for recovery integrity. Normal translation
requests retain their source and context as before.

Summary instructions preserve existing markers exactly, allow omission, and
leave unmarked translated names as plain text. They must not reconstruct source
names or add markers to previously unmarked names. The existing provider-error
and marker validation gates remain active. Existing accepted translations and
recovery bundles remain usable; no migration or restart from scratch is needed.

