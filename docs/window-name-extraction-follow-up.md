# Extraction failures in text_190.txt

The resumed run stopped before translating any body text. Its three extraction
responses failed for different reasons: missing `excluded_candidates`, invalid
long unit IDs, and malformed nested/truncated JSON fences. `finish_reason=stop`
did not establish that the returned JSON was complete.

The retry prompt previously showed a two-field schema and then separately
required a third field. It also made the provider reproduce paragraph IDs that
local code can derive from exact source matches.

## Changes

- The provider returns exact name strings in one consistent JSON contract.
  Local code verifies literal matches and derives witness unit IDs. The input
  carries source paragraphs without their long IDs. Legacy cited responses
  remain subject to their existing strict citation checks.
- Full names and recurring short spellings are requested separately.
- Every retry candidate must be included or excluded. An absent exclusions
  field is accepted when all candidates were included. A label cannot appear
  in both lists. Rejected responses never automatically populate the catalogue.
- Historical candidates are reused only when the request matches the exact
  ordered source window. Matching `w000001` alone is insufficient.
- Preparation hashes the actual successful request, including retry data,
  and records extraction prompt version 3. Failure diagnostics include the
  window ID and source digest. Malformed JSON remains rejected.

## Verification

The focused source-window, EPUB end-to-end, selective retry, bundle, and CLI
documentation suite passed: 88 tests. AutoClaw's AutoCoder independently ran
the same suite: 88 passed. After its stopped turn was resumed, AutoCoder
completed its review and reported no blocking issues.

One live extraction-only request used the current first window and its 21
grounded retry candidates. It passed on the first request in 61.42 seconds,
returning 16 labels, including `一色`, `美琴`, `操祈`, and `黑子`. The request
estimate fell from 12077 to 7624 tokens. The recovery EPUB's hash was unchanged.
This validates the observed response and its literal grounding, rather than
proving semantic completeness of all future extractions or a full-book run.

The current recovery contains zero accepted units and zero catalogue labels.
It is a different run from the earlier recovery with 159 accepted window units;
the available records do not establish how that earlier file was replaced.
The older protected-names fork has its own recovery with 5520 accepted units,
using a different workflow. No recovery books were modified or merged.

Resume from this fork with the same book and budgets, adding `-Resume`. The
extraction-only probe did not checkpoint its result, so resume performs the
normal extraction and publication transaction before translating.
