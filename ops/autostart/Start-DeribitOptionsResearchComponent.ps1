param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("collector", "readiness")]
    [string]$Component,
    [int]$SleepSeconds = 300,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if ($Component -eq "collector") {
    $ComponentRoot = Join-Path $Root "HANDOFF\INCOMING\codex\20260711_deribit_options_surface_collector"
    $ComponentScript = Join-Path $ComponentRoot "collector.py"
    $ContractPath = Join-Path $ComponentRoot "CONTRACT.json"
    $LockPath = Join-Path $ComponentRoot "IMMUTABLE_LOCK_V2.json"
    $RuntimeDir = Join-Path $ComponentRoot "runtime_v2"
    $AllowedDecisions = @("deribit_options_surface_snapshot_healthy", "deribit_options_surface_snapshot_degraded")
    $AllowedLoopStatuses = @("running_once", "sleeping", "sleeping_after_fetch_failure")
    $MutexName = "Local\TradingOSDeribitOptionsSurfaceCollectorLauncher"
    $LauncherStatusPath = Join-Path $Root "logs\deribit_options_surface_collector_autostart_status.json"
} else {
    $ComponentRoot = Join-Path $Root "HANDOFF\INCOMING\codex\20260711_deribit_options_readiness_guard"
    $ComponentScript = Join-Path $ComponentRoot "monitor.py"
    $ContractPath = Join-Path $ComponentRoot "CONTRACT.json"
    $LockPath = Join-Path $ComponentRoot "IMMUTABLE_LOCK.json"
    $RuntimeDir = Join-Path $ComponentRoot "runtime"
    $AllowedDecisions = @("deribit_options_forward_data_collecting", "deribit_options_ready_for_preregistration_review")
    $AllowedLoopStatuses = @("running_once", "sleeping")
    $MutexName = "Local\TradingOSDeribitOptionsReadinessGuardLauncher"
    $LauncherStatusPath = Join-Path $Root "logs\deribit_options_readiness_guard_autostart_status.json"
}

$LoopStatusPath = Join-Path $RuntimeDir "loop_status.json"
$LatestReportPath = Join-Path $RuntimeDir "LATEST.json"
$StdoutPath = Join-Path $RuntimeDir "autostart.stdout.log"
$StderrPath = Join-Path $RuntimeDir "autostart.stderr.log"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $LauncherStatusPath -Parent) | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    if ($Requested -and (Test-Path -LiteralPath $Requested)) { return $Requested }
    if ($env:TRADING_OS_PYTHON -and (Test-Path -LiteralPath $env:TRADING_OS_PYTHON)) { return $env:TRADING_OS_PYTHON }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython) { return $HermesPython }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) { return $Python.Source }
    return $null
}

function Read-JsonSafe {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json } catch { return $null }
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
    param([string]$Status, [object]$Extra = $null)
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        component = $Component
        root = $Root
        component_root = $ComponentRoot
        sleep_seconds = $SleepSeconds
        startup_launch_only = $true
        automatic_restart_allowed = $false
        process_stop_allowed = $false
        collector_or_monitor_only = $true
        alerts_allowed = $false
        signals_allowed = $false
        paper_entries_allowed = $false
        orders_allowed = $false
        can_trade = $false
        extra = $Extra
    }
    $Payload | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $LauncherStatusPath -Encoding UTF8
    $Payload | ConvertTo-Json -Depth 7
}

if ($Root -match "\\My Drive(\\|$)") {
    Write-LauncherStatus -Status "blocked_google_drive_runtime"
    return
}
if ($SleepSeconds -lt 60) {
    Write-LauncherStatus -Status "blocked_invalid_sleep_seconds" -Extra @{ minimum = 60 }
    return
}
$Missing = @(@($ComponentScript, $ContractPath, $LockPath) | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($Missing) {
    Write-LauncherStatus -Status "blocked_missing_component_artifacts" -Extra @{ missing = $Missing }
    return
}
$Python = Get-PreferredPython -Requested $PythonPath
if (-not $Python) {
    Write-LauncherStatus -Status "blocked_python_missing"
    return
}

if ($Component -eq "readiness") {
    $CollectorStatusPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260711_deribit_options_surface_collector\runtime_v2\loop_status.json"
    $CollectorStatus = Read-JsonSafe -Path $CollectorStatusPath
    $CollectorProcess = $null
    if ($CollectorStatus -and $CollectorStatus.pid) {
        $CollectorProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$CollectorStatus.pid)" -ErrorAction SilentlyContinue
    }
    if (
        -not $CollectorStatus -or
        -not $CollectorProcess -or
        $CollectorStatus.status -notin @("running_once", "sleeping", "sleeping_after_fetch_failure") -or
        $CollectorStatus.can_trade -ne $false
    ) {
        Write-LauncherStatus -Status "blocked_upstream_collector_not_alive"
        return
    }
}

