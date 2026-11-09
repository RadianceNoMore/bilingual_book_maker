# Repository layout

The root contains project configuration, the main translation entry point, and
compatibility launchers. Tool implementations, examples, and working books have
separate homes.

```text
book_maker/               Python application package
  cli.py                  Main command-line interface
  loader/                 EPUB, PDF, Markdown, TXT, and subtitle loading
  translator/             Provider implementations and shared translation logic
  tools/                  Fallback runner, progress preview, metadata repairs
  batching.py             Request size policy
  glossary*.py            Vocabulary matching and learned-term review
  translation_*.py        Validation, consistency reports, and provenance
scripts/                  Canonical PowerShell launchers
examples/
  powershell/             Parameterized usage examples
  prompts/                Optional JSON, text, and PromptDown prompt examples
docs/                     User and development documentation
tests/                    Offline tests and explicitly marked provider/corpus tests
  fixtures/               Small test data
  hermetic/               Offline provider stand-ins for subprocess tests
test_books/               Existing sample books and test inputs
input/                    Working source copies, checkpoints, handoffs, review journals
output/                   Finished translated books and reports
ongoing/                  Unfinished protected-mode EPUB recoveries (sibling of output/)
log/                      Runtime logs
.agents/skills/           Repository-specific agent workflows
.github/workflows/        CI, documentation, image, and release automation
```

## Commands and compatibility

| Task | Implementation | Existing command |
| --- | --- | --- |
| Translate a book | `book_maker.cli` | `python make_book.py ...` or `python -m book_maker ...` |
| Current novel launcher | `scripts/run_grey_translation.ps1` | `./run_grey_translation.ps1 [-Resume]` |
| Multi-model PowerShell launcher | `scripts/translate_book.ps1` | `./translate_book.ps1 ...` |
| Gemini fallback runner | `book_maker/tools/make_gg_book.py` | `python make_gg_book.py ...` |
| Preview saved progress | `book_maker/tools/preview_progress.py` | `python preview_progress.py ...` |
| Repair older EPUB metadata | `book_maker/tools/fix_epub_metadata.py` | `python fix_epub_metadata.py ...` |
| Repair older English metadata | `book_maker/tools/fix_en_metadata.py` | `python fix_en_metadata.py ...` |

Python tools also run as modules, for example:

```powershell
python -m book_maker.tools.preview_progress --help
python -m book_maker.tools.fix_epub_metadata --help
```

Edit implementations in `scripts/` or `book_maker/tools/`. Root wrappers preserve
existing commands and Python tool imports. PowerShell wrappers retain the
parameter declarations for named-argument binding; update those declarations
together if a launcher adds or removes parameters.

## Configuration and examples

`pyproject.toml`, `requirements.txt`, `pdm.lock`, Docker files, and CI
configuration stay at the root where the build tools expect them.
`bbm_providers.example.json` and `prompt_template.json` remain root starter files.
Personal `bbm_providers.json` remains ignored and keeps its lookup location.

Other prompt examples live in `examples/prompts/`. Use their explicit paths in
new commands. The CLI still accepts the historical bare filenames when a local
file of that name does not exist; an existing user file always wins. Explicit
missing paths are errors and are not redirected.

The old `Example02.ps1` is now
`examples/powershell/accumulated_translation.ps1`. The old
`RunableExample.sh` contained PowerShell; its replacement is
`examples/powershell/provider_example.ps1`. These examples take a book path and
read credentials from an environment variable or an explicit parameter.

## Working data

Reorganization does not move source books, checkpoints, output books, logs,
environments, or caches. Existing tracked sample books stay where they are.
New work products in `input/`, `output/`, and `log/` are ignored by Git. The
Gemini fallback runner keeps its existing root `make_gg_book.skip.json` state
location, also ignored.

See [Translation workflow](translation-workflow.md) for resume behavior and
report files. Run the offline suite with `python -m pytest`; provider and corpus
tests are selected explicitly by marker. `make tests` uses the same default
suite, and `make fmt` requires Black in the selected Python environment.
