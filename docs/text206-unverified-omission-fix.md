# text206: unverified suggestion omission and retry evidence recovery

Latest status (2026-11-07): the text206 extraction fix and the language-check
false-positive fix passed the combined focused suite (**175 passed**);
`git diff --check` is clean. The control EPUB is resumable from the same
recovery checkpoint. At the latest state read, windows `w000001` through
`w000075` were complete, and w000076 had 79 accepted units saved. The remaining
paragraph in w000076 was stopped after the validator repeatedly misclassified
Gemini's repeated English “Golden Spatula” translation of 25 source repetitions
of `金铲铲` as Romanian. The low-information detector guard is now fixed; resume
the same checkpoint with the command at the end of this note. The recovery EPUB
passed full embedded-state validation; the failed run has exited. No commit was
made.

## Task and session

- Task ID: `text206`
- Agent: `auto-coder`
- Session key: `agent:auto-coder:codex-mcp-7e20ee449e90`
- Session ID: `570f1849-4bc0-409c-8247-7ed82c84cb35`
- Repository: `C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker-window-names`
- Branch: `source-window-names`
- Exact log: `C:\Users\Tina_\AppData\Local\Temp\Ditto\DragFiles\text_206.txt`
  (this temporary path was unavailable during the 2026-11-07 follow-up review;
  the preserved test cases and this report were used instead)
- Recovery control EPUB: `ongoing\超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub`
  (resumed on 2026-11-07; all provider output and accepted chunks remain in this
  checkpoint)

## Failure and fix

The `w000022` retry evidence held 15 exact-source candidates and 11 unverified
name suggestions.

- Attempt 1 proposed exact names plus 11 nonliteral names and excluded the
  current-source literal `小手`. Final validation correctly rejects that
  literal, but the old evidence collector discarded the whole response, losing
  its otherwise valid name evidence.
- Attempt 2 named all 15 exact candidates and had no exclusions. It failed only
  because the validator required all 11 prior unverified suggestions to be
  explicitly excluded.
- Attempt 3 omitted the exact candidate `御坂`; exact-source completeness must
  remain strict.

The fix separates final validation from retry-evidence harvesting:

1. Prior unverified suggestions are optional. Omitting one is an implicit
   discard. Omission is not an error, never enters extracted names or the
   catalogue, and never satisfies an exact-source candidate decision. The
   control-run follow-up below handles repeated absent labels in otherwise
   source-grounded replies without adding them to the extracted names.
2. A rejected response is still rejected in final validation if it excludes a
   current-source literal without a prior candidate. Its structurally valid,
   non-contradictory names may nevertheless seed exact-name and unverified-name
   retry evidence. Unsupported excluded labels are not harvested and cannot
   satisfy any retry decision.
   A second evidence-loss case was found during review: an absent label repeated
   in both `names` and `excluded_candidates` made the whole retry reply appear
   contradictory, dropping unrelated source-backed names. The collector now
   discards that off-window label but preserves the independent exact-source
   candidates. Final extraction still rejects the absent label, and it never
   enters the catalogue or prepared markers.
3. Exact-source candidate completeness, current-source protection,
   context-only boundary rules, malformed rows, duplicate exact-candidate
   exclusions, and name/exclusion contradictions remain strict. Repeated
   optional absent exclusions are inert and are normalized.
4. The extraction prompt contract is now version 9 and tells the model that
   unverified suggestions may be omitted or explicitly excluded, but never
   treated as names; optional absent exclusions need not be repeated.

## Files changed for text206

- `book_maker/source_windows.py`
  - `EXTRACTION_PROMPT_VERSION = 9`.
  - Removed the missing-unverified requirement.
  - Kept `_classify_exclusion` current-source protection and candidate
    completeness.
  - `_retry_response_labels` retains independent exact-source evidence when an
    absent suggestion is repeated in both arrays, while keeping source-backed
    name/exclusion conflicts invalid.
  - Prompt now allows omission or explicit exclusion of unverified suggestions.
- `tests/test_source_windows.py`
  - Existing extraction and prompt contract coverage retained.
- `tests/test_text204_unverified_name_suggestions.py`
  - Omitted-unverified and retry-evidence expectations updated.
