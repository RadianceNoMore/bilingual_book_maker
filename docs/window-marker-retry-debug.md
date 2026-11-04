# Invented markers during translation (text_189.txt)

## Evidence

The November 4 run extracted eight labels and translated three complete chunks.
In chunk 4 of the first window, the initial response omitted unit
`epub:4:chap_4.xhtml:45:0e04477babca8c67`. Four subsequent requests returned
invented markers: `${Iroha}`, `${user_name_1}`, `${...}`, and `${1:Iroha}`.
The prepared source for this unit contains no markers. Strict validation
correctly rejected all four responses, then stopped after five total attempts.
Provider metadata reports `stop`, without a refusal. This was a validation
failure, rather than a hang or a demonstrated token-limit failure.

The eight extracted labels include `御坂一色` and `御坂美琴`, but exclude the
short spellings `一色` and `美琴`. Exact replacement therefore left the short
spellings as ordinary source prose. The extraction passed structural and
literal-grounding checks; those checks cannot establish semantic completeness.

The saved recovery contains 159 accepted window units: 150 checkpoint jobs and
nine additional journal entries. The first window has 164 units. Its summary
has not yet been updated. The configured summary budget is 10500 tokens; it
was not the cause of this first-window failure.

## Fix

- Explain explicitly that units with no supplied markers must produce no markers.
- Send prepared-window repairs a source-derived list of exact marker counts per
  requested ID. An empty count object means zero markers. Raw rejected replies
  and validation error strings stay out of the provider prompt.
- Print each failed unit's ID, attempt, validation stage, and bounded reason.
  Include validation details in the final exhaustion exception.
- Preserve accepted siblings, the prepared catalogue, and existing resume behavior.

Focused offline verification: 107 tests passed across source windows, selective
paragraph retries, and marker workflow behavior. New regression cases exercise
the real provider adapter, zero-marker repair, missing-marker repair, accepted
sibling preservation, terminal diagnostics, and exhaustion details. No live
Gemini request was made; provider compliance still depends on its response.

## Resume

Run from `bilingual_book_maker-window-names` with the same source and budgets,
adding `-Resume`. The failed unit is retried, then remaining unprocessed units
continue. Completed units are reused and the prepared window is not extracted
again. The saved catalogue still omits the short spellings; this fix does not
retroactively add them or rewrite accepted translations.
