# Selective paragraph retries and relaxed source-marker validation

Status: implemented in `bilingual_book_maker-protected-names`; validation record
is maintained in `docs/protected-names.md`.

## 1. Goal and authoritative decisions

Implement only in `bilingual_book_maker-protected-names`. Preserve the original sibling repository and unrelated changes. This document supersedes earlier proposals for whole-chunk recovery, exact equality of marker counts, and blanket rejection of single-character labels.

User-approved behavior:

- Translate the initial chunk normally.
- Keep every valid paragraph. Retry **all outstanding failed paragraphs together in one request**, rather than one request per paragraph or retranslating successful neighbours.
- Match longer source labels first without reusing occupied source characters.
- For each returned marker label, available source occurrences must be **greater than or equal to** returned marker occurrences. Equality is not required.
- Merely being in the catalogue does not require a label to be marked in a paragraph.
- Continue deterministic catalogue registration, independent display mappings, CSS EPUB generation, ongoing recovery, and ordinary context compaction.

This is deliberately a source-grounding check, not proof of name completeness or semantic identity. An unmarked English rendering or a name replaced by a pronoun can pass. CSS cannot subsequently edit that unmarked occurrence. Do not claim otherwise.

## 2. Per-paragraph marker algorithm

Change the validation primitives in `book_maker/marker_names.py` and their workflow callers.

1. Parse each paragraph's returned `${...}` markers with the existing strict syntax checks. Keep labels exactly as returned; do not normalize names, merge aliases, romanize, or infer identity.
2. Build that paragraph's candidate-label set from **its returned markers only**. Neither the entire catalogue nor markers from another paragraph contribute required labels.
3. Sort labels by descending Unicode string length, then lexical order for deterministic ties.
4. For each label, find all exact source matches in ascending start-position order. Accept a match only if none of its source positions have already been reserved by an earlier accepted match. Reserve its positions immediately. Advance by one character when searching to enumerate overlapping candidates, but never reserve overlapping matches.
5. The number of accepted spans is the available count. Reject the paragraph only if a returned label's marker count exceeds its available count. Zero available occurrences always rejects a returned marker.
6. Use the earliest available spans, up to the returned marker count, as deterministic nearby-context examples. Record catalogue counts from **returned markers**, not every available source occurrence. Do not present these example spans as proven alignment between individual translated and source occurrences.
7. Missing markers, including zero markers for a known name, are allowed. Surrounding-prose translation validation still runs; this is not an exemption for untranslated Chinese outside markers.

Use span bookkeeping, not iterative string replacement: removing strings can join surrounding text and manufacture matches that never existed in the source. The intended behavior is equivalent to consuming source spans longest-label-first.

Examples:

| Source | Returned markers | Result |
| --- | --- | --- |
| 美琴…美琴…美琴 | `${美琴}` twice | Pass |
| 美琴 | `${美琴}` twice | Fail |
| 样子 | none; catalogue contains 子 | Pass |
| 黑子 | `${白井黑子}` | Fail |
| 御坂美琴 | `${御坂美琴}` and `${美琴}` | Fail; source cannot be counted twice |
| 御坂美琴…美琴 | `${御坂美琴}` and `${美琴}` | Pass |

Do not introduce a single-character ban or a punctuation-label ban in this repair. Those restrictions were earlier suggestions, not the user's revised rule. `${黑——子——}` may match inside `黑——子——！`; a catalogue entry containing the exclamation mark does not force other paragraphs to use that longer spelling. Exact returned labels remain distinct catalogue keys.

Update the model instruction to require exact source spelling and discourage ordinary-word markers, while explicitly allowing natural pronouns and reduced name repetition. Do not tell the model to force one marker per source occurrence.

## 3. Structured response validation and selective recovery

Primary coordinator: `book_maker/marker_workflow.py`. Keep the public ordered list result for successful loader calls, but introduce internal per-ID results and failures. Do not parse human-readable exception text to recover indexes.

Each pending item needs: original index, stable paragraph ID, source text, attempt count, last structured failure, and optional accepted translation. Allocate IDs once from the original request and preserve them in every subset repair; rebuilding IDs from a shrinking list is prohibited.

