# Live marker validation — 2026-11-02

Endpoint: `http://127.0.0.1:4995/v1`  
Model: `gemini-anon`  
Temperature: `0` (the CLI exposes no separate seed flag)  
Target/source: English / Chinese

Command, run from the repository root with a temporary workspace:

```powershell
python -X utf8 make_book.py `
  --book_name "$env:TEMP\opencode\bbm-live\book.epub" `
  --api_format openai --api_base http://127.0.0.1:4995/v1 `
  --key anon --model gemini-anon `
  --language en --source_lang zh `
  --protected-names --request-max-units 1 --request-max-tokens 1000 `
  --temperature 0
```

The source contains three ordinary narrative paragraphs. The endpoint made six
requests: three body chunks, two EPUB title/metadata labels, and one display
mapping batch. The three body exchanges are stored in the output bundle's
`META-INF/bbm-marker-state.json` under `session.exchanges`; their paragraph IDs
are `p6b373b56f0b2_0`, `pab1230433203_0`, and `pf6428a153aac_0`.

## Results

- Body chunks: 3/3 accepted.
- Marker coverage: 5/5 source occurrences
  (`御坂美琴` ×3, `白井黑子` ×2); no marker was moved or expanded.
- Catalogue: 2 labels, both mapped
  (`御坂美琴` → `Mikoto Misaka`, `白井黑子` → `Kuroko Shirai`).
- Prose: all three English replies translate the surrounding narrative; no
  untranslated Chinese prose survived outside markers.
- Output: one CSS bundle with two name spans in the translated chapter, one
  shared stylesheet, and no external checkpoint/glossary/handoff sidecars.

## Rendering check

Calibre 9.17.0 converted `live_bilingual.epub` to `live_render.pdf`. Extracted
PDF text shows `Mikoto Misaka` and `Kuroko Shirai` in the translated paragraphs
and no source-script names there, confirming that Calibre rendered the
`::before` content and hid `.bbm-original`. This is a Calibre rendering check,
not an exhaustive test of every EPUB reader. Search/copy/accessibility behavior
for CSS-generated text still varies by reader.

## Files

- `live_source.epub` — the three-paragraph source.
- `live_bilingual.epub` — final single CSS EPUB with embedded state.
- `live_catalogue.json` — exported authoritative catalogue.
- `live_render.pdf` — Calibre conversion used for the rendering check.
