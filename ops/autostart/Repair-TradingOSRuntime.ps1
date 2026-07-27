param(
    [string]$HealthReport = "docs\FORWARD_RUNTIME_HEALTH_2026-06-16.json",
    [string]$StatePath = "logs\runtime_safe_repair_state.json",
    [string]$StatusPath = "logs\runtime_safe_repair_last_run.json",
    [int]$MaxRepairs = 3,
    [int]$WindowMinutes = 60,
    [int]$MaxHealthReportAgeMinutes = 30,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1")
if ($MaxRepairs -lt 0 -or $MaxRepairs -gt 100) { throw "MaxRepairs must be between 0 and 100." }
if ($WindowMinutes -lt 1 -or $WindowMinutes -gt 10080) { throw "WindowMinutes must be between 1 and 10080." }
if ($MaxHealthReportAgeMinutes -lt 1 -or $MaxHealthReportAgeMinutes -gt 1440) { throw "MaxHealthReportAgeMinutes must be between 1 and 1440." }

function Resolve-RepairPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$RequiredDirectory
    )
    $Resolved = Resolve-TradingOSRuntimePath -Root $Root -Path $Path
    $RequiredRoot = [System.IO.Path]::GetFullPath((Join-Path $Root $RequiredDirectory)).TrimEnd('\') + '\'
    if (-not $Resolved.StartsWith($RequiredRoot, [System.StringComparison]::OrdinalIgnoreCase) -or -not $Resolved.EndsWith('.json', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Repair path must remain under Root\$RequiredDirectory and end with .json: $Path"
    }
    return $Resolved
}

function Read-StrictHealthReport {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][datetime]$ReferenceUtc
    )
    if (-not (Test-Path -LiteralPath $Path)) { return [pscustomobject]@{ valid = $false; reason = "missing_health_report"; payload = $null; generated_utc = $null } }
    try { $Payload = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch {
        return [pscustomobject]@{ valid = $false; reason = "invalid_health_json"; payload = $null; generated_utc = $null }
    }
    try { $GeneratedUtc = ([datetime]::Parse([string]$Payload.generated_at)).ToUniversalTime() } catch {
        return [pscustomobject]@{ valid = $false; reason = "invalid_health_timestamp"; payload = $Payload; generated_utc = $null }
    }
    $AgeMinutes = ($ReferenceUtc - $GeneratedUtc).TotalMinutes
    if ($AgeMinutes -lt -1 -or $AgeMinutes -gt $MaxHealthReportAgeMinutes) {
        return [pscustomobject]@{ valid = $false; reason = "stale_or_future_health_report"; payload = $Payload; generated_utc = $GeneratedUtc }
    }
    if (-not $Payload.boundary -or [string]$Payload.boundary.classification -ne "forward_runtime_health_local_check_only" -or
        $Payload.boundary.can_trade -isnot [bool] -or $Payload.boundary.sends_orders -isnot [bool] -or
        [bool]$Payload.boundary.can_trade -or [bool]$Payload.boundary.sends_orders) {
        return [pscustomobject]@{ valid = $false; reason = "unsafe_or_missing_health_boundary"; payload = $Payload; generated_utc = $GeneratedUtc }
    }
    $Gates = @($Payload.gates)
    if ($Gates.Count -eq 0) { return [pscustomobject]@{ valid = $false; reason = "missing_health_gates"; payload = $Payload; generated_utc = $GeneratedUtc } }
    $Seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($Gate in $Gates) {
        if (-not [string]$Gate.name -or -not $Seen.Add([string]$Gate.name) -or $Gate.passed -isnot [bool] -or [string]$Gate.severity -notin @("hard", "soft")) {
            return [pscustomobject]@{ valid = $false; reason = "invalid_or_duplicate_health_gate"; payload = $Payload; generated_utc = $GeneratedUtc }
        }
    }
    foreach ($RequiredGateName in $Repairable) {
        $RequiredGate = @($Gates | Where-Object { [string]$_.name -eq $RequiredGateName })
        if ($RequiredGate.Count -ne 1 -or [string]$RequiredGate[0].severity -ne "hard" -or $RequiredGate[0].passed -isnot [bool]) {
            return [pscustomobject]@{ valid = $false; reason = "missing_or_invalid_repairable_health_gate"; payload = $Payload; generated_utc = $GeneratedUtc }
        }
    }
    return [pscustomobject]@{ valid = $true; reason = "valid"; payload = $Payload; generated_utc = $GeneratedUtc }
}

