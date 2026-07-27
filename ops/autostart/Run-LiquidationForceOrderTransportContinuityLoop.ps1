param(
    [int]$SleepSeconds = 30,
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
if ($Root -match "\\My Drive(\\|$)") { throw "Transport continuity sidecar must run from the local TradingOS root." }
if ($SleepSeconds -lt 10 -or $SleepSeconds -gt 180) { throw "SleepSeconds must be between 10 and 180." }

function Test-LoopShutdownRequested {
    try {
        $ShutdownGateResult = Test-TradingOSRuntimeShutdownRequested -Root $Root -AllowedAttemptId $LaunchAttemptId
        if ($ShutdownGateResult -isnot [bool]) { return $true }
        return [bool]$ShutdownGateResult
    } catch {
        return $true
    }
}
if (Test-LoopShutdownRequested) { exit 1 }

$ChildProcessHelper = Join-Path $PSScriptRoot "TradingOSChildProcess.ps1"
. $ChildProcessHelper
$null = Get-Command Enter-TradingOSLoopOwnership -CommandType Function -ErrorAction Stop
$null = Get-Command Exit-TradingOSLoopOwnership -CommandType Function -ErrorAction Stop
$null = Get-Command Write-TradingOSUtf8JsonAtomic -CommandType Function -ErrorAction Stop

$LogDir = Join-Path $Root "logs\liquidation_force_order"
$LockPath = Join-Path $LogDir "liquidation_force_order_transport_continuity_loop.lock.json"
$StatusPath = Join-Path $LogDir "liquidation_force_order_transport_continuity_loop_status.json"
$StdoutPath = Join-Path $LogDir "liquidation_force_order_transport_continuity_stdout.log"
$StderrPath = Join-Path $LogDir "liquidation_force_order_transport_continuity_stderr.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    foreach ($Candidate in @(
        $Requested,
        $env:TRADING_OS_PYTHON,
        (Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
    )) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }
    $Python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Python) { return [string]$Python.Source }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

$Ownership = Enter-TradingOSLoopOwnership `
    -Root $Root `
    -ComponentId "liquidation_force_order_transport_continuity" `
    -LockPath $LockPath `
    -ExpectedScriptPath $PSCommandPath `
    -LaunchAttemptId $LaunchAttemptId
if (-not $Ownership.Acquired) {
    [ordered]@{
        status = "already_running_verified"
        existing_pid = [int]$Ownership.ExistingPid
        audit_only = $true
        can_trade = $false
    } | ConvertTo-Json -Depth 4
    exit 0
}

function Write-LoopStatus {
    param([string]$Status, [int]$ExitCode = 0, [long]$CycleSequence = 0)
    Write-TradingOSUtf8JsonAtomic -Path $StatusPath -Payload ([ordered]@{
        schema_version = 1
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        cycle_sequence = $CycleSequence
        pid = $PID
        owner_token = [string]$Ownership.OwnerToken
        process_creation_utc = [string]$Ownership.ProcessStartUtc
        root = $Root
        launch_attempt_id = $LaunchAttemptId
        sleep_seconds = $SleepSeconds
        ledger = "logs/liquidation_force_order/liquidation_force_order_collector_heartbeat.jsonl"
        audit_only = $true
        data_collector_only = $true
        signals_allowed = $false
        paper_entries_allowed = $false
        orders_allowed = $false
        can_trade = $false
    }) -Depth 6
}

$Python = Get-PreferredPython -Requested $PythonPath
$Arguments = @(
    "tools\liquidation_force_order_transport_liveness_recorder.py",
    "--maximum-heartbeat-age-seconds", "90",
    "--maximum-liveness-age-seconds", "90"
)
$CycleSequence = 0
$TerminalExitCode = 0

try {
    while (-not (Test-LoopShutdownRequested)) {
        $CycleSequence += 1
        Write-LoopStatus -Status "recording_transport_liveness" -CycleSequence $CycleSequence
        Push-Location $Root
        try {
            & $Python @Arguments >> $StdoutPath 2>> $StderrPath
            $RecorderExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        } finally {
            Pop-Location
        }
        $CycleStatus = if ($RecorderExitCode -eq 0) { "sleeping_after_valid_liveness_proof" } else { "sleeping_after_invalid_liveness_proof" }
        Write-LoopStatus -Status $CycleStatus -ExitCode $RecorderExitCode -CycleSequence $CycleSequence

        $RemainingSleep = $SleepSeconds
        while ($RemainingSleep -gt 0 -and -not (Test-LoopShutdownRequested)) {
            $Chunk = [math]::Min(5, $RemainingSleep)
            Start-Sleep -Seconds $Chunk
            $RemainingSleep -= $Chunk
        }
    }
} catch {
    $TerminalExitCode = 70
    Write-LoopStatus -Status "stopped_after_sidecar_infrastructure_failure" -ExitCode $TerminalExitCode -CycleSequence $CycleSequence
    throw
} finally {
    $null = Exit-TradingOSLoopOwnership -Ownership $Ownership -LockPath $LockPath
    if ($TerminalExitCode -eq 0) {
        Write-LoopStatus -Status "stopped" -CycleSequence $CycleSequence
    }
}
