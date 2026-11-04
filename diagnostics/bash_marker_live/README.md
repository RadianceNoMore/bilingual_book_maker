# Live Gemini test: ${original name}

Random seed: 1805562949. Sampled three 40-unit chunks from 78 ordinary narrative chunks after screening; not an unfiltered sample. Source selection and exact requests/responses are retained here.

Names and places from the existing approved source catalogue were marked using longest-match literal replacement. No English name renderings, occurrence IDs, full-window summary, or conversation history were supplied. Paragraph XML IDs were retained. One live request per chunk; no retries.

| Chunk | Units | Estimated source tokens | Markers in/out | Seconds |
| --- | --- | --- | --- | --- |
| 1 | 40 | 574 | 38/38 | 71.91 |
| 2 | 40 | 521 | 20/20 | 94.99 |
| 3 | 40 | 716 | 49/49 | 55.68 |

All three responses parsed as 40 correctly identified paragraphs. All 107 markers retained their exact source names and per-paragraph counts, with no unknown markers, missing markers or invented name XML.

Manual/audit caveat: chunk 1 unit 14 has `${黑子}$`, an intact marker followed by an extra dollar sign. The initial marker-count check did not flag this; audit.json records stray syntax separately. This is a small formatting defect, not a missing name or occurrence-ID failure.

Coverage caveat: aliases absent from the source catalogue were not marked. For example, 泪子 became Ruiko and 佐天同学 became classmate Saten in chunk 1. The test demonstrates retention of supplied markers, not automatic detection of every name or overall translation correctness.

Scope: three modest chunks (521–716 estimated source tokens) are encouraging but do not establish a whole-book success rate or a statistically controlled improvement over XML. No production translation code, EPUB, ledger, or checkpoint was edited.

Translation completeness caveat: chunk 3 unit 27 returned `的原石 abilities are really incredible` outside any protected marker. This is untranslated prose/terminology, and a marker-only pass must not be mistaken for a clean translation. audit.json separately lists Chinese outside valid markers.
