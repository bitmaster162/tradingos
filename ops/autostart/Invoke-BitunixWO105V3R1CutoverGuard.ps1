param(
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LifecycleScript = Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1"
. $LifecycleScript

$ControlDir = Join-Path $Root "_dl\bitunix_wo105_v3r1_cutover"
$StatusPath = Join-Path $ControlDir "GUARD_STATUS.json"
$StopRequestPath = Join-Path $ControlDir "STOP.request"
$ReleaseRequestPath = Join-Path $ControlDir "RELEASE.request"
$StopReceiptPath = Join-Path $Root "docs\BITUNIX_WO105_V3_RUNTIME_STOP_RECEIPT_2026-07-14.json"
$ComponentId = "bitunix_wo105_v3_forward"
$ExpectedScript = Join-Path $Root "ops\autostart\Run-BitunixWO105V3ForwardLoop.ps1"
$LockPath = Join-Path $Root "logs\bitunix_wo105_v3\bitunix_wo105_v3_forward_loop.lock.json"
New-Item -ItemType Directory -Force -Path $ControlDir | Out-Null
Remove-Item -LiteralPath $StopRequestPath, $ReleaseRequestPath -Force -ErrorAction SilentlyContinue

function Write-GuardStatus {
    param([string]$Decision, [object]$Extra = $null)
    [ordered]@{
        schema_version = 1
        generated_at = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        decision = $Decision
        pid = $PID
        component = $ComponentId
        extra = $Extra
        live_trading_locked = $true
        can_trade = $false
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

function Wait-ForFile {
    param([string]$Path, [datetime]$Deadline)
    while (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        if ((Get-Date) -ge $Deadline) { throw "Timed out waiting for $Path" }
        Start-Sleep -Milliseconds 200
    }
}

$Mutex = New-Object System.Threading.Mutex($false, (Get-TradingOSRuntimeMutexName -Root $Root))
$Acquired = $false
try {
    try { $Acquired = $Mutex.WaitOne(10000) }
    catch [System.Threading.AbandonedMutexException] { $Acquired = $true }
    if (-not $Acquired) { throw "Unable to acquire the runtime start mutex." }
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    Write-GuardStatus -Decision "bitunix_wo105_v3r1_cutover_mutex_acquired"
    Wait-ForFile -Path $StopRequestPath -Deadline $Deadline

    $JobState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ExpectedScript
    try {
        $Lock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        if ([string]$JobState.decision -eq "running_verified_job_contained") {
            $Receipt = $JobState.receipt
            $OldPid = [int]$Receipt.pid
            $AttemptId = [string]$Receipt.attempt_id
            if ([int]$Lock.pid -ne $OldPid) { throw "V3 lock PID does not match the managed receipt." }
        } elseif ([string]$JobState.decision -eq "missing_receipt") {
            # Recovery is allowed only for the exact stale lock left after the
            # prior verified Job Object stop removed its receipt first.
            $OldPid = [int]$Lock.pid
            $AttemptId = ""
            if (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) {
                throw "Receipt is absent but the V3 lock PID is still alive."
            }
        } else {
            throw "V3 job is not verified and contained: $($JobState.decision)"
        }
        if (-not ([System.IO.Path]::GetFullPath([string]$Lock.script).Equals([System.IO.Path]::GetFullPath($ExpectedScript), [System.StringComparison]::OrdinalIgnoreCase))) {
            throw "V3 lock script identity mismatch."
        }
    } finally {
        if ($JobState.process) { try { $JobState.process.Dispose() } catch {} }
    }

    $Stopped = if ([string]$JobState.decision -eq "running_verified_job_contained") {
        Stop-TradingOSRuntimeJobReceipt -Root $Root -ComponentId $ComponentId -ExpectedAttemptId $AttemptId -ExpectedProcessId $OldPid -ExpectedScriptPath $ExpectedScript
    } else {
        [pscustomobject]@{ decision = "receipt_already_absent_after_previous_verified_job_stop" }
    }
    $ExitDeadline = (Get-Date).AddSeconds(5)
    while ((Get-Process -Id $OldPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $ExitDeadline) {
        Start-Sleep -Milliseconds 100
    }
    if (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) { throw "V3 process remained alive after verified Job Object termination." }
    $Snapshot = Get-TradingOSProcessSnapshot
    $Allowed = Get-TradingOSAllowedPowerShellExecutables
    $ExactPids = @($Snapshot.Values | Where-Object {
        Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath $ExpectedScript -AllowedPowerShellExecutables $Allowed
    } | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
    if ($ExactPids.Count -ne 0) { throw "Exact V3 script process remained after stop: $($ExactPids -join ',')" }

    $CurrentLock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
    if ([int]$CurrentLock.pid -ne $OldPid) { throw "V3 lock changed during cutover." }
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction Stop
    $ReceiptPayload = [ordered]@{
        schema_version = 1
        generated_at = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        decision = "bitunix_wo105_v3_runtime_stopped_verified"
        component = $ComponentId
        old_pid = $OldPid
        attempt_id = $AttemptId
        expected_script = $ExpectedScript
        job_stop_decision = [string]$Stopped.decision
        exact_script_pids_remaining = $ExactPids
        receipt_removed = -not (Test-Path -LiteralPath (Join-Path $Root "logs\runtime_jobs\bitunix_wo105_v3_forward.json"))
        lock_removed = -not (Test-Path -LiteralPath $LockPath)
        start_mutex_held = $true
        live_trading_locked = $true
        can_trade = $false
    }
    $ReceiptPayload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StopReceiptPath -Encoding UTF8
    Write-GuardStatus -Decision "bitunix_wo105_v3_stopped_waiting_v3r1_release" -Extra $ReceiptPayload
    Wait-ForFile -Path $ReleaseRequestPath -Deadline $Deadline
    Write-GuardStatus -Decision "bitunix_wo105_v3r1_cutover_released" -Extra @{ stop_receipt = $StopReceiptPath }
} catch {
    Write-GuardStatus -Decision "bitunix_wo105_v3r1_cutover_guard_failed_closed" -Extra @{ error = $_.Exception.Message }
    throw
} finally {
    if ($Acquired) { try { $Mutex.ReleaseMutex() } catch {} }
    $Mutex.Dispose()
}
