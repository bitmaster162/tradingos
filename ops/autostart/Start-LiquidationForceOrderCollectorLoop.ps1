param(
    [string]$Symbols = "ALL",
    [string]$StreamMode = "all_market",
    [int]$CycleSeconds = 300,
    [int]$MaxEventsPerCycle = 100,
    [int]$SleepSeconds = 5,
    [string]$PythonPath = "",
    [string]$LaunchAttemptId = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$OwnLaunchAttempt = -not [bool]$LaunchAttemptId
if (-not $LaunchAttemptId) { $LaunchAttemptId = [guid]::NewGuid().ToString() } else { $LaunchAttemptId = ([guid]$LaunchAttemptId).ToString() }
$LogDir = Join-Path $Root "logs\liquidation_force_order"
$LoopScript = Join-Path $Root "ops\autostart\Run-LiquidationForceOrderCollectorLoop.ps1"
$LoopLockPath = Join-Path $LogDir "liquidation_force_order_loop.lock.json"
$LoopStatusPath = Join-Path $LogDir "liquidation_force_order_loop_status.json"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
. (Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1")
$RuntimeOperationMutex = New-Object System.Threading.Mutex($false, (Get-TradingOSRuntimeMutexName -Root $Root))
$RuntimeOperationMutexAcquired = $false
try {
try { $RuntimeOperationMutexAcquired = $RuntimeOperationMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $RuntimeOperationMutexAcquired = $true }
if (-not $RuntimeOperationMutexAcquired) {
    [ordered]@{ status = "blocked_runtime_operation_in_progress"; root = $Root; orders_allowed = $false; can_trade = $false } | ConvertTo-Json -Depth 3
    throw "TradingOS runtime operation is already in progress."
}
$RuntimeManifest = Get-TradingOSRuntimeManifest -Root $Root
$LaunchDisposition = Get-TradingOSRuntimeLaunchDisposition -Root $Root -Manifest $RuntimeManifest -ComponentId "liquidation_force_order_collector" -AttemptId $LaunchAttemptId

$AttemptCommit = $null
if (-not $LaunchDisposition.should_start) {
    if ($OwnLaunchAttempt -and $LaunchDisposition.decision -eq "already_running_verified") { $AttemptCommit = Complete-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $LaunchAttemptId }
    [ordered]@{
        status = if ($LaunchDisposition.decision -eq "already_running_verified") { "already_running" } else { $LaunchDisposition.decision }
        pid = $LaunchDisposition.pid
        status_path = $LoopStatusPath
        launch_disposition = $LaunchDisposition
        attempt_commit = $AttemptCommit
        live_trading_locked = $true
        data_collector_only = $true
    } | ConvertTo-Json -Depth 6
    if ($LaunchDisposition.decision -eq "already_running_verified") { return }
    throw "Runtime component launch blocked: $($LaunchDisposition.decision)"
}

$Args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $LoopScript,
    "-Symbols", $Symbols,
    "-StreamMode", $StreamMode,
    "-CycleSeconds", [string]$CycleSeconds,
    "-MaxEventsPerCycle", [string]$MaxEventsPerCycle,
    "-SleepSeconds", [string]$SleepSeconds,
    "-LaunchAttemptId", $LaunchAttemptId
)
if ($PythonPath) {
    $Args += @("-PythonPath", $PythonPath)
}

$Process = $null
$StatusPidAfter = 0
$Confirmation = $null
$LaunchError = $null
$ConfirmationError = $null
try {
    $Process = Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "liquidation_force_order_collector" -AttemptId $LaunchAttemptId -FilePath "powershell.exe" -ArgumentList $Args -WorkingDirectory $Root -ExpectedScriptPath $LoopScript
    $ConfirmationDeadline = (Get-Date).AddSeconds(5)
    do {
        if (Test-Path -LiteralPath $LoopStatusPath) {
            try {
                $StatusAfter = Get-Content -LiteralPath $LoopStatusPath -Raw | ConvertFrom-Json
                $CandidateStatusPid = [int]$StatusAfter.pid
                if ($CandidateStatusPid -gt 0) { $StatusPidAfter = $CandidateStatusPid }
            } catch {}
        }
        if ($StatusPidAfter -eq $Process.Id) {
            try {
                $Confirmation = Get-TradingOSRuntimeComponentLaunchConfirmation -Root $Root -Manifest $RuntimeManifest -ComponentId "liquidation_force_order_collector" -AttemptId $LaunchAttemptId -ExpectedProcessId $Process.Id -StatusProcessId $StatusPidAfter
                $ConfirmationError = $null
            } catch { $ConfirmationError = $_.Exception.Message }
            if ($Confirmation -and $Confirmation.confirmed) { break }
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $ConfirmationDeadline)
} catch { $LaunchError = $_.Exception.Message }

$Confirmed = $Confirmation -and [bool]$Confirmation.confirmed
if ($Confirmed -and $OwnLaunchAttempt) {
    try { $AttemptCommit = Complete-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $LaunchAttemptId } catch { $LaunchError = "attempt_commit_failed: $($_.Exception.Message)"; $Confirmed = $false }
}
if (-not $Confirmed) {
    $Rollback = $null
    $RollbackError = $null
    try { $Rollback = Undo-TradingOSRuntimeComponentLaunch -Root $Root -AttemptId $LaunchAttemptId -ComponentId "liquidation_force_order_collector" } catch { $RollbackError = $_.Exception.Message }
    [ordered]@{
        status = if ($LaunchError) { "start_failed" } else { "start_unconfirmed" }
        launcher_pid = if ($Process) { $Process.Id } else { $null }
        loop_pid = if ($StatusPidAfter -gt 0) { $StatusPidAfter } else { $null }
        loop_alive = $false
        status_path = $LoopStatusPath
        launch_disposition = $LaunchDisposition
        launch_confirmation = $Confirmation
        launch_error = $LaunchError
        confirmation_error = $ConfirmationError
        rollback = $Rollback
        rollback_error = $RollbackError
        symbols = $Symbols
        stream_mode = $StreamMode
        cycle_seconds = $CycleSeconds
        live_trading_locked = $true
        data_collector_only = $true
    } | ConvertTo-Json -Depth 10
    throw "Runtime component launch failed or was not confirmed: liquidation_force_order_collector"
}

[ordered]@{
    status = "started"
    launcher_pid = $Process.Id
    loop_pid = $StatusPidAfter
    loop_alive = $true
    status_path = $LoopStatusPath
    launch_disposition = $LaunchDisposition
    attempt_commit = $AttemptCommit
    launch_confirmation = $Confirmation
    symbols = $Symbols
    stream_mode = $StreamMode
    cycle_seconds = $CycleSeconds
    live_trading_locked = $true
    data_collector_only = $true
} | ConvertTo-Json -Depth 10
} finally {
    if ($RuntimeOperationMutexAcquired) { try { $RuntimeOperationMutex.ReleaseMutex() } catch {} }
    $RuntimeOperationMutex.Dispose()
}
