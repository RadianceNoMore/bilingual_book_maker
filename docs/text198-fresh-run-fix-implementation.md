# text198 fresh-run fix: implementation handoff

Status: implemented and verified on 2026-11-05.
Target: `bilingual_book_maker-window-names`, branch `source-window-names`,
on top of `b454a1f` (text196 implementation).

## 1. Outcome summary

The five locally controllable defects are fixed with regression coverage:

1. An exhausted paragraph no longer terminates the whole chunk. Eligible
   siblings still receive their remaining attempts.
2. Progress text now reports the actual next request and never prints `6/5`.
3. Request accounting distinguishes logical requests, paragraph
   participations, observed provider calls, and partial usage.
4. The top-of-loop exhaustion path reports the same statistics as other paths.
5. Each request reports its own unit count and output estimate, with the
   original chunk size kept separate.
6. The two text198/text197 standalone provider messages are classified as
   `provider-boilerplate`, with bounded backoff.
7. Fresh runs preserve a colliding prior recovery file before replacing it,
   with digest verification and run provenance.

The upstream response corruption is identified, reproduced offline, and
**not fixed in the live gateway**. The evidence and a proposed one-line patch
are recorded in `diagnostics/text198/`. The client parser remains conservative.

## 2. Fix 2: queue termination

`MarkerWorkflow.translate` now partitions pending items every round:

- `eligible`: attempts remain;
- `exhausted`: at the per-paragraph limit.

Only eligible items enter `_next_retry_batch`. Exhausted items are parked with
their last error and are never sent again. When no eligible item remains and
unresolved items still exist, `_exhaust_recovery` raises. If every item is
accepted, the method returns normally.

The regression test
`tests/test_text198_fresh_run.py::test_exhausted_sibling_does_not_terminate_eligible_repairs`
forces the text198 shape: one unit reaches 5/5 while two siblings are at 4/5.
The siblings receive their fifth send, are journaled, and the final exception
names only the exhausted unit. The exhausted unit is sent exactly five times.

## 3. Fix 3: request and attempt accounting

`_ChunkRequestStats` records, per chunk invocation:

- `logical_requests`: one per `send` call;
- `paragraph_participations`: units carried across those sends;
- `provider_calls`: summed from response metadata;
- `usage_total` and partial/unavailable flags.

Every terminal path receives the same object. Missing measurements are printed
as `unavailable` or `at least N (partial)`, never silently as zero. The
exhaustion diagnostics record the same values in run state.

Progress after each round now derives the actual next group:

```
N/M accepted; next request attempt A/L carries K unit(s);
E eligible, X exhausted, P pending.
```

If no eligible group remains, it prints exhaustion and raises instead of
printing an impossible attempt.

Per-request metadata in `ChatGPTAPI._protected_completion` now uses the payload
of the current request:

- `requested_units = 1` for a singleton retry;
- `original_chunk_units = 40` for the original chunk;
- `estimated_output_allowance` from that payload, separate from any endpoint
  output cap.

## 4. Fix 4: provider boilerplate and pacing

`book_maker/provider_refusal.py` now recognizes these standalone replies:

- `I encountered an error doing what you asked. Could you try again?`
- `I seem to be encountering an error. Can I try something else for you?`

Matching remains whole-response only, after fence/seed-wrapper stripping.
XML, JSON, or narrative prose quoting the message stays ordinary content.
The classifier runs before XML parsing, so these replies print
`provider-boilerplate`, not `missing-unit`.

Consecutive provider-error responses use the existing backoff helper with
`base=2`, `cap=30`; the streak resets after a usable reply, and the final
attempt is not followed by a pointless sleep. The per-paragraph budget remains
five, and no sixth send is possible.

## 5. Fix 5: fresh-run recovery preservation

Before the first checkpoint write of a fresh run, the loader checks the
canonical recovery path. If a file exists that does not belong to the active
run, it is copied to `ongoing/archive/` under a digest-derived name:

```
<stem>.prior-<sha256-prefix>.epub
```

The copy is re-hashed and must match before the active path is replaced. A copy
failure raises before any new checkpoint is written and prints the old path,
archive path, and error. The same guard covers `_write_partial_recovery`, so a
fallback save cannot silently destroy the prior file.

Run state now records `mode` (`fresh` or `resume`), `resume` (boolean), and
`selected_checkpoint`. Existing bundles written before this change report
`mode: null`; the next save adds the fields.

Regression tests:

- `test_second_fresh_run_preserves_the_first_recovery` verifies the archived
  digest equals the first recovery and the active recovery has a new run ID.
- `test_archive_failure_leaves_the_prior_recovery_untouched` simulates a copy
  failure and verifies the original digest is unchanged.

Partial-recovery saves now also print the same durable definition used at stop
time: checkpointed + journal-only = distinct durable units. Promotion of
contiguous journal entries into the checkpoint changes the split, not the total.

## 6. Fix 1: upstream response assembly

### Locating the boundary

The launcher uses `--api_base http://127.0.0.1:4995/v1` and model
`gemini-anon`. The listener is a local Python process (`python server.py`,
PID 25968). The implementation is:

`C:\Users\Tina_\Documents\PlayingWithRepo\AI-BRIDGE\gemini-pool\anonymous\server.py`

No remote infrastructure was changed. The gateway was inspected read-only.

### Proven defect

`_parse_generation` collects Gemini candidates by candidate ID and then does:

