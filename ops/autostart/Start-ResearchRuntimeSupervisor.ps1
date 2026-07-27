param(
    [int]$SleepSeconds = 300,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$SupervisorRoot = Join-Path $Root "HANDOFF\INCOMING\codex\20260712_research_runtime_supervisor"
$SupervisorScript = Join-Path $SupervisorRoot "supervisor.py"
$RegistryPath = Join-Path $SupervisorRoot "REGISTRY.json"
$LockPath = Join-Path $SupervisorRoot "IMMUTABLE_LOCK.json"
$RuntimeDir = Join-Path $SupervisorRoot "runtime"
$LoopStatusPath = Join-Path $RuntimeDir "loop_status.json"
$LatestReportPath = Join-Path $RuntimeDir "LATEST.json"
$StdoutPath = Join-Path $RuntimeDir "supervisor.stdout.log"
$StderrPath = Join-Path $RuntimeDir "supervisor.stderr.log"
$LauncherStatusPath = Join-Path $Root "logs\research_runtime_supervisor_autostart_status.json"
$MutexName = "Local\TradingOSResearchRuntimeSupervisorLauncher"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $LauncherStatusPath -Parent) | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    if ($Requested -and (Test-Path -LiteralPath $Requested)) {
        return $Requested
    }
    if ($env:TRADING_OS_PYTHON -and (Test-Path -LiteralPath $env:TRADING_OS_PYTHON)) {
        return $env:TRADING_OS_PYTHON
    }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython) {
        return $HermesPython
    }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) {
        return $Python.Source
    }
    return $null
}

function Read-JsonSafe {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-LogicalProcessRoots {
    param([object[]]$Processes)
    $Ids = @{}
    foreach ($Item in @($Processes)) { $Ids[[int]$Item.ProcessId] = $true }
    return @($Processes | Where-Object { -not $Ids.ContainsKey([int]$_.ParentProcessId) })
}

function Test-ProcessBelongsToRoot {
    param([int]$RootPid, [object]$Process)
    $Current = $Process
    for ($Depth = 0; $Depth -lt 4 -and $Current; $Depth++) {
        if ([int]$Current.ProcessId -eq $RootPid) { return $true }
        $ParentPid = [int]$Current.ParentProcessId
        if ($ParentPid -le 0) { break }
        $Current = Get-CimInstance Win32_Process -Filter "ProcessId = $ParentPid" -ErrorAction SilentlyContinue
    }
    return $false
}

function Write-LauncherStatus {
    param(
        [string]$Status,
        [object]$Extra = $null
    )
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        root = $Root
        supervisor_root = $SupervisorRoot
        sleep_seconds = $SleepSeconds
        startup_launch_only = $true
        automatic_restart_allowed = $false
        process_stop_allowed = $false
        signals_allowed = $false
        paper_entries_allowed = $false
        orders_allowed = $false
        can_trade = $false
        extra = $Extra
    }
    $Payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $LauncherStatusPath -Encoding UTF8
    $Payload | ConvertTo-Json -Depth 6
}

if ($Root -match "\\My Drive(\\|$)") {
    Write-LauncherStatus -Status "blocked_google_drive_runtime"
    return
}
if ($SleepSeconds -lt 60) {
    Write-LauncherStatus -Status "blocked_invalid_sleep_seconds" -Extra @{ minimum = 60 }
    return
}

