# text204: Recover from ungrounded name suggestions

> Historical plan: the text205 fix later changed the handling of unrelated
> absent exclusions. They are now ignored as inert extras and do not count as
> retry decisions; see `text205-ignored-exclusion-fix.md` for the current rule.
>
> text206 amendment: unverified name suggestions are now optional. A later
> reply may omit one (an implicit discard) or explicitly exclude it; omission
> is not an error, but an unverified suggestion is never a name and cannot
> satisfy an exact-source candidate. See
> `text206-unverified-omission-fix.md` for the current contract.

## Observed failure

The resumed translation completed source windows `w000001` through
`w000014`. It then stopped during name extraction for `w000015`, before sending
any translation request for that window. The window source digest in the saved
diagnostics is
`904fdf36c0c30f0f4ed53491a6e8514e521ba246009baa50b1b85c1daa57a787`.

One extraction reply proposed `食蜂派阀` in `names`, although that exact spelling
does not occur in the current source window. Local validation correctly rejected
it. On later attempts the model moved the spelling to `excluded_candidates`,
but validation rejected that too: it was neither an exact-source retry candidate
nor a label (or permitted boundary shortening) from the prior summary. The same
invalid suggestion therefore kept the extraction from completing within the
three-attempt limit.

The current candidate collector intentionally records only strings with literal
source evidence. That is correct for protected names, but it loses the separate
fact that a prior reply proposed a nonliteral name which the next reply needs to
explicitly discard. The name was not added to the catalogue, and the saved
recovery EPUB remains the resume point.

## Required behavior

1. A protected name must still occur exactly in at least one source unit in the
   current window. Suggestions without that evidence must never enter the
   extracted-name result, catalogue, or marker preparation.
2. Keep exact-source retry candidates separate: the model may include or exclude
   each one, and a name remains subject to exact-source validation.
3. Track a structurally valid but nonliteral `names` suggestion separately as
   unverified. A later reply may omit it (an implicit discard) or put it in
   `excluded_candidates`; it must never be accepted in `names` or used as an
   exact-source candidate decision.
4. Before accepting an unverified suggestion as an exclusion, reject it if the
   same exact text occurs in any current-window source unit. Preserve the
   existing behavior for exact-source retry candidates, which may be explicitly
   excluded after the model decides they are not names.
5. Do not harvest suggestions from malformed JSON, wrong-window replies,
   unknown fields, malformed or duplicate exclusion arrays, contradictory
   name/exclusion decisions, invalid name rows, or invalid legacy citations.
   Do not let an exclusion hide a current-source literal unless it was already
   an exact-source retry candidate in that request.
6. On resume, reconstruct both retry sets only from saved failures whose
   prompts match the exact current window and ordered source. Checkpoint retry
   sets separately from bounded diagnostics so response truncation or failure
   eviction cannot erase them; checksum each row and check candidate/suggestion
   roles against the actual window source before using it.
7. At the time of the text204 plan, only allow absent-from-window exclusions
   that are a prior exact-source candidate, a supplied unverified suggestion,
   or a declared context-only label (including the existing bounded
   leading/trailing shortening rule). Text205 supersedes the rejection of other
   absent extras: they are now inert and cannot seed retry candidates.

## Implementation plan

1. Add a shared strict parser for retry evidence. Keep
   `extraction_candidates` limited to source-literal labels and collect
   absent-from-window name suggestions separately.
2. Extend the extraction validator and prompt with a distinct
   `unverified_name_suggestions` set. Allow omission or explicit exclusion;
   reject the suggestion if it appears in `names` or in the current source.
3. Checkpoint the candidate and unverified-suggestion sets under the matching
   source window and source digest, independently of bounded diagnostics, and
   add a consistency checksum. On resume, verify the checksum and require every
   candidate to occur in the window and every unverified suggestion to be
   absent. Continue reconstructing older diagnostic evidence only after
   `extraction_request_matches_window` confirms the saved request belongs to
   the same ordered source window.
4. Bump the extraction prompt contract version so prepared-window metadata
   records the new instructions.
5. Add regressions for the text204 retry sequence, name/catalogue exclusion,
   omitted or reintroduced suggestions, current-source precedence, malformed
   and contradictory replies, preservation of prior exact-candidate exclusions,
   and resuming from saved diagnostics.

## Verification and resume

