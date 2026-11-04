# Fix plan: preserve localization identity when English renderings collide

Status: implemented in this checkout; validation results are recorded in `docs/protected-names.md`.

Target repository: `C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker-protected-names`

## 1. Goal and boundaries

Fix the remaining navigation/metadata identity bug after the recovery and compaction repairs. Different original labels must remain distinguishable even when their current English renderings are identical.

Do not change the original `bilingual_book_maker` repository. Preserve unrelated edits. Do not reset, clean, commit, push, run a whole novel, or mutate user EPUBs as tests.

Read applicable `AGENTS.md` instructions. The parent asks for Claude consultation for architectural work; the reviewer found no available `claude.ask_claude` tool. Do not claim consultation unless actually performed.

Keep the completed fixes: `ongoing` recovery storage, newest-compatible partial resume, unique fresh-run IDs, catalogue registration before compaction, and atomic bundle publication. Keep existing body markers, shared CSS, independent draft mapping requests, and protected span identities.

## 2. Reproduced failure

The latest code stores structural destinations and renders from saved masters, which fixes sequential replacement cascades. However, it initially discovers the master for each destination by matching already-rendered English text.

Relevant production path in `book_maker/marker_bundle.py`:

1. `_rendered_lookup` groups localization masters by their current displayed string.
2. `_match_localization_value` finds a match using displayed text.
3. If several masters match, it sorts them by cache key and chooses the first.
4. `capture_localization_destinations` saves that chosen master at the structural destination.
5. Future edits faithfully render the wrong master.

The comment acknowledging ambiguity does not make the selection safe.

Reproduction:

```text
Source identity:        first.xhtml       second.xhtml
Original label:        甲                乙
Correct marked master: ${甲}             ${乙}
Initial display:       Alice             Alice
Captured master:       ${甲}             ${甲}      <-- bug

Offline corrections:
甲 -> Correct A
乙 -> Correct B

Expected navigation:   Correct A         Correct B
Actual navigation:     Correct A         Correct A
```

This was reproduced using the actual capture/regeneration helpers. Protected body spans remain intact; the error is in attaching masters to navigation/metadata destinations.

## 3. Required invariant

Bind each localized value to its source/destination identity while that relationship is known. Carry the binding through serialization. Never infer it from translated text.

- Equal English display strings are not equal source identities.
- Shared hrefs do not necessarily imply shared labels: two TOC entries may target the same document but use different fragments or labels.
- Equal original spelling can share an existing translation cache, but each written destination still receives an explicit binding.
- Repeated copies of the same known master may intentionally appear in navigation, NCX, document titles, and metadata.
- An unrelated link whose visible text happens to match a localized title must not be enrolled as a localization destination.

Do not fix this by choosing a different sort order, changing to first/last match, guessing from chapter order, or doing substring replacements.

## 4. Implementation approach

### 4.1 Bind masters before display conversion

Extend the protected-mode localization path in `book_maker/loader/epub_loader.py` and `book_maker/loader/epub_localization.py` to retain the marked master and provenance together when assigning a value.

Use existing source identities:

- TOC entry: source entry identity plus normalized target document/fragment and original label.
- XHTML navigation link: owning resource and actual link destination, linked to the source label being localized.
- OPF metadata: DC field name and source occurrence index, not its displayed text.
- Document title/navigation heading: owning document and the specific localization operation assigning that value.

Keep marked masters separate from display strings in intermediate maps. Audit the metadata-title reuse path: it currently can select values from `translated_titles`, whose tuples hold displayed text. Reuse the associated marked master instead of treating displayed English as the authoritative master.

Maintain existing source matching for deciding which source node should be localized. This change replaces post-render identity inference, not ordinary source-bound title selection.

### 4.2 Carry identity through ebooklib serialization

Use short-lived opaque serialization tokens to carry explicit bindings through generated NCX/nav/OPF output. These tokens are internal transport for localized destination values, not body-name placeholders and not part of the model protocol.

For every localization assignment, register a unique binding ID with:

- Source identity and localization cache key.
- Original source value.
- Exact accepted marked master.
- Single/bilingual presentation mode.

When preparing the disposable book candidate for serialization, assign an opaque token in place of that generated display value. A token should contain a fresh per-serialization nonce and binding ID, using XML-safe ASCII characters. Check for collisions before use. Do not insert tokens into provider prompts, accepted body translations, or session history.

Where ebooklib deliberately propagates one registered title into multiple resources, the copied token identifies the same master at each occurrence. Do not assume every binding must occur exactly once.

Keep source-bound localization decisions and ordinary display calculations separate from token assignment so the tokens cannot influence matching, filename translation, or subsequent localization calls. Use a disposable serialization copy or restore temporary mutations in `finally`; repeated checkpoint saves must not accumulate tokens in live loader state.

### 4.3 Capture final structural destinations

After ebooklib serialization, font handling, body marker rendering, and stylesheet insertion, parse the candidate resources and locate registered tokens in XML text/attribute slots.

For each token occurrence:

1. Resolve its exact registered binding.
2. Preserve surrounding text when the token occupies only part of a slot; record that surrounding text as part of the destination's immutable marked master.
3. Record the resource path, element-only structural address, expected expanded element name, and text/tail/attribute slot being assigned.
4. Store the original source and exact marked master/presentation data for that destination.
5. Replace the token with the rendered value using current target-or-original catalogue mappings.

