param(
    [int]$SleepSeconds = 3600,
    [string]$PythonPath = "",
    [switch]$NoImmediateRun,
    [string]$LaunchAttemptId = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ShutdownGateScript = Join-Path $Root "ops\autostart\TradingOSRuntimeShutdownGate.ps1"
$PreviousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Stop"
    if (-not (Test-Path -LiteralPath $ShutdownGateScript -PathType Leaf -ErrorAction Stop)) { throw "Runtime shutdown gate is unavailable." }
    . $ShutdownGateScript
    $null = Get-Command Test-TradingOSRuntimeShutdownRequested -CommandType Function -ErrorAction Stop
} catch {
    throw "Runtime shutdown gate failed to load: $($_.Exception.Message)"
} finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}
if ($LaunchAttemptId) {
    try { $LaunchAttemptId = ([guid]$LaunchAttemptId).ToString() }
    catch { throw "LaunchAttemptId must be a valid non-empty GUID." }
    if ([guid]$LaunchAttemptId -eq [guid]::Empty) { throw "LaunchAttemptId must be a valid non-empty GUID." }
}
$ShutdownRequested = $true
try {
    $ShutdownGateResult = Test-TradingOSRuntimeShutdownRequested -Root $Root -AllowedAttemptId $LaunchAttemptId
    if ($ShutdownGateResult -is [bool] -and -not $ShutdownGateResult) { $ShutdownRequested = $false }
} catch { $ShutdownRequested = $true }
if ($ShutdownRequested) { exit 1 }
$LogDir = Join-Path $Root "logs\forward_paper_feed"
$LoopLockPath = Join-Path $LogDir "crowd_fade_observer_loop.lock.json"
$LoopStatusPath = Join-Path $LogDir "crowd_fade_observer_loop_status.json"
$OnceScript = Join-Path $Root "ops\autostart\Run-CrowdFadeObserverOnce.ps1"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-LoopStatus {
    param([string]$Status, [object]$Extra = $null)
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        live_trading_locked = $true
        creates_paper_entry_intents = $false
        sends_orders = $false
        extra = $Extra
    } | ConvertTo-Json -Depth 5

    # Google Drive or a concurrent dashboard read can briefly lock the status file.
    # Telemetry write contention must not terminate the observer loop.
    for ($Attempt = 1; $Attempt -le 10; $Attempt++) {
        try {
            $Payload | Set-Content -LiteralPath $LoopStatusPath -Encoding UTF8 -ErrorAction Stop
            return
        } catch {
            if ($Attempt -lt 10) {
                Start-Sleep -Milliseconds (100 * $Attempt)
            }
        }
    }
}

if ($SleepSeconds -lt 900) {
    throw "SleepSeconds must be at least 900 to protect public endpoints and avoid noisy duplicate checks."
}

if (Test-Path -LiteralPath $LoopLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LoopLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            Write-LoopStatus -Status "skipped_existing_loop" -Extra @{ existing_pid = $ExistingPid }
            exit 0
        }
    } catch {
        # Replace a stale or malformed lock.
    }
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
}

[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $LoopLockPath -Encoding UTF8

Write-LoopStatus -Status "running"
try {
    if (-not $NoImmediateRun) {
        Write-LoopStatus -Status "running_once"
        & $OnceScript -PythonPath $PythonPath | Out-Null
        $LastExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        Write-LoopStatus -Status "sleeping" -Extra @{ last_refresh_exit_code = $LastExitCode; next_run_after_seconds = $SleepSeconds }
    }
    while ($true) {
        Start-Sleep -Seconds $SleepSeconds
        Write-LoopStatus -Status "running_once"
        & $OnceScript -PythonPath $PythonPath | Out-Null
        $LastExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        Write-LoopStatus -Status "sleeping" -Extra @{ last_refresh_exit_code = $LastExitCode; next_run_after_seconds = $SleepSeconds }
    }
} finally {
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
    Write-LoopStatus -Status "stopped"
}
