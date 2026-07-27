param(
    [string]$TaskPrefix = "TradingOS",
    [ValidateRange(1024, 65535)][int]$ControlPanelPort = 8765,
    [int]$MemoryMaintenanceMinutes = 15
)

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1")
$TaskPrefix = Assert-TradingOSTaskPrefix -TaskPrefix $TaskPrefix
if ($MemoryMaintenanceMinutes -lt 5 -or $MemoryMaintenanceMinutes -gt 1440) { throw "MemoryMaintenanceMinutes must be between 5 and 1440." }

function Get-TaskSummary {
    param([string]$Name)
    $Task = Get-ScheduledTask -TaskName $Name -TaskPath "\" -ErrorAction SilentlyContinue
    $Info = Get-ScheduledTaskInfo -TaskName $Name -TaskPath "\" -ErrorAction SilentlyContinue
    if (-not $Task) {
        return [ordered]@{ exists = $false; name = $Name }
    }
    return [ordered]@{
        exists = $true
        name = $Name
        state = [string]$Task.State
        last_run_time = if ($Info) { [string]$Info.LastRunTime } else { $null }
        last_task_result = if ($Info) { $Info.LastTaskResult } else { $null }
        next_run_time = if ($Info) { [string]$Info.NextRunTime } else { $null }
        action_execute = if ($Task.Actions) { $Task.Actions[0].Execute } else { $null }
        action_arguments = if ($Task.Actions) { $Task.Actions[0].Arguments } else { $null }
    }
}

function Read-PlainText {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    return [System.IO.File]::ReadAllText($Path)
}

$ControlPanelState = Get-TradingOSControlPanelOwnershipState -Root $Root -Port $ControlPanelPort

