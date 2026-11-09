# text201 transport circuit fix

Status: plan and local implementation for the text_201 connection-outage run.
Target repository: `bilingual_book_maker-window-names`, branch
`source-window-names`, on top of `430a730`.

This change is deliberately scoped to the marker paragraph transport path. It
does not probe the live gateway, does not change provider prompts, and does not
touch any real recovery EPUB.

## 1. Evidence

`text_201.txt` ends window `w000001`, chunk 3/5 with 40 pending paragraphs.
Every one of the 40 was reported as:

```
(paragraph N, 5/5 attempts, transport): transport failure: Connection error.
```

The chunk summary is explicit:

```
This chunk invocation made 106 logical request(s), carrying 200 paragraph
participation(s); observed provider calls: unavailable; usage: unavailable.
```

That is not 40 sends and it is not 200 sends. It is:

| Round | Cap used | Sends | Paragraph participations |
| --- | ---: | ---: | ---: |
| Initial chunk | 40 | 1 | 40 |
| Attempt 2 repair | 8 | 5 | 40 |
| Attempt 3 repair | 2 | 20 | 40 |
| Attempt 4 repair | 1 | 40 | 40 |
| Attempt 5 repair | 1 | 40 | 40 |
| **Total** | | **106** | **200** |

The run spent about 21 minutes in this chunk. The send count is only part of
that: the retry ladder also slept after nearly every failed send, with delays
of 1, 2, 4 and 8 seconds and no response body to show for any of them.

No HTTP status, refusal, finish reason, or usage was observed. The provider
was not reached. The outer exception text was the OpenAI SDK's generic
`Connection error.`; the useful cause chain and the endpoint were never
printed.

## 2. Root cause

`MarkerWorkflow.translate` (`book_maker/marker_workflow.py`) increments
`item.attempts` for every paragraph in the selected subset before calling
`send`. Its `except` branch treats every retryable, non-fatal exception as a
paragraph transport failure. That has three consequences:

1. **A connection outage burns the paragraph budget.** A send that never
   reached the provider still counts as one of the paragraph's
   `max_chunk_attempts` sends.
2. **The repair scheduler shrinks the request.** The next
   `_next_retry_batch` call uses `retry_unit_cap(attempt + 1, n)`, whose
   schedule is `40 -> 8 -> 2 -> 1 -> 1`. A transport retry therefore resends
   a smaller and smaller subset even though nothing about the provider's
   response justifies shrinking.
3. **The backoff is per attempt, not per outage.** Because every group is
   charged as a fresh paragraph attempt, the loop walks the whole ladder and
   sleeps between every group.

The two local defects are independent of the gateway. Even a perfect gateway
cannot help a client that turns one connection outage into 106 sends and 200
charged paragraph attempts.

## 3. Contract

### 3.1 Classify no-response transport failures

Add `book_maker/transport_diagnostics.py` with a deliberately narrow
`is_transport_failure(error)` classifier. It matches connection, DNS, socket,
TLS, timeout and HTTP-client transport exceptions (for example
`openai.APIConnectionError`, `httpx.ConnectError`, `httpcore.ConnectError`,
`ConnectionRefusedError`, `TimeoutError`, `socket.gaierror`, `ssl.SSLError`).

It does **not** classify `RuntimeError`, arbitrary `Exception`, a provider
boilerplate reply, or an HTTP status error such as `RateLimitError`. Those
keep the existing behavior. In particular, the existing regression
`tests/test_marker_selective_retry.py::test_transport_failures_consume_the_budget_and_stop_bounded`
raises `RuntimeError` and must continue to charge the paragraph budget.

### 3.2 Chunk-wide consecutive circuit

The circuit is per chunk invocation, not per paragraph:

- default `DEFAULT_TRANSPORT_ATTEMPTS = 3` transport sends;
- the initial send counts as send 1, so an all-down chunk makes at most three
  sends (the initial send plus two retries);