Validation returns all paragraph outcomes rather than raising on the first bad paragraph:

- Structure: exactly one usable unit for the expected ID, nonempty translation when the source is nonempty, no nested markup or malformed marker syntax.
- Markers: relaxed source-grounding algorithm above, independently per paragraph.
- Prose: existing untranslated-text, contamination, and Markdown checks applied independently. Exempt only syntactically valid source markers from prose-language checks.
- Duplicate expected IDs make that ID invalid, even when copies are identical. Missing IDs remain pending. Unknown IDs never become content or history.

Parse strict XML first. A valid outer response permits per-unit rejection while retaining valid siblings. If the outer XML is malformed, use a bounded fragment scanner only to extract complete, non-nested `bbm-unit` fragments with exact expected IDs; parse each fragment independently with entities, DTDs, and network access disabled. Require a single attributable occurrence of an ID across the response. If framing or duplication is ambiguous, retain no fragment for that ID. Do not use permissive XML recovery or assign paragraphs by order. Enforce the existing response-size safety limit, or add a bounded size check before fragment scanning if none exists.

Unknown units and surrounding commentary are discarded with a diagnostic; they cannot poison otherwise safely attributable units. Explicit provider-refusal handling takes precedence over fragment salvage and stops the run through the existing recovery path.

Recovery loop:

1. Send the initial chunk once. Validate every attributable paragraph.
2. Commit valid items; retain their exact translations unchanged.
3. Send one request for the outstanding IDs only. Include their source, individual errors, and the original source immediately before/after each failed item as read-only context. Deduplicate overlapping context. Context has no response IDs and must not be returned as translated units.
4. Repeat until all items pass or their budget is exhausted. Do not resend an accepted paragraph because a later repair discovers another label.
5. Return the complete translations in original order once all items pass.

Default budget: five sends per paragraph, including the initial request. Reuse `--max-chunk-attempts`; do not add another overlapping retry flag. A paragraph included in a failed transport request consumes an attempt; use the existing bounded backoff. Provider refusal remains terminal. Accepted paragraphs consume no further requests. Resume starts a fresh retry budget for unfinished paragraphs.

In marker mode, this coordinator owns recovery. Bypass the loader/helper's outer whole-chunk retry and recursive split mechanisms so they cannot multiply the budget or repeat already accepted work. Preserve their behavior for other translation modes. `--retry-one-by-one` does not switch marker recovery back to the legacy translator; document that marker mode always uses grouped outstanding-paragraph repair. Update CLI help accordingly.

## 4. Acceptance, session history, and recovery persistence

Selective success must survive an error before the original chunk returns. An in-memory list alone is insufficient.

- Add an optional embedded `paragraph_recovery` state journal with a version field and entries keyed by existing stable source-unit/localization identity plus exact source digest. Store accepted marked text, not failed raw output. Do not use source text alone as identity: identical paragraphs at different positions must remain distinct.
- Pass stable loader unit identities into the coordinator. Where the caller has no body unit object, use its existing localization cache key; for a generic batch fallback, use original batch digest plus original index. Repairs always retain the original identity.
- Populate this journal as items are accepted. At normal batch completion, promote entries through the existing accepted-body/localization recording path, then remove their temporary journal entries. On an exception or interruption, the recovery EPUB must embed the journal, and source-backed regeneration must render its accepted body paragraphs as translated content.
- On resume, merge journal entries into accepted work only after source identity and digest checks. Skip their model requests and do not double-count catalogue occurrences or append duplicate session exchanges. New runs ignore stale state as they already do.
- Existing bundles lacking the optional journal load as an empty journal. Keep the established bundle/localization versions unless an existing validator requires an explicit state schema revision; this feature does not change structural localization identity records.
- Commit accepted journal entries, catalogue deltas, and canonical accepted-only history together using the existing rollback protection. Register catalogue labels **before** history append can trigger compaction. No failed response or repair instructions enter accepted history.
- Record accepted subsets in original index order within each response round. Later repairs may be recorded after their accepted neighbours; canonical IDs preserve identity. The final EPUB remains in original source order.
- Keep the full original source context available for repairs even if acceptance triggers compaction. English display mappings remain separate from translation and compaction context.
- A session-commit failure rolls back that acceptance transaction. Retain validated candidates in memory for another local commit attempt; do not call the translation API merely to retry a local transaction. Persistent local failure stops and saves previously committed work.
- Use the existing ongoing EPUB save mechanism after each successful acceptance round. Never publish a completed output while any paragraph is pending. Continue to embed related state without creating default loose sidecars.