$Mutex = New-Object System.Threading.Mutex($false, $MutexName)
$MutexAcquired = $false
try {
    try { $MutexAcquired = $Mutex.WaitOne(15000) } catch [System.Threading.AbandonedMutexException] { $MutexAcquired = $true }
    if (-not $MutexAcquired) {
        Write-LauncherStatus -Status "blocked_launcher_busy"
        return
    }

    $LoopStatus = Read-JsonSafe -Path $LoopStatusPath
    $ScriptPattern = [regex]::Escape($ComponentScript)
    $LeafPattern = [regex]::Escape((Split-Path $ComponentScript -Leaf))
    $CommandPattern = '(?i)"?' + $ScriptPattern + '"?\s+loop\s+--sleep-seconds\s+' + $SleepSeconds + '(?:\s|$)'
    $LegacyStatusPattern = '(?i)(?:^|\s)"?' + $LeafPattern + '"?\s+loop\s+--sleep-seconds\s+' + $SleepSeconds + '(?:\s|$)'
    $MatchedCandidates = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { [string]$_.CommandLine -match $CommandPattern }
    )
    if ($LoopStatus -and $LoopStatus.pid) {
        $StatusProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$LoopStatus.pid)" -ErrorAction SilentlyContinue
        if (
            $StatusProcess -and
            ([string]$StatusProcess.CommandLine -match $CommandPattern -or [string]$StatusProcess.CommandLine -match $LegacyStatusPattern)
        ) {
            $MatchedCandidates += $StatusProcess
        }
    }
    $MatchedCandidates = @($MatchedCandidates | Sort-Object ProcessId -Unique)
    $Candidates = @(Get-LogicalProcessRoots -Processes $MatchedCandidates)
    if ($Candidates.Count -gt 1) {
        Write-LauncherStatus -Status "blocked_duplicate_component_processes" -Extra @{
            pids = @($Candidates | ForEach-Object { [int]$_.ProcessId })
            count = $Candidates.Count
        }
        return
    }

    $LatestReport = Read-JsonSafe -Path $LatestReportPath
    if ($Candidates.Count -eq 1) {
        $ExistingPid = [int]$Candidates[0].ProcessId
        $StatusProcess = if ($LoopStatus -and $LoopStatus.pid) {
            Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$LoopStatus.pid)" -ErrorAction SilentlyContinue
        } else { $null }
        $ExistingHealthy = [bool](
            $LoopStatus -and
            (Test-ProcessBelongsToRoot -RootPid $ExistingPid -Process $StatusProcess) -and
            $LoopStatus.status -in $AllowedLoopStatuses -and
            $LoopStatus.can_trade -eq $false -and
            $LatestReport -and
            $LatestReport.lock_verified -eq $true -and
            $LatestReport.can_trade -eq $false
        )
        if ($Component -eq "readiness") {
            $ExistingHealthy = $ExistingHealthy -and $LatestReport.collector_integrity.passed -eq $true
        }
        if (-not $ExistingHealthy) {
            Write-LauncherStatus -Status "blocked_existing_process_status_mismatch" -Extra @{ pid = $ExistingPid }
            return
        }
        Write-LauncherStatus -Status "already_running" -Extra @{
            pid = $ExistingPid
            process_count = 1
            decision = $LatestReport.decision
            python = $Candidates[0].ExecutablePath
        }
        return
    }

    & $Python $ComponentScript run-once *> $null
    $PreflightExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    $PreflightReport = Read-JsonSafe -Path $LatestReportPath
    $PreflightOk = [bool](
        $PreflightExitCode -eq 0 -and
        $PreflightReport -and
        $PreflightReport.decision -in $AllowedDecisions -and
        $PreflightReport.lock_verified -eq $true -and
        $PreflightReport.can_trade -eq $false
    )
    if ($Component -eq "readiness") {
        $PreflightOk = $PreflightOk -and $PreflightReport.collector_integrity.passed -eq $true
    }
    if (-not $PreflightOk) {
        Write-LauncherStatus -Status "blocked_preflight_failed" -Extra @{
            exit_code = $PreflightExitCode
            decision = if ($PreflightReport) { $PreflightReport.decision } else { $null }
        }
        return
    }

    try {
        $Process = Start-Process `
            -FilePath $Python `
            -ArgumentList @($ComponentScript, "loop", "--sleep-seconds", [string]$SleepSeconds) `
            -WorkingDirectory $ComponentRoot `
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
    for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
        Start-Sleep -Seconds 1
        $LoopStatusAfter = Read-JsonSafe -Path $LoopStatusPath
        if (-not $LoopStatusAfter) { continue }
        $StatusPid = [int]$LoopStatusAfter.pid
        $StatusProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $StatusPid" -ErrorAction SilentlyContinue
        if (
            (Test-ProcessBelongsToRoot -RootPid $Process.Id -Process $StatusProcess) -and
            $StatusProcess -and
            [string]$StatusProcess.CommandLine -match $CommandPattern -and
            $LoopStatusAfter.status -in $AllowedLoopStatuses -and
            $LoopStatusAfter.can_trade -eq $false
        ) {
            $Confirmed = $true
            $ConfirmedPid = $Process.Id
            break
        }
    }
    Write-LauncherStatus -Status $(if ($Confirmed) { "started" } else { "start_unconfirmed_no_restart" }) -Extra @{
        pid = if ($ConfirmedPid) { $ConfirmedPid } else { $Process.Id }
        process_count = if ($Confirmed) { 1 } else { $null }
        preflight_decision = $PreflightReport.decision
        python = $Python
    }
} finally {
    if ($MutexAcquired) { $Mutex.ReleaseMutex() }
    $Mutex.Dispose()
}
