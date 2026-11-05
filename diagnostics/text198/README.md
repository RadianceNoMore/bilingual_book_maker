# text198 diagnostics

All files here are read-only evidence from the text198 fix. No provider is
called by the reproduction scripts, and the preserved recovery copy is never
written back to `ongoing/`.

| File | Purpose |
| --- | --- |
| `recovery-preserved.epub` | Byte-for-byte copy of the text198 recovery EPUB |
| `recovery-manifest.json` | Source/copy SHA-256, run ID, window counts, durable counts |
| `preserve_recovery.py` | Rebuilds the copy and manifest from the newest recovery |
| `reproduce_assembly.py` | Rejects both client-boundary restart shapes; preserves a safe sibling |
| `assembly-reproduction.json` | Output of that parser reproduction |
| `reproduce_gateway_join.py` | Reproduces the local gateway joining distinct Gemini candidates |
| `gateway-join.json` | Output proving the gateway join and correct snapshot replacement |
| `gateway-candidate-join.patch` | Proposed one-line gateway fix; **not applied** |
| `live_probe.py` | One bounded singleton request through the local gateway |
| `live-probe.json` | Probe metadata and acceptance result |
| `live-trace/marker-trace.jsonl` | Full client-boundary request/response trace for that probe |
| `verification.txt` | Exact test commands, counts, digests |

## Upstream status

The local gateway at
`C:/Users/Tina_/Documents/PlayingWithRepo/AI-BRIDGE/gemini-pool/anonymous/server.py`
joins distinct Gemini candidates with two newlines in `_parse_generation`.
That reproduces the text198 response restart offline. The proposed patch is
not applied: it is a separate repository and a live service, and the choice of
candidate needs protocol review before deployment.

The client parser remains conservative and rejects both restart shapes.
