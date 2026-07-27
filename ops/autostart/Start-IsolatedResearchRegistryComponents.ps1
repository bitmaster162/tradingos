param(
    [ValidateSet(
        "all",
        "trade_arrival_burst",
        "portfolio_overlap",
        "stablecoin_supply_v3",
        "stablecoin_readiness",
        "macro_liquidity",
        "macro_readiness"
    )]
    [string]$Component = "all",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$SupervisorRoot = Join-Path $Root "HANDOFF\INCOMING\codex\20260712_research_runtime_supervisor"
$RegistryPath = Join-Path $SupervisorRoot "REGISTRY.json"
$SupervisorScript = Join-Path $SupervisorRoot "supervisor.py"
$AggregateStatusPath = Join-Path $Root "logs\isolated_research_registry_components_launcher_status.json"
$MutexName = "Local\TradingOSIsolatedResearchRegistryComponentsLauncher"

New-Item -ItemType Directory -Force -Path (Split-Path $AggregateStatusPath -Parent) | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    if ($Requested -and (Test-Path -LiteralPath $Requested -PathType Leaf)) {
        return (Resolve-Path -LiteralPath $Requested).Path
    }
    if ($env:TRADING_OS_PYTHON -and (Test-Path -LiteralPath $env:TRADING_OS_PYTHON -PathType Leaf)) {
        return (Resolve-Path -LiteralPath $env:TRADING_OS_PYTHON).Path
    }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython -PathType Leaf) {
        return $HermesPython
    }
    $Python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if ($Python -and (Test-Path -LiteralPath $Python.Source -PathType Leaf)) {
        return $Python.Source
    }
    return $null
}

function Read-JsonSafe {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
    catch { return $null }
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

function Write-AggregateStatus {
    param([string]$Status, [object[]]$Results, [object]$RegistryAudit = $null)
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        requested_component = $Component
        root = $Root
        registry = $RegistryPath
        startup_launch_only = $true
        automatic_restart_allowed = $false
        process_stop_allowed = $false
        credentials_allowed = $false
        signals_allowed = $false
        paper_entries_allowed = $false
        orders_allowed = $false
        results = @($Results)
        registry_audit = $RegistryAudit
        can_trade = $false
    }
    $Payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $AggregateStatusPath -Encoding UTF8
    $Payload | ConvertTo-Json -Depth 8
}

if ($Root -match "\\My Drive(\\|$)") {
    Write-AggregateStatus -Status "blocked_google_drive_runtime" -Results @()
    return
}

