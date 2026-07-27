param(
    [int]$SleepSeconds = 300,
    [switch]$NoImmediateRun,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LogDir = Join-Path $Root "logs\document_rule_forward_observer"
$LoopLockPath = Join-Path $LogDir "document_rule_forward_observer_loop.lock.json"
$LoopStatusPath = Join-Path $LogDir "document_rule_forward_observer_loop_status.json"
$StdoutPath = Join-Path $LogDir "document_rule_forward_observer_stdout.log"
$StderrPath = Join-Path $LogDir "document_rule_forward_observer_stderr.log"
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
    param([string]$Status, [int]$ExitCode = 0, [object]$Extra = $null)
    [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        live_trading_locked = $true
        observer_only = $true
        extra = $Extra
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $LoopStatusPath -Encoding UTF8
}

if (Test-Path -LiteralPath $LoopLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LoopLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            Write-LoopStatus -Status "skipped_existing_document_rule_forward_observer_loop" -Extra @{ existing_pid = $ExistingPid }
            exit 0
        }
    } catch {}
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
}

[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $LoopLockPath -Encoding UTF8

$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
$ObserverArgs = @()
$ObserverArgs += $Python.Prefix
$ObserverArgs += @(
    "tools\document_rule_forward_observer.py",
    "--out-prefix", "docs\DOCUMENT_RULE_FORWARD_OBSERVER_VOLUME_ACTIVE_RR1X3_2026-06-30"
)
$NotifyArgs = @()
$NotifyArgs += $Python.Prefix
$NotifyArgs += @(
    "tools\document_rule_forward_telegram_notify.py",
    "--out-prefix", "docs\DOCUMENT_RULE_FORWARD_TELEGRAM_NOTIFY_2026-06-30"
)
$ScoreboardArgs = @()
$ScoreboardArgs += $Python.Prefix
$ScoreboardArgs += @(
    "tools\document_rule_forward_scoreboard.py",
    "--out-prefix", "docs\DOCUMENT_RULE_FORWARD_SCOREBOARD_2026-06-30"
)
$ObserverVolzOiArgs = @()
$ObserverVolzOiArgs += $Python.Prefix
$ObserverVolzOiArgs += @(
    "tools\document_rule_forward_observer.py",
    "--guard-profile", "volume_z_oi_delta",
    "--journal-path", "logs\document_rule_forward_observer\signals_volume_z_oi_delta.jsonl",
    "--latest-card-path", "logs\document_rule_forward_observer\latest_signal_card_volume_z_oi_delta.json",
    "--state-path", "logs\document_rule_forward_observer\state_volume_z_oi_delta.json",
    "--out-prefix", "docs\DOCUMENT_RULE_FORWARD_OBSERVER_VOLZ05_OI1_RR1X3_2026-06-30"
)
$NotifyVolzOiArgs = @()
$NotifyVolzOiArgs += $Python.Prefix
$NotifyVolzOiArgs += @(
    "tools\document_rule_forward_telegram_notify.py",
    "--card-path", "logs\document_rule_forward_observer\latest_signal_card_volume_z_oi_delta.json",
    "--state-path", "logs\document_rule_forward_observer\telegram_notify_state_volume_z_oi_delta.json",
    "--out-prefix", "docs\DOCUMENT_RULE_FORWARD_TELEGRAM_NOTIFY_VOLZ05_OI1_2026-06-30"
)
$ScoreboardVolzOiArgs = @()
$ScoreboardVolzOiArgs += $Python.Prefix
$ScoreboardVolzOiArgs += @(
    "tools\document_rule_forward_scoreboard.py",
    "--journal-path", "logs\document_rule_forward_observer\signals_volume_z_oi_delta.jsonl",
    "--out-prefix", "docs\DOCUMENT_RULE_FORWARD_SCOREBOARD_VOLZ05_OI1_2026-06-30"
)

function Invoke-ObserverCycle {
    Write-LoopStatus -Status "running_document_rule_forward_observer_cycle" -Extra @{ python = $Python.Exe; profiles = @("volume_active", "volume_z_oi_delta") }
    & $Python.Exe @ObserverArgs 1>> $StdoutPath 2>> $StderrPath
    $ObserverExit = $LASTEXITCODE
    & $Python.Exe @NotifyArgs 1>> $StdoutPath 2>> $StderrPath
    $NotifyExit = $LASTEXITCODE
    & $Python.Exe @ScoreboardArgs 1>> $StdoutPath 2>> $StderrPath
    $ScoreboardExit = $LASTEXITCODE
    & $Python.Exe @ObserverVolzOiArgs 1>> $StdoutPath 2>> $StderrPath
    $ObserverVolzOiExit = $LASTEXITCODE
    & $Python.Exe @NotifyVolzOiArgs 1>> $StdoutPath 2>> $StderrPath
    $NotifyVolzOiExit = $LASTEXITCODE
    & $Python.Exe @ScoreboardVolzOiArgs 1>> $StdoutPath 2>> $StderrPath
    $ScoreboardVolzOiExit = $LASTEXITCODE
    $ExitSum = $ObserverExit + $NotifyExit + $ScoreboardExit + $ObserverVolzOiExit + $NotifyVolzOiExit + $ScoreboardVolzOiExit
    if ($ExitSum -ne 0) {
        Write-LoopStatus -Status "cycle_failed" -ExitCode $ExitSum -Extra @{
            observer_exit = $ObserverExit
            notify_exit = $NotifyExit
            scoreboard_exit = $ScoreboardExit
            observer_volz_oi_exit = $ObserverVolzOiExit
            notify_volz_oi_exit = $NotifyVolzOiExit
            scoreboard_volz_oi_exit = $ScoreboardVolzOiExit
        }
    } else {
        Write-LoopStatus -Status "cycle_completed" -ExitCode 0 -Extra @{
            observer_exit = $ObserverExit
            notify_exit = $NotifyExit
            scoreboard_exit = $ScoreboardExit
            observer_volz_oi_exit = $ObserverVolzOiExit
            notify_volz_oi_exit = $NotifyVolzOiExit
            scoreboard_volz_oi_exit = $ScoreboardVolzOiExit
        }
    }
}

try {
    if (-not $NoImmediateRun) {
        Push-Location $Root
        try {
            Invoke-ObserverCycle
        } finally {
            Pop-Location
        }
    }
    while ($true) {
        Write-LoopStatus -Status "sleeping" -Extra @{ python = $Python.Exe; next_run_after_seconds = $SleepSeconds }
        Start-Sleep -Seconds $SleepSeconds
        Push-Location $Root
        try {
            Invoke-ObserverCycle
        } finally {
            Pop-Location
        }
    }
} finally {
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
}