- `tests/test_text206_unverified_omission.py` (new)
  - Attempt 1 evidence recovery despite `小手`.
  - Attempt 2 acceptance with all exact candidates and omitted unverified
    suggestions.
  - Attempt 3 omission of `御坂` still rejected.
  - Current-source literal exclusion still rejected without a prior candidate.
  - Unverified suggestions never enter output/catalogue and cannot satisfy a
    candidate decision.
  - A previously recorded suggestion repeated in both `names` and exclusions
    is discarded without suppressing exact-source retry evidence.
  - A workflow-level retry regression checks that valid exact names enter the
    next prompt as retry candidates while the contradictory absent suggestion
    remains unprotected and out of the catalogue; its text is carried in the
    validation error for the next extraction request.
  - Malformed rows, duplicate exact-candidate exclusions, and contradictions
    still rejected; duplicate optional absent exclusions are normalized.
- `docs/protected-names.md`
  - Current contract: unverified suggestions may be omitted or excluded, never
    names.
- `docs/text204-unverified-extraction-plan.md`
  - Historical note amended for text206; the old “must explicitly exclude”
    requirement is marked superseded.
- `docs/text205-ignored-exclusion-fix.md`
  - Attempt-3 and required-unverified wording amended as historical; exact
    candidate completeness remains strict.
- `docs/text206-unverified-omission-fix.md` (this handoff).

Pre-existing text201–text205 worktree changes were preserved. No commit, stage,
or push was performed.

## Control-run follow-up: window 32

The resumed control EPUB completed and checkpointed windows `w000001` through
`w000031`, then stopped before body translation for `w000032`. The recovery ZIP
passed integrity checking and retained the exact-source retry state:

- Exact-source candidates: `一方通行`, `布束砥信`, `御坂美琴`, `爱玲`,
  `食蜂操祈`.
- Optional absent suggestions: `御坂一色`, `白井黑子`.
- The first two replies contained all five literal names plus one or both absent
  suggestions in `names`. The third reply repeated optional absent exclusions.
- The three-attempt extraction limit stopped the run. No `w000032` name entered
  the catalogue and no body translation for that window was sent.

Follow-up behavior: a plain-string suggestion already recorded as unverified
for this window is discarded from a later reply. It is reported in the verbose
log and never reaches `extracted_names`, the catalogue, or prepared markers. A
new unknown absent label still fails extraction and is recorded as a suggestion
for the next attempt; a reply containing only absent labels also fails.
Exact-source retry candidates must still be explicitly included or excluded.
Malformed rows, true name/exclusion contradictions, current-source literal
exclusions, and repeated exact-candidate exclusions remain hard failures.
Repeated optional absent exclusions are normalized because they cannot affect
exact-source candidate decisions. A known absent suggestion repeated in both
arrays is treated as discarded and cannot suppress exact-source retry evidence.
This allows a resumed window to discard the two saved w000032 suggestions
without weakening source grounding.

The recovery EPUB remains the source of truth for resume. Do not remove or
rewrite its retry state. After the focused verification passes, resume it with
the same PowerShell parameters so the live window-32 case validates the fix.

## Verification

Focused offline command:

```text
C:\Users\Tina_\Miniconda3\python.exe -m pytest \
  tests/test_source_windows.py \
  tests/test_text194_extraction_recovery.py \
  tests/test_text202_context_exclusions.py \
  tests/test_text204_unverified_name_suggestions.py \
  tests/test_text206_unverified_omission.py \
  -q --basetemp=C:\Users\Tina_\AppData\Local\Temp\text206-pytest-final4 \
  -p no:cacheprovider
```

Result: **79 passed in 4.65s**.

Window-32 follow-up command used the same focused file set with a clean temp
directory. Result: **85 passed in 4.22s**.

Static check:

```text
git diff --check
```

Result: exit 0, no output.

## Control-run follow-up: w000034 summary overage

After w000032 resumed successfully, the live control run completed w000033 and
translated all 187 units in w000034. It then stopped before starting w000035:
all five cumulative-summary drafts exceeded the configured 10,500 estimated
token cap by 3–611 tokens. The saved incoming summary through w000033 and every
accepted translation through w000034 remain authoritative and durable. Bundle
validation confirms `summary_pending_window = w000034`, 34 prepared windows,
and 5,921 accepted units; no extraction retry rows remain.

The prior retry loop appended a generic “within 10,500 tokens” reminder but kept
asking for the same full-size output on every attempt. It now keeps the
configured target and hard cap on the first attempt. After an over-budget draft,
it lowers the next target by 15%, names the previous estimate and new target in
the retry feedback, and keeps the configured validation/provider output cap
unchanged. `tests/test_summary_recovery.py` covers successful compression,
successive target reductions, unchanged hard limits, and preservation of the
pending summary and accepted translations after exhaustion.

The focused summary and source-window regression suite now reports **99 passed**
in 4.26s; `git diff --check` exits 0. Resume the same recovery EPUB to retry
only the w000034 summary stage, then continue supervising the control run.

