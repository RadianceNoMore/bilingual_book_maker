# text_194 — w000003 Extraction Retry Contract

**Repository:** `C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker-window-names`  
**Branch:** `source-window-names`  
**Prepared:** 2026-11-05 (Asia/Bangkok)  
**Status:** fixed and verified; changes are uncommitted.

> Historical incident notes: this records the text194 behavior and its tests.
> Text205 later changed one narrow rule: unrelated but valid exclusions absent
> from the current source are ignored and do not count as decisions. The current
> contract is documented in `protected-names.md` and
> `text205-ignored-exclusion-fix.md`.

## 1. What failed

A fresh run completed `w000001` and `w000002` and saved **296 accepted translation units**. Extraction for `w000003` then failed all three attempts:

| Attempt | Provider reply | Validation result |
|---|---|---|
| 1 | Names included `哈基黑`, `一色`, `御坂美琴`, `白井黑子`, `御坂`, `食蜂操祈`, `操祈`, `御坂同学`, `一色同学`, `一色弟弟`; exclusions empty | `Name extraction label '哈基黑' does not occur literally in the current window` |
| 2 | Same grounded names, `excluded_candidates: ["哈基黑"]` | `excluded_candidates must uniquely list only supplied retry candidates` |
| 3 | Names omitted `操祈`; exclusions empty | `Retry omitted prior exact-source candidates without deciding them: 操祈` |

The run stopped before any body translation of `w000003`.

## 2. Exact evidence

### Recovery bundle and backup

- Selected recovery: `ongoing/超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub`
- SHA-256: `145008AE56CFA139597E15564548148FFE80E7B3AA0D5044657F20F38A7B45A0`
- Size: **1,569,362 bytes**
- Accepted units: **296** (`checkpoint.accepted_job_ids` = 296, translations = 296)
- Backup: `diagnostics/text194/backup-296units-145008AE56CF-20261105T112323.epub`
- Backup SHA-256: identical to the source; manifest at `diagnostics/text194/backup-manifest.json`

### Exact `w000003` source

- Fixture: `tests/fixtures/text194_w000003_source.json`
- 143 units, 5,453 characters
- Reconstructed source digest: `9cafbfd170ac5a4718b0a731b911a49050693d90b1532f229727db10ccdd13e2`
- Planned digest: identical. The reconstruction is exact, not approximate.

### Where `哈基黑` came from

`哈基黑` is **not in the `w000003` source**. It is a catalogue alias introduced in `w000002`:

- Catalogue entry `哈基黑` has `count: 2`, references:
  - `epub:6:chap_6.xhtml:7:17e61580a3033acf`
  - `epub:6:chap_6.xhtml:16:d4675bfa2b0e95e0`
- Contexts include: `黑子身穿睡衣跪下缩成小小的一只...哈基黑，你这家伙，竟然意外的诱人...`
- The `w000002` cumulative summary says: `${白井黑子}（亦被${御坂一色}称为${哈基黑}）`.
- It appears once in the prior summary at character 578 and **0 times** in the `w000003` source.

`操祈` is different: it occurs **14 times** in `w000003` (12 inside `食蜂操祈`, 2 standalone). It is a legitimate exact-source candidate and was included in attempts 1 and 2.

### Saved replies and diagnostics

- Fixture: `tests/fixtures/text194_w000003_failures.json`
- Contains the three exact replies above plus the exact reasons from `diagnostics.failures`.
- Initial run log: `DELIVERY/text194-initial-failure.log`.

## 3. Root cause

The retry contract had no way to record a **context-only** name:

1. `names` must be literal in the current source. `哈基黑` is not, so attempt 1 correctly failed.
2. At the time, `excluded_candidates` was allowed to contain **only supplied retry candidates**. `哈基黑` was never a candidate because it is not literal, so attempt 2's honest exclusion was rejected. Text205 later made other valid absent extras inert.
3. The model then tried to simplify and dropped `操祈`, a real exact-source candidate, so attempt 3 failed the missing-candidate guard.

The loop bounced between "nonliteral name" and "invalid exclusion" until the budget ran out. This was a contract defect, not a provider-capacity problem.

## 4. Fix

The fix is deliberately narrow. It does **not** auto-accept names from rejected replies, does **not** add retry-state restoration, and does **not** weaken literal grounding.

### 4.1 Declared context-only labels

`book_maker/source_windows.py` adds `context_only_labels(window, prior_summary)`: labels named in the prior summary that do not occur literally in the current window. For this window it returns `['御坂旅挂', '哈基黑']`.

`validate_extraction(..., context_only=...)` now accepts an `excluded_candidates` entry when it is either:

- a supplied retry candidate, or
- a declared context-only label.

It still rejects:

