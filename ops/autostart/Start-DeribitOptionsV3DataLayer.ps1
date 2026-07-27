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
    $ScriptPath = Join-Path $Root "tools\deribit_options_surface_collector_v3.py"
    $ConfigPath = Join-Path $Root "configs\DERIBIT_OPTIONS_SURFACE_COLLECTOR_V3.json"
    $LockPath = Join-Path $Root "configs\DERIBIT_OPTIONS_SURFACE_COLLECTOR_V3_LOCK.json"
    $RuntimeDir = Join-Path $Root "data\forward\deribit_options_surface_v3"
    $AllowedDecisions = @("deribit_options_v3_surface_snapshot_healthy", "deribit_options_v3_surface_snapshot_degraded")
    $AllowedStatuses = @("running_once", "sleeping", "sleeping_after_fetch_failure")
    $MutexName = "Local\TradingOSDeribitOptionsV3CollectorLauncher"
} else {
    $ScriptPath = Join-Path $Root "tools\deribit_options_readiness_guard_v2.py"
    $ConfigPath = Join-Path $Root "configs\DERIBIT_OPTIONS_READINESS_GUARD_V2.json"
    $LockPath = Join-Path $Root "configs\DERIBIT_OPTIONS_READINESS_GUARD_V2_LOCK.json"
    $RuntimeDir = Join-Path $Root "data\forward\deribit_options_readiness_v2"
    $AllowedDecisions = @("deribit_options_v3_forward_data_collecting", "deribit_options_v3_ready_for_observer_review")
    $AllowedStatuses = @("running_once", "sleeping")
    $MutexName = "Local\TradingOSDeribitOptionsV3ReadinessLauncher"
}

$LoopStatusPath = Join-Path $RuntimeDir "loop_status.json"
$LatestPath = Join-Path $RuntimeDir "LATEST.json"
$LauncherStatusPath = Join-Path $Root "logs\deribit_options_v3_$($Component)_launcher.json"
$StdoutPath = Join-Path $RuntimeDir "loop.stdout.log"
$StderrPath = Join-Path $RuntimeDir "loop.stderr.log"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $LauncherStatusPath -Parent) | Out-Null

function Read-JsonSafe {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json } catch { return $null }
}

function Get-PreferredPython {
    if ($PythonPath -and (Test-Path -LiteralPath $PythonPath)) { return $PythonPath }
    if ($env:TRADING_OS_PYTHON -and (Test-Path -LiteralPath $env:TRADING_OS_PYTHON)) { return $env:TRADING_OS_PYTHON }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython) { return $HermesPython }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) { return $Python.Source }
    return $null
}

function Write-Status {
    param([string]$Status, [object]$Extra = $null)
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        component = $Component
        script = $ScriptPath
        sleep_seconds = $SleepSeconds
        startup_launch_only = $true
        automatic_restart_allowed = $false
        process_stop_allowed = $false
        public_research_only = $true
        signals_allowed = $false
        orders_allowed = $false
        can_trade = $false
        extra = $Extra
    }
    $Payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $LauncherStatusPath -Encoding UTF8
    $Payload | ConvertTo-Json -Depth 6
}

if ($Root -match "\\My Drive(\\|$)") { Write-Status "blocked_google_drive_runtime"; return }
if ($SleepSeconds -lt 60) { Write-Status "blocked_invalid_sleep_seconds"; return }
$Missing = @($ScriptPath, $ConfigPath, $LockPath) | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($Missing) { Write-Status "blocked_missing_artifacts" @{ missing = @($Missing) }; return }
$Python = Get-PreferredPython
if (-not $Python) { Write-Status "blocked_python_missing"; return }

if ($Component -eq "readiness") {
    $CollectorStatus = Read-JsonSafe (Join-Path $Root "data\forward\deribit_options_surface_v3\loop_status.json")
    $CollectorProcess = if ($CollectorStatus -and $CollectorStatus.pid) {
        Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$CollectorStatus.pid)" -ErrorAction SilentlyContinue
    } else { $null }
    if (-not $CollectorStatus -or -not $CollectorProcess -or $CollectorStatus.status -notin @("running_once", "sleeping", "sleeping_after_fetch_failure") -or $CollectorStatus.can_trade -ne $false) {
        Write-Status "blocked_upstream_collector_not_alive"
        return
    }
}

$Mutex = New-Object System.Threading.Mutex($false, $MutexName)
$Acquired = $false
try {
    try { $Acquired = $Mutex.WaitOne(15000) } catch [System.Threading.AbandonedMutexException] { $Acquired = $true }
    if (-not $Acquired) { Write-Status "blocked_launcher_busy"; return }

    $Pattern = '(?i)' + [regex]::Escape($ScriptPath) + '"?\s+loop(?:\s|$)'
    $Processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { [string]$_.CommandLine -match $Pattern })
    $ProcessMap = @{}
    foreach ($Item in $Processes) { $ProcessMap[[int]$Item.ProcessId] = $true }
    $Roots = @($Processes | Where-Object { -not $ProcessMap.ContainsKey([int]$_.ParentProcessId) })
    if ($Roots.Count -gt 1) { Write-Status "blocked_duplicate_processes" @{ pids = @($Roots.ProcessId) }; return }
    if ($Roots.Count -eq 1) {
        $LoopStatus = Read-JsonSafe $LoopStatusPath
        $Latest = Read-JsonSafe $LatestPath
        if (-not $LoopStatus -or $LoopStatus.status -notin $AllowedStatuses -or $LoopStatus.can_trade -ne $false -or -not $Latest -or $Latest.decision -notin $AllowedDecisions -or $Latest.can_trade -ne $false) {
            Write-Status "blocked_existing_process_status_mismatch" @{ pid = [int]$Roots[0].ProcessId }
            return
        }
        Write-Status "already_running" @{ pid = [int]$Roots[0].ProcessId; decision = $Latest.decision }
        return
    }

    & $Python $ScriptPath run-once --config $ConfigPath --lock $LockPath --runtime-dir $RuntimeDir *> $null
    $ExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    $Preflight = Read-JsonSafe $LatestPath
    if ($ExitCode -ne 0 -or -not $Preflight -or $Preflight.decision -notin $AllowedDecisions -or $Preflight.can_trade -ne $false -or $Preflight.lock_verified -ne $true) {
        Write-Status "blocked_preflight_failed" @{ exit_code = $ExitCode; decision = if ($Preflight) { $Preflight.decision } else { $null } }
        return
    }

    $Process = Start-Process -FilePath $Python -ArgumentList @($ScriptPath, "loop", "--config", $ConfigPath, "--lock", $LockPath, "--runtime-dir", $RuntimeDir, "--sleep-seconds", [string]$SleepSeconds) -WorkingDirectory $Root -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath -WindowStyle Hidden -PassThru
    $Confirmed = $false
    for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
        Start-Sleep -Seconds 1
        $LoopStatus = Read-JsonSafe $LoopStatusPath
        if (-not $LoopStatus -or $LoopStatus.status -notin $AllowedStatuses -or $LoopStatus.can_trade -ne $false) { continue }
        $StatusProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$LoopStatus.pid)" -ErrorAction SilentlyContinue
        if ($StatusProcess -and [string]$StatusProcess.CommandLine -match $Pattern) { $Confirmed = $true; break }
    }
    Write-Status $(if ($Confirmed) { "started" } else { "start_unconfirmed_no_restart" }) @{ pid = $Process.Id; preflight_decision = $Preflight.decision; python = $Python }
} finally {
    if ($Acquired) { $Mutex.ReleaseMutex() }
    $Mutex.Dispose()
}
