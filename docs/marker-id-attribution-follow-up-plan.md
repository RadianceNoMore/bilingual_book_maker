# Follow-up: reject ambiguous paragraph IDs in every parsing path

Status: implemented and validated in `bilingual_book_maker-protected-names`;
evidence is recorded in the ID-attribution validation record in
`docs/protected-names.md`.

## 1. Goal and preserved behavior

Work only in `bilingual_book_maker-protected-names`. This narrowly extends `marker-selective-retry-repair-plan.md`; preserve the original sibling repository and unrelated edits.

The last independent review passed 496 tests across selected suites. The original malformed-XML reproductions and provider-truncation recovery are fixed. Two additional attribution gaps remain: the strict XML path ignores nested duplicate IDs, and the unfinished-tag scanner stops after the first ID attribute.

Required invariant: an expected paragraph ID is acceptable only when it has exactly one unambiguous, eligible response unit. An occurrence inside an invalid unit or malformed opening cannot be ignored when deciding whether another occurrence is unique.

Keep selective grouped repairs, relaxed source-marker counts, stable IDs, per-paragraph budgets, accepted-only history, recovery journals, CSS mappings, localization identity, and the newly working handling of provider `finish_reason='length'`. No CLI, prompt, or bundle-format change is needed.

## 2. Strict XML: inspect nested occurrences before accepting units

Relevant code: `_parse_strict_tree` in `book_maker/marker_workflow.py`.

### Exact reproduction

```python
raw = (
    '<bbm-response>'
    '<bbm-unit id="a">outer'
    '<bbm-unit id="b">first B</bbm-unit></bbm-unit>'
    '<bbm-unit id="b">second B</bbm-unit>'
    '</bbm-response>'
)
reply = parse_paragraph_reply(raw, ['a', 'b'])
```

Current incorrect result: accepts `b: second B`, rejecting only `a` for nested markup. Required result: neither `a` nor `b` is accepted.

### Required change

- After strict parsing, make a bounded prepass over all actual `bbm-unit` elements in the parsed tree, including descendants of invalid/unknown elements. Count expected IDs across the whole response tree, rather than only direct root children.
- Track unit ancestry. A nested unit is ineligible, and all enclosing units containing it remain invalid. Any expected ID occurring more than once is ambiguous even if one occurrence is nested or otherwise ineligible.
- Only direct `bbm-unit` children of the accepted `bbm-response` root may yield content. A unit hidden under unknown markup cannot become a substitute response.
- Reject the affected IDs before building `reply.texts`. Do not choose the first, last, or best-looking duplicate. Preserve valid, unrelated direct siblings.
- XML comments, CDATA text, processing instructions, and escaped markup examples are not actual unit elements; do not count strings resembling units in those locations.
- Use the strict parser's decoded attribute values consistently. Preserve the existing per-unit nested-markup, nonempty-text, marker, and prose checks. Retain strict XML parsing as the first path; do not replace it with salvage for well-formed responses.
- Return structured per-ID ambiguity errors using `ParagraphReply`, so the existing coordinator retries only outstanding IDs together.

## 3. Unfinished opening: inspect every ID attribute

Relevant code: `_unfinished_unit_touch` and `_scan_response_units` in `book_maker/marker_workflow.py`.

### Exact reproduction

```python
raw = (
    '<bbm-unit id="b">first B</bbm-unit>'
    '<bbm-unit id="a" id="b"'
)
reply = parse_paragraph_reply(raw, ['a', 'b'])
```

Current incorrect result: keeps `b: first B`; only `a` is marked ambiguous. Required result: both `a` and `b` remain pending.

### Required change

- Replace the early-return, single-touch result with an internal aggregate containing all attributable exact IDs, all partial ID prefixes, and a flag for unbounded ambiguity.
- Scan the entire available unfinished opening, respecting quoted attribute values. Discovering one complete `id` attribute must not stop inspection of subsequent attributes.
- Every complete exact `id` attribute taints its corresponding expected ID, including repeated or conflicting attributes. Partial ID values taint every expected ID matching their decoded prefix. Union these results; do not overwrite earlier discoveries.
- A tag ending after `id="a"` without a final `>` taints `a`, while unrelated safe IDs may remain accepted. Merely reaching EOF after a fully parsed attribute does not create unbounded ambiguity.
- If another incomplete attribute could itself be a further `id` and cannot be bounded to a subset, conservatively taint all expected IDs. Apply the existing conservative policy for malformed or unattributable unit openings rather than silently assuming uniqueness.
- Parse attribute names exactly: `data-id` and `id=` text inside another quoted value are not ID attributes. Preserve entity decoding consistent with the existing scanner and strict XML parser.
- Maintain forward progress and the existing response-size bound. Do not repair the malformed start tag or infer a missing ID.
- Keep the lexical scanner's protections for comments, CDATA, processing instructions, quoted `>`, unknown wrappers, nested units, and truncated closing tags.

## 4. Tests and acceptance

Add tests to the selective-retry suite and exercise the public `parse_paragraph_reply` entry point. Testing private helpers alone is insufficient because the first bug depends on strict-path dispatch.

### Parser matrix

- Both exact reproductions above must reject their affected IDs.
- For the strict reproduction, reverse the two `b` occurrences; reject either order. Add safe direct sibling `c`; accept only `c`.
- Put an expected duplicate under an unknown element, followed by its direct sibling; reject the duplicate ID. A single nested-only expected unit remains ineligible.
- Keep a valid, direct, unique paragraph containing CDATA or escaped examples of unit markup; those examples must not produce phantom ID counts.
- For incomplete tags, cover `id="a" id="b"`, reversed attribute order, repeated identical IDs, an additional partial ID prefix, unrelated attributes between IDs, and entity-encoded ID values.
- Add a safe sibling `c` to bounded incomplete-tag cases; it must survive. A genuinely unbounded later attribute must conservatively block unsafe acceptance.
- Verify `data-id` lookalikes and ID-looking text inside quoted values do not taint unrelated IDs.
- Preserve all previous salvage tests, including fake units inside comments/CDATA, malformed duplicate attributes, and safe complete units before truncated output.

### Workflow integration

Use a deterministic fake provider and real coordinator for each bug. First return the ambiguous response plus valid `c`; then return repaired `a` and `b`.

- Assert the first response commits only `c` and persists its journal entry before the repair request.
- Assert the repair request contains exactly `a` and `b`, preserving their original IDs; `c` is never requested again.
- Assert rejected candidate text never reaches accepted history, catalogue counts, recovery journal, or EPUB content. Inspect committed state after the first response, not only the final result.
- Complete the run and verify final source ordering and no duplicate acceptance accounting.
- Interrupt before repair completes and resume from the actual ongoing EPUB; previously accepted `c` must be reused without another provider request.

Run the same selected suites used by the last review: marker selective retry/names/workflow/bundle/end-to-end/resume, then protected names, translation quality, glossary gate, session handoff/session mode, translation policy, untranslated recovery, run state, output naming, CLI documentation, repository layout, EPUB metadata/output validity, provider refusal, and chunk retry. Confirm provider-truncation adapter tests still pass.

Report exact test counts and any failures; selected-suite success is not a full-suite result. No live Gemini calls are required for these deterministic parsing defects.

## 5. Definition of done

- Strict and salvage paths enforce the same ID-uniqueness invariant.
- An unfinished opening contributes every attributable ID, not only its first attribute.
- Ambiguous paragraphs are retried, valid siblings are preserved, and resume does not repeat accepted work.
- Existing truncation recovery and source-marker rules remain intact.
- Update this handoff's status and the workflow validation record only after both reproductions and regression tests pass.
