param(
    [string]$HostAddress = "127.0.0.1",
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [string]$PythonPath = "",
    [string]$LaunchAttemptId = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$OwnLaunchAttempt = -not [bool]$LaunchAttemptId
if (-not $LaunchAttemptId) { $LaunchAttemptId = [guid]::NewGuid().ToString() } else { $LaunchAttemptId = ([guid]$LaunchAttemptId).ToString() }
. (Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1")
$LogDir = Join-Path $Root "logs"
$StatusPath = Join-Path $LogDir "control_panel_autostart_status.json"
$StdoutPath = Join-Path $LogDir "control_panel_stdout.log"
$StderrPath = Join-Path $LogDir "control_panel_stderr.log"
$ControlPanelScript = Join-Path $Root "ops\control_panel\control_panel.py"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
if (-not (Test-Path -LiteralPath $ControlPanelScript)) {
    throw "Missing control panel script: $ControlPanelScript"
}

function Normalize-ProcessEnvironment {
    # Some launchers inject both Path and PATH; Start-Process rejects that duplicate key.
    $CurrentPath = [Environment]::GetEnvironmentVariable("Path", "Process")
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $CurrentPath, "Process")
}

function Get-PreferredPython {
    param([string]$Requested)
    if ($Requested -and (Test-Path -LiteralPath $Requested)) {
        return @{ Exe = $Requested; Prefix = @() }
    }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython) {
        return @{ Exe = $HermesPython; Prefix = @() }
    }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) {
        return @{ Exe = $Python.Source; Prefix = @() }
    }
    $Py = Get-Command py -ErrorAction SilentlyContinue
    if ($Py) {
        return @{ Exe = $Py.Source; Prefix = @("-3") }
    }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

function Test-ControlPanelListening {
    try {
        $Conn = Get-NetTCPConnection -LocalAddress $HostAddress -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return [bool]$Conn
    } catch {
        $Needle = "$HostAddress`:$Port"
        $Rows = netstat -ano | Select-String $Needle
        return [bool]$Rows
    }
}

function Write-JsonFileSafe {
    param([string]$Path, [object]$Payload)
    Write-TradingOSJsonFileAtomic -Path $Path -Payload $Payload -Depth 5
}

function Write-Status {
    param(
        [string]$Status,
        [string]$Message,
        [object]$Extra = $null
    )
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        message = $Message
        host = $HostAddress
        port = $Port
        root = $Root
        live_trading_locked = $true
        can_trade = $false
        extra = $Extra
    }
    Write-JsonFileSafe -Path $StatusPath -Payload $Payload
}

