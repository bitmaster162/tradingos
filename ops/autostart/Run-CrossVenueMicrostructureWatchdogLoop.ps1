param(
    [int]$SleepSeconds = 60,
    [int]$InitialDelaySeconds = 20,
    [string]$PythonPath = "",
    [switch]$NoImmediateRun,
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
$LogDir = Join-Path $Root "logs\cross_venue_microstructure"
$LockPath = Join-Path $LogDir "microstructure_watchdog_loop.lock.json"
$StatusPath = Join-Path $LogDir "microstructure_watchdog_loop_status.json"
$SourceIntegrityStdout = Join-Path $LogDir "active_source_integrity_guard_stdout.log"
$SourceIntegrityStderr = Join-Path $LogDir "active_source_integrity_guard_stderr.log"
$HealthStdout = Join-Path $LogDir "microstructure_health_stdout.log"
$HealthStderr = Join-Path $LogDir "microstructure_health_stderr.log"
$BookCoverageStdout = Join-Path $LogDir "microstructure_book_coverage_diagnostic_stdout.log"
$BookCoverageStderr = Join-Path $LogDir "microstructure_book_coverage_diagnostic_stderr.log"
$StorageStdout = Join-Path $LogDir "microstructure_storage_guard_stdout.log"
$StorageStderr = Join-Path $LogDir "microstructure_storage_guard_stderr.log"
$CollectorSlaStdout = Join-Path $LogDir "microstructure_collector_sla_guard_stdout.log"
$CollectorSlaStderr = Join-Path $LogDir "microstructure_collector_sla_guard_stderr.log"
$CollectorSlaNotifyStdout = Join-Path $LogDir "microstructure_collector_sla_notify_stdout.log"
$CollectorSlaNotifyStderr = Join-Path $LogDir "microstructure_collector_sla_notify_stderr.log"
$CollectorSlaReplayStdout = Join-Path $LogDir "microstructure_collector_sla_replay_stdout.log"
$CollectorSlaReplayStderr = Join-Path $LogDir "microstructure_collector_sla_replay_stderr.log"
$NotifyStdout = Join-Path $LogDir "microstructure_health_notify_stdout.log"
$NotifyStderr = Join-Path $LogDir "microstructure_health_notify_stderr.log"
$SealStdout = Join-Path $LogDir "microstructure_snapshot_gate_stdout.log"
$SealStderr = Join-Path $LogDir "microstructure_snapshot_gate_stderr.log"
$SealNotifyStdout = Join-Path $LogDir "microstructure_snapshot_gate_notify_stdout.log"
$SealNotifyStderr = Join-Path $LogDir "microstructure_snapshot_gate_notify_stderr.log"
$ReadinessProgressStdout = Join-Path $LogDir "microstructure_readiness_progress_stdout.log"
$ReadinessProgressStderr = Join-Path $LogDir "microstructure_readiness_progress_stderr.log"
$SnapshotTransitionStdout = Join-Path $LogDir "microstructure_snapshot_transition_monitor_stdout.log"
$SnapshotTransitionStderr = Join-Path $LogDir "microstructure_snapshot_transition_monitor_stderr.log"
$SnapshotTransitionNotifyStdout = Join-Path $LogDir "microstructure_snapshot_transition_notify_stdout.log"
$SnapshotTransitionNotifyStderr = Join-Path $LogDir "microstructure_snapshot_transition_notify_stderr.log"
$PreregStdout = Join-Path $LogDir "microstructure_prereg_queue_stdout.log"
$PreregStderr = Join-Path $LogDir "microstructure_prereg_queue_stderr.log"
$RunnerContractStdout = Join-Path $LogDir "microstructure_runner_contract_stdout.log"
$RunnerContractStderr = Join-Path $LogDir "microstructure_runner_contract_stderr.log"
$ResearchRunnerStdout = Join-Path $LogDir "microstructure_research_runner_stdout.log"
$ResearchRunnerStderr = Join-Path $LogDir "microstructure_research_runner_stderr.log"
$CandidateGovernanceStdout = Join-Path $LogDir "microstructure_candidate_governance_stdout.log"
$CandidateGovernanceStderr = Join-Path $LogDir "microstructure_candidate_governance_stderr.log"
$CandidateReviewStdout = Join-Path $LogDir "microstructure_candidate_review_pack_stdout.log"
$CandidateReviewStderr = Join-Path $LogDir "microstructure_candidate_review_pack_stderr.log"
$ValidationProtocolStdout = Join-Path $LogDir "microstructure_validation_protocol_stdout.log"
$ValidationProtocolStderr = Join-Path $LogDir "microstructure_validation_protocol_stderr.log"
$ValidationApprovalStdout = Join-Path $LogDir "microstructure_validation_approval_audit_stdout.log"
$ValidationApprovalStderr = Join-Path $LogDir "microstructure_validation_approval_audit_stderr.log"
$ValidationRunnerStdout = Join-Path $LogDir "microstructure_validation_runner_skeleton_stdout.log"
$ValidationRunnerStderr = Join-Path $LogDir "microstructure_validation_runner_skeleton_stderr.log"
$ResearchRunnerNotifyStdout = Join-Path $LogDir "microstructure_research_runner_notify_stdout.log"
$ResearchRunnerNotifyStderr = Join-Path $LogDir "microstructure_research_runner_notify_stderr.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    if ($Requested -and (Test-Path -LiteralPath $Requested)) { return @{ Exe = $Requested; Prefix = @() } }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython) { return @{ Exe = $HermesPython; Prefix = @() } }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) { return @{ Exe = $Python.Source; Prefix = @() } }
    $Py = Get-Command py -ErrorAction SilentlyContinue
    if ($Py) { return @{ Exe = $Py.Source; Prefix = @("-3") } }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

function Write-JsonFileSafe {
    param([string]$Path, [object]$Payload)
    $Json = $Payload | ConvertTo-Json -Depth 5
    $LastErrorMessage = $null
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        $TempPath = "$Path.tmp.$PID.$Attempt"
        try {
            $Json | Set-Content -LiteralPath $TempPath -Encoding UTF8
            Move-Item -LiteralPath $TempPath -Destination $Path -Force
            return
        } catch {
            $LastErrorMessage = $_.Exception.Message
            Start-Sleep -Milliseconds (100 * $Attempt)
        } finally {
            Remove-Item -LiteralPath $TempPath -Force -ErrorAction SilentlyContinue
        }
    }
    try {
        $FallbackPath = "$Path.write_failed.$PID.json"
        [ordered]@{
            ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            target = $Path
            error = $LastErrorMessage
            can_trade = $false
        } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $FallbackPath -Encoding UTF8
    } catch {}
}

