param(
    [int]$SleepSeconds = 600,
    [string]$Symbols = "ALL",
    [string]$StreamMode = "all_market",
    [int]$CycleSeconds = 300,
    [int]$MaxEventsPerCycle = 100,
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
$LogDir = Join-Path $Root "logs\liquidation_force_order"
$LockPath = Join-Path $LogDir "liquidation_force_order_watchdog_loop.lock.json"
$StatusPath = Join-Path $LogDir "liquidation_force_order_watchdog_loop_status.json"
$StdoutPath = Join-Path $LogDir "liquidation_force_order_watchdog_stdout.log"
$StderrPath = Join-Path $LogDir "liquidation_force_order_watchdog_stderr.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    if ($Requested -and (Test-Path -LiteralPath $Requested)) {
        return @{ Exe = $Requested; Prefix = @() }
    }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython) {
        return @{ Exe = $HermesPython; Prefix = @() }
    }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) {
        return @{ Exe = $Python.Source; Prefix = @() }
    }
    $Py = Get-Command py -ErrorAction SilentlyContinue
    if ($Py) {
        return @{ Exe = $Py.Source; Prefix = @("-3") }
    }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

function Write-WatchdogStatus {
    param(
        [string]$Status,
        [int]$ExitCode = 0,
        [object]$Extra = $null
    )
    [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        symbols = $Symbols
        stream_mode = $StreamMode
        live_trading_locked = $true
        data_collector_only = $true
        extra = $Extra
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

if (Test-Path -LiteralPath $LockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            Write-WatchdogStatus -Status "skipped_existing_liquidation_force_order_watchdog_loop" -Extra @{ existing_pid = $ExistingPid }
            exit 0
        }
    } catch {
        # stale or malformed lock; replace it
    }
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
    symbols = $Symbols
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LockPath -Encoding UTF8

$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
$Args = @()
$Args += $Python.Prefix
$Args += @(
    "tools\liquidation_force_order_collector_watchdog.py",
    "--symbols", $Symbols,
    "--stream-mode", $StreamMode,
    "--cycle-seconds", "$CycleSeconds",
    "--max-events-per-cycle", "$MaxEventsPerCycle",
    "--out-prefix", "docs\LIQUIDATION_FORCE_ORDER_COLLECTOR_WATCHDOG_2026-06-30",
    "--data-quality-prefix", "docs\LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30"
)

Write-WatchdogStatus -Status "running" -Extra @{ python = $Python.Exe }

try {
    while ($true) {
        Push-Location $Root
        try {
            Write-WatchdogStatus -Status "running_watchdog_cycle" -Extra @{ python = $Python.Exe }
            & $Python.Exe @Args >> $StdoutPath 2>> $StderrPath
            $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            Write-WatchdogStatus -Status "ran_watchdog_cycle" -ExitCode $ExitCode -Extra @{ python = $Python.Exe }
        } finally {
            Pop-Location
        }
        Start-Sleep -Seconds $SleepSeconds
    }
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Write-WatchdogStatus -Status "stopped"
}
