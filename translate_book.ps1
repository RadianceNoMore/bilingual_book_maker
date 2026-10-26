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

$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
  $PSNativeCommandUseErrorActionPreference = $false
}

# ---- UTF-8 everywhere (the console is the usual Windows failure point) -------
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
try { $OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Add-Log([string]$Text) {
  [IO.File]::AppendAllText($script:LogPath, $Text + [Environment]::NewLine, $utf8NoBom)
}

function Write-Log([string]$Text) {
  Write-Host $Text
  Add-Log $Text
}

# ---- locate python ----------------------------------------------------------
if (-not $Python) {
  foreach ($c in @("python", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source; break }
  }
}
if (-not $Python) { throw "python not found on PATH; pass -Python <full path to python.exe>" }

# ---- locate the book and the repo ------------------------------------------
if (-not (Test-Path -LiteralPath $Book)) { throw "book not found: $Book" }
$BookPath = (Resolve-Path -LiteralPath $Book).Path
$bookDir = Split-Path -Parent $BookPath
$stem = [IO.Path]::GetFileNameWithoutExtension($BookPath)

$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location $RepoRoot
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "make_book.py"))) {
  throw "make_book.py not found next to this script ($RepoRoot). Put translate_book.ps1 in the repo root."
}

$script:Checkpoint = Join-Path $bookDir ".$stem.temp.bin"
$OutEpub = Join-Path $bookDir "${stem}_bilingual.epub"
$script:LogPath = Join-Path $bookDir "${stem}_translate.log"

if (-not $Key) { $Key = $env:OPENROUTER_API_KEY }
if (-not $Key) { throw "no API key: pass -Key or set `$env:OPENROUTER_API_KEY" }

# Accept "a,b", "a","b", or a mix (powershell -File passes args as strings).
$Models = @($Models | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($Models.Count -eq 0) { throw "-Models is empty" }

if ($Prompt -and -not (Test-Path -LiteralPath $Prompt)) {
  Write-Warning "prompt file '$Prompt' not found; running without --prompt"
  $Prompt = ""
}

# Clear run_fingerprint so a DIFFERENT model is allowed to --resume the same
# book (the loader refuses a model change otherwise). Mirrors make_gg_book.py.
$StripPy = @'
import pickle, shutil, sys
from pathlib import Path
p = Path(sys.argv[1])
try:
    with open(p, "rb") as f:
        state = pickle.load(f)
except Exception as e:
    print("STRIP-ERROR", e); sys.exit(0)
if not isinstance(state, dict) or "run_fingerprint" not in state:
    print("STRIP-NOOP"); sys.exit(0)
bak = p.with_suffix(p.suffix + ".bak")
try:
    if not bak.is_file():
        shutil.copy2(p, bak)
    del state["run_fingerprint"]
    with open(p, "wb") as f:
        pickle.dump(state, f)
    print("STRIPPED")
except Exception as e:
    print("STRIP-ERROR", e)
'@

function Clear-RunFingerprint {
  if (-not (Test-Path -LiteralPath $script:Checkpoint)) { return }
  try {
    $out = $StripPy | & $Python - $script:Checkpoint 2>&1
    Write-Host "[strip] $out"
    Add-Log "[strip] $out"
  } catch {
    Write-Warning "could not strip run_fingerprint: $_"
  }
}

function Invoke-MakeBook([string[]]$ArgList) {
  Write-Log ("`n[run] {0} {1}" -f $Python, ($ArgList -join " "))
  if ($DryRun) { return 0 }
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"   # native stderr must not throw
  try {
    & $Python @ArgList 2>&1 | Tee-Object -FilePath $script:LogPath -Append | ForEach-Object { Write-Host $_ }
    return $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $prev
  }
}

# ---- translate --------------------------------------------------------------
$ckptState = if (Test-Path -LiteralPath $script:Checkpoint) { "  (exists -> resume)" } else { "  (none -> fresh run)" }
Write-Log "book        : $BookPath"
Write-Log "output      : $OutEpub"
Write-Log "checkpoint  : $($script:Checkpoint)$ckptState"
Write-Log "models      : $($Models -join ', ')"
Write-Log "plan mode   : $PlanClassify   accumulated_num: $AccumulatedNum"
Write-Log "log         : $($script:LogPath)"

$succeeded = $false
$usedModel = ""

for ($mi = 0; $mi -lt $Models.Count -and -not $succeeded; $mi++) {
  $model = $Models[$mi]
  for ($attempt = 1; $attempt -le $MaxAttempts -and -not $succeeded; $attempt++) {
    $useResume = Test-Path -LiteralPath $script:Checkpoint

    # switching models mid-book is the one case the loader refuses
    if ($useResume -and $mi -gt 0 -and -not $NoStripFingerprint) {
      Clear-RunFingerprint
    }

    $args = @(
      "make_book.py",
      "--book_name", $BookPath,
      "--api_base", $ApiBase,
      "--key", $Key,
      "--model", $model,
      "--language", $Language,
      "--accumulated_num", "$AccumulatedNum"
    )
    if ($SourceLang) { $args += @("--source_lang", $SourceLang) }
    if ($SingleTranslate) { $args += "--single_translate" }
    if ($Prompt) { $args += @("--prompt", $Prompt) }
    if ($PlanClassify) { $args += @("--plan-classify", $PlanClassify) }
    if ($Test) { $args += "--test" }
    if ($useResume) { $args += "--resume" }

    Write-Log ("`n=== model {0}/{1} '{2}', attempt {3}/{4} ({5}) ===" -f `
        ($mi + 1), $Models.Count, $model, $attempt, $MaxAttempts, $(if ($useResume) { "resume" } else { "fresh" }))

    $rc = Invoke-MakeBook $args

    if ($DryRun) { break }              # nothing ran; just print every attempt

    if ($rc -eq 0) {
      $succeeded = $true
      $usedModel = $model
      Write-Log "[ok] translation finished on $model"
    }
    else {
      Write-Log "[fail] exit $rc on $model (attempt $attempt)"
      if ($attempt -lt $MaxAttempts) { Start-Sleep -Seconds $RetryDelaySeconds }
    }
  }
}

if ($DryRun) {
  Write-Log "`n[dry-run] commands printed above; nothing was executed."
  return
}

if (-not $succeeded) {
  Write-Log "`n[abort] every model/attempt failed. Progress is saved in the checkpoint."
  Write-Log "Rerun the SAME command to continue with --resume (it will)."
  exit 1
}
if (-not (Test-Path -LiteralPath $OutEpub)) {
  Write-Log "[warn] exit 0 but $OutEpub is missing; check the log."
  exit 1
}

# ---- localize title / chapter titles / TOC / metadata ------------------------
if (-not $SkipMetadataFix) {
  $fix = Join-Path $RepoRoot "fix_epub_metadata.py"
  if (Test-Path -LiteralPath $fix) {
    Write-Log "`n[fix] localizing title/TOC/metadata -> $Language"
    & $Python $fix $OutEpub --lang $Language 2>&1 | Tee-Object -FilePath $script:LogPath -Append | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Warning "fix_epub_metadata.py exited $LASTEXITCODE" }
  } else {
    Write-Warning "fix_epub_metadata.py not found; skipping metadata localization"
  }
}

Write-Log "`n[done] $OutEpub   (model: $usedModel)"
