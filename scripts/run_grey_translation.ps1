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

With the default -VeniceFallbackLimit (nonzero) the run also starts a local
refusal-fallback proxy (scripts/refusal_fallback_proxy.py) in front of the
gemini gateway: the proxy forwards every request to gemini-anon and re-sends
an individual chunk to the Venice guest bridge only when its Gemini reply is
the standalone policy refusal, starting the Venice gateway on demand. -Resume
still picks up where a refused run saved its progress.

Retryable provider/validation stage exhaustion is supervised automatically:
the saved EPUB is verified, then only unfinished work resumes after a cooldown.
Three consecutive resumes without durable book progress stop the supervisor.
Refusals, invalid configuration, interrupted runs, and failed saves stop directly.

.PARAMETER Resume
Resume the newest compatible partial recovery in "ongoing".

.PARAMETER BookPath
Source EPUB path.

.PARAMETER ChunkAttempts
Per-paragraph content-validation and independent response-protocol failure limit,
and display-mapping attempt limit
(default 5). Failed paragraphs are repaired together; accepted neighbours are
never retranslated with them.

.PARAMETER MaxUnits
Body request unit cap and pending display-mapping batch cap (default 40).

.PARAMETER MaxTokens
Body and mapping request token cap (default 4000).

.PARAMETER NameWindowTokens
Maximum original-language source tokens inspected in one name-extraction request (default 8192).

.PARAMETER SourceSummaryTokens
Maximum provider-reported output tokens in the cumulative original-language
story summary when available; otherwise the local estimate is used (default 1500).

.PARAMETER VeniceFallbackLimit
Maximum Gemini policy refusals this workflow may reroute to the Venice guest
bridge, counted in a rolling window (default 8 per 24 h; 0 disables the
fallback and keeps every request on gemini-anon). Venice is expensive, so only
a chunk whose Gemini reply is the standalone policy refusal ("I can help with
many kinds of requests... policy-guidelines...", or the structured refusal /
content-filter shapes this repository classifies the same way) is re-sent,
through scripts/refusal_fallback_proxy.py, to Venice. Everything else passes
through byte for byte. Refusals are logged to log/venice_fallback.log.

.PARAMETER VenicePort
Port of the Venice guest bridge (default 4996). When it is not running the
proxy starts it from -VeniceDir; a port held by a process whose /health is not
the Venice bridge is reported rather than trusted.

.PARAMETER FallbackPort
Port the refusal-fallback proxy listens on (default 4998). The translator's
--api_base points here while the fallback is active; a proxy already running
on the port is reused with its own settings.

.PARAMETER VeniceDir
Directory holding the Venice bridge's server.py (default
C:\Users\Tina_\Documents\PlayingWithRepo\AI-BRIDGE\Venice), used only to
launch it on demand; no AI-BRIDGE file is ever modified.

.PARAMETER GatewayPython
Python interpreter used to start the fallback proxy and, on demand, the
Venice bridge (default "python").
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
$exitCode = 1
$repoRoot = Split-Path -Parent $PSScriptRoot
$activeFallbackPort = $FallbackPort

# --- Venice refusal fallback (scripts/refusal_fallback_proxy.py) -------------
#
# gemini-anon stays the translator: with the fallback active the translator
# points at the local proxy, which forwards to gemini-anon and re-sends the
# rare standalone policy refusal to the Venice guest bridge, starting that
# gateway on demand. No AI-BRIDGE file is touched; server.py is only launched.

function Test-ServiceHealth {
    param(
        [Parameter(Mandatory)][string]$HealthUrl,
        [Parameter(Mandatory)][string]$ServicePattern,
        [switch]$RequireServing
    )
    try {
        $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 5
    }
    catch {
        return $false
    }
    if ($null -eq $health) {
        return $false
    }
    if ("$($health.service)" -notmatch $ServicePattern) {
        return $false
    }
    if ($RequireServing -and $health.ok -ne $true) {
        return $false
    }
    return $true
}

function Wait-ServiceHealth {
    param(
        [Parameter(Mandatory)][string]$HealthUrl,
        [Parameter(Mandatory)][string]$ServicePattern,
        [int]$Seconds,
        [switch]$RequireServing
    )
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-ServiceHealth -HealthUrl $HealthUrl -ServicePattern $ServicePattern -RequireServing:$RequireServing) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return (Test-ServiceHealth -HealthUrl $HealthUrl -ServicePattern $ServicePattern -RequireServing:$RequireServing)
}

