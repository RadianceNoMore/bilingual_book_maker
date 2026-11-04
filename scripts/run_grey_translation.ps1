<#
.SYNOPSIS
Translate one EPUB with source-window name extraction and cumulative original-language context.

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
(default 5). Failed paragraphs are repaired together; accepted neighbours are
never retranslated with them.

.PARAMETER MaxUnits
Body request unit cap and pending display-mapping batch cap (default 40).

.PARAMETER MaxTokens
Body and mapping request token cap (default 4000).

.PARAMETER NameWindowTokens
Maximum original-language source tokens inspected in one name-extraction request (default 8192).

.PARAMETER SourceSummaryTokens
Maximum estimated tokens in the cumulative original-language story summary (default 1500).
#>
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
    [switch]$TranslateFilename = $true,
    [switch]$ProtectedNames = $true,
    [ValidateRange(1, 1000000)]
    [int]$NameWindowTokens = 8192,
    [ValidateRange(1, 1000000)]
    [int]$SourceSummaryTokens = 1500
)

$ErrorActionPreference = 'Stop'
$exitCode = 1
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
    $translationArgs += @(
        '--protected-names',
        '--name-window-tokens', "$NameWindowTokens",
        '--source-summary-tokens', "$SourceSummaryTokens"
    )
}

if ($ProtectedNames) {
    Write-Host "Source-window workflow: name extraction runs once over each original-source window, then names are bracketed locally, chunks are translated with the prior original-language summary, and that summary is updated after the window is complete."
    Write-Host "Budgets: name window=$NameWindowTokens tokens; translation chunk=$MaxTokens tokens; cumulative source summary=$SourceSummaryTokens tokens."
}
Write-Verbose "Resume=$Resume; ProtectedNames=$ProtectedNames; MaxUnits=$MaxUnits; ChunkAttempts=$ChunkAttempts; BookPath=$BookPath"
Write-Verbose "NameWindowTokens=$NameWindowTokens; SourceSummaryTokens=$SourceSummaryTokens; RequestMaxTokens=$MaxTokens"

Push-Location -LiteralPath $repoRoot
try {
    Write-Verbose "Starting make_book.py with the configured translation route."
    & python -X utf8 make_book.py @translationArgs
    if ($null -ne $LASTEXITCODE) {
        $exitCode = $LASTEXITCODE
    }
}
catch {
    $exitCode = 1
    [Console]::Error.WriteLine("Could not start or run make_book.py: $($_.Exception.Message)")
}
finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    if ($ProtectedNames) {
        [Console]::Error.WriteLine("Translation stopped with exit code $exitCode. Review the last 'Protected names:' stage message for the window, unit IDs, failed validation, and saved-progress details. The run keeps its recovery under the ongoing directory; rerun with -Resume after resolving the reported cause.")
    }
    else {
        [Console]::Error.WriteLine("Translation stopped with exit code $exitCode. Review the Python error above and the saved recovery under the ongoing directory; rerun with -Resume after resolving the reported cause.")
    }
}
else {
    Write-Verbose "Translation completed successfully."
}

exit $exitCode
