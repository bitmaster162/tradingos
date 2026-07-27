param(
    [int]$SleepSeconds = 60,
    [int]$ChildTimeoutSeconds = 90,
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
$LogDir = Join-Path $Root "logs\cex_dex_funding"
$LockPath = Join-Path $LogDir "cex_dex_funding_freshness_watchdog_loop.lock.json"
$StatusPath = Join-Path $LogDir "cex_dex_funding_freshness_watchdog_loop_status.json"
$StdoutPath = Join-Path $LogDir "cex_dex_funding_freshness_watchdog_stdout.log"
$StderrPath = Join-Path $LogDir "cex_dex_funding_freshness_watchdog_stderr.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if ($SleepSeconds -lt 60) {
    throw "SleepSeconds must be at least 60."
}
if ($ChildTimeoutSeconds -lt 15 -or $ChildTimeoutSeconds -gt 300) {
    throw "ChildTimeoutSeconds must be between 15 and 300."
}

$ChildProcessHelper = Join-Path $PSScriptRoot "TradingOSChildProcess.ps1"
$PreviousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Stop"
    if (-not (Test-Path -LiteralPath $ChildProcessHelper -PathType Leaf)) {
        throw "Hidden child-process helper is unavailable: $ChildProcessHelper"
    }
    . $ChildProcessHelper
    $null = Get-Command Invoke-TradingOSChildProcess -CommandType Function -ErrorAction Stop
} finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}

function Get-PreferredPython {
    param([string]$Requested)
    if ($Requested) {
        if (-not (Test-Path -LiteralPath $Requested -PathType Leaf)) {
            throw "Requested Python executable is missing or is not a file: $Requested"
        }
        return (Resolve-Path -LiteralPath $Requested).Path
    }
    # Windows PowerShell can return every matching python.exe from PATH here.
    # Bind the watchdog to one concrete executable so the hidden-child helper
    # never receives an Object[] for its scalar FilePath parameter.
    $SystemPython = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -and (Test-Path -LiteralPath $_.Source -PathType Leaf) } |
        Select-Object -First 1
    if ($SystemPython) {
        return [string]$SystemPython.Source
    }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython -PathType Leaf) {
        return $HermesPython
    }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

function Convert-ChildResultForStatus {
    param([AllowNull()]$Result)
    if ($null -eq $Result) { return $null }
    return [ordered]@{
        started = [bool]$Result.Started
        process_id = [int]$Result.ProcessId
        exit_code = [int]$Result.ExitCode
        timed_out = [bool]$Result.TimedOut
        stream_drain_timed_out = [bool]$Result.StreamDrainTimedOut
        tree_kill_succeeded = [bool]$Result.TreeKillSucceeded
        duration_ms = [int]$Result.DurationMs
    }
}

function Test-ChildInfrastructureHealthy {
    param([AllowNull()]$Result)
    if ($null -eq $Result) { return $false }
    foreach ($Name in @('Started', 'ProcessId', 'ExitCode', 'TimedOut', 'StreamDrainTimedOut', 'TreeKillSucceeded', 'DurationMs')) {
        if ($null -eq $Result.PSObject.Properties[$Name]) { return $false }
    }
    return (
        [bool]$Result.Started -and
        -not [bool]$Result.TimedOut -and
        -not [bool]$Result.StreamDrainTimedOut -and
        [bool]$Result.TreeKillSucceeded -and
        [int]$Result.ExitCode -notin @(124, 125)
    )
}

function Write-Status {
    param(
        [string]$Status,
        [int]$ExitCode = 0,
        [int]$HealthExitCode = 0,
        [int]$IncidentAlertExitCode = 0,
        [string]$StopReason = '',
        [AllowNull()]$HealthResult = $null,
        [AllowNull()]$IncidentAlertResult = $null,
        [long]$CycleSequence = 0
    )
    $Payload = [ordered]@{
        schema_version = 2
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        stop_reason = $StopReason
        exit_code = $ExitCode
        health_exit_code = $HealthExitCode
        incident_alert_exit_code = $IncidentAlertExitCode
        health_result = Convert-ChildResultForStatus -Result $HealthResult
        incident_alert_result = Convert-ChildResultForStatus -Result $IncidentAlertResult
        cycle_sequence = $CycleSequence
        pid = $PID
        owner_token = if ($Ownership -and $Ownership.Acquired) { [string]$Ownership.OwnerToken } else { '' }
        process_creation_utc = if ($Ownership -and $Ownership.Acquired) { [string]$Ownership.ProcessStartUtc } else { '' }
        root = $Root
        launch_attempt_id = $LaunchAttemptId
        sleep_seconds = $SleepSeconds
        watchdog_only = $true
        automatic_restart_allowed = $false
        credentials_allowed = $false
        signals_allowed = $false
        paper_entries_allowed = $false
        orders_allowed = $false
        can_trade = $false
    }
    Write-TradingOSUtf8JsonAtomic -Path $StatusPath -Payload $Payload -Depth 8
}

# Resolve every dependency before creating the ownership lock. A bad explicit
# Python path must fail closed without leaving a stale lock behind.
$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })

$Ownership = Enter-TradingOSLoopOwnership `
    -Root $Root `
    -ComponentId 'cex_funding_freshness_watchdog' `
    -LockPath $LockPath `
    -ExpectedScriptPath $PSCommandPath `
    -LaunchAttemptId $LaunchAttemptId
if (-not $Ownership.Acquired) {
    [ordered]@{
        status = "already_running_verified"
        existing_pid = [int]$Ownership.ExistingPid
        watchdog_only = $true
        can_trade = $false
    } | ConvertTo-Json -Depth 4
    exit 0
}

$Arguments = @(
    "tools\cex_funding_freshness_watchdog.py",
    "--contract", "configs\CEX_FUNDING_FRESHNESS_WATCHDOG_LOCK_2026-07-13.json",
    "--out-prefix", "docs\CEX_FUNDING_FRESHNESS_WATCHDOG_2026-07-13"
)
$IncidentAlertArguments = @(
    "tools\cex_funding_freshness_incident_alert.py",
    "--contract", "configs\CEX_FUNDING_FRESHNESS_INCIDENT_ALERT_LOCK_2026-07-13.json",
    "--send-telegram"
)

$CycleSequence = 0
$HealthResult = $null
$IncidentAlertResult = $null
$HealthExitCode = 0
$IncidentAlertExitCode = 0
$TerminalStatus = 'stopped'
$TerminalReason = 'loop_completed'
$TerminalExitCode = 0
$FatalError = $null

try {
    while ($true) {
        if (Test-LoopShutdownRequested) {
            $TerminalReason = 'shutdown_gate_closed_before_cycle'
            break
        }

        $CycleSequence += 1
        $HealthResult = $null
        $IncidentAlertResult = $null
        $HealthExitCode = 0
        $IncidentAlertExitCode = 0
        Write-Status -Status "running_watchdog_cycle" -CycleSequence $CycleSequence

        $HealthResult = Invoke-TradingOSChildProcess -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $Root -StdoutPath $StdoutPath -StderrPath $StderrPath -TimeoutSeconds $ChildTimeoutSeconds
        $HealthExitCode = [int]$HealthResult.ExitCode
        if (-not (Test-ChildInfrastructureHealthy -Result $HealthResult)) {
            $TerminalStatus = 'stopped_after_health_infrastructure_failure'
            $TerminalReason = 'health_child_timeout_launch_stream_or_tree_cleanup_failure'
            $TerminalExitCode = 70
            break
        }
        if (Test-LoopShutdownRequested) {
            $TerminalReason = 'shutdown_gate_closed_after_health'
            break
        }

        $IncidentAlertResult = Invoke-TradingOSChildProcess -FilePath $Python -ArgumentList $IncidentAlertArguments -WorkingDirectory $Root -StdoutPath $StdoutPath -StderrPath $StderrPath -TimeoutSeconds $ChildTimeoutSeconds
        $IncidentAlertExitCode = [int]$IncidentAlertResult.ExitCode
        if (-not (Test-ChildInfrastructureHealthy -Result $IncidentAlertResult)) {
            $TerminalStatus = 'stopped_after_incident_infrastructure_failure'
            $TerminalReason = 'incident_child_timeout_launch_stream_or_tree_cleanup_failure'
            $TerminalExitCode = 70
            break
        }
        if (Test-LoopShutdownRequested) {
            $TerminalReason = 'shutdown_gate_closed_after_incident'
            break
        }

        $ExitCode = if ($HealthExitCode -eq 0 -and $IncidentAlertExitCode -eq 0) { 0 } else { 1 }
        $LoopStatus = if ($HealthExitCode -ne 0) {
            "sleeping_after_health_failure"
        } elseif ($IncidentAlertExitCode -ne 0) {
            "sleeping_after_incident_alert_failure"
        } else {
            "sleeping"
        }
        Write-Status -Status $LoopStatus -ExitCode $ExitCode -HealthExitCode $HealthExitCode -IncidentAlertExitCode $IncidentAlertExitCode -HealthResult $HealthResult -IncidentAlertResult $IncidentAlertResult -CycleSequence $CycleSequence

        $RemainingSleep = $SleepSeconds
        $ShutdownDuringSleep = $false
        while ($RemainingSleep -gt 0) {
            $SleepChunk = [math]::Min(5, $RemainingSleep)
            Start-Sleep -Seconds $SleepChunk
            $RemainingSleep -= $SleepChunk
            if (Test-LoopShutdownRequested) {
                $ShutdownDuringSleep = $true
                break
            }
        }
        if ($ShutdownDuringSleep) {
            $TerminalReason = 'shutdown_gate_closed_during_sleep'
            break
        }
    }
} catch {
    $FatalError = $_
    $TerminalStatus = 'stopped_after_loop_exception'
    $TerminalReason = $_.Exception.Message
    $TerminalExitCode = 70
} finally {
    try {
        Write-Status -Status $TerminalStatus -ExitCode $TerminalExitCode -HealthExitCode $HealthExitCode -IncidentAlertExitCode $IncidentAlertExitCode -StopReason $TerminalReason -HealthResult $HealthResult -IncidentAlertResult $IncidentAlertResult -CycleSequence $CycleSequence
    } catch {
        if ($TerminalExitCode -eq 0) { $TerminalExitCode = 74 }
        if (-not $FatalError) { $FatalError = $_ }
    }
    try {
        $LockReleased = Exit-TradingOSLoopOwnership -Ownership $Ownership -LockPath $LockPath
        if (-not $LockReleased -and $TerminalExitCode -eq 0) { $TerminalExitCode = 75 }
    } catch {
        if ($TerminalExitCode -eq 0) { $TerminalExitCode = 75 }
        if (-not $FatalError) { $FatalError = $_ }
    }
}

if ($FatalError) { Write-Error -ErrorRecord $FatalError }
if ($TerminalExitCode -ne 0) { exit $TerminalExitCode }