$RequiredPaths = @($SupervisorScript, $RegistryPath, $LockPath)
$MissingPaths = @($RequiredPaths | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($MissingPaths) {
    Write-LauncherStatus -Status "blocked_missing_supervisor_artifacts" -Extra @{ missing = $MissingPaths }
    return
}

$Python = Get-PreferredPython -Requested $PythonPath
if (-not $Python) {
    Write-LauncherStatus -Status "blocked_python_missing"
    return
}

$Mutex = New-Object System.Threading.Mutex($false, $MutexName)
$MutexAcquired = $false
try {
    try {
        $MutexAcquired = $Mutex.WaitOne(15000)
    } catch [System.Threading.AbandonedMutexException] {
        $MutexAcquired = $true
    }
    if (-not $MutexAcquired) {
        Write-LauncherStatus -Status "blocked_launcher_busy"
        return
    }

    & $Python $SupervisorScript run-once *> $null
    $PreflightExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    $PreflightReport = Read-JsonSafe -Path $LatestReportPath
    if (
        $PreflightExitCode -ne 0 -or
        -not $PreflightReport -or
        $PreflightReport.decision -ne "research_runtime_registry_healthy" -or
        $PreflightReport.can_trade -ne $false
    ) {
        Write-LauncherStatus -Status "blocked_preflight_failed" -Extra @{
            exit_code = $PreflightExitCode
            decision = if ($PreflightReport) { $PreflightReport.decision } else { $null }
        }
        return
    }

    $AbsolutePattern = '(?i)' + [regex]::Escape($SupervisorScript) + '"?\s+loop\s+--sleep-seconds\s+' + $SleepSeconds + '(?:\s|$)'
    $LegacyPattern = '(?i)(?:^|\s)"?supervisor\.py"?\s+loop\s+--sleep-seconds\s+' + $SleepSeconds + '(?:\s|$)'
    $MatchedCandidates = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $CommandLine = [string]$_.CommandLine
                $CommandLine -match $AbsolutePattern -or $CommandLine -match $LegacyPattern
            }
    )
    $LoopStatus = Read-JsonSafe -Path $LoopStatusPath
    if ($LoopStatus -and $LoopStatus.pid) {
        $StatusProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$LoopStatus.pid)" -ErrorAction SilentlyContinue
        if (
            $StatusProcess -and
            ([string]$StatusProcess.CommandLine -match $AbsolutePattern -or [string]$StatusProcess.CommandLine -match $LegacyPattern)
        ) {
            $MatchedCandidates += $StatusProcess
        }
    }
    $MatchedCandidates = @($MatchedCandidates | Sort-Object ProcessId -Unique)
    $Candidates = @(Get-LogicalProcessRoots -Processes $MatchedCandidates)
    if ($Candidates.Count -gt 1) {
        Write-LauncherStatus -Status "blocked_duplicate_supervisor_processes" -Extra @{
            pids = @($Candidates | ForEach-Object { [int]$_.ProcessId })
            count = $Candidates.Count
        }
        return
    }

    if ($Candidates.Count -eq 1) {
        $ExistingPid = [int]$Candidates[0].ProcessId
        $StatusProcess = if ($LoopStatus -and $LoopStatus.pid) {
            Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$LoopStatus.pid)" -ErrorAction SilentlyContinue
        } else { $null }
        $ExistingHealthy = [bool](
            $LoopStatus -and
            (Test-ProcessBelongsToRoot -RootPid $ExistingPid -Process $StatusProcess) -and
            $LoopStatus.status -in @("running_once", "sleeping") -and
            $LoopStatus.can_trade -eq $false
        )
        if (-not $ExistingHealthy) {
            Write-LauncherStatus -Status "blocked_existing_process_status_mismatch" -Extra @{
                pid = $ExistingPid
                loop_status = if ($LoopStatus) { $LoopStatus.status } else { $null }
                loop_status_pid = if ($LoopStatus) { $LoopStatus.pid } else { $null }
            }
            return
        }
        Write-LauncherStatus -Status "already_running" -Extra @{
            pid = $ExistingPid
            process_count = 1
            preflight_decision = $PreflightReport.decision
            python = $Candidates[0].ExecutablePath
        }
        return
    }

    try {
        $Process = Start-Process `
            -FilePath $Python `
            -ArgumentList @($SupervisorScript, "loop", "--sleep-seconds", [string]$SleepSeconds) `
            -WorkingDirectory $SupervisorRoot `
            -RedirectStandardOutput $StdoutPath `
            -RedirectStandardError $StderrPath `
            -WindowStyle Hidden `
            -PassThru
    } catch {
        Write-LauncherStatus -Status "start_failed" -Extra @{ error = $_.Exception.GetType().Name }
        return
    }

    $Confirmed = $false
    $ConfirmedPid = $null
    for ($Attempt = 1; $Attempt -le 15; $Attempt++) {
        Start-Sleep -Seconds 1
        $LoopStatusAfter = Read-JsonSafe -Path $LoopStatusPath
        if ($LoopStatusAfter) {
            $StatusPid = [int]$LoopStatusAfter.pid
            $StatusProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $StatusPid" -ErrorAction SilentlyContinue
            $StatusCommand = if ($StatusProcess) { [string]$StatusProcess.CommandLine } else { "" }
            if (
                (Test-ProcessBelongsToRoot -RootPid $Process.Id -Process $StatusProcess) -and
                $StatusCommand -match $AbsolutePattern -and
                $LoopStatusAfter.status -in @("running_once", "sleeping") -and
                $LoopStatusAfter.can_trade -eq $false
            ) {
                $Confirmed = $true
                $ConfirmedPid = $Process.Id
                break
            }
        }
    }

    Write-LauncherStatus -Status $(if ($Confirmed) { "started" } else { "start_unconfirmed_no_restart" }) -Extra @{
        pid = if ($ConfirmedPid) { $ConfirmedPid } else { $Process.Id }
        process_count = if ($Confirmed) { 1 } else { $null }
        preflight_decision = $PreflightReport.decision
        python = $Python
    }
} finally {
    if ($MutexAcquired) {
        $Mutex.ReleaseMutex()
    }
    $Mutex.Dispose()
}
