#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Resume,
    [string]$BookPath = "C:\Users\Tina_\Downloads\CN_Let'sBecomeGoddessanDominateWorld.epub",
    [ValidateRange(1, 100)]
    [int]$ChunkAttempts = 5,
    [ValidateRange(1, 10000)]
    [int]$MaxUnits = 40,
    [ValidateRange(1, 1000000)]
    [int]$MaxTokens = 4000,
    [switch]$WholeChapter,
    [switch]$AccumulateAcrossChapters = $true,
    [switch]$TranslateFilename = $true
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

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
    '--accumulated_num', "$MaxTokens",
    '--request-max-units', "$MaxUnits",
    '--request-max-tokens', "$MaxTokens",
    '--use_context', 'session',
    '--glossary-auto', 'on',
    '--max-chunk-attempts', "$ChunkAttempts"
)

if ($WholeChapter) {
    $translationArgs += '--whole-chapter'
}

if ($AccumulateAcrossChapters) {
    $translationArgs += '--accumulate-across-chapters'
}

if ($Resume) {
    $translationArgs += '--resume'
}

if ($TranslateFilename) {
    $translationArgs += '--translate-filename'
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
