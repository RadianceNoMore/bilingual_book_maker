# Repair selective recovery: safe XML attribution and truncated responses

Status: implemented and validated in `bilingual_book_maker-protected-names`;
evidence is recorded in the salvage-and-truncation validation record in
`docs/protected-names.md`.

## 1. Goal and boundaries

Apply these repairs only in `bilingual_book_maker-protected-names`. Preserve the original sibling repository, unrelated changes, and the implemented behavior in `marker-selective-retry-plan.md`.

Keep valid paragraphs and retry all outstanding failures together. Retain the relaxed source-marker rule (returned counts may be less than available exact source counts), stable IDs, five-send default/configurable budget, accepted-only history, embedded recovery journal, CSS mappings, and structural localization identities.

The review independently ran 479 tests successfully across selected suites, but found gaps not covered by those tests. The following two repairs are required before declaring this feature complete. No new CLI flags or bundle schema changes are needed.

## 2. Bug A: malformed XML salvage can accept the wrong unit

Relevant implementation: fragment scanning and `_parse_salvaged` in `book_maker/marker_workflow.py`.

Current scanning counts complete outer unit fragments but misses IDs inside rejected nested units and inside unfinished start tags. It also scans markup-looking text inside CDATA as if it were actual XML elements.

These exact calls reproduce the problem using expected IDs `['a', 'b']`:

```python
samples = {
    'nested_duplicate': (
        '<broken><bbm-unit id="a">outer'
        '<bbm-unit id="b">first B</bbm-unit></bbm-unit>'
        '<bbm-unit id="b">second B</bbm-unit>'
    ),
    'cdata_example': (
        '<broken><![CDATA['
        '<bbm-unit id="b">example, not a response unit</bbm-unit>]]>'
    ),
    'truncated_duplicate': (
        '<broken><bbm-unit id="b">first B</bbm-unit>'
        '<bbm-unit id="b"'
    ),
}
```

Current incorrect outcomes: respectively accept `second B`, accept the example text, and accept `first B`. All three must leave `b` pending. Do not weaken ordinary prose validation to make these tests pass.

### Required parsing design

- Retain strict XML parsing as the first path. Keep strict per-unit validation and safe sibling acceptance for well-formed responses.
- Replace the regex-only fallback with a bounded lexical scanner. Distinguish ordinary text, tags, quoted attribute values, comments, CDATA, and processing instructions. A `>` inside a quoted attribute is not a tag end. Markup-looking characters inside comments, CDATA, attributes, or processing instructions never open response units.
- Maintain a ledger of **all real unit openings** for expected IDs, including nested, invalid, and incomplete units. A complete usable unit is accepted only if its ID has exactly one unambiguous opening and no tainted occurrence elsewhere.
- A unit inside another unit is never independently salvageable. Mark both enclosing and nested unit IDs as tainted, and continue scanning so a later standalone copy cannot escape duplicate detection.
- Unknown ordinary wrappers such as `<broken>` must not cause arbitrary descendants to become valid response units. Salvage units only at document-fragment level or as direct children of an actual `bbm-response` frame. The examples above still reject `b`; use additional wrapper-free fixtures to prove duplicate detection independently of this rule.
- Parse attribute names exactly. `data-id`, text containing `id=`, duplicate attributes, or entity spelling must not be mistaken for a unique valid `id`. Decode valid XML attribute references consistently with the strict parser. Any candidate must still pass independent strict XML parsing before acceptance.
- On an unfinished opening tag, record a fully attributable expected ID as tainted. For a partial ID value, taint expected IDs matching that prefix. If the unfinished unit opening cannot be assigned to any bounded subset of expected IDs, conservatively mark all pending IDs ambiguous. Never accept an earlier occurrence merely because the last opening lacks `>`.
- An incomplete comment, CDATA section, or processing instruction consumes the rest of the response as non-unit material. Retain safely attributable units before it, unless another ambiguity independently taints them. Complete CDATA inside a genuine unit remains text and is interpreted only by that unit's strict XML parser.
- Reject DTD/entity declarations through the existing global declaration check. Keep entity expansion and network access disabled. Preserve the two-million-character response bound and bounded diagnostics. The scanner must make forward progress; avoid recursive parsing or repeatedly rescanning the remaining response.
- Do not auto-close tags, infer IDs by order, choose the first/last duplicate, or use permissive XML recovery.

Return valid `texts` plus explicit per-ID `errors` for rejected/missing units using the existing `ParagraphReply` interface. Distinguish duplicate/ambiguous framing from untranslated prose in diagnostics. Only safely accepted units may reach catalogue registration, journal state, history, or EPUB content.