- Run the source-window and extraction-recovery tests, including the new
  text204 regression. These tests must use offline fake providers.
- Run `git diff --check` and verify the existing recovery EPUB is unchanged and
  its saved complete windows remain intact.
- Have AutoClaw perform a read-only review of the implementation and plan.
- Do not issue a translation-provider request as part of the fix. Resume the
  saved book with the existing `-Resume` PowerShell command after the fix is
  installed; the extraction reply for `w000015` must now be able to exclude
  `食蜂派阀` while protecting only exact current-window strings.

## Implemented and verified

- Added a separate unverified-suggestion collector, retry prompt field, and
  validator path. Exact-source candidates remain separate and can still be
  explicitly included or excluded; an unverified suggestion may be omitted or
  explicitly excluded, but can never become a name or catalogue entry.
  Current-window literals cannot be hidden as unverified exclusions.
- New retry evidence is checkpointed separately from bounded diagnostic
  transcripts and is tied to the frozen window ID and source digest. Backward
  recovery from diagnostics still requires an exact ordered-source request
  match. This preserves retry decisions when a diagnostic response is
  truncated or old failures are evicted.
- At the text204 implementation stage, retry collectors rejected unrelated
  absent exclusions before harvesting names, while preserving exact-source
  candidate exclusions and the bounded context-label shortening rule. The
  text205 fix later made these unrelated absent exclusions inert.
- Bundle validation checks retry-state window membership, source digest,
  duplicate IDs/labels, label safety, disjoint candidate/suggestion sets, and
  the retry-row consistency checksum. Runtime resume checks candidate and
  unverified-label occurrence against the actual ordered source units.
- The row checksum is unkeyed. It detects accidental or partial row changes,
  but does not authenticate a bundle against a coordinated rewrite that also
  recomputes the checksum. The workflow treats recovery EPUB state as trusted
  local state; imported untrusted bundles would need authenticated provenance.
- Added regressions for the text204 response sequence, retry-state resumption
  across a real EPUB state round-trip despite truncated/evicted diagnostics,
  malformed checkpoint integrity/roles, semantic exclusion harvesting,
  catalogue isolation, and prompt behavior. The focused source-window, text194,
  text202, text204, and marker-bundle suites pass: **69 passed**.
- `git diff --check` passes after the final code, test, and documentation edits.
- A broader `pytest -x` audit stopped at
  `TestShiftedReplyIsCaughtByItsMarkers.test_a_token_in_the_wrong_slot_sends_the_batch_to_the_ladder`
  after 28 passes and one skip. Its fake English replies are rejected as mostly
  Latin for target `zh` by translation-quality validation; this failure is
  outside the text204 files. The full suite was not run to completion.
- The saved recovery EPUB remains intact: ZIP integrity passes, SHA-256 is
  `a61ab50e43867d1dbd30a5995165b21d451af1d41320d762483daacf273ad66f`, and its
  checkpoint still has 14 complete windows and 2,385 accepted window units.
- No translation-provider request was made and the book was not resumed.
- AutoClaw's read-only review encountered provider code `810001`; the enabled
  watcher nudged the same session, and a manual `Continue` followed when that
  nudge failed. The subsequent review found that malformed
  replies could still seed exact-source retry candidates. Candidate harvesting
  now rejects duplicate exclusions, contradictory name/exclusion decisions,
  invalid names, and source-literal exclusions without a prior candidate. A
  focused regression verifies that resume does not inherit the contradictory
  candidate. The completed AutoClaw review passed the strict candidate parser
  with no high or medium findings. Its remaining low findings concerned
  unrelated absent exclusions, exact wording for the source-precedence rule,
  and diagnostic truncation/eviction. The code and docs now address those
  cases. The final AutoClaw review passed those three findings and raised one
  low integrity concern: a saved retry row's labels were not checked against
  the current window. Runtime role checks, a per-row checksum, and a real EPUB
  state round-trip regression now address accidental/corrupt-row cases. The
  review clarified that coordinated bundle tampering remains outside the
  trusted-local-checkpoint model because the checksum is not keyed. Do not
  launch a translation-provider request or resume the book. For every new AutoClaw
  high-demand/upgrade failure, follow the user-specific continuation rule in
  the global MCP Guide: count an already queued watcher nudge, then issue
  `Continue` to the same idle session after each subsequent failure.