```python
text = "\n\n".join(part for part in candidates.values() if part)
```

Distinct candidates are independent generations, but this OpenAI-compatible
endpoint returns a single `choices[0].message.content`. Joining candidates
produces exactly the observed restart shape. The offline reproduction
`diagnostics/text198/reproduce_gateway_join.py` builds two candidates:

- candidate `rc_1`: `<bbm-response>... "Don't`
- candidate `rc_2`: a complete `<bbm-response>...</bbm-response>`

and the real `_parse_generation` returns both joined by two newlines. The same
script shows repeated snapshots of one candidate are correctly replaced, not
joined. Output: `diagnostics/text198/gateway-join.json`.

The client parser rejects both text198 restart shapes and preserves an
independently safe sibling; see `diagnostics/text198/assembly-reproduction.json`.

### Live probe

One bounded singleton request was sent through the local gateway with tracing
enabled. It returned a clean single-root response in 21.662 seconds, one
provider call, `finish_reason=stop`, 182 characters. The gateway log shows that
request (`request_id=f4b7672da908`) made one upstream attempt through one route,
so this probe did not exercise the multi-candidate branch. A successful probe
does not prove the intermittent multi-candidate case is gone.

The probe used the preserved copy's prepared source and incoming cumulative
summary. The accepted-source prefix was empty because raw prior source is not
persisted in the bundle; that prefix does not affect candidate assembly.

Artifacts:

- `diagnostics/text198/live-probe.json`
- `diagnostics/text198/live-trace/marker-trace.jsonl`

### Status and proposed patch

Status: **identified and reproducible, not fixed in the live service.**

`diagnostics/text198/gateway-candidate-join.patch` proposes returning one
candidate instead of joining:

```python
text = next((part for part in candidates.values() if part), "")
```

This patch was deliberately not applied because the gateway is a separate
repository and a live service; applying it requires deciding whether the first
candidate is always the correct one for Gemini's candidate semantics, then
restarting the service. The client-side retry/alias work does not claim to fix
this upstream fault.

## 7. Recovery evidence

`diagnostics/text198/preserve_recovery.py` copied the current recovery EPUB to
`recovery-preserved.epub` and wrote `recovery-manifest.json`:

- source SHA-256: `edd1675648491946195342a97fa234ce59d884e8b780c3032449b5e986e317b4`
- copy SHA-256: identical
- run ID: `4453886b-ea10-4258-bed7-86a77ae1e7d0`
- window 1: 164/164 accepted, summary complete
- window 2: 117/132 accepted, summary not updated
- checkpoint IDs: 246
- journal IDs: 35 (35 additional)
- distinct durable IDs: 281

After the live probe the original recovery digest was recomputed and matched
the manifest exactly. The real book was not advanced.

## 8. Verification

Focused affected suite (17 files):

```powershell
& 'C:\Users\Tina_\Miniconda3\python.exe' -m pytest `
  tests/test_text198_fresh_run.py tests/test_marker_selective_retry.py `
  tests/test_marker_workflow.py tests/test_marker_end_to_end.py `
  tests/test_marker_resume.py tests/test_provider_refusal.py `
  tests/test_marker_diagnostics.py tests/test_source_windows.py `
  tests/test_marker_bundle.py tests/test_summary_recovery.py `
  tests/test_marker_names.py tests/test_marker_repair.py `
  tests/test_protected_names.py tests/test_translation_policy.py `
  tests/test_untranslated_recovery.py tests/test_text194_extraction_recovery.py `
  tests/test_chatgptapi_translator.py -q
```

Result: **436 passed, 0 failed in 20.98s**.

Offline reproductions:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python diagnostics/text198/reproduce_assembly.py
python diagnostics/text198/reproduce_gateway_join.py
```

Both make zero provider calls and do not modify recovery files.

## 9. Risks

1. **The gateway still joins candidates.** Intermittent two-root responses can
   recur until the gateway is patched and restarted. The client rejects them
   conservatively, so work is lost only for the affected paragraphs, not
   silently misattributed.
2. **The proposed gateway patch needs protocol review.** Choosing the first
   candidate assumes candidates are alternatives, not continuation fragments.
   Validate against real Gemini payloads before deploying.
3. **More requests when a paragraph exhausts early.** Queue draining lets
   siblings finish, which can increase total calls compared with stopping the
   chunk immediately. This is intentional and bounded by each paragraph's five
   attempts.
4. **Provider-error backoff adds delay.** Consecutive boilerplate replies wait
   up to 30 seconds each. Other formatting failures do not trigger it.
5. **Archives accumulate.** `ongoing/archive/` has no automatic pruning.
6. **Existing recovery bundles have no mode field.** Provenance is added on the
   next save; the preserved text198 copy still reports `mode: null`.

## 10. Rollback

- No commit was created. Revert the working-tree changes or restore these files
  from `b454a1f`: `book_maker/marker_workflow.py`,
  `book_maker/provider_refusal.py`, `book_maker/loader/epub_loader.py`,
  `book_maker/translator/chatgptapi_translator.py`.
- Delete `ongoing/archive/` only if the preserved prior recovery is no longer
  needed; it is not consulted by resume.
- Unset `BBM_MARKER_TRACE_DIR` to disable tracing.
- The new run fields are additive; older code can read bundles that contain
  them, and new code reads old bundles with `mode: null`.
- Do not apply the gateway patch without a separate review of Gemini candidate
  semantics.