Address the actual text slot, not the aggregate text of an entire subtree. Preserve inline wrappers, their text/tails, unrelated links, and attributes. Define text/tail/attribute slot types explicitly in validation and regeneration.

Reject unknown tokens and unresolved intended assignments before publication. After capture, assert that no token from the current serialization nonce remains in any published resource or embedded accepted text. Optional destinations not emitted by ebooklib may be absent, but every assignment to a resource that is actually emitted must be resolved.

Store only the final structural records and provenance in embedded state. The transient token registry must not be required for resume or offline editing.

Remove production use of `_rendered_lookup` and `_match_localization_value` for attaching masters. It is acceptable to remove obsolete helpers entirely once references/tests are updated. Existing sequential replacement helpers must remain outside the new production path.

### 4.4 Offline regeneration

Continue rendering each saved destination directly from its marked master and updated catalogue. No provider request and no source EPUB are required for newly written bundles.

Validate the structural destination and slot type, assign only that slot, regenerate shared CSS, verify the candidate bundle, and atomically replace the destination.

Changing a catalogue target must not mutate any master, original label, catalogue identity, accepted body record, or unrelated node.

## 5. State version and compatibility

Advance destination-aware localization records from version 2 to version 3 and record that version 3 was captured using explicit source bindings. Keep the existing marker workflow ID and outer checkpoint schema unless the implementation proves an outer schema change necessary; this repair only changes localization records.

Continue reading earlier bundles for normal source-backed resume. Rebuild bindings from the source and accepted marked translations during the next protected-mode serialization; never copy a version-2 destination master merely because its English text matches.

Treat version-2 destination bindings as potentially ambiguous. Existing embedded data cannot prove that a wrong master was attached correctly. Do not silently relabel these records as trusted version 3.

For offline `apply-catalogue` on localization versions 1 or 2:

- Permit a CSS-only update when changed labels provably do not affect any saved navigation/metadata master.
- If a changed label affects saved localization masters, or the relationship cannot be established safely, reject the operation before replacing the EPUB and explain that source-backed regeneration is required.
- Do not use displayed text to migrate or repair identity.

New version-3 bundles must support repeated offline edits without source access. Existing source-backed resume must preserve accepted body translations rather than requiring paid body retranslation merely to rebuild localization bindings.

## 6. Required tests

### Identity collisions

- Two distinct marked masters initially render identically. After distinct catalogue corrections, each TOC/NCX/metadata destination displays its own correct value.
- Repeat with reversed master insertion order and cache-key order; results must not change.
- Two entries target the same document with different fragments and different masters.
- Different original spellings map to the same initial English name.
- Repeated legitimate copies of one master all update correctly.
- A body hyperlink with matching English text but no localization assignment remains unchanged.

### Serialization and structure

- Generated EPUB nav, NCX, document titles, relevant meta attributes, and repeated DC metadata occurrences receive the correct bindings.
- Inline wrappers, whitespace, surrounding prose, and element tails survive capture and regeneration.
- Quotes, ampersands, angle brackets, and Unicode are escaped correctly.
- Missing destinations and unknown tokens fail before atomic replacement.
- Serialization tokens never enter API requests, accepted translations, history, final XHTML, metadata, or embedded master state.
- Repeated checkpoint serialization starts from clean live state and does not accumulate temporary tokens.

### Offline and compatibility

- Apply the same catalogue twice; displayed values are semantically unchanged on the second application.
- Swap mappings and clear mappings; results always derive from immutable originals.
- Protect the existing non-cascade test `Alice -> Bob`, `Bob -> Carol`.
- Body spans/catalogue identities remain unchanged.
- Legacy affected navigation edits fail safely; provably unrelated CSS-only edits still work.
- Source-backed resume upgrades older records without retranslating accepted body paragraphs.

### Integration acceptance

Create a tiny temporary source EPUB containing two distinct original navigation/metadata labels whose draft mappings initially collide. Produce the actual bundle through the loader, remove or hide the temporary source, then apply distinct corrections using the offline tool. Check XHTML nav, NCX, OPF metadata, document titles, embedded masters, and unchanged protected body spans.

This test must exercise actual serialization and capture. A test that manually constructs already-correct destination records cannot demonstrate that this bug is fixed.

Keep the `ongoing` interruption/resume/final-publication tests and the new-name-at-compaction-boundary tests passing.

## 7. Validation and completion report

Run focused marker tests and relevant localization, metadata, EPUB output, session, and recovery regressions. The preceding review independently observed 134 focused tests and 310 selected regressions passing; those counts are historical, not proof of this repair.

No live Gemini call is necessary to reproduce or fix this deterministic bug. Use a fake provider with intentionally colliding draft targets for integration tests, and identify it accurately in the report.

Update workflow documentation to distinguish version-3 trusted provenance from older destination records. Explain the source-backed compatibility route without claiming that ambiguous old output can be repaired from English strings alone.

The completion report must include the two-name collision reproduction before/after, actual test outcomes, compatibility limitations, and confirmation that the original repository/user books were not modified. Do not claim completion based only on stored-destination unit tests.
