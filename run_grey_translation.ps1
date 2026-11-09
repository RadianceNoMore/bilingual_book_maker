<#
.SYNOPSIS
Compatibility launcher for the protected-name EPUB workflow.

.DESCRIPTION
Finished books go to output; unfinished checkpoints go to the sibling
"ongoing" directory. -Resume picks the newest compatible partial recovery in
"ongoing" and starts fresh when none is available. With the default
-VeniceFallbackLimit, Gemini-refused chunks are rescued by the Venice guest
bridge (started on demand) through scripts/refusal_fallback_proxy.py. See
scripts/run_grey_translation.ps1 for all parameters.
#>
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
    [switch]$TranslateFilename = $true,
    [switch]$ProtectedNames = $true,
    [ValidateRange(1, 1000000)]
    [int]$NameWindowTokens = 8192,
    [ValidateRange(1, 1000000)]
    [int]$SourceSummaryTokens = 1500,
    [ValidateRange(0, 10000)]
    [int]$VeniceFallbackLimit = 8,
    [ValidateRange(1024, 65535)]
    [int]$VenicePort = 4996,
    [ValidateRange(1024, 65535)]
    [int]$FallbackPort = 4998,
    [string]$VeniceDir = "C:\Users\Tina_\Documents\PlayingWithRepo\AI-BRIDGE\Venice",
    [string]$GatewayPython = "python"
)

$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0
# Compatibility launcher; edit scripts/run_grey_translation.ps1 for implementation changes.
& (Join-Path $PSScriptRoot 'scripts\run_grey_translation.ps1') @PSBoundParameters
exit $LASTEXITCODE
