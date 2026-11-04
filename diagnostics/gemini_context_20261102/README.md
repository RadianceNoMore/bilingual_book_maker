# Local Gemini context comparison

Date: 2026-11-02. Endpoint: local gemini-anon service.

The same two manually inspected, ordinary Chinese paragraphs from source window 2, units 40–41 were used in every request. Production protected-name/XML framing was used.

| Case | Valid translations | Seconds |
| --- | --- | --- |
| xml_only | 3/3 | 27.02, 7.93, 17.09 |
| preceding_context | 3/3 | 25.88, 38.53, 14.65 |
| checked_handoff_seed | 3/3 | 6.57, 16.51, 6.1 |

Each success passed paragraph-ID parsing, protected-name restoration and translation-language validation; HTTP 200 alone was not counted as success.

The preceding-context case supplied eight original preceding units. The history case added only the manually inspected, benign earlier handoff seed. The old full-window summary and the complete old conversation were not sent. Consequently this comparison does not establish the cause of the original refusal or guarantee acceptance of later passages.

All nine requests and raw responses are stored alongside results.json. The book ledger SHA-256 was identical before and after testing. The production checkpoint was not used or modified.

Offline verification: 615 regression tests passed (three existing XML parser warnings). Replaying the old failure log recognized all 25 explicit paragraph refusals without network calls.
