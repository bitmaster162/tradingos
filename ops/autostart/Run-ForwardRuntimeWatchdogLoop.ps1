param(
    [int]$SleepSeconds = 900,
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
$LogDir = Join-Path $Root "logs\forward_paper_feed"
$LoopLockPath = Join-Path $LogDir "forward_runtime_watchdog_loop.lock.json"
$LoopStatusPath = Join-Path $LogDir "forward_runtime_watchdog_loop_status.json"
$StdoutPath = Join-Path $LogDir "forward_runtime_watchdog_stdout.log"
$StderrPath = Join-Path $LogDir "forward_runtime_watchdog_stderr.log"
$NotifyStdoutPath = Join-Path $LogDir "forward_runtime_health_notify_stdout.log"
$NotifyStderrPath = Join-Path $LogDir "forward_runtime_health_notify_stderr.log"
$HealthReportPath = Join-Path $Root "docs\FORWARD_RUNTIME_HEALTH_2026-06-16.json"
$RuntimeScript = Join-Path $Root "ops\autostart\Start-TradingOSRuntime.ps1"
$RepairScript = Join-Path $Root "ops\autostart\Repair-TradingOSRuntime.ps1"
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
        sleep_seconds = $SleepSeconds
        live_trading_locked = $true
        extra = $Extra
    } | ConvertTo-Json -Depth 5
    for ($Attempt = 1; $Attempt -le 10; $Attempt++) {
        try {
            $Payload | Set-Content -LiteralPath $LoopStatusPath -Encoding UTF8 -ErrorAction Stop
            return
        } catch {
            if ($Attempt -lt 10) { Start-Sleep -Milliseconds (100 * $Attempt) }
        }
    }
}

if (Test-Path -LiteralPath $LoopLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LoopLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            Write-LoopStatus -Status "skipped_existing_watchdog_loop" -Extra @{ existing_pid = $ExistingPid }
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
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $LoopLockPath -Encoding UTF8

$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
$Args = @()
$Args += $Python.Prefix
$Args += @(
    "tools\forward_runtime_health_check.py",
    "--out-prefix", "docs\FORWARD_RUNTIME_HEALTH_2026-06-16"
)
$NotifyArgs = @()
$NotifyArgs += $Python.Prefix
$NotifyArgs += @(
    "tools\forward_runtime_health_telegram_notify.py",
    "--health-json-path", "docs\FORWARD_RUNTIME_HEALTH_2026-06-16.json",
    "--out-prefix", "docs\FORWARD_RUNTIME_HEALTH_TELEGRAM_NOTIFY_2026-06-16"
)
$LastHealthExitCode = $null
$LastNotifyExitCode = $null
$LastRepairAction = "none"

function Invoke-SafeRuntimeRepair {
    try {
        $Repair = & $RepairScript -HealthReport $HealthReportPath | ConvertFrom-Json
        return [string]$Repair.decision + ":" + (@($Repair.matched_repairable_gates) -join ",")
    } catch {
        return "repair_error:" + $_.Exception.GetType().Name
    }
}

Write-LoopStatus -Status "running" -Extra @{ python = $Python.Exe }

try {
    if (-not $NoImmediateRun) {
        Push-Location $Root
        try {
            & $Python.Exe @Args >> $StdoutPath 2>> $StderrPath
            $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            & $Python.Exe @NotifyArgs >> $NotifyStdoutPath 2>> $NotifyStderrPath
            $NotifyExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            $LastHealthExitCode = $ExitCode
            $LastNotifyExitCode = $NotifyExitCode
            $LastRepairAction = Invoke-SafeRuntimeRepair
            Write-LoopStatus -Status "ran_health_check" -ExitCode $ExitCode -Extra @{ python = $Python.Exe; notify_exit_code = $NotifyExitCode; repair_action = $LastRepairAction }
        } finally {
            Pop-Location
        }
    }
    while ($true) {
        Write-LoopStatus -Status "sleeping" -Extra @{ next_run_after_seconds = $SleepSeconds; python = $Python.Exe; last_health_exit_code = $LastHealthExitCode; last_notify_exit_code = $LastNotifyExitCode; last_repair_action = $LastRepairAction }
        Start-Sleep -Seconds $SleepSeconds
        Write-LoopStatus -Status "running_health_check" -Extra @{ python = $Python.Exe }
        Push-Location $Root
        try {
            & $Python.Exe @Args >> $StdoutPath 2>> $StderrPath
            $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            & $Python.Exe @NotifyArgs >> $NotifyStdoutPath 2>> $NotifyStderrPath
            $NotifyExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            $LastHealthExitCode = $ExitCode
            $LastNotifyExitCode = $NotifyExitCode
            $LastRepairAction = Invoke-SafeRuntimeRepair
            Write-LoopStatus -Status "ran_health_check" -ExitCode $ExitCode -Extra @{ python = $Python.Exe; notify_exit_code = $NotifyExitCode; repair_action = $LastRepairAction }
        } finally {
            Pop-Location
        }
    }
} finally {
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
    Write-LoopStatus -Status "stopped"
}