function Test-HealthGatesPassed {
    param(
        [Parameter(Mandatory = $true)]$Health,
        [Parameter(Mandatory = $true)][string[]]$GateNames
    )
    foreach ($GateName in $GateNames) {
        $Gate = @($Health.gates | Where-Object { [string]$_.name -eq $GateName } | Select-Object -First 1)
        if ($Gate.Count -ne 1 -or $Gate[0].passed -isnot [bool] -or -not [bool]$Gate[0].passed) { return $false }
    }
    return $true
}

$HealthPath = Resolve-RepairPath -Path $HealthReport -RequiredDirectory "docs"
$RepairStatePath = Resolve-RepairPath -Path $StatePath -RequiredDirectory "logs"
$RepairStatusPath = Resolve-RepairPath -Path $StatusPath -RequiredDirectory "logs"
$RuntimeScript = Join-Path $Root "ops\autostart\Start-TradingOSRuntime.ps1"
$RuntimeStartStatusPath = Join-Path $Root "logs\runtime_autostart_status.json"
$Repairable = @(
    "panel_port_open",
    "forward_loop_pid_alive",
    "crowd_loop_pid_alive",
    "daily_backup_loop_pid_alive",
    "microstructure_book_loop_status_ok",
    "microstructure_book_loop_pid_alive",
    "microstructure_book_loop_fresh",
    "real_edge_observer_loop_status_ok",
    "real_edge_observer_loop_pid_alive",
    "real_edge_observer_loop_fresh"
)