- a usable response of any kind resets the consecutive counter;
- the limit is configurable through `model.max_transport_attempts` or
  `run.effective_settings["max_transport_attempts"]`; values below 1 fall
  back to 3.

When the third consecutive send receives no response, the chunk stops with a
transport-circuit error. It does not walk the per-paragraph ladder and does
not call `_exhaust_recovery`, because the paragraphs are not exhausted.

### 3.3 Same subset, no charging, no shrinking

On a transport failure:

- refund the `attempts += 1` that was applied before the send;
- keep `last_stage`/`last_error` for diagnostics;
- retry the exact same identities, in the same order and at the same size;
- do not call `_next_retry_batch` for the retry;
- do not print a misleading `repair request attempt ... (cap ...)` line;
- do not sleep after the final permitted send.

A transport retry may still carry prior response-derived repair feedback if
the same paragraphs had already failed validation before the connection
dropped. No transport text is added to a provider prompt.

### 3.4 Diagnostics

Every transport retry prints one line containing:

- the transport send number out of the chunk limit;
- the number of units in the retried subset;
- the sanitized actual endpoint;
- the real exception cause chain, outermost to innermost.

The endpoint is read from `model.api_base`, then
`model.openai_client.base_url`, then `model.client.base_url`. It is never
invented. `sanitize_endpoint` strips username/password, query and fragment,
and applies the existing `redaction.redact` registry, so a credentialed URL
cannot leak its secret.

The exception chain walks `__cause__` / `__context__` with cycle protection
and a depth limit. It prints the actual innermost cause rather than only
`APIConnectionError: Connection error.`.

When the circuit opens, a final block reports the endpoint, last chain, every
transport attempt with its unit count and chain, pending and accepted counts,
durable checkpoint/journal counts, and request statistics (logical requests,
paragraph participations, transport sends, provider calls, usage).

### 3.5 Accepted recovery is preserved

Accepted paragraphs are committed through `_commit_round` and the caller's
`persist` callback after every successful response. Transport failures never
roll back that work. Opening the circuit raises after the last failed send;
the recovery journal/checkpoint written by earlier rounds remains authoritative.
`self.retry_feedback` is cleared so a resume does not inject connection text
into the next provider request.

### 3.6 Explicit refusal is unchanged

`ProviderRefusal` remains terminal:

- exactly one send;
- no transport retry;
- no backoff;
- the existing `provider-refusal` diagnostic;
- the original exception is re-raised.

Fatal and non-retryable errors likewise keep their existing single-send
behavior.

### 3.7 Finite overall budget

The circuit bounds consecutive no-response sends at 3. Every non-transport
response round charges at least one paragraph attempt for every unit in its
subset, and each paragraph has at most `max_chunk_attempts` charges. Since a
transport episode can only occur between response rounds, the total number of
sends in one chunk is bounded by:

```
3 * (max_chunk_attempts * number_of_paragraphs + 1)
```

The bound is generous, but it is finite. There is no path that retries a
connection outage forever, and no path that converts an outage into extra
paragraph attempts.

## 4. Code changes

1. `book_maker/transport_diagnostics.py` (new): classifier, endpoint
   sanitizer, endpoint resolver, exception-chain formatter, default limit.
2. `book_maker/marker_workflow.py`:
   - import the helpers;
   - add `transport_sends` and `record_transport_failure()` to
     `_ChunkRequestStats`;
   - resolve the per-chunk transport limit;
   - keep a `transport_retry_ids` subset across a transport retry;
   - refund attempt increments for no-response failures;
   - reset the consecutive counter on any usable response;
   - add `_abort_transport` with the evidence block and a
     `MarkerValidationError` that says the circuit opened, not that the
     paragraphs exhausted their budget.
3. `tests/test_text201_transport_circuit.py` (new): regression coverage for
   the cases below.

## 5. Regression tests