$ForwardStatusPath = Join-Path $Root "logs\forward_paper_feed\scheduled_task_last_run.json"
$PanelStatusPath = Join-Path $Root "logs\control_panel_autostart_status.json"
$ForwardLoopStatusPath = Join-Path $Root "logs\forward_paper_feed\forward_scheduler_loop_status.json"
$WatchdogLoopStatusPath = Join-Path $Root "logs\forward_paper_feed\forward_runtime_watchdog_loop_status.json"
$HealthStatusPath = Join-Path $Root "docs\FORWARD_RUNTIME_HEALTH_2026-06-16.json"
$CrowdFadeLoopStatusPath = Join-Path $Root "logs\forward_paper_feed\crowd_fade_observer_loop_status.json"
$CrowdFadeLastRunPath = Join-Path $Root "logs\forward_paper_feed\crowd_fade_refresh_last_run.json"
$BackupLoopStatusPath = Join-Path $Root "logs\runtime_backup\daily_drive_backup_loop_status.json"
$BackupLastRunPath = Join-Path $Root "logs\runtime_backup\daily_drive_backup_last_run.json"
$CrossVenueLoopStatusPath = Join-Path $Root "logs\cross_venue_data\cross_venue_data_loop_status.json"
$CrossVenueLastRunPath = Join-Path $Root "logs\cross_venue_data\cross_venue_refresh_last_run.json"
$MicrostructureLoopStatusPath = Join-Path $Root "logs\cross_venue_microstructure\microstructure_loop_status.json"
$MicrostructureLastRunPath = Join-Path $Root "logs\cross_venue_microstructure\microstructure_refresh_last_run.json"
$MicrostructureBookLoopStatusPath = Join-Path $Root "logs\cross_venue_microstructure\microstructure_book_loop_status.json"
$MicrostructureWatchdogStatusPath = Join-Path $Root "logs\cross_venue_microstructure\microstructure_watchdog_loop_status.json"
$MicrostructureUnblockStatusLoopPath = Join-Path $Root "logs\cross_venue_microstructure\microstructure_unblock_status_loop_status.json"
$MicrostructureUnblockStatusReportPath = Join-Path $Root "docs\MICROSTRUCTURE_UNBLOCK_STATUS_2026-07-03.json"
$MicrostructureHealthPath = Join-Path $Root "docs\CROSS_VENUE_MICROSTRUCTURE_HEALTH_2026-06-24.json"
$MicrostructureHealthNotifyPath = Join-Path $Root "docs\CROSS_VENUE_MICROSTRUCTURE_HEALTH_TELEGRAM_2026-06-24.json"
$MicrostructureSnapshotGatePath = Join-Path $Root "docs\CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json"
$LiquidationForceOrderLoopStatusPath = Join-Path $Root "logs\liquidation_force_order\liquidation_force_order_loop_status.json"
$LiquidationForceOrderWatchdogStatusPath = Join-Path $Root "logs\liquidation_force_order\liquidation_force_order_watchdog_loop_status.json"
$LiquidationForceOrderWatchdogReportPath = Join-Path $Root "docs\LIQUIDATION_FORCE_ORDER_COLLECTOR_WATCHDOG_2026-06-30.json"
$LiquidationForceOrderDataQualityPath = Join-Path $Root "docs\LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30.json"
$LiquidationForceOrderFirstEventPath = Join-Path $Root "docs\LIQUIDATION_FORCE_ORDER_FIRST_EVENT_TRIGGER_2026-06-30.json"
$BybitAllLiquidationLoopStatusPath = Join-Path $Root "logs\liquidation_bybit\bybit_all_liquidation_loop_status.json"
$BybitAllLiquidationWatchdogStatusPath = Join-Path $Root "logs\liquidation_bybit\bybit_all_liquidation_watchdog_loop_status.json"
$BybitAllLiquidationWatchdogReportPath = Join-Path $Root "docs\BYBIT_ALL_LIQUIDATION_COLLECTOR_WATCHDOG_2026-07-01.json"
$ResearchRuntimeSupervisorLauncherPath = Join-Path $Root "logs\research_runtime_supervisor_autostart_status.json"
$ResearchRuntimeSupervisorLoopPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260712_research_runtime_supervisor\runtime\loop_status.json"
$ResearchRuntimeSupervisorReportPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260712_research_runtime_supervisor\runtime\LATEST.json"
$IsolatedResearchRegistryComponentsLauncherPath = Join-Path $Root "logs\isolated_research_registry_components_launcher_status.json"
$DeribitOptionsSurfaceCollectorLauncherPath = Join-Path $Root "logs\deribit_options_surface_collector_autostart_status.json"
$DeribitOptionsSurfaceCollectorLoopPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260711_deribit_options_surface_collector\runtime_v2\loop_status.json"
$DeribitOptionsSurfaceCollectorReportPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260711_deribit_options_surface_collector\runtime_v2\LATEST.json"
$DeribitOptionsReadinessLauncherPath = Join-Path $Root "logs\deribit_options_readiness_guard_autostart_status.json"
$DeribitOptionsReadinessLoopPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260711_deribit_options_readiness_guard\runtime\loop_status.json"
$DeribitOptionsReadinessReportPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260711_deribit_options_readiness_guard\runtime\LATEST.json"
$DeribitOptionsSkewForwardLauncherPath = Join-Path $Root "logs\deribit_options_skew_forward_autostart_status.json"
$DeribitOptionsSkewForwardLoopPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260712_deribit_options_skew_forward\runtime\loop_status.json"
$DeribitOptionsSkewForwardReportPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260712_deribit_options_skew_forward\runtime\LATEST.json"
$CexDexFundingCollectorStatusPath = Join-Path $Root "logs\cex_dex_funding\cex_dex_funding_collector_loop_status.json"
$CexDexFundingCollectorReportPath = Join-Path $Root "docs\CEX_DEX_FUNDING_LEAD_LAG_DATA_QUALITY_2026-07-13.json"
$CexFundingFreshnessWatchdogStatusPath = Join-Path $Root "logs\cex_dex_funding\cex_dex_funding_freshness_watchdog_loop_status.json"
$CexFundingFreshnessWatchdogReportPath = Join-Path $Root "docs\CEX_FUNDING_FRESHNESS_WATCHDOG_2026-07-13.json"
$CexFundingFreshnessIncidentAlertReportPath = Join-Path $Root "docs\CEX_FUNDING_FRESHNESS_INCIDENT_ALERT_2026-07-13.json"
$CrossStackReplicationTransitionStatusPath = Join-Path $Root "logs\cross_stack_replication\cross_stack_replication_transition_loop_status.json"
$RealEdgeObserverStatusPath = Join-Path $Root "logs\real_edge_observer\real_edge_observer_pulse_loop_status.json"
$RuntimeMemoryMaintenanceStatusPath = Join-Path $Root "logs\runtime_memory_maintenance_status.json"
$RuntimeMemoryMaintenanceWhatIfStatusPath = Join-Path $Root "logs\runtime_memory_maintenance_whatif_status.json"
$RuntimeManifest = Get-TradingOSRuntimeManifest -Root $Root
$RuntimeStates = @(Get-TradingOSRuntimeStates -Root $Root -Manifest $RuntimeManifest)
$RuntimeHealthy = @($RuntimeStates | Where-Object { $_.required -and -not $_.job_contained }).Count -eq 0
$MaintenanceTaskPattern = '^' + [regex]::Escape($TaskPrefix) + '_RuntimeMemoryMaintenance_[0-9]+M$'
$MaintenanceTaskCandidates = @(Get-ScheduledTask -TaskPath "\" -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -match $MaintenanceTaskPattern } |
    ForEach-Object { Get-TaskSummary $_.TaskName })