- a literal current-window label that was not a retry candidate (prevents hiding a real name);
- an undeclared nonliteral label;
- a label that is both extracted and excluded;
- duplicates and malformed labels.

The final `names` list still has to be literal. Context-only labels can never become current-source names.

### 4.2 Strict candidate harvesting

`extraction_candidates()` now harvests labels only from a reply that is:

- a JSON object with `window_id`, `names`, and only the optional `excluded_candidates`;
- for **this** window ID;
- structurally valid, including legacy `{source, unit_ids}` citations that name exactly one known current-window unit and whose label is literal in that unit.

A wrong-window reply, an extra-field reply, a malformed exclusion list, or a bad legacy citation yields **no candidates**. The reply may still be rejected for a nonliteral name; its literal names are harvested only as **retry candidates**, never as approvals.

### 4.3 Prompt contract

`source_window_prompt()` now carries:

- `source_units` — the only evidence;
- `prior_original_language_summary` — context only, not evidence;
- `context_only_labels` — prior-summary labels absent here; never put them in `names`, optionally list them in `excluded_candidates`;
- `retry_candidates` — exact-source labels from earlier structurally valid replies; each must be explicitly included or excluded.

It states that a rejected reply does not approve its names and that the final valid reply must decide every candidate.

### 4.4 Workflow

`MarkerWorkflow.prepare_source_window()` computes `context_only`, passes it to the prompt and validator, and keeps the existing 3-attempt loop and missing-candidate guard. It no longer carries accepted/excluded state across attempts.

## 5. Files changed

| File | Change |
|---|---|
| `book_maker/source_windows.py` | `context_only_labels`, strict `extraction_candidates`, context-only validation, prompt fields |
| `book_maker/marker_workflow.py` | Passes `context_only` to prompt/validator; no auto-acceptance |
| `diagnostics/text194_extraction_probe.py` | Reusable extraction-only probe: 120s timeout, SDK retries disabled, hard total-call cap, verbose timing |
| `tests/test_text194_extraction_recovery.py` | 11 focused regression tests |
| `tests/fixtures/text194_w000003_source.json` | Exact 143-unit source fixture, digest-verified |
| `tests/fixtures/text194_w000003_failures.json` | Exact three replies and saved reasons |
| `docs/text194-extraction-retry-contract.md` | This handoff |

The earlier cumulative-summary fix in `book_maker/marker_names.py`, `book_maker/marker_workflow.py`, `book_maker/source_windows.py`, and `tests/test_summary_recovery.py` is preserved.

## 6. Tests

Focused command:

```powershell
& "C:\Users\Tina_\Miniconda3\python.exe" -m pytest -q `
  tests/test_text194_extraction_recovery.py `
  tests/test_source_windows.py tests/test_marker_workflow.py `
  tests/test_marker_names.py tests/test_marker_bundle.py `
  tests/test_marker_end_to_end.py tests/test_summary_recovery.py `
  tests/test_provider_refusal.py
```

Result: **154 passed in 12.87s** (`DELIVERY/text194-test-results.txt`).

The 11 text194 tests cover:

1. The real second reply passes with the declared `哈基黑` context-only exclusion.
2. The real third reply still fails if `操祈` is omitted (missing-candidate guard preserved).
3. Direct validation of the real second reply.
4. Candidate harvesting from the real first reply includes `操祈`, excludes `哈基黑`.
5. Malformed JSON is rejected and harvests nothing.
6. A wrong-window prior reply does not seed candidates.
7. An invalid legacy citation prior reply does not seed candidates.
8. A literal label cannot be excluded unless it is a retry candidate.
9. Under the text194 contract, an undeclared context-only exclusion was
   rejected. Text205 supersedes this behavior for unrelated absent extras; they
   are now ignored and cannot satisfy required retry decisions.
10. `context_only_labels` is computed from prior-summary markers and literal presence.
11. Cross-window diagnostics do not seed candidates.

The earlier summary regression suite (`tests/test_summary_recovery.py`) still passes.

## 7. Live extraction-only probe

### First attempt — interrupted, one uncertain request

- Helper: `diagnostics/text194_extraction_probe.py`
- The first version had **no explicit HTTP timeout** and **no `max_retries=0`**; it stalled after request 1 for 4m45s.
- The verified helper process (pid 21052, job `brisk-wharf` pid 22824) was stopped.
- **One live request was sent and its outcome is unrecorded.** It counts as one used request.
- No report was produced; the recovery hash was not modified.

### Second attempt — hardened, successful

Probe hardening:

- `OpenAI(..., timeout=120.0, max_retries=0)`
- Hard cap checked **before** each network call (`LiveExtractionModel.calls >= max_requests`).
- `--prior-used 1 --max-requests 2` enforces the original total of 3.
- Start/end/elapsed/error logging.

Command:

```powershell
$env:PYTHONPATH = (Get-Location).Path
& "C:\Users\Tina_\Miniconda3\python.exe" -X utf8 diagnostics\text194_extraction_probe.py `
  --epub "ongoing\超炮，某不科学的恋爱喜剧-c00de9cde483_bilingual_temp.epub" `
  --book "input\超炮，某不科学的恋爱喜剧.epub" `
  --window w000003 --prior-used 1 --max-requests 2 `
  --report "diagnostics\text194\probe-live-2.json"
