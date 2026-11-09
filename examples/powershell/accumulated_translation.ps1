#Requires -Version 5.1
<#
.SYNOPSIS
Translate a novel in batches capped at 800 estimated source tokens.
.EXAMPLE
./examples/powershell/accumulated_translation.ps1 -BookPath 'C:\Books\novel.epub' -Resume
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BookPath,
    [switch]$Resume
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
& (Join-Path $repoRoot 'scripts/run_grey_translation.ps1') `
    -BookPath $BookPath -MaxTokens 800 -Resume:$Resume
exit $LASTEXITCODE
