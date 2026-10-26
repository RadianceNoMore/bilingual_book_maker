#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Resume
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$bookPath = "C:\Users\Tina_\Downloads\CN_Let'sBecomeGoddessanDominateWorld.epub"

$translationArgs = @(
    '--book_name', $bookPath,
    '--api_format', 'openai',
    '--api_base', 'http://127.0.0.1:4995/v1',
    '--key', 'anon',
    '--model', 'gemini-anon',
    '--language', 'en',
    '--source_lang', 'zh',
    '--single_translate',
    '--plan-classify', 'none',
    '--translate-tags', 'h1,h2,p',
    '--whole-chapter',
    '--use_context', 'session',
    '--glossary-auto', 'on'
)

if ($Resume) {
    $translationArgs += '--resume'
}

Push-Location -LiteralPath $repoRoot
try {
    & python -X utf8 make_book.py @translationArgs
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode