<#
.SYNOPSIS
Translate one EPUB with the source-marker protected-name workflow.

.DESCRIPTION
Finished books are written to the configured output directory. Unfinished
checkpoints are written to an "ongoing" directory beside it, named
<source-stem>-<source-hash12>_bilingual_temp.epub. -Resume searches only that
directory, chooses the newest compatible partial recovery, and starts a fresh
run when none exists. A completed book in output is never treated as a recovery.
You may move an existing new-format partial EPUB into "ongoing" before resuming.

.PARAMETER Resume
Resume the newest compatible partial recovery in "ongoing".

.PARAMETER BookPath
Source EPUB path.

.PARAMETER ChunkAttempts
Per-paragraph marker-repair send limit and display-mapping attempt limit
(default 10). Failed paragraphs are repaired together; accepted neighbours are
never retranslated with them.

.PARAMETER MaxUnits
Body request unit cap and pending display-mapping batch cap (default 40).

.PARAMETER MaxTokens
Body and mapping request token cap (default 4000).

.PARAMETER GlossaryWindowTokens
Deprecated alias for the rolling context-compaction budget (default 8192).
--context-compact-at wins when both are supplied.
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Resume,
    [string]$BookPath = "C:\Users\Tina_\Downloads\CN_Let'sBecomeGoddessanDominateWorld.epub",
    [ValidateRange(1, 100)]
    [int]$ChunkAttempts = 10,
    [ValidateRange(1, 10000)]
    [int]$MaxUnits = 40,
    [ValidateRange(1, 1000000)]
    [int]$MaxTokens = 4000,
    [switch]$WholeChapter,
    [switch]$AccumulateAcrossChapters = $true,
    [switch]$TranslateFilename = $true,
    [switch]$ProtectedNames = $true,
    [ValidateRange(1, 1000000)]
    [int]$GlossaryWindowTokens = 8192
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
    '--max-chunk-attempts', "$ChunkAttempts"
)

if ($WholeChapter) {
    $translationArgs += '--whole-chapter'
}

if ($AccumulateAcrossChapters -and -not $WholeChapter) {
    $translationArgs += '--accumulate-across-chapters'
}

if ($Resume) {
    $translationArgs += '--resume'
}

if ($TranslateFilename) {
    $translationArgs += '--translate-filename'
}

if ($ProtectedNames) {
    $translationArgs += @('--protected-names', '--glossary-window-tokens', "$GlossaryWindowTokens")
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
