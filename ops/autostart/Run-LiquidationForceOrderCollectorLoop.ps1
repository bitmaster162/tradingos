param(
    [string]$Symbols = "ALL",
    [string]$StreamMode = "all_market",
    [int]$CycleSeconds = 300,
    [int]$MaxEventsPerCycle = 100,
    [int]$SleepSeconds = 5,
    [switch]$NoImmediateRun,
    [string]$PythonPath = "",
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
$LogDir = Join-Path $Root "logs\liquidation_force_order"
$LoopLockPath = Join-Path $LogDir "liquidation_force_order_loop.lock.json"
$LoopStatusPath = Join-Path $LogDir "liquidation_force_order_loop_status.json"
$StdoutPath = Join-Path $LogDir "liquidation_force_order_stdout.log"
$StderrPath = Join-Path $LogDir "liquidation_force_order_stderr.log"
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

function Write-LoopStatus {
    param(
        [string]$Status,
        [int]$ExitCode = 0,
        [object]$Extra = $null
    )
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        pid = $PID
        root = $Root
        symbols = $Symbols
        stream_mode = $StreamMode
        cycle_seconds = $CycleSeconds
        max_events_per_cycle = $MaxEventsPerCycle
        live_trading_locked = $true
        data_collector_only = $true
        extra = $Extra
    } | ConvertTo-Json -Depth 5
    $Payload | Set-Content -LiteralPath $LoopStatusPath -Encoding UTF8
}

if (Test-Path -LiteralPath $LoopLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LoopLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            Write-LoopStatus -Status "skipped_existing_liquidation_force_order_loop" -Extra @{ existing_pid = $ExistingPid }
            exit 0
        }
    } catch {
        # stale or malformed lock; replace it
    }
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
}

[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
    symbols = $Symbols
    stream_mode = $StreamMode
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $LoopLockPath -Encoding UTF8

$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
$Args = @()
$Args += $Python.Prefix
$Args += @(
    "tools\binance_force_order_real_feed_collector.py",
    "--symbols", $Symbols,
    "--stream-mode", $StreamMode,
    "--max-events", "$MaxEventsPerCycle",
    "--max-seconds", "$CycleSeconds",
    "--out-prefix", "docs\LIQUIDATION_FORCE_ORDER_FORWARD_COLLECTOR_LATEST"
)

Write-LoopStatus -Status "running" -Extra @{ python = $Python.Exe }

try {
    while ($true) {
        if ($NoImmediateRun) {
            Write-LoopStatus -Status "sleeping_initial" -Extra @{ next_run_after_seconds = $SleepSeconds; python = $Python.Exe }
            Start-Sleep -Seconds $SleepSeconds
        }
        $NoImmediateRun = $false
        Push-Location $Root
        try {
            Write-LoopStatus -Status "running_collector_cycle" -Extra @{ python = $Python.Exe }
            & $Python.Exe @Args >> $StdoutPath 2>> $StderrPath
            $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            Write-LoopStatus -Status "ran_collector_cycle" -ExitCode $ExitCode -Extra @{ python = $Python.Exe }
        } finally {
            Pop-Location
        }
        Start-Sleep -Seconds $SleepSeconds
    }
} finally {
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
    Write-LoopStatus -Status "stopped"
}
