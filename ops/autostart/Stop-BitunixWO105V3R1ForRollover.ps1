param()

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LifecycleScript = Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1"
. $LifecycleScript

$ComponentId = "bitunix_wo105_v3r1_forward"
$ExpectedScript = Join-Path $Root "ops\autostart\Run-BitunixWO105V3R1ForwardLoop.ps1"
$LockPath = Join-Path $Root "logs\bitunix_wo105_v3r1\bitunix_wo105_v3r1_forward_loop.lock.json"
$FirstCyclePath = Join-Path $Root "docs\BITUNIX_WO105_V3R1_FIRST_CYCLE_GATE_2026-07-14.json"
$ReceiptPath = Join-Path $Root "docs\BITUNIX_WO105_V3R1_RUNTIME_STOP_RECEIPT_2026-07-14.json"

if (Test-Path -LiteralPath $ReceiptPath) {
    throw "Refusing to overwrite immutable V3R1 stop receipt."
}
$FirstCycle = Get-Content -LiteralPath $FirstCyclePath -Raw | ConvertFrom-Json
if (
    [string]$FirstCycle.decision -ne "bitunix_wo105_v3_first_cycle_accepted_shadow_only" -or
    $FirstCycle.edge_evaluated -ne $false -or
    $FirstCycle.can_trade -ne $false
) {
    throw "V3R1 first-cycle is not accepted and outcome-blind."
}

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
        throw "V3R1 is not a verified contained runtime job: $($JobState.decision)"
    }
    $Receipt = $JobState.receipt
    $Lock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
    if ([int]$Lock.pid -ne [int]$Receipt.pid) { throw "V3R1 lock PID does not match job receipt." }
    if (-not ([IO.Path]::GetFullPath([string]$Lock.script).Equals([IO.Path]::GetFullPath($ExpectedScript), [StringComparison]::OrdinalIgnoreCase))) {
        throw "V3R1 lock script identity mismatch."
    }

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
        throw "V3R1 root process remained alive after verified Job Object stop."
    }

    $Snapshot = Get-TradingOSProcessSnapshot
    $Allowed = Get-TradingOSAllowedPowerShellExecutables
    $Remaining = @($Snapshot.Values | Where-Object {
        Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath $ExpectedScript -AllowedPowerShellExecutables $Allowed
    } | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
    if ($Remaining.Count -ne 0) { throw "Exact V3R1 script process remained: $($Remaining -join ',')" }

    if (Test-Path -LiteralPath $LockPath) {
        $CurrentLock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        if ([int]$CurrentLock.pid -ne [int]$Receipt.pid) { throw "V3R1 lock changed during verified stop." }
        Remove-Item -LiteralPath $LockPath -Force
    }
    $Payload = [ordered]@{
        schema_version = 1
        generated_at = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        decision = "bitunix_wo105_v3r1_runtime_stopped_verified"
        component = $ComponentId
        old_pid = [int]$Receipt.pid
        attempt_id = [string]$Receipt.attempt_id
        expected_script = $ExpectedScript
        job_stop_decision = [string]$Stopped.decision
        exact_script_pids_remaining = $Remaining
        job_receipt_removed = -not (Test-Path -LiteralPath (Join-Path $Root "logs\runtime_jobs\$ComponentId.json"))
        lock_removed = -not (Test-Path -LiteralPath $LockPath)
        first_cycle_decision = [string]$FirstCycle.decision
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