```

Result: **1 request used, 20.533s**, validated:

- 12 exact names: `一色同学`, `一色弟弟`, `御坂同学`, `御坂美琴`, `白井黑子`, `食蜂操祈`, `一色`, `御坂`, `操祈`, `美琴`, `食蜂`, `黑子`
- Excluded context-only: `御坂旅挂`, `哈基黑`
- 143 prepared units, 176 bracketed spans
- Recovery SHA-256 before and after: `145008AE56CFA139597E15564548148FFE80E7B3AA0D5044657F20F38A7B45A0` (unchanged)
- Accepted units before and after: **296 / 296**

Report: `diagnostics/text194/probe-live-2.json` and `DELIVERY/text194-live-probe.json`.

### Live limitations

- This is an **extraction-only** probe. It does not translate `w000003`, does not commit the prepared window, and does not run the remaining 75 windows.
- The probe used 2 of the 3 total live requests (1 uncertain + 1 successful); one remains unused. No further calls are needed for this verification.
- The probe's in-memory prepared window is discarded. A normal `-Resume` will extract `w000003` again with the fixed contract and continue.

## 8. Resume guidance

The 296 accepted units are unchanged. Run the original command with the repository interpreter:

```powershell
cd C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker-window-names
$env:PATH = "C:\Users\Tina_\Miniconda3;" + $env:PATH
$env:PYTHONPATH = (Get-Location).Path
.\run_grey_translation.ps1 `
  -BookPath "C:\Users\Tina_\Downloads\CiweimaoDownloader-main\output\超炮，某不科学的恋爱喜剧.epub" `
  -NameWindowTokens 8192 -SourceSummaryTokens 10500 -MaxTokens 4000 -Resume -Verbose
```

`w000001` and `w000002` are complete and will not be retranslated. `w000003` will be extracted with the fixed contract, then translated and summarized.

## 9. Risks and rollback

| Risk | Assessment | Mitigation |
|---|---|---|
| A context-only label is actually a current name | Prevented: `context_only_labels` is computed by literal absence from every source unit; a literal label cannot be excluded as context-only. | Tests cover literal non-candidate rejection and context-only declaration. |
| A rejected reply's names are published | Prevented: harvested labels are only retry candidates; the final valid reply must explicitly include or exclude each. | Tests cover wrong-window, bad legacy citation, malformed JSON, and missing-candidate rejection. |
| Candidate harvesting over-filters a useful reply | A wrong-window or structurally invalid reply is not evidence; the next valid reply is requested. | 11 focused tests plus 154-test suite. |
| Probe accidentally writes the recovery | Probe uses a deep-copied state and `persist=None`; it verifies the byte hash before/after. | Live result: hash unchanged, 296 units unchanged. |
| Rolling back the shared files also removes the earlier summary fix | `book_maker/source_windows.py` and `book_maker/marker_workflow.py` contain both fixes. | Reverse only `DELIVERY/text194-extraction-fix.patch`, which is relative to the summary-fix baseline. Do not `git checkout --` the shared files. |

Rollback of only the text194 change:

```powershell
cd C:\Users\Tina_\Documents\PlayingWithRepo\bilingual_book_maker-window-names
git apply -R C:\Users\Tina_\.openclaw-autoclaw\workspace\DELIVERY\text194-extraction-fix.patch
```

The summary fix remains. The recovery bundle is not modified by rollback.

## 10. Assumptions

- The saved 296-unit recovery is authoritative and must not be retranslated.
- `哈基黑` is an alias from `w000002`, not a `w000003` name.
- `-SourceSummaryTokens 10500` remains the user's chosen budget; this fix does not change it.
- No commit, push, or publication was performed.

## 11. Deliverable index

- docs/text194-extraction-retry-contract.md - this handoff inside the repo.
- DELIVERY/text194-handoff.md - delivery copy of the handoff.
- DELIVERY/text194-extraction-fix.patch - text194-only patch, relative to the summary-fix baseline.
- DELIVERY/text194-test-results.txt - focused suite output.
- DELIVERY/text194-live-probe.json - hardened live probe report.
- DELIVERY/text194-initial-failure.log - original failing run log.
- DELIVERY/text194-backup-manifest.json - backup hash and path.
- diagnostics/text194/backup-296units-145008AE56CF-20261105T112323.epub - preserved 296-unit recovery.

