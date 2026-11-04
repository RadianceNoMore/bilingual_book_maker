# Marker prompt reinforcement and focused paragraph repairs

Status: implemented and validated locally on 2026-11-03; see "Validation
record: prompt reinforcement and focused repairs (2026-11-03)" in
`docs/protected-names.md`. The combined-suite metadata-destination flake
that surfaced during validation is documented and fixed there. The live
comparison in §"Live comparison against Gemini" was run on 2026-11-03;
it found no measurable improvement (see the validation record).

## Handoff scope

Implement in `bilingual_book_maker-protected-names` only. This document is an implementation plan, not a claim that code has already changed. Preserve the original sibling repository and existing recovery EPUBs. Do not reset the dirty working tree.

The user approved reinforcing critical marker rules with selective capitalization, a concrete example, and an identical reminder before and after request data. Retain the existing selective paragraph retry workflow and deterministic marker validation.

## Observed incident and evidence

`text_178.txt` resumed the novel successfully after context repair. The recovery diagnostics record `resume-auto-rebuild` succeeding on attempt 1 with 27 accepted evidence units; pending repair is now false. Do not confuse this incident with the earlier provider-error summary contamination.

The later batch retained 35 valid paragraphs and exhausted five sends for five outstanding IDs in `chap_25.xhtml`. Raw replies contain `${his}`, `${he}`, `${him}`, `${He}`, `${hids}`, and `${他的}` where those labels do not exist in the corresponding source paragraphs. Earlier batches also repeated protected names beyond their exact source counts or expanded implicit subjects into marked names. Validation correctly rejected those candidates.

The current prompt already says to copy exact source spellings and not mark ordinary words. It lacks concrete correct/incorrect examples and an explicit plain-pronoun reminder. This plan tests whether clearer instructions improve compliance; it does not assume prompt emphasis guarantees correctness or that the catalogue caused the incident.

Recovery artifact for read-only reproduction:

`ongoing/超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub`

Read embedded `META-INF/bbm-marker-state.json` for the actual requests, responses, source strings, and IDs. Do not overwrite this artifact during testing.

## Required prompt behavior

Keep the normal translation instructions, source-language/target-language settings, natural prose requirements, XML framing, stable IDs, and data-versus-instructions boundary. Add one shared marker reminder constant/helper. Use exactly the same reminder text before and after the request data:

```text
MARKER RULES:
- ONLY personal names, place names, and named terminology may use ${...}.
- Inside ${...}, COPY AN EXACT SUBSTRING FROM THAT PARAGRAPH'S ORIGINAL SOURCE.
- NEVER put translated English pronouns or ordinary words inside markers.
- Write he, she, his, her, him, they, their as NORMAL TRANSLATED PROSE.
- DO NOT replace a source pronoun or an implicit subject with a marked character name.
- A marker's occurrence count MUST NOT EXCEED its exact source occurrence count in that paragraph.
- Natural pronouns and omitted repeated names are allowed; markers are NOT required for every source occurrence.
```

For non-English targets, adapt the two English-specific lines to prohibit translated pronouns in the target language, using plain generic wording rather than an English-only rule. The core rule remains language-independent: marker contents copy original source labels, not translations. Do not introduce a language whitelist or reject names merely because they resemble pronouns in another language.

Include this example once, before source data, clearly labeled as an instruction example rather than a paragraph to translate:

```text
MARKER EXAMPLE ONLY — DO NOT RETURN THIS EXAMPLE:
Source: 小明抬起手。
CORRECT: ${小明} raised his hand.
WRONG: ${小明} raised ${his} hand.
WRONG: ${小明} raised ${小明}'s hand.
The last example is wrong because it uses the name twice although the source contains it once.
```

Use a target-appropriate example or omit the English example for other targets while retaining the general rules. Do not accidentally tell a non-English translator to output English. Capitalize critical prohibitions only; do not uppercase the entire translation prompt.

## Request assembly

Build a single shared request assembly function so live sends, committed canonical history, and reconstructed context evidence cannot drift apart. Suggested layout:

1. Existing translation and XML output instructions.
2. Shared marker reminder and illustrative example.
3. If repairing: repair-only instruction, specific feedback, and clearly labeled read-only neighboring source context.
4. The actual `<bbm-request>...</bbm-request>` payload.
5. The identical marker reminder as the final instruction.

The suffix belongs outside the XML source payload. The complete user message is instructions plus XML data; the parser must still require the assistant to return only the existing `bbm-response` XML. No additional response wrapper or invented paragraph ID is needed.

Inspect these current paths and their callers before editing:

- `book_maker/marker_workflow.py`: `marker_prompt`, `paragraph_request`, `canonical_evidence_exchanges`, `_repair_feedback`, and canonical accepted-history construction.
- `book_maker/translator/chatgptapi_translator.py`: marker paragraph request assembly, currently near the `marker_prompt(feedback)` call.
- Existing marker workflow, selective retry, resume, repair, and end-to-end tests.

