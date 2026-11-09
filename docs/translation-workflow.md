# Resumable novel translation

The launcher implementation is in `scripts/run_grey_translation.ps1`; the root
`run_grey_translation.ps1` forwards to it. See [Repository layout](repository-layout.md)
for all tools and examples.

The PowerShell launcher keeps the current novel and provider settings in one
place. Its defaults group paragraphs within a chapter, closing a request at
40 units or 4,000 estimated source tokens, whichever comes first.

```powershell
./run_grey_translation.ps1
./run_grey_translation.ps1 -Resume
./run_grey_translation.ps1 -BookPath 'C:\Books\novel.epub'
./run_grey_translation.ps1 -MaxUnits 25 -MaxTokens 2400 -ChunkAttempts 5
./run_grey_translation.ps1 -WholeChapter
```

These limits are a conservative starting point, not a measured optimum for every
model. Output, prompts, glossary, and session history require additional context
space. A single oversized paragraph stays intact; inspect or preprocess such a
paragraph if it exceeds the endpoint's capacity.

## Requests and recovery

Without `--resume` (PowerShell: `-Resume`), translation starts fresh. Previous
generated checkpoints, handoff summaries, approved glossary decisions, and review
logs are moved to `.run-history/<book>/<run>/` beside the working source. They
are not read by the new run. Explicitly supplied glossary files, source books,
classification plans, and completed output EPUBs are retained. With `--resume`,
the saved translation and glossary state are reused and checked as usual.

`--accumulated_num` enables paragraph grouping. The additional
`--request-max-units` (default 40) and `--request-max-tokens` (default 4000)
bound requests in both tag and plan modes. Plan mode's smaller existing limits
still apply. Normal grouping stays within chapters unless
`--accumulate-across-chapters` is explicitly selected.

`--whole-chapter` overrides body limits. Navigation labels always use bounded
groups, including in whole-chapter mode. Each completed TOC group is checkpointed;
matching chapter headings reuse approved labels.

After a failed response, the same chunk is retried up to
`--max-chunk-attempts` times (ten by default, including the initial attempt).
Persistent alignment mismatches or untranslated replies then split into smaller groups. Automatic
recovery keeps at least two units together. `--retry-one-by-one` explicitly
allows individual paragraph retries. Language and contamination failures remain
subject to validation and cannot be accepted merely by splitting.
Retries after an untranslated reply include an instruction to return only the
target-language translations while preserving the required output structure.
The smaller groups inherit this instruction; it does not affect later chunks.

The default protected-name workflow does not use those whole-chunk retries or
splits. It keeps every valid paragraph from the initial response, then sends one
request for all outstanding failed paragraphs together with their errors and
read-only neighbouring source context. `--max-chunk-attempts` is the per-
paragraph send budget, and `--retry-one-by-one` is accepted but has no effect.
See [Protected-name workflow](protected-names.md) for marker grounding and
resume details.

## Glossary and style

`--glossary-auto on` learns provisional renderings from session handoffs. The
first accepted rendering stays stable. A later disagreement is recorded as a
proposal rather than silently replacing the established term. Operator entries
from `--glossary FILE` remain authoritative.

Notes label entries as names, places, peoples, or vocabulary. Entries without a
recognized label remain unclassified. These categories are model observations,
not verified entity identification. Obvious place/people ambiguities are held
out of the provisional inventory for review, without guessing a replacement.
`#` notes are parsed separately and are not part of the translated name.

Handoffs carry plot continuity and vocabulary. They no longer choose a new
writing style at each window. Session translation follows the source's tone and
register; an explicit `--prompt` style overrides that default. Resuming an old
snapshot does not restore model-invented style instructions.

## Files and reports

| Location | Purpose |
| --- | --- |
| `input/` | Working source, resume checkpoint, and handoff snapshot |
| Beside the handoff: `*_glossary_review.jsonl` | Append-only learned entries, conflicts, and quarantined proposals; survives resume |
| `output/*_bilingual.epub` | Finished book, including localized TOC and metadata |
| `output/*_translation_report.json` | Offline advisory consistency report and glossary inventory |

