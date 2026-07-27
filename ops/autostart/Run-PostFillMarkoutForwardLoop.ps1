param(
    [int]$PulseSeconds = 300,
    [int]$RestartSeconds = 15,
    [string]$PythonPath = "",
    [string]$LaunchAttemptId = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ShutdownGateScript = Join-Path $Root "ops\autostart\TradingOSRuntimeShutdownGate.ps1"
try {
    if (-not (Test-Path -LiteralPath $ShutdownGateScript -PathType Leaf)) { throw "Runtime shutdown gate is unavailable." }
    . $ShutdownGateScript
    $null = Get-Command Test-TradingOSRuntimeShutdownRequested -CommandType Function -ErrorAction Stop
} catch {
    throw "Runtime shutdown gate failed to load: $($_.Exception.Message)"
}

if ($LaunchAttemptId) {
    try { $LaunchAttemptId = ([guid]$LaunchAttemptId).ToString() }
    catch { throw "LaunchAttemptId must be a valid non-empty GUID." }
    if ([guid]$LaunchAttemptId -eq [guid]::Empty) { throw "LaunchAttemptId must be a valid non-empty GUID." }
}
if ($Root -match "\\My Drive(\\|$)") { throw "Post-fill runtime must run from the local TradingOS root." }
if ($PulseSeconds -lt 10) { throw "PulseSeconds must be at least 10." }
if ($RestartSeconds -lt 5) { throw "RestartSeconds must be at least 5." }

function Test-ShutdownRequested {
    $ShutdownRequested = $true
    try {
        $Result = Test-TradingOSRuntimeShutdownRequested -Root $Root -AllowedAttemptId $LaunchAttemptId
        if ($Result -is [bool] -and -not $Result) { $ShutdownRequested = $false }
    } catch { $ShutdownRequested = $true }
    return $ShutdownRequested
}

if (Test-ShutdownRequested) { exit 1 }

$LogDir = Join-Path $Root "logs\post_fill_markout_forward"
$LockPath = Join-Path $LogDir "post_fill_markout_forward_loop.lock.json"
$StatusPath = Join-Path $LogDir "post_fill_markout_forward_loop_status.json"
$WorkerStatusPath = Join-Path $LogDir "worker_status.json"
$PulseHistoryPath = Join-Path $LogDir "pulse_history.jsonl"
$StdoutPath = Join-Path $LogDir "worker_stdout.log"
$StderrPath = Join-Path $LogDir "worker_stderr.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    foreach ($Candidate in @(
        $Requested,
        $env:TRADING_OS_PYTHON,
        (Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe")
    )) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) { return (Resolve-Path -LiteralPath $Candidate).Path }
    }
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCommand) { return $PythonCommand.Source }
    $PyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($PyCommand) { return $PyCommand.Source }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

function Write-JsonFileSafe {
    param([string]$Path, [object]$Payload)
    $Json = $Payload | ConvertTo-Json -Depth 10
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        $TempPath = "$Path.tmp.$PID.$Attempt"
        try {
            $Json | Set-Content -LiteralPath $TempPath -Encoding UTF8
            Move-Item -LiteralPath $TempPath -Destination $Path -Force
            return
        } catch {
            Start-Sleep -Milliseconds (100 * $Attempt)
        } finally {
            Remove-Item -LiteralPath $TempPath -Force -ErrorAction SilentlyContinue
        }
    }
    throw "Unable to atomically write runtime status: $Path"
}

function Write-LoopStatus {
    param([string]$Status, [int]$ExitCode = 0, [object]$Extra = $null)
    Write-JsonFileSafe -Path $StatusPath -Payload ([ordered]@{
        ts = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        pid = $PID
        root = $Root
        pulse_seconds = $PulseSeconds
        worker_status_path = $WorkerStatusPath
        public_book_ticker_capture = $true
        signed_read_endpoint_allowlist = @("/fapi/v1/userTrades")
        income_endpoint_allowed = $false
        signals_allowed = $false
        paper_entries_allowed = $false
        orders_allowed = $false
        capital_permission = "DENY"
        can_trade = $false
        extra = $Extra
    })
}

if (Test-Path -LiteralPath $LockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            Write-LoopStatus -Status "skipped_existing_post_fill_markout_forward_loop" -Extra @{ existing_pid = $ExistingPid }
            exit 0
        }
    } catch {}
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

$Python = Get-PreferredPython -Requested $PythonPath
$PythonPrefix = @()
if ([System.IO.Path]::GetFileNameWithoutExtension($Python) -ieq "py") { $PythonPrefix = @("-3") }
$PythonArgs = @()
$PythonArgs += $PythonPrefix
$PythonArgs += @(
    "tools\post_fill_markout_forward_runtime.py",
    "--project-root", $Root,
    "--prereg-path", "configs\POST_FILL_MARKOUT_FORWARD_PREREG_2026-07-14.json",
    "--worker-status", "logs\post_fill_markout_forward\worker_status.json",
    "--pulse-history", "logs\post_fill_markout_forward\pulse_history.jsonl",
    "--pulse-seconds", [string]$PulseSeconds
)

Write-JsonFileSafe -Path $LockPath -Payload ([ordered]@{
    pid = $PID
    started_at = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    script = $MyInvocation.MyCommand.Path
    launch_attempt_id = $LaunchAttemptId
    public_book_ticker_capture = $true
    signed_read_endpoint_allowlist = @("/fapi/v1/userTrades")
    orders_allowed = $false
    capital_permission = "DENY"
    can_trade = $false
})

try {
    while (-not (Test-ShutdownRequested)) {
        Push-Location $Root
        try {
            $env:BOT_ENV = "demo"
            Write-LoopStatus -Status "running_post_fill_markout_forward_worker" -Extra @{ python = $Python; restart_seconds = $RestartSeconds }
            & $Python @PythonArgs >> $StdoutPath 2>> $StderrPath
            $ExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        } finally {
            Pop-Location
        }
        if (Test-ShutdownRequested) { break }
        Write-LoopStatus -Status "post_fill_markout_forward_worker_restarting" -ExitCode $ExitCode -Extra @{ restart_after_seconds = $RestartSeconds }
        Start-Sleep -Seconds $RestartSeconds
    }
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Write-LoopStatus -Status "stopped"
}
