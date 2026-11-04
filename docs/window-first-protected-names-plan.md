# Window-first protected names and cumulative source-language context

Status: implementation plan only, 2026-11-04. No translation behavior has been
changed by this document.

## Fork and scope

Work in `C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker-window-names`,
branch `source-window-names`. This is a separate local Git clone of
`bilingual_book_maker-protected-names`, including its current tracked changes,
untracked source files, and tracked deletions. The copied working state is
recorded in baseline commit `c59ae8e`. The source repository was at commit
`5988a17`; its local working changes are part of this baseline.

Implement the workflow described here in this fork. Keep source books and the
existing repository's recoveries intact. This plan does not run a translation
or publish a GitHub fork; the source repository's `origin` is a local directory.

## Agreed behavior

Process the novel sequentially in source windows with a default budget of
8,192 tokens. For each window:

1. Make one name-extraction request containing the entire original window and
   the cumulative original-language summary from the preceding window.
2. Validate the extracted exact source names. Insert `${original name}` brackets
   locally throughout the window before making any translation request.
3. Translate the prepared window in smaller chunks. Every chunk receives the
   previous window's summary as read-only context. Gemini preserves the supplied
   brackets and translates the surrounding prose.
4. After every chunk in the window has been accepted, make a separate request
   to update the cumulative summary in the original language.
5. Persist that summary and pass it to both extraction and translation for the
   next window. Repeat through the final, possibly shorter window.

The user explicitly selected **extract names first; update the summary after
translating the window**. The extraction response therefore contains names
only. It does not summarize upcoming events for the translation requests.

In symbols, with `C_0` empty:

```text
N_i = extract_names(original_window_i, C_(i-1))
P_i = bracket_exact_matches(original_window_i, N_i)       # local operation
T_i = translate_chunks(P_i, C_(i-1))                    # sequential
C_i = compact_source_context(C_(i-1), P_i)              # after T_i accepted
```

`C_i` represents the story from the beginning through window `i`, rather than
only the latest window. It is a bounded, lossy summary; the complete source and
exact name catalogue remain authoritative for details the summary omits.

## Existing implementation to change

The active workflow is `source-markers-css-v1` in `book_maker/marker_workflow.py`.
`assemble_marker_request()` currently asks the translating model to decide which
names to mark. `book_maker/marker_names.py` validates returned labels against
source text, but allows missing markers, including zero markers for known names.
That cannot enforce complete preservation of a prepared set of names.

The launcher selects the OpenAI-compatible route with `gemini-anon`, using
`ChatGPTAPI.translate_marker_units()` in
`book_maker/translator/chatgptapi_translator.py`. The native
`gemini_translator.py` is a separate route and is not the initial integration
target. Preserve the configured endpoint and model rather than changing routes.

`SessionHistory` and `_record_session_exchange()` currently allow token-triggered
compaction. The launcher's `GlossaryWindowTokens`/`--glossary-window-tokens`
currently acts as a deprecated compaction-budget alias, not an extraction-window
size. These behaviors must be separated explicitly.

`book_maker/protected_names.py` contains an older prepared-window/voting workflow.
Its source-span and preparation ideas may be useful, but do not reactivate its
voting, approved-rendering requirements, or sidecar ledger. Retain the current
marker masters, catalogue, CSS display mapping, strict paragraph IDs, selective
retry, and embedded EPUB recovery architecture.

## Window and chunk boundaries

- Build an ordered stream of selected, cleaned narrative source units before
  body translation batching. Use the same filtering and cleanup as the loader;
  extraction must inspect the text that will actually be translated.
- Greedily fill each window to at most 8,192 source tokens. Prefer paragraph
  boundaries; allow shorter windows at chapter boundaries when chapter grouping
  is disabled. Preserve existing cross-chapter grouping settings otherwise.
- Partition original text before inserting brackets. Fix unit IDs, source
  digests, and window membership for the run. No overlapping narrative windows
  and no paragraph translated twice at a boundary.
- Use one explicit, deterministic token-count policy. Reuse the repository's
  language-aware estimate initially and record that counts are estimates, not
  exact Gemini token counts. Do not use the marker module's `len(text) // 4`
  estimate as a competing policy. An exact token mode would need a compatible
  tokenizer and a fingerprinted tokenizer version.