$RepairMutexName = (Get-TradingOSRuntimeMutexName -Root $Root) + "_Repair"
$RepairMutex = New-Object System.Threading.Mutex($false, $RepairMutexName)
$RepairMutexAcquired = $false
$Report = $null
try {
    try { $RepairMutexAcquired = $RepairMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $RepairMutexAcquired = $true }
    if (-not $RepairMutexAcquired) {
        $Report = [ordered]@{
            ts = (Get-Date).ToUniversalTime().ToString("o")
            decision = "skipped_repair_in_progress"
            root = $Root
            repair_mutex = $RepairMutexName
            live_trading_locked = $true
            changes_strategy = $false
            sends_orders = $false
            can_trade = $false
        }
        $Report | ConvertTo-Json -Depth 6
        return
    }

    $Now = (Get-Date).ToUniversalTime()
    $History = @()
    $StateValid = $true
    $StateError = $null
    $UnresolvedAttemptId = $null
    if (Test-Path -LiteralPath $RepairStatePath) {
        try {
            $State = Get-Content -Raw -LiteralPath $RepairStatePath -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            foreach ($RequiredStateField in @("schema_version", "updated_at", "window_minutes", "max_repairs", "active_attempt_id", "repair_timestamps")) {
                if ($State.PSObject.Properties.Name -notcontains $RequiredStateField) { throw "missing repair state field: $RequiredStateField" }
            }
            if ([int]$State.schema_version -ne 1) { throw "unsupported repair state schema" }
            $StateUpdatedUtc = ([datetime]::Parse([string]$State.updated_at)).ToUniversalTime()
            if ($StateUpdatedUtc -gt $Now.AddMinutes(1)) { throw "future repair state update" }
            if ([int]$State.window_minutes -lt 1 -or [int]$State.window_minutes -gt 10080) { throw "invalid stored window_minutes" }
            if ([int]$State.max_repairs -lt 0 -or [int]$State.max_repairs -gt 100) { throw "invalid stored max_repairs" }
            if ($null -eq $State.repair_timestamps) { throw "missing repair_timestamps" }
            $ParsedHistory = New-Object System.Collections.Generic.List[datetime]
            foreach ($Timestamp in @($State.repair_timestamps)) {
                $Parsed = ([datetime]::Parse([string]$Timestamp)).ToUniversalTime()
                if ($Parsed -gt $Now.AddMinutes(1)) { throw "future repair timestamp" }
                $ParsedHistory.Add($Parsed) | Out-Null
            }
            $History = @($ParsedHistory | Sort-Object)
            if ([string]$State.active_attempt_id) {
                $UnresolvedAttemptId = ([guid][string]$State.active_attempt_id).ToString()
                if ($History.Count -eq 0 -or [math]::Abs(($StateUpdatedUtc - $History[-1]).TotalMinutes) -gt 5) {
                    throw "active attempt has no matching reserved timestamp"
                }
            }
            if ([string]$State.last_attempt_id) { $null = ([guid][string]$State.last_attempt_id) }
        } catch {
            $StateValid = $false
            $StateError = $_.Exception.Message
        }
    }
    $WindowStart = $Now.AddMinutes(-$WindowMinutes)
    $RecentHistory = @($History | Where-Object { $_ -ge $WindowStart })
    $Matched = @()
    $RuntimeStartResult = $null
    $RepairError = $null
    $AttemptId = $null
    $StartupInProgress = Test-TradingOSRuntimeStartInProgress -Root $Root
    $ShutdownRequested = Test-TradingOSRuntimeShutdownRequested -Root $Root

    if ($ShutdownRequested) {
        $Decision = "blocked_shutdown_requested"
    } elseif (-not $StateValid) {
        # Fail closed and preserve the corrupt bytes for manual recovery.
        $Decision = "blocked_invalid_repair_state"
    } elseif ($UnresolvedAttemptId) {
        $Decision = "blocked_unresolved_repair_attempt"
    } elseif ($StartupInProgress) {
        $Decision = "skipped_startup_in_progress"
    } else {
        $HealthRead = Read-StrictHealthReport -Path $HealthPath -ReferenceUtc $Now
        if (-not $HealthRead.valid) {
            $Decision = if ($HealthRead.reason -eq "missing_health_report") { "skipped_missing_health_report" } else { "blocked_invalid_or_stale_health_report" }
            $RepairError = $HealthRead.reason
        } else {
            $Health = $HealthRead.payload
            $Failed = @($Health.gates | Where-Object { [string]$_.severity -eq "hard" -and $_.passed -is [bool] -and -not [bool]$_.passed } | ForEach-Object { [string]$_.name })
            $Matched = @($Failed | Where-Object { $_ -in $Repairable })
            $LiveRestartPairs = [ordered]@{
                microstructure_book_loop_status_ok = "microstructure_book_loop_pid_alive"
                microstructure_book_loop_fresh = "microstructure_book_loop_pid_alive"
                real_edge_observer_loop_status_ok = "real_edge_observer_loop_pid_alive"
                real_edge_observer_loop_fresh = "real_edge_observer_loop_pid_alive"
            }
            $UnsafeLiveRestartGates = @($Matched | Where-Object { $LiveRestartPairs.Contains($_) -and $LiveRestartPairs[$_] -notin $Matched })
            if ($Matched.Count -eq 0) {
                $Decision = "not_needed_or_nonrepairable"
            } elseif ($UnsafeLiveRestartGates.Count -gt 0) {
                $Decision = "blocked_live_component_requires_targeted_restart"
            } elseif ($RecentHistory.Count -ge $MaxRepairs) {
                $Decision = "blocked_restart_budget_exhausted"
            } elseif ($DryRun) {
                $Decision = "dry_run_restart_ready"
            } else {
                $AttemptStarted = (Get-Date).ToUniversalTime()
                $AttemptId = [guid]::NewGuid().ToString()
                # Reserve budget durably before starting; a crash cannot make the
                # same attempt disappear from the rate limiter.
                $RecentHistory = @($RecentHistory + $AttemptStarted)
                $ReservedState = [ordered]@{
                    schema_version = 1
                    updated_at = $AttemptStarted.ToString("o")
                    window_minutes = $WindowMinutes
                    max_repairs = $MaxRepairs
                    active_attempt_id = $AttemptId
                    repair_timestamps = @($RecentHistory | ForEach-Object { $_.ToString("o") })
                }
                Write-TradingOSJsonFileAtomic -Path $RepairStatePath -Payload $ReservedState -Depth 6

                try {
                    & $RuntimeScript -AttemptId $AttemptId -InvocationMode AutomaticRepair | Out-Null
                } catch {
                    $RepairError = $_.Exception.Message
                }
                if (Test-Path -LiteralPath $RuntimeStartStatusPath) {
                    try { $RuntimeStartResult = Get-Content -LiteralPath $RuntimeStartStatusPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch {}
                }
                $StartStatusOwned = $false
                try {
                    $StartStatusOwned = [string]$RuntimeStartResult.attempt_id -eq $AttemptId -and
                        [System.IO.Path]::GetFullPath([string]$RuntimeStartResult.root).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase) -and
                        ([datetime]::Parse([string]$RuntimeStartResult.ts)).ToUniversalTime() -ge $AttemptStarted.AddSeconds(-1)
                } catch {}
                $Manifest = Get-TradingOSRuntimeManifest -Root $Root
                $RuntimeStates = @(Get-TradingOSRuntimeStates -Root $Root -Manifest $Manifest)
                $RuntimeVerified = @($RuntimeStates | Where-Object { $_.required -and -not $_.job_contained }).Count -eq 0
                $PanelState = Get-TradingOSControlPanelOwnershipState -Root $Root -Port 8765
                $PanelVerified = [bool]$PanelState.job_contained
                $StartCompleted = $StartStatusOwned -and [string]$RuntimeStartResult.status -eq "completed" -and @($RuntimeStartResult.runtime_components_failed).Count -eq 0

                $RefreshedHealth = $null
                $HealthRefreshDeadline = (Get-Date).AddSeconds(5)
                do {
                    $Candidate = Read-StrictHealthReport -Path $HealthPath -ReferenceUtc ((Get-Date).ToUniversalTime())
                    if ($Candidate.valid -and $Candidate.generated_utc -ge $AttemptStarted -and (Test-HealthGatesPassed -Health $Candidate.payload -GateNames $Matched)) {
                        $RefreshedHealth = $Candidate
                        break
                    }
                    if ((Get-Date) -ge $HealthRefreshDeadline) { break }
                    Start-Sleep -Milliseconds 500
                } while ($true)

                if ($StartCompleted -and $RuntimeVerified -and $PanelVerified -and $RefreshedHealth) {
                    $Decision = "restart_verified"
                } elseif ($StartCompleted -and $RuntimeVerified -and $PanelVerified) {
                    $Decision = "restart_runtime_verified_health_refresh_pending"
                } elseif ($StartStatusOwned -and [string]$RuntimeStartResult.status -eq "degraded") {
                    $Decision = "restart_attempt_degraded"
                } elseif ($RepairError) {
                    $Decision = "restart_attempt_failed"
                } else {
                    $Decision = "restart_attempt_unverified"
                }
            }
        }
    }

    if ($StateValid -and -not $UnresolvedAttemptId -and -not $ShutdownRequested -and -not $StartupInProgress -and -not ($AttemptId -and -not $DryRun)) {
        $RepairState = [ordered]@{
            schema_version = 1
            updated_at = $Now.ToString("o")
            window_minutes = $WindowMinutes
            max_repairs = $MaxRepairs
            active_attempt_id = $null
            repair_timestamps = @($RecentHistory | ForEach-Object { $_.ToString("o") })
        }
        Write-TradingOSJsonFileAtomic -Path $RepairStatePath -Payload $RepairState -Depth 6
    } elseif ($StateValid -and $AttemptId) {
        $CompletedState = [ordered]@{
            schema_version = 1
            updated_at = (Get-Date).ToUniversalTime().ToString("o")
            window_minutes = $WindowMinutes
            max_repairs = $MaxRepairs
            active_attempt_id = $null
            last_attempt_id = $AttemptId
            last_attempt_decision = $Decision
            repair_timestamps = @($RecentHistory | ForEach-Object { $_.ToString("o") })
        }
        Write-TradingOSJsonFileAtomic -Path $RepairStatePath -Payload $CompletedState -Depth 6
    }

    $Report = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("o")
        decision = $Decision
        attempt_id = $AttemptId
        unresolved_attempt_id = $UnresolvedAttemptId
        matched_repairable_gates = $Matched
        repair_count_in_window = $RecentHistory.Count
        max_repairs_in_window = $MaxRepairs
        window_minutes = $WindowMinutes
        dry_run = [bool]$DryRun
        startup_in_progress = $StartupInProgress
        shutdown_requested = $ShutdownRequested
        runtime_start_status = if ($RuntimeStartResult) { [string]$RuntimeStartResult.status } else { $null }
        runtime_start_attempt_owned = [bool]($AttemptId -and $RuntimeStartResult -and [string]$RuntimeStartResult.attempt_id -eq $AttemptId)
        runtime_start_failed_components = if ($RuntimeStartResult) { @($RuntimeStartResult.runtime_components_failed) } else { @() }
        state_error = $StateError
        error = $RepairError
        root = $Root
        repair_mutex = $RepairMutexName
        live_trading_locked = $true
        changes_strategy = $false
        sends_orders = $false
        can_trade = $false
    }
    Write-TradingOSJsonFileAtomic -Path $RepairStatusPath -Payload $Report -Depth 7
    $Report | ConvertTo-Json -Depth 7
} finally {
    if ($RepairMutexAcquired) {
        try { $RepairMutex.ReleaseMutex() } catch {}
    }
    $RepairMutex.Dispose()
}
