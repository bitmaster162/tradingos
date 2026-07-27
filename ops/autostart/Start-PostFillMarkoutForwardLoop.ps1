param(
    [int]$PulseSeconds = 300,
    [int]$RestartSeconds = 15,
    [string]$PythonPath = "",
    [string]$LaunchAttemptId = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$OwnLaunchAttempt = -not [bool]$LaunchAttemptId
if (-not $LaunchAttemptId) { $LaunchAttemptId = [guid]::NewGuid().ToString() } else { $LaunchAttemptId = ([guid]$LaunchAttemptId).ToString() }
$LogDir = Join-Path $Root "logs\post_fill_markout_forward"
$LoopScript = Join-Path $Root "ops\autostart\Run-PostFillMarkoutForwardLoop.ps1"
$LoopStatusPath = Join-Path $LogDir "post_fill_markout_forward_loop_status.json"
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
$LaunchDisposition = Get-TradingOSRuntimeLaunchDisposition -Root $Root -Manifest $RuntimeManifest -ComponentId "post_fill_markout_forward" -AttemptId $LaunchAttemptId

$AttemptCommit = $null
if (-not $LaunchDisposition.should_start) {
    if ($OwnLaunchAttempt -and $LaunchDisposition.decision -eq "already_running_verified") { $AttemptCommit = Complete-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $LaunchAttemptId }
    [ordered]@{
        status = if ($LaunchDisposition.decision -eq "already_running_verified") { "already_running" } else { $LaunchDisposition.decision }
        pid = $LaunchDisposition.pid
        status_path = $LoopStatusPath
        launch_disposition = $LaunchDisposition
        attempt_commit = $AttemptCommit
        pulse_seconds = $PulseSeconds
        public_book_ticker_capture = $true
        signed_read_endpoint_allowlist = @("/fapi/v1/userTrades")
        orders_allowed = $false
        can_trade = $false
    } | ConvertTo-Json -Depth 8
    if ($LaunchDisposition.decision -eq "already_running_verified") { return }
    throw "Runtime component launch blocked: $($LaunchDisposition.decision)"
}

$Args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $LoopScript,
    "-PulseSeconds", [string]$PulseSeconds,
    "-RestartSeconds", [string]$RestartSeconds,
    "-LaunchAttemptId", $LaunchAttemptId
)
if ($PythonPath) { $Args += @("-PythonPath", $PythonPath) }

$Process = $null
$StatusPidAfter = 0
$Confirmation = $null
$LaunchError = $null
$ConfirmationError = $null
try {
    $Process = Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "post_fill_markout_forward" -AttemptId $LaunchAttemptId -FilePath "powershell.exe" -ArgumentList $Args -WorkingDirectory $Root -ExpectedScriptPath $LoopScript
    $ConfirmationDeadline = (Get-Date).AddSeconds(8)
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
                $Confirmation = Get-TradingOSRuntimeComponentLaunchConfirmation -Root $Root -Manifest $RuntimeManifest -ComponentId "post_fill_markout_forward" -AttemptId $LaunchAttemptId -ExpectedProcessId $Process.Id -StatusProcessId $StatusPidAfter
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
    try { $Rollback = Undo-TradingOSRuntimeComponentLaunch -Root $Root -AttemptId $LaunchAttemptId -ComponentId "post_fill_markout_forward" } catch { $RollbackError = $_.Exception.Message }
    [ordered]@{
        status = if ($LaunchError) { "start_failed" } else { "start_unconfirmed" }
        launcher_pid = if ($Process) { $Process.Id } else { $null }
        loop_pid = if ($StatusPidAfter -gt 0) { $StatusPidAfter } else { $null }
        launch_error = $LaunchError
        confirmation_error = $ConfirmationError
        rollback = $Rollback
        rollback_error = $RollbackError
        orders_allowed = $false
        can_trade = $false
    } | ConvertTo-Json -Depth 10
    throw "Runtime component launch failed or was not confirmed: post_fill_markout_forward"
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
    pulse_seconds = $PulseSeconds
    public_book_ticker_capture = $true
    signed_read_endpoint_allowlist = @("/fapi/v1/userTrades")
    orders_allowed = $false
    can_trade = $false
} | ConvertTo-Json -Depth 10
} finally {
    if ($RuntimeOperationMutexAcquired) { try { $RuntimeOperationMutex.ReleaseMutex() } catch {} }
    $RuntimeOperationMutex.Dispose()
}