function Start-VeniceFallbackGateway {
    # Serves when it can; every outcome the run can survive is survived with a
    # spoken warning, because a missing rescue must never stop the book.
    $healthUrl = "http://127.0.0.1:$VenicePort/health"
    if (Test-ServiceHealth -HealthUrl $healthUrl -ServicePattern 'venice' -RequireServing) {
        Write-Host "Venice gateway: already serving on port $VenicePort (reusing it)."
        return $true
    }
    if (Test-ServiceHealth -HealthUrl $healthUrl -ServicePattern 'venice') {
        [Console]::Error.WriteLine("Venice fallback: the gateway on port $VenicePort answers as Venice but not ok=true yet (its egress pool is still recovering); refusals may pass through until it recovers, and the proxy retries it per refusal.")
        return $true
    }
    $squatting = Get-NetTCPConnection -LocalPort $VenicePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $squatting) {
        [Console]::Error.WriteLine("Venice fallback: port $VenicePort is held by PID $($squatting.OwningProcess) but its /health is not the Venice bridge; stop that process or pass a different -VenicePort. Refusals will pass through unrescued.")
        return $false
    }
    if (-not (Test-Path -LiteralPath (Join-Path $VeniceDir 'server.py'))) {
        [Console]::Error.WriteLine("Venice fallback: no server.py in $VeniceDir; point -VeniceDir at the AI-BRIDGE\Venice checkout. Refusals will pass through unrescued.")
        return $false
    }
    Write-Host "Venice gateway: not running; starting it from $VeniceDir (port $VenicePort)..."
    Start-Process -FilePath $GatewayPython -ArgumentList @('-X', 'utf8', 'server.py', '--port', "$VenicePort") -WorkingDirectory $VeniceDir -WindowStyle Hidden | Out-Null
    if (Wait-ServiceHealth -HealthUrl $healthUrl -ServicePattern 'venice' -Seconds 90 -RequireServing) {
        Write-Host "Venice gateway: serving on port $VenicePort."
        return $true
    }
    [Console]::Error.WriteLine("Venice fallback: the gateway did not report ok=true within 90s (egress discovery takes a while on a cold pool). The proxy retries it whenever a refusal arrives; until then refusals pass through unrescued.")
    return $false
}

function Start-RefusalFallbackProxy {
    $healthUrl = "http://127.0.0.1:$activeFallbackPort/health"
    if (Test-ServiceHealth -HealthUrl $healthUrl -ServicePattern 'bbm-refusal-fallback-proxy') {
        $existingHealth = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5
        $existingGeminiTimeout = 0.0
        if ($null -ne $existingHealth.timeouts) {
            $existingGeminiTimeout = [double]$existingHealth.timeouts.gemini_seconds
        }
        if ($existingGeminiTimeout -lt 420.0) {
            $stalePort = $activeFallbackPort
            $alternativeFound = $false
            $lastCandidate = [Math]::Min(65535, $FallbackPort + 20)
            for ($candidatePort = ($FallbackPort + 1); $candidatePort -le $lastCandidate; $candidatePort++) {
                $candidateListener = Get-NetTCPConnection -LocalPort $candidatePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($null -eq $candidateListener) {
                    $activeFallbackPort = [int]$candidatePort
                    $script:activeFallbackPort = $activeFallbackPort
                    $healthUrl = "http://127.0.0.1:$activeFallbackPort/health"
                    $alternativeFound = $true
                    break
                }
            }
            if (-not $alternativeFound) {
                [Console]::Error.WriteLine("Refusal-fallback proxy: port $stalePort has an outdated $existingGeminiTimeout-second Gemini timeout and no free port was found in $($FallbackPort + 1)-$lastCandidate. Venice fallback is disabled for this run; gemini-anon will be used directly.")
                return $false
            }
            Write-Host "Refusal-fallback proxy: keeping the shared outdated listener on port $stalePort intact; starting a fresh 420-second proxy on port $activeFallbackPort."
        }
        else {
            Write-Host "Refusal-fallback proxy: already running on port $activeFallbackPort with Gemini timeout $existingGeminiTimeout seconds (reusing it)."
            return $true
        }
    }
    $squatting = Get-NetTCPConnection -LocalPort $activeFallbackPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $squatting) {
        [Console]::Error.WriteLine("Refusal-fallback proxy: port $activeFallbackPort is held by PID $($squatting.OwningProcess) and is not the fallback proxy; pass a different -FallbackPort. Refusals will pass through unrescued.")
        return $false
    }
    $proxyPath = Join-Path $PSScriptRoot 'refusal_fallback_proxy.py'
    if (-not (Test-Path -LiteralPath $proxyPath)) {
        [Console]::Error.WriteLine("Refusal-fallback proxy: $proxyPath is missing; run without the fallback or restore the file.")
        return $false
    }
    Write-Host "Refusal-fallback proxy: starting on port $activeFallbackPort with a 420-second Gemini timeout (forwards to gemini-anon on 4995, rescues refused chunks with Venice on $VenicePort)..."
    $argLine = @(
        '-X utf8',
        ('"' + $proxyPath + '"'),
        ('--listen-port ' + $activeFallbackPort),
        '--gemini-url http://127.0.0.1:4995/v1',
        ('--venice-url http://127.0.0.1:' + $VenicePort + '/v1'),
        ('--venice-dir "' + $VeniceDir + '"'),
        ('--venice-python "' + $GatewayPython + '"'),
        ('--venice-limit ' + $VeniceFallbackLimit),
        '--gemini-timeout 420',
        ('--log-file "' + $veniceFallbackLog + '"')
    ) -join ' '
    Start-Process -FilePath $GatewayPython -ArgumentList $argLine -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
    if (Wait-ServiceHealth -HealthUrl $healthUrl -ServicePattern 'bbm-refusal-fallback-proxy' -Seconds 30) {
        $startedHealth = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5
        if ([double]$startedHealth.timeouts.gemini_seconds -lt 420.0) {
            [Console]::Error.WriteLine("Refusal-fallback proxy: started on port $activeFallbackPort but reports only $($startedHealth.timeouts.gemini_seconds) seconds for Gemini; expected at least 420. Check the launcher and proxy version.")
            return $false
        }
        Write-Host "Refusal-fallback proxy: running on port $activeFallbackPort with a $($startedHealth.timeouts.gemini_seconds)-second Gemini timeout; decisions are logged to $veniceFallbackLog."
        return $true
    }
    [Console]::Error.WriteLine("Refusal-fallback proxy: it did not report a healthy /health within 30s on port $activeFallbackPort; see $veniceFallbackLog and the console above. Refusals will pass through unrescued (gemini-anon stays the translator).")
    return $false
}