$RuntimeOperationMutex = New-Object System.Threading.Mutex($false, (Get-TradingOSRuntimeMutexName -Root $Root))
$RuntimeOperationMutexAcquired = $false
try {
try { $RuntimeOperationMutexAcquired = $RuntimeOperationMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $RuntimeOperationMutexAcquired = $true }
if (-not $RuntimeOperationMutexAcquired) {
    Write-Status -Status "blocked_runtime_operation_in_progress" -Message "Another runtime lifecycle operation owns the launch mutex."
    throw "Another runtime lifecycle operation owns the launch mutex."
}
$AttemptReservation = Enter-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $LaunchAttemptId

if (Test-TradingOSRuntimeShutdownRequested -Root $Root -AllowedAttemptId $LaunchAttemptId) {
    Write-Status -Status "blocked_runtime_shutdown_requested" -Message "Explicit runtime shutdown is still in effect. Start the full runtime to clear it safely."
    throw "Explicit runtime shutdown is still in effect."
}

$ExistingPanelState = Get-TradingOSControlPanelOwnershipState -Root $Root -Port $Port
if ($ExistingPanelState.job_contained) {
    if ($OwnLaunchAttempt) { Complete-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $LaunchAttemptId | Out-Null }
    Write-Status -Status "already_running" -Message "Control panel port is already listening."
    return
}
if ($ExistingPanelState.listening -or $ExistingPanelState.exact_script_pids.Count -gt 0) {
    Write-Status -Status "blocked_foreign_or_unverified_listener" -Message "Control panel has an uncontained, duplicate, or foreign process and will not be replaced." -Extra @{ decision = $ExistingPanelState.ownership_decision; job_decision = $ExistingPanelState.job_decision; candidate_pids = $ExistingPanelState.candidate_pids; exact_script_pids = $ExistingPanelState.exact_script_pids }
    throw "Control panel has an uncontained, duplicate, or foreign process."
}

$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
$Args = @()
$Args += $Python.Prefix
$Args += @($ControlPanelScript, "--host", $HostAddress, "--port", [string]$Port)

Normalize-ProcessEnvironment
$Process = Start-TradingOSRuntimeJobProcess `
    -Root $Root `
    -ComponentId "control_panel_$Port" `
    -AttemptId $LaunchAttemptId `
    -FilePath $Python.Exe `
    -ArgumentList $Args `
    -WorkingDirectory $Root `
    -ExpectedScriptPath $ControlPanelScript `
    -StdoutPath $StdoutPath `
    -StderrPath $StderrPath

for ($Attempt = 1; $Attempt -le 15; $Attempt++) {
    Start-Sleep -Seconds 1
    $StartedPanelState = Get-TradingOSControlPanelOwnershipState -Root $Root -Port $Port
    $ListenerInLaunchJob = $StartedPanelState.job_contained -and (Test-TradingOSRuntimeJobContainsProcess -Root $Root -ComponentId "control_panel_$Port" -ProcessId ([int]$StartedPanelState.pid) -ExpectedAttemptId $LaunchAttemptId)
    if ($ListenerInLaunchJob) {
        if ($OwnLaunchAttempt) {
            try { Complete-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $LaunchAttemptId | Out-Null } catch {
                $CommitError = $_
                $Rollback = Undo-TradingOSRuntimeComponentLaunch -Root $Root -AttemptId $LaunchAttemptId -ComponentId "control_panel_$Port"
                Write-Status -Status "failed" -Message "Control panel launch could not be committed and was rolled back." -Extra @{ pid = $Process.Id; rollback = $Rollback; error = $CommitError.Exception.Message }
                throw $CommitError
            }
        }
        Write-Status -Status "started" -Message "Control panel started." -Extra @{ python = $Python.Exe; script = $ControlPanelScript; launcher_pid = $Process.Id; listener_pid = $StartedPanelState.pid; wait_seconds = $Attempt }
        return
    }
    $StillRunning = Get-Process -Id $Process.Id -ErrorAction SilentlyContinue
    if (-not $StillRunning) {
        $ExitCode = $null
        try {
            $Process.Refresh()
            $ExitCode = $Process.ExitCode
        } catch {}
        $Rollback = Undo-TradingOSRuntimeComponentLaunch -Root $Root -AttemptId $LaunchAttemptId -ComponentId "control_panel_$Port"
        Write-Status -Status "failed" -Message "Control panel process exited before opening the expected port." -Extra @{ python = $Python.Exe; pid = $Process.Id; exit_code = $ExitCode; wait_seconds = $Attempt }
        throw "Control panel process exited before opening the expected port."
    }
}

$Rollback = Undo-TradingOSRuntimeComponentLaunch -Root $Root -AttemptId $LaunchAttemptId -ComponentId "control_panel_$Port"
Write-Status -Status "failed" -Message "Control panel did not open the expected port." -Extra @{ python = $Python.Exe; pid = $Process.Id; wait_seconds = 15 }
throw "Control panel did not open the expected port."
} finally {
    if ($RuntimeOperationMutexAcquired) { try { $RuntimeOperationMutex.ReleaseMutex() } catch {} }
    $RuntimeOperationMutex.Dispose()
}
