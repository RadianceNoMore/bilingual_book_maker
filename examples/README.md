# Examples

Run examples from a checkout. Pass a source book you want to translate; no
book-specific absolute paths or credentials are embedded in these scripts.

| File | Purpose |
| --- | --- |
| `powershell/accumulated_translation.ps1` | Use the novel launcher with an 800-token source budget |
| `powershell/provider_example.ps1` | Choose an OpenAI-compatible endpoint and model; reads `BBM_API_KEY` |
| `prompts/prompt_md.json` | Markdown translation prompt in JSON |
| `prompts/prompt_md.prompt.md` | Markdown translation prompt in PromptDown block form |
| `prompts/prompt_template_sample.txt` | Minimal text prompt template |
| `prompts/prompt_decensor.json` | Optional custom fiction prompt |

```powershell
./examples/powershell/accumulated_translation.ps1 -BookPath 'C:\Books\novel.epub' -Resume

$env:BBM_API_KEY = 'anon'
./examples/powershell/provider_example.ps1 -BookPath 'C:\Books\novel.epub'

python make_book.py --book_name article.md --prompt examples/prompts/prompt_md.json
```

The general three-section starter remains at
[`prompt_template.json`](../prompt_template.json). See
[repository layout](../docs/repository-layout.md) for commands and compatibility.
