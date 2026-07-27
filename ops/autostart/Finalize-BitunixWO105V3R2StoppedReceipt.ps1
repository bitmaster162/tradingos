param()

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LifecycleScript = Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1"
. $LifecycleScript

$ComponentId = "bitunix_wo105_v3r2_forward"
$ExpectedScript = Join-Path $Root "ops\autostart\Run-BitunixWO105V3R2ForwardLoop.ps1"
$LoopStatusPath = Join-Path $Root "logs\bitunix_wo105_v3r2\bitunix_wo105_v3r2_forward_loop_status.json"
$LoopLockPath = Join-Path $Root "logs\bitunix_wo105_v3r2\bitunix_wo105_v3r2_forward_loop.lock.json"
$WsManifestPath = Join-Path $Root "_dl\bitunix_wo105_v3r2_ws_intake\WS_INTAKE_MANIFEST.json"
$LedgerPath = Join-Path $Root "_dl\bitunix_wo105_shadow_v3r2\EVENT_LEDGER.jsonl"
$ReceiptPath = Join-Path $Root "docs\BITUNIX_WO105_V3R2_RUNTIME_STOP_RECEIPT_2026-07-14.json"

if (Test-Path -LiteralPath $ReceiptPath) { throw "Refusing to overwrite immutable V3R2 stop receipt." }
$LoopStatus = Get-Content -LiteralPath $LoopStatusPath -Raw | ConvertFrom-Json
$WsManifest = Get-Content -LiteralPath $WsManifestPath -Raw | ConvertFrom-Json
if ([string]$LoopStatus.status -ne "stopped" -or $LoopStatus.can_trade -ne $false) {
    throw "V3R2 loop is not stopped with fail-closed boundary."
}
if ([string]$WsManifest.decision -ne "bitunix_wo105_ws_intake_ready" -or $WsManifest.can_trade -ne $false) {
    throw "V3R2 accepted WS intake proof is missing."
}
$LedgerRows = 0
if (Test-Path -LiteralPath $LedgerPath) {
    $LedgerRows = @((Get-Content -LiteralPath $LedgerPath) | Where-Object { $_.Trim() }).Count
}
if ($LedgerRows -ne 0) { throw "V3R2 is not a zero-event operational rollover." }

$JobState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ExpectedScript
if ([string]$JobState.decision -ne "stale_receipt_process_absent") {
    throw "V3R2 job state is not safely stopped: $($JobState.decision)"
}
$OldReceipt = $JobState.receipt
$Snapshot = Get-TradingOSProcessSnapshot
$Allowed = Get-TradingOSAllowedPowerShellExecutables
$Remaining = @($Snapshot.Values | Where-Object {
    Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath $ExpectedScript -AllowedPowerShellExecutables $Allowed
} | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
if ($Remaining.Count -ne 0) { throw "Exact V3R2 script process remained: $($Remaining -join ',')" }
if (Test-Path -LiteralPath $LoopLockPath) { throw "V3R2 loop lock still exists after stopped status." }

$JobReceiptPath = Get-TradingOSRuntimeJobReceiptPath -Root $Root -ComponentId $ComponentId
Remove-Item -LiteralPath $JobReceiptPath -Force -ErrorAction Stop
$Payload = [ordered]@{
    schema_version = 1
    generated_at = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    decision = "bitunix_wo105_v3r2_runtime_stopped_verified_after_interface_failure"
    component = $ComponentId
    old_pid = [int]$OldReceipt.pid
    attempt_id = [string]$OldReceipt.attempt_id
    expected_script = $ExpectedScript
    prior_job_state = [string]$JobState.decision
    exact_script_pids_remaining = $Remaining
    job_receipt_removed = -not (Test-Path -LiteralPath $JobReceiptPath)
    lock_removed = -not (Test-Path -LiteralPath $LoopLockPath)
    ws_intake_decision = [string]$WsManifest.decision
    ledger_rows = $LedgerRows
    outcome_metrics_inspected = $false
    signals_allowed = $false
    orders_allowed = $false
    capital_permission = "DENY"
    can_trade = $false
}
$Payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReceiptPath -Encoding UTF8
$Payload | ConvertTo-Json -Depth 8