## 5. Diagnostics and regression tests

Report useful aggregates rather than a warning for every marker mismatch:

`37/40 accepted; retrying 3 failed paragraphs together, attempt 2/5`

Diagnostics retain original ID/index, failure category, label counts when relevant, and bounded response examples. On exhaustion, list outstanding IDs and confirm progress was saved. Never log credentials. Unknown-unit and salvage events should be distinguishable from translation-quality failures.

Required deterministic tests:

1. A 40-unit reply with three failures produces a single request for exactly those three; the other 37 responses remain byte-for-byte unchanged. A second-round single failure requests just that ID.
2. Validate every example in section 2, plus a zero-marker translation for a known name, isolated-character markers grounded in source, internal punctuation, exact-label expansion rejection, and an ordinary Chinese substring that must not be made mandatory by catalogue membership.
3. Exercise longest-first priority across different start positions, repeated and overlapping labels, deterministic ties, and prevention of replacement-created matches. Catalogue counts must equal returned markers, not available source counts.
4. One bad marker cannot reject valid siblings. A label newly selected in a repair cannot invalidate already accepted paragraphs.
5. Test missing/duplicate/unknown/reordered IDs, nested markup, malformed outer XML with safe independent fragments, ambiguous/truncated fragments, XML entities/DTD rejection, and stray commentary. Never invent output for missing IDs.
6. Test per-paragraph untranslated prose and Markdown contamination without exempting unmarked Chinese. Keep provider-refusal behavior terminal even when fragments exist.
7. Assert five-send maximum per outstanding paragraph, configurable limits including one, transport failures, no outer retries/splits, and no accidental legacy single-translation path.
8. Interrupt after partial acceptance, reload the actual ongoing EPUB, and verify accepted paragraphs, catalogue counts, history, and display spans survive without additional model calls or duplicate accounting. Include identical source paragraphs at distinct positions and resume with changed request grouping.
9. Force compaction immediately after acceptance; verify accepted labels exist before summary validation and failed candidates never enter history. Test transaction rollback and local commit failure independently of provider retry.
10. Generate a complete EPUB after repairs and verify paragraph ordering, nav/NCX/metadata source identity, editable CSS mappings, absence of serialization tokens, and existing offline catalogue editing.

Run the focused marker, bundle, resume, end-to-end, translation-quality, refusal, and chunk-retry suites. Then run the previously passing session, metadata, output-validity, CLI-documentation, and run-state regression suites. Report new regressions separately from known inherited failures; do not rewrite unrelated tests to hide failures.

Use fake deterministic provider responses for correctness. Any live Gemini smoke test should use a tiny non-sensitive fixture and have a bounded request budget; it supplements, rather than replaces, assertions about exact IDs and call counts.

## 6. Completion checklist

- Only failed paragraphs are retried together; successful translations persist through failure and resume.
- Returned marker counts cannot exceed non-overlapping exact source availability; missing markers do not fail solely because of the catalogue.
- No blanket single-character/punctuation ban, automatic name repair, extra glossary voting, or new AI catalogue extraction.
- No repeated whole-chunk retries or split fallback in marker mode.
- Existing shared CSS catalogue, structural localization identity, source input copy, ongoing/output lifecycle, and filename translation remain intact.
- Update user-facing workflow documentation and CLI help to describe the relaxed completeness guarantee and selective retry behavior.

Repository guidance requests architectural consultation through `claude.ask_claude`; that tool was not available when this handoff was written. No external consultation is claimed.