## Control-run follow-up: w000046 context-only name

After resuming the w000034 summary stage, the run advanced through all 166 units
of w000045 and saved its cumulative original-language summary. It then stopped
before translation for w000046 after three rejected extraction replies. The
recovery EPUB validates with 45 completed windows and one saved w000046 retry
record containing 26 exact-source candidates and one optional absent suggestion.
Progress at the stop was 7,714/12,774 units (about 60%).

The final w000046 reply included `白井黑子` in `names`, although this label was
present only in the prior cumulative summary and absent from the current source
window. It also returned several retry candidates in both `names` and
`excluded_candidates`, a genuine contradiction that remains a hard failure.
The validator now discards a name only when it is already known to be a
summary-only label absent from this window (or a previously recorded optional
unverified suggestion). Such discarded labels do not enter the extraction,
catalogue, or markers, and cannot satisfy a retry candidate. Unknown absent
names still trigger a retry. Prompt version 9 now explicitly requires the two
decision arrays to be disjoint and every supplied exact-source candidate to
receive exactly one decision.

Regression coverage checks summary-only name discard, continued candidate
completeness, and strict rejection of real name/exclusion contradictions. The
focused suite passed **101 tests in 3.28s**; `git diff --check` exited 0.
AutoClaw's read-only review task for this follow-up failed with `402 status code
(no body)`; the configured failure watcher queued its continuation. No AutoClaw
review reply is available yet.

## Control-run follow-up: w000056 excess full-name marker for an alias

After w000055 completed, w000056 prepared 14 labels and 132 marked spans. All
units translated except one paragraph, after 79 of 140 w000056 units had been
accepted. Its source contained one marked `佐天泪子` occurrence and an earlier
unmarked `佐天同学` reference. Each of five paragraph-repair replies added a
second `${佐天泪子}` marker by translating the unmarked alias as the full name,
so validation correctly rejected it. The prepared window, catalogue, accepted
siblings, and recovery EPUB remain saved; resume retries only this paragraph.

The excess-marker retry correction now reaches the prepared-window provider
prompt. For the failed unit ID, it derives the allowed marker count from the
source and says that unmarked aliases, nicknames, titles, and honorifics must
stay ordinary prose instead of being replaced with the full-name marker. It
does not quote the rejected translation. The deterministic regression exercises
both repair attempts for the `佐天泪子` / `佐天同学` case and verifies the
corrected response is accepted.

The targeted retry checks pass **4 tests**. The integration case confirmed that
prepared-marker validation now exposes structured excess-count details and the
same-window retry prompt receives the source-derived alias instruction without
including the rejected reply. AutoClaw's gateway is still refusing local
connections (`WinError 1225`); the existing session was not duplicated. A
separate read-only reviewer confirmed the retry-prompt routing issue.

## Remaining risks and next action

- Translation calls repeatedly returned malformed XML, missing or duplicate
  unit IDs, and romanized replacements for protected names. The translation
  repair path recovered those chunks through smaller retries; they remain a
  quality and latency concern, not the window-32 extraction stop.
- Unsupported excluded labels remain rejected by final validation; they are
  never harvested as retry evidence. Do not weaken the current-source literal
  check to make a review response pass.
- This is an intentionally focused verification, not the broad test suite. The
  recovery contains 56 planned windows: w000001–w000055 are complete, and
  w000056 is translating with one paragraph unresolved after 79/140 units were
  accepted. The same recovery remains the source of truth; do not start fresh.
  No commit was made.

## Control-run follow-up: w000056 ignored generic alias guidance

The next resume successfully reached w000056, but the provider added the
`佐天泪子` marker for both the unmarked `佐天同学` reference and the later
marked `佐天泪子` occurrence on all five retries. The checkpoint stayed valid
with 9,330 complete units; w000056 still has 79/140 accepted units and the same
single paragraph unresolved. The saved provider responses confirm the generic
alias instruction was present in the actual request, so the next correction
uses the source span explicitly rather than repeating the same instruction.

Prepared-window retry guidance now states that a marker belongs only to its
exact marked source span. For an excess label, it includes a short unmarked
source fragment that shares literal text with that label, when one is found;
for this paragraph the excerpt identifies `佐天同学` as unmarked. It still
does not quote the rejected translation or echo invented labels when the
source supplies zero markers. The validator also records missing marker counts
separately, so a mixed missing/excess response gets guidance for both.

AutoClaw's read-only review found no blocking correctness defect in the retry
routing. It identified zero-count and mixed missing/excess coverage gaps; both
now have deterministic regression cases.

Because Gemini ignored even the source-span-specific repair instruction, the
workflow now has a guarded normalization fallback after repeated validation
failures. It applies only to excess-only responses where every surplus marker
has at least one source-authorized occurrence and an existing catalogue entry.
If that entry has no display target yet, the workflow requests a targeted
display mapping and requires the mapping to pass the normal target validator.
It then rebuilds the response from parsed marker/text parts, preserves exactly
the source-authorized markers, converts only surplus copies to validated plain
target-language text, and validates the complete paragraph again. It never
normalizes missing markers, source-absent labels, malformed marker syntax, or
labels without a validated mapped target. Regression tests cover successful
targeted mapping plus refusal when the source has a missing marker.

The first read-only AutoClaw audit of this fallback found that it ran after
attempt two despite a five-attempt translation budget, and that syntax-only
display-target checks could accept punctuation or leave Chinese text in an
English alias. Both issues are corrected: normalization waits until the final
configured translation attempt; mapping targets must contain text and may not
reuse a source-only script unsupported by the target language. Targeted mapping
still runs only for the source-authorized labels, and its provider diagnostics
are restored after the mapping call so exhaustion records retain the final
paragraph request and response. Every requested mapping batch is attempted
even if an earlier batch fails.

The marker-name, workflow, and selective-retry tests pass **139 tests** using a
fresh local pytest temp directory. An earlier invocation had reached 100% but
pytest exited while cleaning an old shared temp directory; the isolated rerun
completed cleanly. The same saved control checkpoint was replayed through w21
without new translation requests, then stopped safely before the unresolved
w56 unit. `git diff --check` exits 0.

Next action: resume the same saved control EPUB again, then supervise through
the remaining windows and final EPUB output.

## Final fallback terminal-error checkpoint safety

The final AutoClaw implementation review highlighted one remaining durability
edge: a targeted display-mapping refusal was raised from inside the paragraph
reply loop, before valid sibling paragraphs from that same reply reached
`_commit_round`. The refusal was correctly terminal, but accepted work in that
reply could have been lost from the recovery journal.

`MarkerWorkflow.translate` now defers `ProviderRefusal` and
`ContextRepairPending` from the targeted fallback until it has finished
validating the other units in the already-received reply. It commits and calls
the supplied checkpoint callback for any accepted siblings, records both the
paragraph response and terminal fallback failure, then raises before retrying
or sending another request. Once a terminal fallback error occurs, remaining
units are validated normally and cannot trigger another targeted mapping call.

The refusal regression sends two invalid replies, then a final reply containing
a valid sibling and one excess-marker unit whose mapping request refuses. It
asserts that the supplied checkpoint callback observes the sibling in the
recovery journal before the refusal is raised, the unresolved unit is not
committed, and the run makes exactly three translation sends and one mapping
request. A second regression injects `ContextRepairPending` immediately before
targeted mapping and checks the same sibling persistence, no mapping request,
and the pending context-repair state.

Focused verification after this change:

```text
C:\Users\Tina_\Miniconda3\python.exe -m pytest tests/test_marker_names.py tests/test_marker_workflow.py tests/test_marker_selective_retry.py -q --basetemp=C:\Users\Tina_\AppData\Local\Temp\pytest-text206-marker-fallback-final4-20261107 -p no:cacheprovider
```

Result: **148 passed in 10.79s**. AutoClaw's read-only review found that the
first regression version asserted only in-memory state and did not cover the
`ContextRepairPending` branch. Both coverage gaps were fixed. Earlier runs also
exposed a local exception-class shadowing issue and a fixture that lacked an
actual excess marker; both were corrected before the passing run.

AutoClaw's follow-up read-only review of the final persistence and
context-repair cases found no blocking issues.

## Control-run stop: explicit Gemini refusal in w000060

After w000056's corrected excess-marker fallback succeeded, the control run
completed w000057 through w000059. In w000060, extraction succeeded on the first
attempt with 19 exact labels and 179 locally inserted marker spans. Translation
chunk 1/4 (40 units) was accepted and checkpointed. Gemini explicitly refused
chunk 2/4 (40 units) with a policy refusal. The script stopped without retrying
or splitting that request; 40 units in that request still had retry budget, and
chunks 3/4 and 4/4 have not been sent.

The refused source is in chapters 109–110 and includes close physical contact
between school-aged characters. This may have triggered Gemini's safety
refusal. Do not retry the same passage in smaller batches or route it through
another model to get around that refusal. The only safe next steps are to leave
the affected content untranslated, edit it to remove the suggestive material,
or keep the run stopped.

The saved recovery EPUB is `ongoing\超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub`.
Its ZIP integrity check passes; marker state reports 60 prepared windows,
w000057–w000059 complete, w000060 translating, and the cumulative source
summary through w000059. The run is stopped with exit code 1 and there is no
translation process running. Current displayed progress was 9,825/12,774 units
(about 77%). Resume only after deciding how to handle the refused passage.

AutoClaw is healthy and separate from this refusal: the gateway status is
healthy, and the failure watcher is alive/listening with `last_error: null`.

The control EPUB checkpoint has not been modified by this fix or review, and
no translation process is running. Resume the same `w000056` checkpoint and
continue supervising through the remaining windows and final EPUB output.

## Control-run follow-up: w000060 translation and w000076 language false positive

After the user's instruction to translate the refused passage, w000060 chunk 2
was manually translated and inserted into the recovery EPUB with explicit
provenance (`manual assistant translation`). The remaining w000060 chunks and
subsequent windows were translated and checkpointed; this manual passage is no
longer pending. The checkpoint records one manual translation covering the
40 units in w000060 chunk 2.

The run later stopped at w000076 on source unit
`epub:178:chap_178.xhtml:2:a53984d5019c68b0`. Its source is `金铲铲` repeated 25
times. Gemini returned the matching English phrase “Golden Spatula” 25 times.
The statistical language detector incorrectly classified the low-diversity
phrase as Romanian, and five successful provider responses were rejected for
that same detector result. All accepted siblings were saved; the bundle has
75 complete windows and 79 accepted units in w000076, with that paragraph left
pending for resume.

`book_maker/translation_quality.py` now skips language identification when a
sample has too little lexical variety (unique word count at most
`max(2, total word count // 10)`). The source-script check still runs before
this guard, and diverse Romanian prose targeting English remains rejected.
Regression tests cover the repeated phrase and the real wrong-language case.
The combined focused extraction, translation-quality, and marker retry suite
passed **175 tests** in 3.69 seconds; `git diff --check` exits 0.

Resume the exact saved control run with:

```powershell
& .\run_grey_translation.ps1 -Verbose -Resume -BookPath 'C:/Users/Tina_/Downloads/CiweimaoDownloader-main/output/超炮，某不科学的恋爱喜剧.epub' -NameWindowTokens 8192 -SourceSummaryTokens 10500 -MaxTokens 4000
```

Do not start a fresh translation. After resuming, supervise the same recovery
through the remaining windows and verify the final EPUB, full bundle state, and
ZIP integrity. Do not commit unless the user asks.

## Auxiliary finalization recovery and completed control EPUB

The next resume reached finalization after all 78 story windows were complete,
but stopped while extracting names for the short TOC label `首页`. Gemini kept
suggesting `御坂一色`, which was already present in the cumulative novel
summary but did not occur in that label. The auxiliary extraction path had not
been applying the source-window context and retry-evidence rules, so the
off-window suggestion kept invalidating the request.

`prepare_auxiliary_source` now treats known catalogue labels in a plain
source-language summary as context-only, sends those labels as context rather
than evidence, carries exact source candidates and off-window suggestions
across retries, and checkpoints retry evidence. A repeated summary-only name is
discarded from the auxiliary result; it does not become a new extracted name
or marker for the title. The regression test covers the `首页` / `御坂一色`
case, a valid exact-source candidate, and a malformed reply followed by a valid
empty result.

AutoClaw's focused command passed **121 tests** across auxiliary extraction,
marker workflow, source-window extraction, and the text204/text206 regressions.
`git diff --check` also passed. The fix and regression are in
`book_maker/marker_workflow.py`, `book_maker/source_windows.py`, and
`tests/test_auxiliary_extraction.py`.

The saved run was resumed with the same input and checkpoint. It reused all
saved story translations, including the user-directed manual translation for
w000060 chunk 2, then completed title, navigation, metadata, and EPUB assembly.
The final `首页` auxiliary record contains no extracted names. Verification of
`output/Railgun, A Certain Unscientific Romantic Comedy_bilingual.epub` found:

- Run status `complete`, with **78/78** source windows complete and no pending
  summary or extraction retries.
- **20/20** auxiliary records complete.
- One manual translation provenance record for w000060 covering 40 units.
- ZIP integrity check passed (`ZipFile.testzip()` returned `None`); the EPUB
  contains 228 archive members.

The successful run removed its temporary recovery EPUB from `ongoing`. The
final EPUB is in `output/`. No changes were committed.
