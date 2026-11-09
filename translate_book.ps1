#Requires -Version 5.1
<#
.SYNOPSIS
  Robust whole-EPUB translation for bilingual_book_maker.

.DESCRIPTION
  Wraps `python make_book.py` for one EPUB and makes a long free-tier run
  survivable:

    * forces UTF-8 I/O, so echoing Korean/Thai source text cannot crash a
      cp1252 Windows console;
    * translates the WHOLE book (plan mode, every text node) instead of just
      <p>, unless you pass -PlanClassify none;
    * resumes automatically: the first run starts clean and --resume is added
      ONLY when a checkpoint actually exists (a blind --resume crashes with
      "can not load resume file");
    * falls back across a model list and retries on failure, clearing the
      checkpoint's run_fingerprint so another model may continue the same book
      (the finished book mixes models by design; -NoStripFingerprint forbids it);
    * logs everything to <stem>_translate.log;
    * after success, localizes the EPUB title, chapter titles, TOC and metadata
      with fix_epub_metadata.py (body translation never touches those).

.EXAMPLE
  .\translate_book.ps1 -Book "output\1935, ....epub" -Language th -SourceLang ko `
      -Models "qwen/qwen3.8-27b:free","qwen/qwen3-30b-a3b:free"

.EXAMPLE
  # tag mode instead of plan mode, one model, preview only
  .\translate_book.ps1 -Book book.epub -PlanClassify none -Models "qwen/qwen3.8-27b:free" -Test
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$Book,

  [string]$Language = "th",
  [string]$SourceLang = "",

  [string[]]$Models = @("qwen/qwen3.8-27b:free"),
  [string]$Key = "",
  [string]$ApiBase = "https://openrouter.ai/api/v1",

  [string]$Prompt = "prompt_decensor.json",
  [ValidateSet("auto", "none", "all", "model", "agent")]
  [string]$PlanClassify = "all",
  [int]$AccumulatedNum = 800,

  [int]$MaxAttempts = 2,
  [int]$RetryDelaySeconds = 8,

  [switch]$SingleTranslate,
  [switch]$SkipMetadataFix,
  [switch]$Test,
  [switch]$NoStripFingerprint,
  [switch]$DryRun,

  [string]$Python = ""
)

$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0
# Compatibility launcher; edit scripts/translate_book.ps1 for implementation changes.
& (Join-Path $PSScriptRoot 'scripts\translate_book.ps1') @PSBoundParameters
exit $LASTEXITCODE
