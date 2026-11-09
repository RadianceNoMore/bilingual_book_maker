# text202: Context-only name exclusion recovery

> Historical plan: the text205 fix later changed the handling of unrelated
> absent exclusions. They are now ignored as inert extras and do not count as
> retry decisions; see `text205-ignored-exclusion-fix.md` for the current rule.

## Observed failure

The resumed book run stopped before translating source window `w000006` after
three name-extraction replies. The first reply included `御坂` as a name even
though that exact string did not occur in the current source window. The local
validator correctly refused to protect it. On retries, Gemini moved `御坂` to
`excluded_candidates`, but validation rejected that too: the prior summary
catalogue supplied the longer context-only labels `御坂美琴`, `御坂一色`, and
`御坂旅挂`; it did not list `御坂` as an exact label. The same correction was
rejected on all three attempts, so body translation for window 6 never began.

The recovery EPUB remains usable. Its diagnostics contain the full source
window, all three requests and replies, and the saved progress through 775
accepted units. Do not rebuild or discard that recovery state.

## Required behavior

1. A name enters the protected-name set only when its exact spelling occurs in
   a current-window source unit.
2. A prior-summary label absent from the window can be excluded as context.
   A shorter exact leading or trailing part of at least two characters can
   also be excluded, since a model may shorten a supplied name while
   correcting an invalid extraction.
3. This boundary-shortening rule applies only to `excluded_candidates`. It must
   never approve a protected name.
4. An exclusion must be rejected if its text occurs anywhere in the current
   window. Current source text is authoritative even when it overlaps a
   context-only label.
5. At the time of the text202 fix, unrelated, undeclared exclusion strings
   remained invalid. Text205 supersedes that rule: valid absent extras are now
   ignored and cannot count as retry decisions.

## Implementation

- Extend extraction validation to recognize a non-empty exact substring of a
  declared context-only label as an allowed exclusion, after checking that it
  does not occur in current-window source.
- Clarify the extraction prompt so the provider is told that a shortened
  context-only label may be excluded, but cannot be returned as a name without
  exact current-window evidence. Record this prompt contract as extraction
  prompt version 4.
- Add regression coverage for the exact text202 retry sequence, continued
  rejection from `names`, rejection when the substring occurs in the current
  source, rejection of interior/single-character fragments, and acceptance of
  leading or trailing short forms as exclusions only.

## Verification and resume

- Run the focused source-window and text202 regression tests, then the existing
  extraction-recovery tests.
- Verify the EPUB recovery hash and durable-unit count remain unchanged while
  tests run; tests must not call the translation provider.
- Resume with the existing PowerShell command and `-Resume`. The corrected
  validator should accept an extraction reply that places `御坂` only in
  `excluded_candidates`, preserve all accepted translations, and proceed to
  translate the remaining window-6 units.
- Inspect the next saved recovery checkpoint if the provider produces a new
  failure; do not treat the old text202 extraction rejection as an unresolved
  failure after the new code is installed.

## Implemented and verified

- Extraction validation now permits an absent-from-window leading or trailing
  part of at least two characters only in `excluded_candidates`. Names still
  require exact current-window evidence, and a current-window occurrence
  cannot be hidden by a context exclusion.
- The prompt contract is version 4. AutoClaw's independent review found that
  arbitrary substrings would admit interior fragments and one-character
  exclusions, so the implementation is limited to boundaries and two or more
  characters.
- Focused verification passed: 45 tests across `test_source_windows.py`,
  `test_text194_extraction_recovery.py`, and
  `test_text202_context_exclusions.py`; `git diff --check` passed.
- The recovery EPUB stayed byte-for-byte unchanged at SHA-256
  `15940051c1a3b8fa73f396cb211a6489e2557294ae4a9bdf70bcdf565670a958`.
  ZIP integrity passed and the checkpoint still contains 775 accepted units.
- No translation-provider request was made for this fix, and the book was not
  resumed automatically. Resume it with the existing `-Resume` command.