$Status = [ordered]@{
    ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
    live_trading_locked = $true
    can_trade = $false
    runtime_health = [ordered]@{
        decision = if ($RuntimeHealthy) { "runtime_components_healthy" } else { "runtime_components_degraded" }
        expected = $RuntimeStates.Count
        healthy = @($RuntimeStates | Where-Object { $_.job_contained }).Count
        failed = @($RuntimeStates | Where-Object { $_.required -and -not $_.job_contained } | ForEach-Object { $_.id })
        working_set_mb = [math]::Round((($RuntimeStates | Measure-Object -Property working_set_mb -Sum).Sum), 1)
        private_mb = [math]::Round((($RuntimeStates | Measure-Object -Property private_mb -Sum).Sum), 1)
        components = @($RuntimeStates | ForEach-Object {
            [ordered]@{
                id = $_.id
                decision = $_.ownership_decision
                process_decision = $_.decision
                job_decision = $_.job_decision
                job_contained = [bool]$_.job_contained
                matching_script_pids = @($_.matching_script_pids)
                pid = $_.pid
                working_set_mb = $_.working_set_mb
                private_mb = $_.private_mb
                lock_present = $_.lock_present
                identity_valid = $_.identity_valid
            }
        })
    }
    control_panel = [ordered]@{
        port = $ControlPanelPort
        listening = [bool]$ControlPanelState.listening
        decision = $ControlPanelState.ownership_decision
        process_decision = $ControlPanelState.decision
        job_decision = $ControlPanelState.job_decision
        job_contained = [bool]$ControlPanelState.job_contained
        identity_valid = [bool]$ControlPanelState.identity_valid
        api_root_valid = [bool]$ControlPanelState.api_root_valid
        owning_process = $ControlPanelState.pid
        candidate_pids = @($ControlPanelState.candidate_pids)
        exact_script_pids = @($ControlPanelState.exact_script_pids)
        status_file = $PanelStatusPath
    }
    tasks = [ordered]@{
        control_panel_logon = Get-TaskSummary "${TaskPrefix}_ControlPanel_Logon"
        forward_paper_4h = Get-TaskSummary "${TaskPrefix}_ForwardPaper_4H"
        runtime_memory_maintenance = Get-TaskSummary "${TaskPrefix}_RuntimeMemoryMaintenance_${MemoryMaintenanceMinutes}M"
        runtime_memory_maintenance_candidates = $MaintenanceTaskCandidates
    }
    latest_status_files = [ordered]@{
        control_panel_autostart = Read-PlainText $PanelStatusPath
        forward_scheduler = Read-PlainText $ForwardStatusPath
        forward_loop = Read-PlainText $ForwardLoopStatusPath
        watchdog_loop = Read-PlainText $WatchdogLoopStatusPath
        runtime_health = Read-PlainText $HealthStatusPath
        crowd_fade_loop = Read-PlainText $CrowdFadeLoopStatusPath
        crowd_fade_last_run = Read-PlainText $CrowdFadeLastRunPath
        daily_backup_loop = Read-PlainText $BackupLoopStatusPath
        daily_backup_last_run = Read-PlainText $BackupLastRunPath
        cross_venue_data_loop = Read-PlainText $CrossVenueLoopStatusPath
        cross_venue_data_last_run = Read-PlainText $CrossVenueLastRunPath
        cross_venue_microstructure_loop = Read-PlainText $MicrostructureLoopStatusPath
        cross_venue_microstructure_last_run = Read-PlainText $MicrostructureLastRunPath
        cross_venue_microstructure_book_loop = Read-PlainText $MicrostructureBookLoopStatusPath
        cross_venue_microstructure_watchdog = Read-PlainText $MicrostructureWatchdogStatusPath
        cross_venue_microstructure_unblock_loop = Read-PlainText $MicrostructureUnblockStatusLoopPath
        cross_venue_microstructure_unblock_report = Read-PlainText $MicrostructureUnblockStatusReportPath
        cross_venue_microstructure_health = Read-PlainText $MicrostructureHealthPath
        cross_venue_microstructure_health_notify = Read-PlainText $MicrostructureHealthNotifyPath
        cross_venue_microstructure_snapshot_gate = Read-PlainText $MicrostructureSnapshotGatePath
        liquidation_force_order_loop = Read-PlainText $LiquidationForceOrderLoopStatusPath
        liquidation_force_order_watchdog = Read-PlainText $LiquidationForceOrderWatchdogStatusPath
        liquidation_force_order_watchdog_report = Read-PlainText $LiquidationForceOrderWatchdogReportPath
        liquidation_force_order_data_quality = Read-PlainText $LiquidationForceOrderDataQualityPath
        liquidation_force_order_first_event = Read-PlainText $LiquidationForceOrderFirstEventPath
        bybit_all_liquidation_loop = Read-PlainText $BybitAllLiquidationLoopStatusPath
        bybit_all_liquidation_watchdog = Read-PlainText $BybitAllLiquidationWatchdogStatusPath
        bybit_all_liquidation_watchdog_report = Read-PlainText $BybitAllLiquidationWatchdogReportPath
        research_runtime_supervisor_launcher = Read-PlainText $ResearchRuntimeSupervisorLauncherPath
        research_runtime_supervisor_loop = Read-PlainText $ResearchRuntimeSupervisorLoopPath
        research_runtime_supervisor_report = Read-PlainText $ResearchRuntimeSupervisorReportPath
        isolated_research_registry_components_launcher = Read-PlainText $IsolatedResearchRegistryComponentsLauncherPath
        deribit_options_surface_collector_launcher = Read-PlainText $DeribitOptionsSurfaceCollectorLauncherPath
        deribit_options_surface_collector_loop = Read-PlainText $DeribitOptionsSurfaceCollectorLoopPath
        deribit_options_surface_collector_report = Read-PlainText $DeribitOptionsSurfaceCollectorReportPath
        deribit_options_readiness_launcher = Read-PlainText $DeribitOptionsReadinessLauncherPath
        deribit_options_readiness_loop = Read-PlainText $DeribitOptionsReadinessLoopPath
        deribit_options_readiness_report = Read-PlainText $DeribitOptionsReadinessReportPath
        deribit_options_skew_forward_launcher = Read-PlainText $DeribitOptionsSkewForwardLauncherPath
        deribit_options_skew_forward_loop = Read-PlainText $DeribitOptionsSkewForwardLoopPath
        deribit_options_skew_forward_report = Read-PlainText $DeribitOptionsSkewForwardReportPath
        cex_dex_funding_collector_loop = Read-PlainText $CexDexFundingCollectorStatusPath
        cex_dex_funding_collector_report = Read-PlainText $CexDexFundingCollectorReportPath
        cex_funding_freshness_watchdog_loop = Read-PlainText $CexFundingFreshnessWatchdogStatusPath
        cex_funding_freshness_watchdog_report = Read-PlainText $CexFundingFreshnessWatchdogReportPath
        cex_funding_freshness_incident_alert_report = Read-PlainText $CexFundingFreshnessIncidentAlertReportPath
        cross_stack_replication_transition_loop = Read-PlainText $CrossStackReplicationTransitionStatusPath
        real_edge_observer_loop = Read-PlainText $RealEdgeObserverStatusPath
        runtime_memory_maintenance = Read-PlainText $RuntimeMemoryMaintenanceStatusPath
        runtime_memory_maintenance_whatif = Read-PlainText $RuntimeMemoryMaintenanceWhatIfStatusPath
    }
}

$Status | ConvertTo-Json -Depth 8
