param(
    [int]$SleepSeconds = 60,
    [string]$PythonPath = "",
    [string]$LaunchAttemptId = ""
)

$ErrorActionPreference = "Continue"
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
$LogDir = Join-Path $Root "logs\cex_dex_funding"
$LockPath = Join-Path $LogDir "cex_dex_funding_collector_loop.lock.json"
$StatusPath = Join-Path $LogDir "cex_dex_funding_collector_loop_status.json"
$StdoutPath = Join-Path $LogDir "cex_dex_funding_collector_stdout.log"
$StderrPath = Join-Path $LogDir "cex_dex_funding_collector_stderr.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if ($SleepSeconds -lt 60) {
    throw "SleepSeconds must be at least 60."
}

function Get-PreferredPython {
    param([string]$Requested)
    if ($Requested -and (Test-Path -LiteralPath $Requested)) {
        return $Requested
    }
    $SystemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($SystemPython) {
        return $SystemPython.Source
    }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython) {
        return $HermesPython
    }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

function Write-Status {
    param(
        [string]$Status,
        [int]$ExitCode = 0,
        [int]$PrimaryExitCode = 0,
        [int]$ReplicationExitCode = 0,
        [int]$AlignmentExitCode = 0,
        [int]$ReadinessExitCode = 0,
        [int]$CycleDurationMilliseconds = 0,
        [int]$NextSleepMilliseconds = 0
    )
    [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        primary_exit_code = $PrimaryExitCode
        direct_replication_exit_code = $ReplicationExitCode
        source_alignment_exit_code = $AlignmentExitCode
        research_readiness_exit_code = $ReadinessExitCode
        cycle_duration_milliseconds = $CycleDurationMilliseconds
        next_sleep_milliseconds = $NextSleepMilliseconds
        cadence_policy = "anchored_start_to_start"
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        collector_only = $true
        credentials_allowed = $false
        signals_allowed = $false
        orders_allowed = $false
        can_trade = $false
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

if (Test-Path -LiteralPath $LockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            Write-Status -Status "skipped_existing_cex_dex_funding_collector_loop"
            exit 0
        }
    } catch {
        # Replace stale or malformed lock below.
    }
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
    collector_only = $true
    can_trade = $false
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LockPath -Encoding UTF8

$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
$Arguments = @(
    "tools\hyperliquid_cross_venue_funding_collector.py",
    "--contract", "configs\CEX_DEX_FUNDING_LEAD_LAG_PREREG_2026-07-13.json",
    "--journal", "data\forward\cex_dex_funding_lead_lag\funding_snapshots.jsonl",
    "--out-prefix", "docs\CEX_DEX_FUNDING_LEAD_LAG_DATA_QUALITY_2026-07-13"
)
$ReplicationArguments = @(
    "tools\direct_cex_funding_replication_collector.py",
    "--contract", "configs\CEX_FUNDING_DIRECT_REPLICATION_PREREG_2026-07-13.json",
    "--journal", "data\forward\cex_dex_funding_lead_lag\direct_cex_funding_snapshots.jsonl",
    "--out-prefix", "docs\CEX_FUNDING_DIRECT_REPLICATION_DATA_QUALITY_2026-07-13"
)
$AlignmentArguments = @(
    "tools\cex_funding_source_alignment_monitor.py",
    "--lock", "configs\CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V3_2026-07-14.json",
    "--out-prefix", "docs\CEX_FUNDING_SOURCE_ALIGNMENT_V3_2026-07-14"
)
$ReadinessArguments = @(
    "tools\cex_funding_research_readiness_monitor.py",
    "--alignment-lock", "configs\CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V3_2026-07-14.json",
    "--alignment-report", "docs\CEX_FUNDING_SOURCE_ALIGNMENT_V3_2026-07-14.json",
    "--out-prefix", "docs\CEX_FUNDING_RESEARCH_READINESS_2026-07-13"
)

try {
    while ($true) {
        $CycleStarted = [DateTimeOffset]::UtcNow
        $NextSleepMilliseconds = $SleepSeconds * 1000
        Push-Location $Root
        try {
            Write-Status -Status "running_collection_cycle"
            & $Python @Arguments >> $StdoutPath 2>> $StderrPath
            $PrimaryExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            & $Python @ReplicationArguments >> $StdoutPath 2>> $StderrPath
            $ReplicationExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            & $Python @AlignmentArguments >> $StdoutPath 2>> $StderrPath
            $AlignmentExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            & $Python @ReadinessArguments >> $StdoutPath 2>> $StderrPath
            $ReadinessExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            $ExitCode = if ($PrimaryExitCode -eq 0 -and $ReplicationExitCode -eq 0 -and $AlignmentExitCode -eq 0 -and $ReadinessExitCode -eq 0) { 0 } else { 1 }
            $CycleDurationMilliseconds = [int][Math]::Ceiling(([DateTimeOffset]::UtcNow - $CycleStarted).TotalMilliseconds)
            $NextSleepMilliseconds = [int][Math]::Max(1000, ($SleepSeconds * 1000) - $CycleDurationMilliseconds)
            Write-Status `
                -Status $(if ($ExitCode -eq 0) { "sleeping" } else { "sleeping_after_collection_failure" }) `
                -ExitCode $ExitCode `
                -PrimaryExitCode $PrimaryExitCode `
                -ReplicationExitCode $ReplicationExitCode `
                -AlignmentExitCode $AlignmentExitCode `
                -ReadinessExitCode $ReadinessExitCode `
                -CycleDurationMilliseconds $CycleDurationMilliseconds `
                -NextSleepMilliseconds $NextSleepMilliseconds
        } finally {
            Pop-Location
        }
        Start-Sleep -Milliseconds $NextSleepMilliseconds
    }
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Write-Status -Status "stopped"
}