function Write-Status {
    param([string]$Status, [object]$Extra = $null)
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        health_only = $true
        sends_orders = $false
        can_trade = $false
        extra = $Extra
    }
    Write-JsonFileSafe -Path $StatusPath -Payload $Payload
}

if ($SleepSeconds -lt 30) { throw "SleepSeconds must be at least 30." }
if ($InitialDelaySeconds -lt 0 -or $InitialDelaySeconds -gt 300) { throw "InitialDelaySeconds must be within [0, 300]." }
if (Test-Path -LiteralPath $LockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        if (Get-Process -Id ([int]$Existing.pid) -ErrorAction SilentlyContinue) {
            Write-Status -Status "skipped_existing_loop" -Extra @{ existing_pid = [int]$Existing.pid }
            exit 0
        }
    } catch {}
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}
[ordered]@{ pid = $PID; started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"); root = $Root } |
    ConvertTo-Json | Set-Content -LiteralPath $LockPath -Encoding UTF8

$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
$SourceIntegrityArgs = @()
$SourceIntegrityArgs += $Python.Prefix
$SourceIntegrityArgs += @("tools\active_source_integrity_guard.py", "check")
$HealthArgs = @()
$HealthArgs += $Python.Prefix
$HealthArgs += @("tools\cross_venue_microstructure_health.py")
$BookCoverageArgs = @()
$BookCoverageArgs += $Python.Prefix
$BookCoverageArgs += @("tools\cross_venue_microstructure_book_coverage_diagnostic.py")
$StorageArgs = @()
$StorageArgs += $Python.Prefix
$StorageArgs += @("tools\cross_venue_microstructure_storage_guard.py")
$CollectorSlaArgs = @()
$CollectorSlaArgs += $Python.Prefix
$CollectorSlaArgs += @("tools\cross_venue_microstructure_collector_sla_guard.py")
$CollectorSlaNotifyArgs = @()
$CollectorSlaNotifyArgs += $Python.Prefix
$CollectorSlaNotifyArgs += @("tools\cross_venue_microstructure_collector_sla_telegram_notify.py")
$CollectorSlaReplayArgs = @()
$CollectorSlaReplayArgs += $Python.Prefix
$CollectorSlaReplayArgs += @("tools\cross_venue_microstructure_collector_sla_replay.py")
$NotifyArgs = @()
$NotifyArgs += $Python.Prefix
$NotifyArgs += @("tools\cross_venue_microstructure_health_telegram_notify.py")
$SealArgs = @()
$SealArgs += $Python.Prefix
$SealArgs += @("tools\cross_venue_microstructure_snapshot_gate.py")
$SealNotifyArgs = @()
$SealNotifyArgs += $Python.Prefix
$SealNotifyArgs += @("tools\cross_venue_microstructure_snapshot_gate_telegram_notify.py")
$ReadinessProgressArgs = @()
$ReadinessProgressArgs += $Python.Prefix
$ReadinessProgressArgs += @("tools\cross_venue_microstructure_readiness_progress_monitor.py")
$SnapshotTransitionArgs = @()
$SnapshotTransitionArgs += $Python.Prefix
$SnapshotTransitionArgs += @("tools\cross_venue_microstructure_snapshot_transition_monitor.py")
$SnapshotTransitionNotifyArgs = @()
$SnapshotTransitionNotifyArgs += $Python.Prefix
$SnapshotTransitionNotifyArgs += @("tools\cross_venue_microstructure_snapshot_transition_telegram_notify.py")
$PreregArgs = @()
$PreregArgs += $Python.Prefix
$PreregArgs += @("tools\cross_venue_microstructure_prereg_queue.py")
$RunnerContractArgs = @()
$RunnerContractArgs += $Python.Prefix
$RunnerContractArgs += @("tools\cross_venue_microstructure_runner_contract.py")
$ResearchRunnerArgs = @()
$ResearchRunnerArgs += $Python.Prefix
$ResearchRunnerArgs += @("tools\cross_venue_microstructure_post_seal_auto_run_guard.py", "--execute")
$CandidateGovernanceArgs = @()
$CandidateGovernanceArgs += $Python.Prefix
$CandidateGovernanceArgs += @("tools\cross_venue_microstructure_candidate_governance_gate.py")
$CandidateReviewArgs = @()
$CandidateReviewArgs += $Python.Prefix
$CandidateReviewArgs += @("tools\cross_venue_microstructure_candidate_review_pack.py")
$ValidationProtocolArgs = @()
$ValidationProtocolArgs += $Python.Prefix
$ValidationProtocolArgs += @("tools\cross_venue_microstructure_validation_protocol_builder.py")
$ValidationApprovalArgs = @()
$ValidationApprovalArgs += $Python.Prefix
$ValidationApprovalArgs += @("tools\cross_venue_microstructure_validation_approval_audit.py")
$ValidationRunnerArgs = @()
$ValidationRunnerArgs += $Python.Prefix
$ValidationRunnerArgs += @("tools\cross_venue_microstructure_validation_runner_skeleton.py")
$ResearchRunnerNotifyArgs = @()
$ResearchRunnerNotifyArgs += $Python.Prefix
$ResearchRunnerNotifyArgs += @("tools\cross_venue_microstructure_research_runner_telegram_notify.py")

function Invoke-Health {
    Write-Status -Status "running_health_check"
    Push-Location $Root
    try {
        & $Python.Exe @SourceIntegrityArgs > $SourceIntegrityStdout 2> $SourceIntegrityStderr
        $SourceIntegrityExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @StorageArgs > $StorageStdout 2> $StorageStderr
        $StorageExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @CollectorSlaArgs > $CollectorSlaStdout 2> $CollectorSlaStderr
        $CollectorSlaExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @CollectorSlaNotifyArgs > $CollectorSlaNotifyStdout 2> $CollectorSlaNotifyStderr
        $CollectorSlaNotifyExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @CollectorSlaReplayArgs > $CollectorSlaReplayStdout 2> $CollectorSlaReplayStderr
        $CollectorSlaReplayExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @HealthArgs > $HealthStdout 2> $HealthStderr
        $HealthExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @BookCoverageArgs > $BookCoverageStdout 2> $BookCoverageStderr
        $BookCoverageExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @NotifyArgs > $NotifyStdout 2> $NotifyStderr
        $NotifyExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @SealArgs > $SealStdout 2> $SealStderr
        $SealExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @SealNotifyArgs > $SealNotifyStdout 2> $SealNotifyStderr
        $SealNotifyExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @ReadinessProgressArgs > $ReadinessProgressStdout 2> $ReadinessProgressStderr
        $ReadinessProgressExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @SnapshotTransitionArgs > $SnapshotTransitionStdout 2> $SnapshotTransitionStderr
        $SnapshotTransitionExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @SnapshotTransitionNotifyArgs > $SnapshotTransitionNotifyStdout 2> $SnapshotTransitionNotifyStderr
        $SnapshotTransitionNotifyExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @PreregArgs > $PreregStdout 2> $PreregStderr
        $PreregExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @RunnerContractArgs > $RunnerContractStdout 2> $RunnerContractStderr
        $RunnerContractExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        if ($SourceIntegrityExit -eq 0) {
            & $Python.Exe @ResearchRunnerArgs > $ResearchRunnerStdout 2> $ResearchRunnerStderr
            $ResearchRunnerExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        } else {
            $ResearchRunnerExit = 78
            "source_integrity_blocked_research_runner" | Set-Content -LiteralPath $ResearchRunnerStdout -Encoding UTF8
            "" | Set-Content -LiteralPath $ResearchRunnerStderr -Encoding UTF8
        }
        & $Python.Exe @CandidateGovernanceArgs > $CandidateGovernanceStdout 2> $CandidateGovernanceStderr
        $CandidateGovernanceExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @CandidateReviewArgs > $CandidateReviewStdout 2> $CandidateReviewStderr
        $CandidateReviewExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @ValidationProtocolArgs > $ValidationProtocolStdout 2> $ValidationProtocolStderr
        $ValidationProtocolExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @ValidationApprovalArgs > $ValidationApprovalStdout 2> $ValidationApprovalStderr
        $ValidationApprovalExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @ValidationRunnerArgs > $ValidationRunnerStdout 2> $ValidationRunnerStderr
        $ValidationRunnerExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        & $Python.Exe @ResearchRunnerNotifyArgs > $ResearchRunnerNotifyStdout 2> $ResearchRunnerNotifyStderr
        $ResearchRunnerNotifyExit = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
    } finally { Pop-Location }
    Write-Status -Status "sleeping" -Extra @{ last_source_integrity_exit_code = $SourceIntegrityExit; research_runner_blocked_by_source_integrity = ($SourceIntegrityExit -ne 0); last_storage_exit_code = $StorageExit; last_collector_sla_exit_code = $CollectorSlaExit; last_collector_sla_notify_exit_code = $CollectorSlaNotifyExit; last_collector_sla_replay_exit_code = $CollectorSlaReplayExit; last_health_exit_code = $HealthExit; last_book_coverage_diagnostic_exit_code = $BookCoverageExit; last_notify_exit_code = $NotifyExit; last_seal_exit_code = $SealExit; last_seal_notify_exit_code = $SealNotifyExit; last_readiness_progress_exit_code = $ReadinessProgressExit; last_snapshot_transition_exit_code = $SnapshotTransitionExit; last_snapshot_transition_notify_exit_code = $SnapshotTransitionNotifyExit; last_prereg_exit_code = $PreregExit; last_runner_contract_exit_code = $RunnerContractExit; last_research_runner_exit_code = $ResearchRunnerExit; last_candidate_governance_exit_code = $CandidateGovernanceExit; last_candidate_review_exit_code = $CandidateReviewExit; last_validation_protocol_exit_code = $ValidationProtocolExit; last_validation_approval_exit_code = $ValidationApprovalExit; last_validation_runner_exit_code = $ValidationRunnerExit; last_research_runner_notify_exit_code = $ResearchRunnerNotifyExit; next_run_after_seconds = $SleepSeconds }
}

try {
    if (-not $NoImmediateRun) {
        if ($InitialDelaySeconds -gt 0) {
            Write-Status -Status "startup_grace" -Extra @{ initial_delay_seconds = $InitialDelaySeconds }
            Start-Sleep -Seconds $InitialDelaySeconds
        }
        Invoke-Health
    }
    while ($true) {
        Start-Sleep -Seconds $SleepSeconds
        Invoke-Health
    }
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Write-Status -Status "stopped"
}
