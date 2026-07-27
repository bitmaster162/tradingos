param(
    [int]$SleepSeconds = 10,
    [string]$PythonPath = "",
    [string]$LaunchAttemptId = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ShutdownGateScript = Join-Path $Root "ops\autostart\TradingOSRuntimeShutdownGate.ps1"
if (-not (Test-Path -LiteralPath $ShutdownGateScript -PathType Leaf)) {
    throw "Runtime shutdown gate is unavailable."
}
. $ShutdownGateScript
$null = Get-Command Test-TradingOSRuntimeShutdownRequested -CommandType Function -ErrorAction Stop
if ($LaunchAttemptId) {
    try { $LaunchAttemptId = ([guid]$LaunchAttemptId).ToString() }
    catch { throw "LaunchAttemptId must be a valid non-empty GUID." }
    if ([guid]$LaunchAttemptId -eq [guid]::Empty) {
        throw "LaunchAttemptId must be a valid non-empty GUID."
    }
}
$ShutdownRequested = $true
try {
    $ShutdownGateResult = Test-TradingOSRuntimeShutdownRequested -Root $Root -AllowedAttemptId $LaunchAttemptId
    if ($ShutdownGateResult -is [bool] -and -not $ShutdownGateResult) { $ShutdownRequested = $false }
} catch {
    $ShutdownRequested = $true
}
if ($ShutdownRequested) { exit 1 }
if ($SleepSeconds -lt 5) { throw "SleepSeconds must be at least 5." }

$LogDir = Join-Path $Root "logs\binance_spot_perp_aggressor_flow"
$LockPath = Join-Path $LogDir "binance_spot_perp_aggressor_flow_loop.lock.json"
$StatusPath = Join-Path $LogDir "binance_spot_perp_aggressor_flow_loop_status.json"
$StdoutPath = Join-Path $LogDir "binance_spot_perp_aggressor_flow_stdout.log"
$StderrPath = Join-Path $LogDir "binance_spot_perp_aggressor_flow_stderr.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    if ($Requested -and (Test-Path -LiteralPath $Requested)) { return $Requested }
    if ($env:TRADING_OS_PYTHON -and (Test-Path -LiteralPath $env:TRADING_OS_PYTHON)) {
        return $env:TRADING_OS_PYTHON
    }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) { return $Python.Source }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython) { return $HermesPython }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

function Write-Status {
    param(
        [string]$Status,
        [int]$ExitCode = 0,
        [int]$CycleDurationMilliseconds = 0,
        [int]$NextSleepMilliseconds = 0
    )
    [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        cycle_duration_milliseconds = $CycleDurationMilliseconds
        next_sleep_milliseconds = $NextSleepMilliseconds
        cadence_policy = "anchored_start_to_start"
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        collector_only = $true
        public_data_only = $true
        credentials_allowed = $false
        hypothesis_registered = $false
        strategy_search_allowed = $false
        signals_allowed = $false
        paper_entries_allowed = $false
        telegram_send_allowed = $false
        orders_allowed = $false
        can_trade = $false
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

if (Test-Path -LiteralPath $LockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            Write-Status -Status "skipped_existing_binance_spot_perp_aggressor_flow_loop"
            exit 0
        }
    } catch {
        # Replace only a stale or malformed local lock.
    }
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
    collector_only = $true
    public_data_only = $true
    can_trade = $false
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LockPath -Encoding UTF8

$Python = Get-PreferredPython -Requested $PythonPath
$Arguments = @(
    "tools\binance_spot_perp_aggressor_flow_collector.py",
    "--contract", "configs\BINANCE_SPOT_PERP_AGGRESSOR_FLOW_COLLECTION_CONTRACT_2026-07-15.json",
    "--out-dir", "data\binance_spot_perp_aggressor_flow",
    "--report-prefix", "docs\BINANCE_SPOT_PERP_AGGRESSOR_FLOW_DATA_QUALITY_2026-07-15",
    "--max-backfill-pages", "20"
)

try {
    while ($true) {
        $CycleStarted = [DateTimeOffset]::UtcNow
        Push-Location $Root
        try {
            Write-Status -Status "running_collection_cycle"
            $PreviousErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                & $Python @Arguments >> $StdoutPath 2>> $StderrPath
                $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            } finally {
                $ErrorActionPreference = $PreviousErrorActionPreference
            }
        } finally {
            Pop-Location
        }
        $CycleDurationMilliseconds = [int][Math]::Ceiling(
            ([DateTimeOffset]::UtcNow - $CycleStarted).TotalMilliseconds
        )
        $NextSleepMilliseconds = [int][Math]::Max(
            1000,
            ($SleepSeconds * 1000) - $CycleDurationMilliseconds
        )
        Write-Status `
            -Status $(if ($ExitCode -eq 0) { "sleeping" } else { "sleeping_after_collection_failure" }) `
            -ExitCode $ExitCode `
            -CycleDurationMilliseconds $CycleDurationMilliseconds `
            -NextSleepMilliseconds $NextSleepMilliseconds
        Start-Sleep -Milliseconds $NextSleepMilliseconds
    }
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Write-Status -Status "stopped"
}