| Test | Contract |
| --- | --- |
| `test_all_down_stops_after_three_transport_sends` | 40 units, every send raises a connection error: exactly 3 sends, identical 40-unit subset each time, 120 participations, no journal entries |
| `test_intermittent_transport_recovers_without_shrinking_or_charging` | one connection failure then a complete response: 2 sends, both carry all 40 units, no shrink/repair line |
| `test_accepted_siblings_survive_and_are_not_resent` | one accepted sibling stays durable; later transport retries carry only the remaining unit; circuit opens without resending the accepted unit |
| `test_explicit_provider_refusal_unchanged` | `ProviderRefusal` propagates after one send, with no backoff |
| `test_no_extra_last_sleep` | three all-down sends record sleeps for retries 1 and 2 only |
| `test_endpoint_and_chain_are_explained_without_secrets` | endpoint is origin/path only, exception chain reaches the inner cause, credentials and query are absent |
| `test_stats_transport_counter_is_partial_aware` | stats count logical requests, participations and transport sends, and mark missing provider metadata as unavailable |

Existing regressions remain in the run: `test_text198_fresh_run.py`,
`test_marker_selective_retry.py`, `test_marker_diagnostics.py`,
`test_provider_refusal.py`, and `test_marker_end_to_end.py`.

## 6. Verification

Offline only, with an isolated pytest basetemp:

```
C:\Users\Tina_\Miniconda3\python.exe -m pytest \
  tests/test_text201_transport_circuit.py \
  tests/test_text198_fresh_run.py \
  tests/test_marker_selective_retry.py \
  tests/test_marker_diagnostics.py \
  tests/test_provider_refusal.py \
  tests/test_marker_end_to_end.py \
  -q --basetemp=<isolated-temp> -p no:cacheprovider
```

The exact result is recorded in the implementation status below.

## 7. Out of scope

- No live gateway probe and no diagnosis of the actual connection failure.
  The endpoint and cause chain printed by this change are the evidence needed
  for that separate investigation.
- No mutation of `ongoing/*.epub`, `output/*.epub`, or the real recovery
  bundle.
- No commit, stage, or push.
- No change to name extraction, source summaries, provider prompts, or the
  per-paragraph validation retry ladder for non-transport failures.

## Supervisor verification and actual gateway repair

The "out of scope" statements above describe AutoCoder's client subtask only.
Codex also inspected the actual local gateway and restored it:

- localhost:4995 refused connections before intervention; corrected service
  started hidden (launch PID37132), then /health returned HTTP200.
- AI-BRIDGE/gemini-pool/anonymous/server.py no longer joins independent
  candidates. It exposes the first nonempty candidate, with snapshots still
  updating that candidate. Incomplete output remains for strict client recovery.
- Four new gateway regressions cover alternatives, snapshots, and empty replies;
  all 65 gateway tests pass. A live protected-name XML translation through the
  real /v1/chat/completions route passed in4.26seconds with one response root.
- Final independent focused verification of expanded AutoCoder implementation:
  206 passed in22.93seconds across text201/text198, selective retry, provider
  refusal, source windows, workflow, bundle, resume, end-to-end and diagnostics.
  COLUMNS=1000 avoids an existing Rich-wrapping assertion at long temp paths.
- No book translation was started and no commit made. Recovery hash remains
  14fb2be8f9be6c3ca6621b5d2169b00acdda96d00a17a90f261971cdc6f79bf3
  with80 durable units. A small probe does not guarantee whole-novel throughput.
- AutoCoder session agent:auto-coder:codex-mcp-7e20ee449e90 initially could not
  start ZCode without a bound workspace; it subsequently supplied expanded
  transport diagnostics and tests. No high-demand failure occurred to nudge.

Additional verification:16 summary/recovery tests pass in4.18seconds. A broader
legacy translation-plan run hit the existing Windows cp1252 read_text decoding
failure; the UTF8 rerun was stopped in unrelated slow loader tests. No full-suite
pass is claimed. The independent206-test focused pass and65 gateway tests are the
final acceptance checks for these changes. AutoClaw's expanded review was closed
after its useful code contribution to avoid leaving duplicate broad checks running.
