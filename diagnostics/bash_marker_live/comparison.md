# Self-selected versus mechanically inserted source-name markers

One fresh live call to local gemini-anon on the same chunk 3 (40 units), without pre-inserted name markers, an English glossary, or prior history. Original XML paragraph IDs were retained. temperature=1.0; 17.7 seconds; no retries.

| Check | Pre-inserted markers | Model-selected markers |
| --- | --- | --- |
| Paragraph IDs aligned | 40/40 | 40/40 |
| Person/place references marked | 49/51 | 51/51 after manual alias matching |
| Other terms marked | 0 | 3: 净化, 原石, Level3 |
| Changes to original name spelling inside markers | 0 | 27 short-name/surname occurrences expanded |
| Chinese outside markers | 的原石 in unit 27 | None |
| Stray dollar signs | None | None |

The manually counted 51-reference set includes 食蜂 and 学园之舍, which were absent from the earlier premarking catalogue. Alias matching here is a diagnostic judgment, not an automatic production rule.

The model changed 黑子 to ${白井黑子} (14 occurrences), 美琴 to ${御坂美琴} (11), and 食蜂/操祈 to ${食蜂操祈} (2), despite an explicit instruction to retain exact original substrings. These expansions affect 21 paragraphs. The expanded 白井黑子 spelling does not occur anywhere in this original chunk.

This trial found no missing person/place reference after the manually verified alias equivalences. It did not demonstrate exact source-label retention. That matters when later review is supposed to resolve identities: the earlier translation model has already made that decision inside the marker.

Terminology coverage was inconsistent: 净化 and 原石 were marked, while 空间移动 and 水银 were translated normally. Level3 was marked but Lv.4 was translated normally. Which of those should count as proper terminology requires a declared scope, so they are not included in the person/place 51-reference denominator.

Conclusion: this is encouraging for simple marker structure and name discovery on this passage. One call cannot establish reliability. Full-name normalization must be checked against source or repaired to source forms before treating markers as reversible source labels. No EPUB, checkpoint, ledger, or production implementation was changed.

Files: self_marked.request.json, self_marked.response.json, self_marked.aligned.json, self_marked.results.json, comparison.audit.json. The premarked baseline remains chunk_3.*.json.