The final report checks accepted target slots, so the Chinese originals in a
bilingual edition are not mistaken for untranslated output. It records missing
expected renderings, script/format contamination, and glossary issues, with
chapter and unit locations where available. It avoids a second full statistical
language-identification pass; translation-time validation still performs that
check. It does not certify semantic accuracy or historical terminology, and it
does not modify the EPUB. Pronouns and grammatical inflections can produce valid
advisory findings.

A new translation using the same book name appends to the existing glossary
review journal; archive that journal for a completely separate review history.
Existing completed EPUBs are not changed by upgrading the code.

## Code layout

| Module | Responsibility |
| --- | --- |
| `book_maker/batching.py` | Shared request-boundary policy |
| `book_maker/glossary.py` | Glossary parsing, matching, and prompt presentation |
| `book_maker/glossary_review.py` | Stable learning, category hints, ambiguity quarantine, review journal |
| `book_maker/session_context.py` | Context windows and handoff serialization |
| `book_maker/translation_quality.py` | Rejection checks during translation |
| `book_maker/translation_audit.py` | Final advisory report |
| `book_maker/loader/epub_loader.py` | EPUB jobs, checkpoints, translation orchestration, and output |

Legacy metadata repair implementations live in `book_maker/tools/`, with root
compatibility scripts for older books and external callers. Normal completed EPUB runs perform localization directly.

## Optional translated output filename

Add `--translate-filename` to the Python command, or use
`./run_grey_translation.ps1 -TranslateFilename` (also works with `-Resume`).
The filename stem is translated after the book finishes. The `_bilingual.epub`
suffix stays intact and the audit report uses the same translated stem.
Working inputs and checkpoints retain their existing names. If the translated
output already exists, move it or select another `--output-dir` before retrying.



Automatic glossary review: for EPUB session runs through an OpenAI-compatible
API, `--glossary-auto on` uses the original context-compaction extraction prompt:
at most 16 names/renderings per report. Normal translation chunks make no separate
glossary-extraction requests. The final short session window is compacted before
publishing as well, including metadata and filename translation requests.

Each new entry receives three independent review requests with its proposed
rendering, nearby original source context, and the compaction summary (treated as
fallible supporting evidence). Two matching votes within one round settle the
entry. If all three differ, their votes are archived and discarded for voting;
only unresolved entries receive a fresh three-vote round. Votes from different
rounds never combine. A partially completed round resumes its missing requests.
Each entry is limited to `--max-chunk-attempts` rounds (default 10). Reviewers use
the configured model in separate requests; no external reviewer or manual glossary
is required. Agreement does not guarantee correct identity.

If the original rendering passes, translation continues without replay. If the
reviewers agree on a correction, that corrected mapping is enforced. If they agree
on rejection without a replacement, the rejection reason guides corrective
translation. Only original request chunks containing the rejected source term are
retranslated; several rejected terms in one chunk cause one replay. Corrective
passes discard their session exchanges and do not extract or review new glossary
entries. They retain the normal alignment, language validation and chunk retry
limits. Settled entries remain fixed and deduplicated across compactions/resume.

`<book>_approved_glossary.json` stores votes, archived rounds, original request
boundaries, the uncompacted session tail, and pending/completed repairs, bound to
the source EPUB hash and language pair. Keep it when resuming. Repairs update saved
body and label translations; the finished EPUB is rebuilt from original source and
corrected caches so earlier serialized paragraphs cannot retain stale names. Old
reviewed checkpoints remain readable; when they lack request boundaries, cached
text is grouped using the chapter/batch plan and current request limits. Checkpoints
without review provenance require restarting without `--resume`. Other formats
retain review-only handoff extraction; unsupported EPUB providers fail explicitly.
