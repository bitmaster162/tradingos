param()

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LifecycleScript = Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1"
. $LifecycleScript

$ComponentId = "bitunix_wo105_v3r3_forward"
$ExpectedScript = Join-Path $Root "ops\autostart\Run-BitunixWO105V3R3ForwardLoop.ps1"
$LockPath = Join-Path $Root "logs\bitunix_wo105_v3r3\bitunix_wo105_v3r3_forward_loop.lock.json"
$EvaluationPath = Join-Path $Root "_dl\bitunix_wo105_shadow_v3r3\LAST_EVALUATION.json"
$LedgerPath = Join-Path $Root "_dl\bitunix_wo105_shadow_v3r3\EVENT_LEDGER.jsonl"
$ReceiptPath = Join-Path $Root "docs\BITUNIX_WO105_V3R3_RUNTIME_STOP_RECEIPT_2026-07-15.json"

if (Test-Path -LiteralPath $ReceiptPath) { throw "Refusing to overwrite immutable V3R3 stop receipt." }
$Evaluation = Get-Content -LiteralPath $EvaluationPath -Raw | ConvertFrom-Json
if (
    [string]$Evaluation.state -ne "CAPTURE_INVALID" -or
    $Evaluation.event_id -ne $null -or
    $Evaluation.edge_evaluated -ne $false -or
    $Evaluation.can_trade -ne $false
) {
    throw "V3R3 evaluation is not the outcome-blind receipt-order failure." 
}
$ExpectedFailures = @("htf_bars:receipt_time_reordered", "outcome_bars:receipt_time_reordered") | Sort-Object -Unique
$ActualFailures = @($Evaluation.failures | Sort-Object -Unique)
if (($ActualFailures -join "|") -ne ($ExpectedFailures -join "|")) {
    throw "V3R3 failure set changed; refusing operational rollover."
}
$LedgerRows = 0
if (Test-Path -LiteralPath $LedgerPath) {
    $LedgerRows = @((Get-Content -LiteralPath $LedgerPath) | Where-Object { $_.Trim() }).Count
}
if ($LedgerRows -ne 0) { throw "V3R3 admitted events; outcome-blind rollover is forbidden." }

$Mutex = New-Object System.Threading.Mutex($false, (Get-TradingOSRuntimeMutexName -Root $Root))
$Acquired = $false
$JobState = $null
try {
    try { $Acquired = $Mutex.WaitOne(10000) }
    catch [System.Threading.AbandonedMutexException] { $Acquired = $true }
    if (-not $Acquired) { throw "Unable to acquire runtime start mutex." }

    $JobState = Get-TradingOSRuntimeJobReceiptState `
        -Root $Root `
        -ComponentId $ComponentId `
        -ExpectedScriptPath $ExpectedScript
    if ([string]$JobState.decision -ne "running_verified_job_contained") {
        throw "V3R3 is not a verified contained runtime job: $($JobState.decision)"
    }
    $Receipt = $JobState.receipt
    $Lock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
    if ([int]$Lock.pid -ne [int]$Receipt.pid) { throw "V3R3 lock PID does not match job receipt." }

    $Stopped = Stop-TradingOSRuntimeJobReceipt `
        -Root $Root `
        -ComponentId $ComponentId `
        -ExpectedAttemptId ([string]$Receipt.attempt_id) `
        -ExpectedProcessId ([int]$Receipt.pid) `
        -ExpectedScriptPath $ExpectedScript
    $Deadline = (Get-Date).AddSeconds(10)
    while ((Get-Process -Id ([int]$Receipt.pid) -ErrorAction SilentlyContinue) -and (Get-Date) -lt $Deadline) {
        Start-Sleep -Milliseconds 100
    }
    if (Get-Process -Id ([int]$Receipt.pid) -ErrorAction SilentlyContinue) {
        throw "V3R3 root process remained alive after verified Job Object stop."
    }

    $Snapshot = Get-TradingOSProcessSnapshot
    $Allowed = Get-TradingOSAllowedPowerShellExecutables
    $Remaining = @($Snapshot.Values | Where-Object {
        Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath $ExpectedScript -AllowedPowerShellExecutables $Allowed
    } | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
    if ($Remaining.Count -ne 0) { throw "Exact V3R3 script process remained: $($Remaining -join ',')" }

    if (Test-Path -LiteralPath $LockPath) {
        $CurrentLock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        if ([int]$CurrentLock.pid -ne [int]$Receipt.pid) { throw "V3R3 lock changed during verified stop." }
        Remove-Item -LiteralPath $LockPath -Force
    }
    $Payload = [ordered]@{
        schema_version = 1
        generated_at = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        decision = "bitunix_wo105_v3r3_runtime_stopped_verified_after_receipt_order_failure"
        component = $ComponentId
        old_pid = [int]$Receipt.pid
        attempt_id = [string]$Receipt.attempt_id
        expected_script = $ExpectedScript
        job_stop_decision = [string]$Stopped.decision
        exact_script_pids_remaining = $Remaining
        job_receipt_removed = -not (Test-Path -LiteralPath (Join-Path $Root "logs\runtime_jobs\$ComponentId.json"))
        lock_removed = -not (Test-Path -LiteralPath $LockPath)
        evaluation_state = [string]$Evaluation.state
        evaluation_failures = $ActualFailures
        ledger_rows = $LedgerRows
        outcome_metrics_inspected = $false
        signals_allowed = $false
        orders_allowed = $false
        capital_permission = "DENY"
        can_trade = $false
    }
    $Payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReceiptPath -Encoding UTF8
    $Payload | ConvertTo-Json -Depth 8
} finally {
    if ($JobState -and $JobState.process) { try { $JobState.process.Dispose() } catch {} }
    if ($Acquired) { try { $Mutex.ReleaseMutex() } catch {} }
    $Mutex.Dispose()
}