## 3. Bug B: provider truncation bypasses selective recovery

Relevant implementation: `translate_marker_units` in `book_maker/translator/chatgptapi_translator.py`.

It currently raises `MarkerValidationError` when the completion has `finish_reason='length'`, before returning the raw response to the coordinator. A response containing a complete unit A followed by truncated B is therefore handled like a failed transport call; A is unnecessarily requested again.

Required behavior:

1. Keep `_protected_completion` refusal checks and error handling intact. Provider refusal, content filtering, authentication failure, and true transport exceptions retain their existing policies.
2. For **marker paragraph responses only**, return available raw text even when the finish reason is `length`. Keep that finish reason in existing provider response metadata and add a bounded truncation diagnostic. Do not manufacture a closing root or unit tag.
3. Let ordinary strict parsing, safe fragment salvage, per-paragraph marker checks, and prose validation decide what can be accepted. Complete units may pass; incomplete, ambiguous, missing, or otherwise invalid units remain pending.
4. The request still consumes one attempt for every requested paragraph. Commit accepted units and persist recovery before sending one grouped repair for the remainder.
5. An empty truncated response accepts nothing and consumes the normal budget. A length-finished but structurally complete response follows existing paragraph validation; do not reject every unit solely because the provider reported length.
6. Keep truncation rejection unchanged for display-mapping JSON, compaction, the older protected-name workflow, and unrelated translation methods. This repair is specific to independently recoverable marker paragraph units.

The coordinator should continue receiving a string; transport metadata does not need a new public return type. A truncation diagnostic is informational and must not itself trigger another request for already accepted IDs.

## 4. Regression tests and acceptance evidence

### Parser tests

- Add the three exact reproductions above. `b` must never be accepted.
- Repeat nested-duplicate and unfinished-duplicate cases without the unknown wrapper, so the ledger behavior is directly tested.
- Mix each ambiguous example with one safe standalone sibling. Keep the sibling and repair the ambiguous IDs only.
- Cover nested units under unknown parents, duplicate IDs in reversed order, complete and truncated duplicate openings, missing ID attributes, partial ID prefixes, and openings too incomplete to assign safely.
- Put fake unit examples inside CDATA, complete/incomplete comments, quoted attributes, and processing instructions. None may become returned paragraphs.
- Cover valid CDATA text inside a real unit, escaped attribute references, `data-id` lookalikes, duplicate attributes, and `>` in quoted attributes.
- Retain tests for safe complete units before a truncated final unit, unknown IDs, reordered units, response size limits, forbidden declarations, nonempty prose checks, and no identity assignment by position.

### Provider-to-workflow tests

Use the real `ChatGPTAPI.translate_marker_units` adapter with a fake `_protected_completion`; a coordinator-only fake that returns raw strings would miss this regression.

- First completion: `finish_reason='length'`, complete A plus unfinished B. Next completion: repaired B. Assert exactly two sends, with A requested once and B twice.
- Verify A is journaled and persisted before the second request, appears once in history/catalogue counts, and is never replaced by a repair response.
- Test empty truncation and repeated truncation reaching the configured limit without outer whole-chunk retries or recursive splitting.
- Test a length-finished response containing all valid units: accept them under the existing validators, without resending the full request.
- Test refusal even when response text contains seemingly valid fragments. Refusal remains terminal and no fragments from that response are accepted.
- Interrupt between partial acceptance and repair; reopen the actual ongoing EPUB and resume. A must remain translated and only B is sent again.
- Assert ambiguous/example fragments never enter the journal, accepted history, catalogue, or final EPUB.

Run the marker names, selective retry, workflow, bundle, end-to-end, and resume suites. Then run the selected regression suites used in the review: protected names, translation quality, glossary gate, session handoff/session mode, translation policy, untranslated recovery, run state, output naming, CLI documentation, repository layout, EPUB metadata/output validity, provider refusal, and chunk retry.

Report exact results and newly added tests. Do not claim a full-suite pass from selected suites, and do not edit unrelated fixtures to hide inherited failures. Deterministic offline completions are sufficient for these bugs; no live Gemini request is required.

## 5. Completion criteria

- Ambiguous, nested, unfinished duplicate IDs and markup-looking examples cannot be silently accepted.
- Safe completed paragraphs survive a provider-truncated response and are not requested again.
- Repair requests contain only outstanding IDs, and persisted accepted work survives resume.
- Existing marker grounding, retry budgets, refusal handling, CSS editing, and structural localization identity remain intact.
- Update the workflow documentation and this document's status only after the reproductions and regression checks pass. Implementation reporting must include any remaining limitations.
