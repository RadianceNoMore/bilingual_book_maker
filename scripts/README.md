# PowerShell launchers

| Script | Purpose |
| --- | --- |
| `run_grey_translation.ps1` | Current novel and local Gemini bridge; bounded paragraph batches and optional resume |
| `translate_book.ps1` | General multi-model wrapper with retry and resume options |

The corresponding root scripts forward parameters here, so existing terminal
commands remain valid. Both canonical launchers locate the application relative
to this directory. Use `Get-Help ./scripts/SCRIPT.ps1 -Full` for parameters.

See [translation workflow](../docs/translation-workflow.md) for the current
novel launcher and [repository layout](../docs/repository-layout.md) for the
rest of the project.
