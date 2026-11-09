#Requires -Version 5.1
<#
.SYNOPSIS
Translate through a configurable OpenAI-compatible endpoint.
.EXAMPLE
$env:BBM_API_KEY = 'anon'
./examples/powershell/provider_example.ps1 -BookPath 'C:\Books\novel.epub'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BookPath,
    [string]$ApiBase = 'http://127.0.0.1:4995/v1',
    [string]$Model = 'gemini-anon',
    [string]$Key = $env:BBM_API_KEY,
    [switch]$Resume
)

$ErrorActionPreference = 'Stop'
if (-not $Key) { throw 'Set BBM_API_KEY or pass -Key.' }
$sourcePath = (Resolve-Path -LiteralPath $BookPath).Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$translationArgs = @(
    '--book_name', $sourcePath,
    '--api_format', 'openai', '--api_base', $ApiBase,
    '--key', $Key, '--model', $Model,
    '--language', 'en', '--source_lang', 'zh', '--single_translate',
    '--plan-classify', 'none', '--translate-tags', 'h1,h2,p',
    '--accumulated_num', '4000', '--request-max-units', '40',
    '--use_context', 'session', '--glossary-auto', 'on'
)
if ($Resume) { $translationArgs += '--resume' }
Push-Location -LiteralPath $repoRoot
try {
    & python -X utf8 make_book.py @translationArgs
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $exitCode