$veniceFallbackLog = Join-Path $repoRoot 'log\venice_fallback.log'
$fallbackReady = $false
# The args-capture harness (tests/test_repository_layout.py, $env:BBM_TEST_CAPTURE)
# runs this launcher offline with python stubbed; starting real gateways there
# would make the default test selection non-hermetic, so the preflight is
# skipped exactly when capture mode is on and the run keeps the direct
# gemini-anon base. Outside the harness the fallback is the default.
$runningUnderCaptureHarness = -not [string]::IsNullOrWhiteSpace($env:BBM_TEST_CAPTURE)
if ($VeniceFallbackLimit -gt 0 -and -not $runningUnderCaptureHarness) {
    $null = Start-VeniceFallbackGateway
    if (Start-RefusalFallbackProxy) {
        $fallbackReady = $true
    }
}

$activeApiBase = 'http://127.0.0.1:4995/v1'
if ($fallbackReady) {
    $activeApiBase = "http://127.0.0.1:$activeFallbackPort/v1"
    Write-Host "Venice refusal fallback active: only the chunk whose Gemini reply is the standalone policy refusal is re-sent to the Venice bridge (limit $VeniceFallbackLimit per 24h); every other request - and every retryable provider error - stays on gemini-anon."
}
else {
    if ($VeniceFallbackLimit -gt 0 -and -not $runningUnderCaptureHarness) {
        [Console]::Error.WriteLine("Venice refusal fallback is OFF for this run: gemini-anon serves everything directly, exactly as before. A refusal stops the run with progress saved under ongoing; rerun with -Resume once the gateway situation is fixed.")
    }
    Write-Verbose "Venice refusal fallback disabled (VeniceFallbackLimit=$VeniceFallbackLimit; capture harness: $runningUnderCaptureHarness)."
}

$translationArgs = @(
    '--book_name', $bookPath,
    '--api_format', 'openai',
    '--api_base', $activeApiBase,
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
    & python -X utf8 -m book_maker.supervisor -- make_book.py @translationArgs
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
        [Console]::Error.WriteLine("Translation stopped with exit code $exitCode. Automatic recovery has either reached its no-progress limit or this failure requires intervention; see the supervisor and stage messages above. The saved recovery is under ongoing; -Resume continues its unfinished work.")
    }
    else {
        [Console]::Error.WriteLine("Translation stopped with exit code $exitCode. Review the Python error above and the saved recovery under the ongoing directory; rerun with -Resume after resolving the reported cause.")
    }
}
else {
    Write-Verbose "Translation completed successfully."
}

exit $exitCode