- An individually oversized source unit needs deterministic, lossless sentence
  fragments with stable child IDs and parent reassembly. If it cannot be split
  safely without breaking inline structure, stop with its identity and a useful
  error; do not silently exceed the window limit or cut arbitrary characters.
- Split the prepared window into translation requests using existing unit/token
  caps (currently 40 units and 4,000 source tokens by default). Recount marked
  payloads for request sizing. Never mix windows in one translation request or
  split a `${...}` expression.
- The source-window budget is separate from total model input. Account for the
  summary, XML framing, instructions, bracket expansion, and output allowance
  when checking provider limits. A smaller chunk can resolve translation input
  pressure; extraction still needs the complete window to fit. Reject an
  incompatible window configuration before sending it.
- Whole-chapter mode may influence packing, but cannot override the extraction
  window cap. Plan all fragments before freezing extraction input.

## One extraction request per window

Add an independent `extract_window_names()` provider hook. Its input contains:
the window ID, ordered original units and IDs, the validated prior summary,
source language, and a short policy covering personal names, place names, and
named terminology. Mark source and summary as data. Request exact source
spellings only, without romanization, translation, invented aliases, or display
mappings. Full names, surnames, and nicknames are separate exact labels when
they actually occur.

Example response contract:

```json
{
  "window_id": "w000001",
  "names": [
    {"source": "御坂美琴", "unit_ids": ["chap1:p3"]},
    {"source": "美琴", "unit_ids": ["chap1:p7"]}
  ]
}
```

Validate the response schema, expected window ID, label syntax, and grounding
in at least one cited current-window unit. Reject unknown unit IDs, empty labels,
reserved bracket syntax, source-absent names, and extra response fields. Exact
duplicates may be deduplicated deterministically. An explicit empty `names`
array is valid for a window containing no names; an empty reply is not.

Prior context can help recognize a recurring name or relationship, but names
appearing only in the previous summary cannot become current-window matches.
Previously registered exact labels reuse their catalogue keys. Do not send the
entire novel's growing catalogue in every request or infer alias identity from
similar spelling.

One validated extraction response prepares the whole window. There is no
per-chunk extraction, voting, or secondary model review. Transport, truncation,
or malformed-output failures may retry the same request within a separate
bounded extraction budget. Exhaustion saves progress and stops before body
translation. Once extraction is durably prepared, chunk failures and resumes
reuse it without another extraction request.

## Local bracket insertion and translation validation

Implement the requested `sed` step as a pure Python span operation on the
window's in-memory source units. This is literal substitution; names must never
be interpreted as regular expressions or shell commands.

Reuse/adapt `source_spans()` and `source_occurrences()` in `marker_names.py` for
deterministic longest-first, non-overlapping matches. Build each result from the
original offsets in one pass. Never repeatedly replace inside text that already
contains brackets. Label matching stays within a source unit and cannot span a
paragraph separator.

```text
Extracted labels: 御坂美琴, 美琴
Original:         御坂美琴看着美琴。
Prepared:         ${御坂美琴}看着${美琴}。
```

Record the selected spans and the expected marker multiset per unit. Reject
original source containing reserved `${` syntax before extraction, using the
existing preflight rule. Keep original strings and digests separately; bracket
insertion does not modify the source EPUB or the bilingual original text.

The translation prompt must say: translate surrounding prose, copy every
supplied marker exactly once in its own unit, and never add, remove, translate,
expand, or rename a marker. Remove the old instruction asking Gemini to select
names or allowing it to omit marked names in favor of pronouns. Pronouns remain
available for unmarked source material and grammar around preserved markers.

Require exact equality between returned and prepared marker multisets per
unit. Reordering within the translated sentence is allowed for grammar; moving
markers between units is not. New labels, translated labels, missing markers,
and duplicated occurrences fail that unit. Preserve the existing strict XML-ID
parser, safe fragment salvage, and ordinary prose quality checks.

Persist valid siblings immediately and retry only outstanding units together,
with the same prepared source and pre-chunk context. Never re-extract names to
make a failed translation pass. Register extraction provenance separately from
accepted-translation counts so preparation and retries cannot double-count the
catalogue. Display mapping remains independent and does not change masters.

