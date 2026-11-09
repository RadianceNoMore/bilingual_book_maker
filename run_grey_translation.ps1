#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Resume,
    [string]$BookPath = "placeholder.epub",
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
$global:LASTEXITCODE = 0
# Compatibility launcher; edit scripts/run_grey_translation.ps1 for implementation changes.
& (Join-Path $PSScriptRoot 'scripts\run_grey_translation.ps1') @PSBoundParameters
exit $LASTEXITCODE
