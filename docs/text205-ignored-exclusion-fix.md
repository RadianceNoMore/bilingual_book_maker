# text205: Ignore unrelated absent extraction exclusions

> text206 amendment: unverified name suggestions are now optional. Omission is
> an implicit discard and is not a validation error. The required-unverified
> statements and the attempt-3 analysis below are historical; exact-source
> candidate completeness remains strict.

## Failure evidence

The fresh run completed 21 source windows and stopped during name extraction
for `w000022`, before sending that window to body translation. Its ordered-source
digest is
`17b5a89f2bc7b82fa67b50b47db6fb6537e90033cf1c46cad5c4d5ad9d7243c5`.
The retry checkpoint held 15 exact-source candidates and 11 unverified name
suggestions.

Attempt 1 proposed `白井黑子`, which was absent from the current window. Rejecting
that response was correct. Attempt 2 supplied all 15 exact-source candidate
decisions and all 11 unverified-suggestion exclusions, but also included
`交通工具`. That label was absent from the window and was neither a retry
candidate, an unverified suggestion, nor a context-only label. The validator
rejected the whole reply before checking that its decisions were complete. Under
the fix, this reply is accepted and no third attempt is needed. Attempt 3, if
reached, listed the exact-source names but excluded only `交通工具`, omitting the
11 unverified suggestions. text205 rejected that reply for the missing suggestions; text206 supersedes
that rule by treating omission as an implicit discard. Exact-source candidate
completeness remains strict, and an unrelated extra never waives it.

The log also shows earlier translation retries for unsupported markers such as
`御坂一色`. Marker validation caught those replies, and the run recovered; the
available evidence does not establish why the model invented them. They are a
separate latency issue, not the cause of the `w000022` extraction stop.

## Plan

1. Keep exact-source candidate decisions strict. Every prior exact-source
   candidate must be named or excluded. Prior unverified suggestions are
   optional: they may be omitted (implicit discard) or explicitly excluded,
   and omission never satisfies a candidate decision.
2. Treat a well-formed absent exclusion with no recognized basis as an inert
   extra. It must not become a candidate, suggestion, protected name, catalogue
   entry, or decision for any required retry label.
3. Preserve hard failures for malformed and duplicate exclusions, an exclusion
   that contradicts a returned name, and any current-window literal exclusion
   unless it is a supplied exact-source retry candidate.
4. Apply the same classification to validation and rejected-response evidence
   collection. A harmless extra must not prevent independent exact literals or
   unverified suggestions from being recovered from an otherwise well-formed
   response.
5. Tell the model to limit exclusions to supplied decisions, bump the prompt
   contract version, and log the count of ignored extras in verbose mode.
6. Add regressions for the captured retry shape, catalogue isolation, required
   decision completeness, malformed input, and current-source precedence.
7. Run offline focused regressions and static diff checks. Do not resume the
   user's book or send translation-provider requests as part of the fix.

## Implemented behavior

The exclusion classifier now resolves supplied exact-source candidates first,
then enforces current-source precedence, then recognizes unverified suggestions
and context-only labels or their permitted boundary shortenings. Any remaining
valid absent label is ignored. The retry collectors use that same classifier;
they still refuse a current-source literal exclusion without a supplied
candidate and still reject malformed arrays, duplicates, and contradictions.

Validation adds ignored labels to an optional diagnostics list only after all
required decisions pass. The workflow prints a concise count and explicitly
states that ignored entries did not satisfy a retry decision or enter the
catalogue. The extraction prompt contract used by text205 was version 6; text206 bumps it to version 7.

## Verification and resume

- Focused offline regressions passed: **70 passed** across source-window,
  text194, text202, and text204 extraction-recovery suites. This includes the
  text205 retry replay, catalogue isolation, required-decision checks, and
  duplicate-extra rejection.
- `git diff --check` passed. Git reported an existing inaccessible
  `.openclaw/tmp/pytest-text201-baseline/` directory while enumerating the
  pre-existing worktree; this did not affect the tests or diff check.
- The ongoing recovery EPUB was not modified by verification: SHA-256
  `6a36fddc7b0668de8f7c184ca318412c6b6ccc468637e84b56392ffe55ff6bec`, ZIP
  integrity passed, and its checkpoint still contains 21 completed windows
  plus retry state for `w000022`.
- The saved recovery EPUB remains the resume point. Do not automatically resume
  it; its accepted translations and retry evidence must remain available.
- No live translation request is needed to validate this change. Resume the
  existing run separately after reviewing the patch.