Literal matching can still protect an ordinary-word homonym or miss a spelling
the extractor did not return. Save extracted labels and actual source spans for
review. Mechanical preservation guarantees apply to prepared matches; they are
not proof that model extraction found every real name or resolved every identity.

## Context supplied to chunks

Freeze `C_(i-1)` for the duration of window `i`. Each chunk request contains the
standing translation instructions, that validated prior summary, the prepared
units to translate, and any already accepted source prefix needed for local
continuity. Treat the prefix as read-only original-language context; it is not
part of the requested output. Bound it within the current window and provider
input allowance. Retry requests use the context captured before the chunk.

Build this source-language context explicitly. Do not replay earlier target
translations or let ordinary chat history add an implicit second story context.
For the first window, use an empty prior summary. The extractor sees its whole
window, but translation receives no summary of that window's future events.

In this workflow, disable automatic token-triggered marker compaction during
per-unit acceptance. Completing a window is the only normal compaction trigger.
Existing session behavior remains available for ordinary translation workflows.

## Cumulative original-language summary

After all prepared units in window `i` are accepted, call a dedicated compactor
with `C_(i-1)` and the complete prepared original-language window `P_i`. Derive
plot context from original source, not the translated prose. The request must
merge prior context with the current events instead of replacing the prior
summary with a synopsis of just this window.

Retain the main characters and relationships, consequential events, unresolved
threads, current situation, and useful narrative voice/referent information.
Use the configured original language for all summary prose, with exact source
labels inside `${...}` when a protected name is mentioned. Summaries may omit
names or reduce repetitions; translation's strict occurrence-equality rule does
not apply to summaries.

Keep a separate configurable summary budget (proposed default 1,500 estimated
tokens). Preserve essential earlier facts during compression. Validate output
structure, nonempty substantive content, marker syntax and membership in the
prepared/earlier registered catalogue, and source-language compliance with a
language-aware check. Keep protocol field names separate from summary prose.
Reject translated name labels, known labels outside brackets, truncated replies,
provider boilerplate, and refusals using the existing applicable validators.
Do not cut an accepted summary blindly through a marker or structured field;
request a shorter valid summary when needed.

Atomically store `C_i` with its window coverage, input digest, language, budget,
and validation version before beginning window `i+1`. If summary generation
fails, keep all accepted translations and `C_(i-1)`, mark summary pending, and
stop. Resume retries only this summary stage. Never continue with an empty or
unvalidated seed. Summarize the final partial window too, so completed state
records context through the end of the novel.

Navigation, filename, and metadata requests remain isolated from narrative
progress. If they need unseen protected names, prepare those source strings in
separate bounded extraction groups with the same marker contract. They do not
advance the novel's window index or enter its cumulative summary.

## Durable state and compatibility

Use a new workflow identity, proposed `source-window-markers-css-v2`, and an
explicit state schema version. Preserve the existing embedded marker-state
path, catalogue representation, CSS rendering, and atomic recovery publication,
but update readers to recognize the new identity and enforce its state rules.

Each window needs its ordered unit IDs and original digests, count policy and
budget, extraction request fingerprint, validated labels, prepared spans and
marker expectations, accepted-unit progress, incoming summary reference, and
outgoing summary with validation metadata. Fingerprints also include source
identity, selection/cleanup policy, fragmenting policy, languages, model, and
prompt versions; never persist credentials.

```text
planned -> prepared -> translating -> summary_pending -> complete
```

`planned` has fixed source membership but no committed extraction. `prepared`
means extraction and substitutions are durably checkpointed, before the first
translation call. `summary_pending` means every unit is accepted but the next
window is blocked. A verified summary checkpoint completes the window.

Resume verifies source/window identity, reuses preparation, skips accepted
units, and restores the exact incoming summary for an unfinished window.
Checkpoint the accepted-unit journal and catalogue changes together as today.
An interrupted extraction or summary call before local persistence may need to
be repeated; the provider does not supply an exactly-once guarantee.

Do not treat older model-selected-marker recoveries as compatible with the
new preservation contract. Keep old bundles readable and editable by their
existing tools, while fresh runs in this fork use the new identity. Resume
discovery must report incompatible state clearly and preserve it. Automatic
migration of old partially translated books is outside this plan.

## Implementation sequence and file map