Current code concatenates `marker_prompt() + payload` at multiple sites. Replacing only the adapter call would leave synthetic history inconsistent. Consolidate these sites or explicitly cover all of them with tests. Verify reconstruction token estimation includes the new reminder and example overhead. Preserve original source text byte-for-byte inside its escaped XML representation.

Do not apply the translation reminder to display-mapping requests or summary compaction requests: those perform different tasks. Existing compaction protections against provider-error messages remain active. Historical saved exchanges need not be rewritten; all newly built requests and evidence must use consistent assembly.

## Targeted repair feedback

Keep the original detailed validation error, ID, and neighboring original context. Add actionable corrections based on the actual failure type:

- For an invalid English pronoun marker, say: `The previous reply incorrectly marked the English pronoun "his". Write "his" as plain prose. Do not change it to another marker.`
- For a source-language pronoun marked despite being absent from that source paragraph, explain that invented source labels are forbidden and the translated pronoun belongs in ordinary prose. Do not mechanically unwrap it into untranslated Chinese.
- For a name exceeding source availability, explicitly tell the model to use natural plain pronouns for extra references and keep no more marked occurrences than the source permits.
- For a source spelling typo or expansion, ask it to copy the actual original spelling exactly. Do not instruct it to remove all name markers.
- For XML/ID failures, retain the existing specific XML/ID recovery guidance; do not label those as pronoun errors.

Prefer structured validation information if available. If the current validator provides strings only, add narrowly scoped structured metadata without destabilizing its public exception behavior. Avoid broad regex parsing of arbitrary prose to guess whether a label is a pronoun. A small explicit English-pronoun classification may tailor instructions, but must not become a global validator ban on those strings.

All failed paragraphs still go together in one repair request. Valid paragraphs remain accepted and are not resent. Preserve the configured five-send default, per-paragraph attempt accounting, atomic accepted history/catalogue/journal commits, and resume behavior. Do not silently strip braces, repair IDs, alter returned prose, accept excess names, relax exact-source validation, split into new whole-chunk retries, or add AI glossary reviews.

## Automated verification

Add meaningful tests around request boundaries and failure recovery:

1. Initial and repair messages contain identical reminder blocks before and after the actual source XML. The final reminder follows the closing request tag. Example text is outside source units and never gains an output ID.
2. Actual source/XML escaping and stable IDs remain unchanged, including source containing `${...}`, XML characters, or text that resembles instructions.
3. English pronoun-marker feedback is explicit and applies to the right paragraph; valid source labels are not globally banned.
4. Over-repeated name feedback recommends plain pronouns while preserving exact-source count checks.
5. A 40-unit batch with five malformed replies accepts 35 once, resends only five, and commits corrected candidates once. Exhaustion preserves those 35 for resume.
6. Fresh live requests, canonical accepted history, and context rebuild evidence use the shared assembly; added overhead counts toward the configured context budget.
7. Non-English target instructions remain appropriate and do not require English output.
8. Existing invalid `${his}`/`${he}`/`${him}` and absent `${他的}` cases remain rejected; plain English pronouns with legitimate Chinese name markers pass.

Run the relevant marker tests and record exact results. A metadata destination test previously passed alone but failed in a combined suite; if it remains, report it separately and investigate enough to distinguish it from changes introduced here. Do not claim the entire repository is green without running it.

## Live comparison against Gemini

After local tests, run an isolated comparison using the five failing source paragraphs and exact IDs saved in the recovery diagnostics. Use the currently configured anonymous endpoint and model; do not start the full novel or modify its checkpoint. Never print credentials.

Compare the baseline and reinforced prompt under the same clean contextual evidence and generation settings. Use the existing source context or an equivalent bounded accepted context, with the same contents for both variants. Exclude provider-error seeds. Do not chain one variant's responses into the other variant's history.

Run at least three independent initial trials per variant, interleaving their order to reduce time/order bias. Record first-send accepted counts, validation reasons, latency, provider errors, and total requests. Then exercise selective repair with the existing attempt limit and report eventual completion separately. Preserve genuine errors; do not count provider failures as evidence that one marker prompt is semantically worse without explanation.

An improved small sample supports adoption but cannot prove universal reliability. If the reinforced version is no better, report the results rather than asserting success. Keep the deterministic validator regardless of live outcomes. Store any diagnostic artifacts together under a task-specific `diagnostics` directory, not loose files in input/output. Use existing credential-redaction conventions.

## Completion criteria and handoff report

- Shared before/after reminder, example, and focused repair guidance are implemented for the marker translation path.
- Current validation, accepted-progress persistence, mapping, and compaction behavior remain intact.
- Targeted automated tests pass, with unrelated failures reported accurately.
- Live comparison results include counts and limitations, or a specific endpoint failure if testing cannot complete.
- Report changed files, checks, observed results, and the unchanged resume command. Do not auto-resume the whole novel as part of this implementation task.

No migration or fresh translation is required solely for a prompt wording change. Existing saved accepted paragraphs and display mappings remain reusable; the outstanding paragraphs receive the new prompt on their next actual send.
