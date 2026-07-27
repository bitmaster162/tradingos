param(
    [int]$SleepSeconds = 600,
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
$LoopLockPath = Join-Path $LogDir "strategy_mix_guarded_1h_forward_observer_loop.lock.json"
$LoopStatusPath = Join-Path $LogDir "strategy_mix_guarded_1h_forward_observer_loop_status.json"
$StdoutPath = Join-Path $LogDir "strategy_mix_guarded_1h_forward_observer_loop_stdout.log"
$StderrPath = Join-Path $LogDir "strategy_mix_guarded_1h_forward_observer_loop_stderr.log"
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
    [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        strategy_id = "mix_1h_long_breakout_up_20_expansion_atr_funding_negative_rr1x3_h12__guard_body_accept_volume_hot"
        live_trading_locked = $true
        forward_observer_only = $true
        telegram_watch_notify_only = $true
        opens_paper_entries = $false
        sends_orders = $false
        uses_private_credentials = $false
        orders_allowed = $false
        can_trade = $false
        extra = $Extra
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $LoopStatusPath -Encoding UTF8
}

if (Test-Path -LiteralPath $LoopLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LoopLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            Write-LoopStatus -Status "skipped_existing_strategy_mix_guarded_1h_forward_observer_loop" -Extra @{ existing_pid = $ExistingPid }
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
    live_trading_locked = $true
    orders_allowed = $false
    can_trade = $false
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LoopLockPath -Encoding UTF8

$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
$FeedArgs = @()
$FeedArgs += $Python.Prefix
$FeedArgs += @(
    "tools\strategy_mix_forward_paper_feed.py",
    "--source-report", "docs\STRATEGY_MIX_FORWARD_LOCKED_CANDIDATE_2026-07-01_1H_GUARDED_LONG.json",
    "--candidate-verdicts", "paper_replay_candidate_locked",
    "--symbol", "BTCUSDT",
    "--interval", "1h",
    "--limit", "420",
    "--min-closed-bars", "260",
    "--with-spot",
    "--journal-path", "logs\forward_paper_feed\strategy_mix_guarded_1h_forward_paper_feed.jsonl",
    "--state-path", "logs\forward_paper_feed\strategy_mix_guarded_1h_forward_paper_feed_state.json",
    "--signal-card-json-path", "logs\forward_paper_feed\latest_signal_card_guarded_1h.json",
    "--signal-card-md-path", "logs\forward_paper_feed\latest_signal_card_guarded_1h.md",
    "--out-prefix", "docs\STRATEGY_MIX_GUARDED_1H_FORWARD_PAPER_FEED_2026-07-01"
)

$NotifyArgs = @()
$NotifyArgs += $Python.Prefix
$NotifyArgs += @(
    "tools\strategy_mix_forward_telegram_notify.py",
    "--card-json-path", "logs\forward_paper_feed\latest_signal_card_guarded_1h.json",
    "--state-path", "logs\forward_paper_feed\strategy_mix_guarded_1h_telegram_notify_state.json",
    "--notify-statuses", "paper_entry_intent,signal_entry_pending_next_bar",
    "--out-prefix", "docs\STRATEGY_MIX_GUARDED_1H_FORWARD_TELEGRAM_NOTIFY_2026-07-01"
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
            Write-LoopStatus -Status "running_feed_cycle" -Extra @{ python = $Python.Exe }
            & $Python.Exe @FeedArgs >> $StdoutPath 2>> $StderrPath
            $FeedExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            Write-LoopStatus -Status "running_notify_cycle" -ExitCode $FeedExitCode -Extra @{ python = $Python.Exe; feed_exit_code = $FeedExitCode }
            & $Python.Exe @NotifyArgs >> $StdoutPath 2>> $StderrPath
            $NotifyExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            $CombinedExitCode = if ($FeedExitCode -ne 0) { $FeedExitCode } else { $NotifyExitCode }
            Write-LoopStatus -Status "ran_forward_observer_cycle" -ExitCode $CombinedExitCode -Extra @{ python = $Python.Exe; feed_exit_code = $FeedExitCode; notify_exit_code = $NotifyExitCode }
        } finally {
            Pop-Location
        }
        Start-Sleep -Seconds $SleepSeconds
    }
} finally {
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
    Write-LoopStatus -Status "stopped"
}