$RequiredPaths = @($RegistryPath, $SupervisorScript)
$MissingPaths = @($RequiredPaths | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
if ($MissingPaths) {
    Write-AggregateStatus -Status "blocked_missing_registry_artifacts" -Results @(
        [ordered]@{ missing = $MissingPaths; can_trade = $false }
    )
    return
}

$Registry = Read-JsonSafe -Path $RegistryPath
if (
    -not $Registry -or
    $Registry.registry_id -ne "ISOLATED_RESEARCH_RUNTIME_REGISTRY_V3" -or
    $Registry.can_trade -ne $false
) {
    Write-AggregateStatus -Status "blocked_registry_contract_mismatch" -Results @()
    return
}

$Python = Get-PreferredPython -Requested $PythonPath
if (-not $Python) {
    Write-AggregateStatus -Status "blocked_python_missing" -Results @()
    return
}

$Specs = @(
    [pscustomobject]@{
        Id = "trade_arrival_burst"
        Root = "HANDOFF\INCOMING\codex\20260711_trade_arrival_burst_forward"
        Entrypoint = "observer.py"
        Runtime = "runtime"
        SleepSeconds = 60
    },
    [pscustomobject]@{
        Id = "portfolio_overlap"
        Root = "HANDOFF\INCOMING\codex\20260711_observer_portfolio_overlap_audit"
        Entrypoint = "audit.py"
        Runtime = "runtime"
        SleepSeconds = 300
    },
    [pscustomobject]@{
        Id = "stablecoin_supply_v3"
        Root = "HANDOFF\INCOMING\codex\20260711_stablecoin_supply_pulse_collector"
        Entrypoint = "collector.py"
        Runtime = "runtime_v3"
        SleepSeconds = 21600
    },
    [pscustomobject]@{
        Id = "stablecoin_readiness"
        Root = "HANDOFF\INCOMING\codex\20260712_stablecoin_supply_readiness_guard"
        Entrypoint = "monitor.py"
        Runtime = "runtime"
        SleepSeconds = 21600
    },
    [pscustomobject]@{
        Id = "macro_liquidity"
        Root = "HANDOFF\INCOMING\codex\20260712_macro_usd_liquidity_collector"
        Entrypoint = "collector.py"
        Runtime = "runtime"
        SleepSeconds = 21600
    },
    [pscustomobject]@{
        Id = "macro_readiness"
        Root = "HANDOFF\INCOMING\codex\20260712_macro_usd_liquidity_readiness_guard"
        Entrypoint = "monitor.py"
        Runtime = "runtime"
        SleepSeconds = 21600
    }
)

$ActiveIds = @($Registry.active_components | ForEach-Object { [string]$_.component_id })
$RegistryDrift = @($Specs | Where-Object { $_.Id -notin $ActiveIds } | ForEach-Object { $_.Id })
if ($RegistryDrift) {
    Write-AggregateStatus -Status "blocked_components_not_active_in_registry" -Results @(
        [ordered]@{ component_ids = $RegistryDrift; can_trade = $false }
    )
    return
}

$SelectedSpecs = if ($Component -eq "all") {
    @($Specs)
} else {
    @($Specs | Where-Object { $_.Id -eq $Component })
}

$Mutex = New-Object System.Threading.Mutex($false, $MutexName)
$MutexAcquired = $false
try {
    try { $MutexAcquired = $Mutex.WaitOne(15000) }
    catch [System.Threading.AbandonedMutexException] { $MutexAcquired = $true }
    if (-not $MutexAcquired) {
        Write-AggregateStatus -Status "blocked_launcher_busy" -Results @()
        return
    }

    $Results = @()
    foreach ($Spec in $SelectedSpecs) {
        $ComponentRoot = Join-Path $Root $Spec.Root
        $ScriptPath = Join-Path $ComponentRoot $Spec.Entrypoint
        $RuntimeDir = Join-Path $ComponentRoot $Spec.Runtime
        $StatusPath = Join-Path $RuntimeDir "loop_status.json"
        $ReportPath = Join-Path $RuntimeDir "LATEST.json"
        New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

        if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
            $Results += [ordered]@{ component_id = $Spec.Id; status = "blocked_missing_entrypoint"; can_trade = $false }
            continue
        }

        $AbsolutePattern = '(?i)' + [regex]::Escape($ScriptPath) + '"?\s+loop\s+--sleep-seconds\s+' + $Spec.SleepSeconds + '(?:\s|$)'
        $MatchedCandidates = @(
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { [string]$_.CommandLine -match $AbsolutePattern }
        )
        $LoopStatus = Read-JsonSafe -Path $StatusPath
        if ($LoopStatus -and $LoopStatus.pid) {
            $StatusProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$LoopStatus.pid)" -ErrorAction SilentlyContinue
            if ($StatusProcess -and [string]$StatusProcess.CommandLine -match $AbsolutePattern) {
                $MatchedCandidates += $StatusProcess
            }
        }
        $Candidates = @(Get-LogicalProcessRoots -Processes @($MatchedCandidates | Sort-Object ProcessId -Unique))

        if ($Candidates.Count -gt 1) {
            $Results += [ordered]@{
                component_id = $Spec.Id
                status = "blocked_duplicate_component_processes"
                pids = @($Candidates | ForEach-Object { [int]$_.ProcessId })
                can_trade = $false
            }
            continue
        }

        if ($Candidates.Count -eq 1) {
            $ExistingPid = [int]$Candidates[0].ProcessId
            $StatusProcess = if ($LoopStatus -and $LoopStatus.pid) {
                Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$LoopStatus.pid)" -ErrorAction SilentlyContinue
            } else { $null }
            $Healthy = [bool](
                $LoopStatus -and
                (Test-ProcessBelongsToRoot -RootPid $ExistingPid -Process $StatusProcess) -and
                $LoopStatus.status -in @("running_once", "sleeping") -and
                $LoopStatus.can_trade -eq $false
            )
            $Results += [ordered]@{
                component_id = $Spec.Id
                status = if ($Healthy) { "already_running" } else { "blocked_existing_process_status_mismatch" }
                pid = $ExistingPid
                loop_pid = if ($LoopStatus) { $LoopStatus.pid } else { $null }
                can_trade = $false
            }
            continue
        }

        & $Python $ScriptPath run-once *> $null
        $PreflightExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        $PreflightReport = Read-JsonSafe -Path $ReportPath
        if (
            $PreflightExitCode -ne 0 -or
            -not $PreflightReport -or
            $PreflightReport.can_trade -ne $false
        ) {
            $Results += [ordered]@{
                component_id = $Spec.Id
                status = "blocked_preflight_failed"
                exit_code = $PreflightExitCode
                decision = if ($PreflightReport) { $PreflightReport.decision } else { $null }
                can_trade = $false
            }
            continue
        }

        try {
            $Process = Start-Process `
                -FilePath $Python `
                -ArgumentList @($ScriptPath, "loop", "--sleep-seconds", [string]$Spec.SleepSeconds) `
                -WorkingDirectory $ComponentRoot `
                -RedirectStandardOutput (Join-Path $RuntimeDir "autostart.stdout.log") `
                -RedirectStandardError (Join-Path $RuntimeDir "autostart.stderr.log") `
                -WindowStyle Hidden `
                -PassThru
        } catch {
            $Results += [ordered]@{ component_id = $Spec.Id; status = "start_failed"; can_trade = $false }
            continue
        }

        $Confirmed = $false
        $ConfirmedLoopPid = $null
        for ($Attempt = 1; $Attempt -le 15; $Attempt++) {
            Start-Sleep -Seconds 1
            $LoopStatusAfter = Read-JsonSafe -Path $StatusPath
            if (-not $LoopStatusAfter -or -not $LoopStatusAfter.pid) { continue }
            $StatusProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$LoopStatusAfter.pid)" -ErrorAction SilentlyContinue
            if (
                (Test-ProcessBelongsToRoot -RootPid $Process.Id -Process $StatusProcess) -and
                [string]$StatusProcess.CommandLine -match $AbsolutePattern -and
                $LoopStatusAfter.status -in @("running_once", "sleeping") -and
                $LoopStatusAfter.can_trade -eq $false
            ) {
                $Confirmed = $true
                $ConfirmedLoopPid = [int]$LoopStatusAfter.pid
                break
            }
        }
        $Results += [ordered]@{
            component_id = $Spec.Id
            status = if ($Confirmed) { "started" } else { "start_unconfirmed_no_restart" }
            pid = $Process.Id
            loop_pid = $ConfirmedLoopPid
            preflight_decision = $PreflightReport.decision
            can_trade = $false
        }
    }

    $RegistryAudit = $null
    if ($Component -eq "all") {
        & $Python $SupervisorScript run-once *> $null
        $RegistryAuditReport = Read-JsonSafe -Path (Join-Path $SupervisorRoot "runtime\LATEST.json")
        $RegistryAudit = [ordered]@{
            exit_code = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
            decision = if ($RegistryAuditReport) { $RegistryAuditReport.decision } else { $null }
            healthy_components = if ($RegistryAuditReport) { $RegistryAuditReport.summary.healthy_components } else { $null }
            registered_components = if ($RegistryAuditReport) { $RegistryAuditReport.summary.registered_components } else { $null }
            scope = "full_registry_observability_only"
            affects_managed_launcher_status = $false
            can_trade = $false
        }
    }

    $Failed = @($Results | Where-Object { $_.status -notin @("started", "already_running") })
    # This launcher owns only the six non-Deribit specs above. The sealed V2
    # registry also contains separately managed Deribit components, so its
    # aggregate audit is useful drift evidence but must not create a false
    # failure for the processes this launcher actually started and verified.
    $Status = if ($Failed.Count -eq 0) {
        "research_registry_components_ready"
    } else {
        "research_registry_components_degraded_blocked"
    }
    Write-AggregateStatus -Status $Status -Results $Results -RegistryAudit $RegistryAudit
} finally {
    if ($MutexAcquired) { $Mutex.ReleaseMutex() }
    $Mutex.Dispose()
}