1. **Window model and deterministic preparation.** Add
   `book_maker/source_windows.py` for ordered source windows, token policy,
   fragment IDs, extraction validation, and prepared span records. Reuse marker
   matching primitives without changing older workflows' validation contract.
2. **Provider stages.** Add extraction and source-summary request hooks beside
   `translate_marker_units()` in `translator/chatgptapi_translator.py`. Reuse
   transport, refusal classification, usage accounting, and bounded diagnostics.
   Change `assemble_marker_request()` for the new premarked contract only.
3. **Orchestration.** Update `loader/epub_loader.py` to collect windows before
   existing request batching. Extend `marker_workflow.py` to own preparation,
   strict expected-marker validation, per-window progress, and summary stages.
   Pass original and prepared source separately at every acceptance boundary.
4. **Context boundaries.** Adjust `translator/base_translator.py` and
   `session_context.py` integration so acceptance cannot auto-compact this mode.
   Assemble frozen prior summaries and accepted source context explicitly;
   rebuild context from original source plus saved window boundaries on resume.
5. **Recovery and rendering.** Update `run_state.py`, `marker_bundle.py`, loader
   fingerprints, and offline marker tools for the new version. Preserve CSS
   display mappings, accepted masters, localized destinations, and journal
   transactions.
6. **Configuration and documentation.** Add `--name-window-tokens` (default
   8192) and `--source-summary-tokens` (proposed default 1500). Keep the legacy
   glossary-window alias semantics in old modes; reject an explicitly supplied
   ambiguous alias in the new mode with the replacement flag. Update canonical
   `scripts/run_grey_translation.ps1` and its root wrapper together to expose
   `NameWindowTokens` and the summary budget. Explain that token-triggered
   `--context-compact-at` and `--no-context-compact` do not apply to this mode;
   explicit conflicting values should fail preflight. Update
   `docs/protected-names.md`, `docs/translation-workflow.md`, and CLI docs.
7. **Validation.** Complete the checks below before making the fork's launcher
   select the new workflow by default. This planning task does not change that
   launcher or invoke live provider requests.

## Acceptance checks for implementation

- A two-window fixture records this exact successful call order:
  `extract W1 -> translate W1 chunks -> summarize W1 -> extract W2 -> translate
  W2 chunks -> summarize W2`. There is one successful extraction per window,
  none per chunk, and no mid-window summary call.
- With a deliberately small budget, source order and coverage are exact across
  short windows, chapter boundaries, an oversized paragraph, and the last partial
  window. Every selected source fragment appears once and reassembles losslessly.
- Exact-name replacement handles overlapping full/short names, repeats, literal
  regex characters, multilingual labels, XML escaping, and reserved source
  syntax. Generated markers never become replacement input.
- A body response loses a marker, adds one, changes its label, duplicates one,
  or moves one into another unit: only failing units remain pending. Valid
  siblings stay byte-for-byte stable through retries and resume.
- Request capture confirms extraction W2 and every W2 translation chunk receive
  the validated W1 summary in the original language. No extraction response,
  rejected reply, repair text, target-language history, or future-window text is
  accidentally used as chunk context.
- A later summary keeps a consequential fact introduced only in W1 while adding
  W2 events. The summary request includes the prior summary and current original
  source. Marker rules survive compaction and the configured size bound.
- Simulate interruption before/after preparation persistence, mid-chunk, after
  all translations but before summary, and after summary persistence. Resume
  repeats only unfinished stages and never retranslates accepted units,
  re-extracts a committed window, or increments catalogue counts twice.
- Empty extraction, malformed extraction, source-absent names, truncated
  responses, provider boilerplate, wrong-language summaries, and summary
  exhaustion have explicit outcomes. A pending summary blocks the next window.
- Old recoveries are preserved and rejected for new-mode resume. New recovery
  survives extraction and summary failures. CSS catalogue edits and offline
  localization still operate on immutable masters without network calls.

Extend the relevant existing marker, resume, bundle, context, batching, and CLI
tests; add focused window/extraction tests. Run the focused suite, then
`python -m pytest`. Keep corpus and live-provider tests explicitly selected as
the repository already requires. A later live smoke run should inspect two real
windows' stage order, original-language summaries, and marker preservation;
record that evidence before declaring the implementation complete.
