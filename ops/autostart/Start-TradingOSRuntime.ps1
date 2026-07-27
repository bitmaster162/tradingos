param(
    [ValidateRange(1024, 65535)][int]$ControlPanelPort = 8765,
    [int]$ForwardSleepSeconds = 14400,
    [int]$CrowdFadeSleepSeconds = 3600,
    [int]$WatchdogSleepSeconds = 900,
    [int]$BackupSleepSeconds = 86400,
    [int]$CrossVenueSleepSeconds = 86400,
    [int]$CrossVenueMicrostructureSleepSeconds = 15,
    [int]$CrossVenueMicrostructureBookSleepSeconds = 20,
    [int]$CrossVenueMicrostructureWatchdogSleepSeconds = 60,
    [int]$BinanceSpotPerpAggressorFlowSleepSeconds = 10,
    [int]$BybitAllLiquidationWatchdogSleepSeconds = 300,
    [int]$LiquidationForceOrderCycleSeconds = 300,
    [int]$LiquidationForceOrderWatchdogSleepSeconds = 600,
    [int]$LiquidationForceOrderTransportContinuitySleepSeconds = 30,
    [int]$CrossStackReplicationTransitionSleepSeconds = 900,
    [int]$RealEdgeObserverPulseSleepSeconds = 900,
    [int]$MicrostructureUnblockStatusSleepSeconds = 900,
    [int]$ResearchRuntimeSupervisorSleepSeconds = 300,
    [int]$DeribitOptionsSurfaceCollectorSleepSeconds = 300,
    [int]$DeribitOptionsReadinessSleepSeconds = 300,
    [int]$DeribitOptionsSkewForwardSleepSeconds = 300,
    [int]$CexDexFundingCollectorSleepSeconds = 60,
    [int]$CexFundingFreshnessWatchdogSleepSeconds = 60,
    [int]$BitunixWO105V3R4RestCadenceSeconds = 300,
    [int]$PostFillMarkoutForwardPulseSeconds = 300,
    [string]$AttemptId = "",
    [ValidateSet("Explicit", "Autostart", "AutomaticRepair")][string]$InvocationMode = "Explicit"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LifecycleScript = Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1"
. $LifecycleScript
if ($AttemptId) {
    try { $AttemptId = ([guid]$AttemptId).ToString() } catch { throw "AttemptId must be a GUID." }
} else {
    $AttemptId = [guid]::NewGuid().ToString()
}
if ([guid]$AttemptId -eq [guid]::Empty) { throw "AttemptId cannot be the empty GUID." }
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$StatusPath = Join-Path $LogDir "runtime_autostart_status.json"
$StartMutexName = Get-TradingOSRuntimeMutexName -Root $Root
$StartMutex = New-Object System.Threading.Mutex($false, $StartMutexName)
$StartMutexAcquired = $false
$RuntimeHealthy = $false
$RuntimeVerified = $false
$StartCommitted = $false
$AttemptReserved = $false
$ShutdownSentinelPath = Get-TradingOSRuntimeShutdownSentinelPath -Root $Root
$ShutdownStartMarkerPath = Get-TradingOSRuntimeShutdownStartMarkerPath -Root $Root
$ShutdownStartBypassAcquired = $false
$ShutdownSentinelCleared = $false
$RollbackResult = $null
try {
    try {
        $StartMutexAcquired = $StartMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $StartMutexAcquired = $true
    }
    if (-not $StartMutexAcquired) {
        $BusyStatusPath = Join-Path $Root "logs\runtime_startup_mutex_status.json"
        $BusyStatus = [ordered]@{
            ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            status = "startup_already_in_progress"
            root = $Root
            mutex = $StartMutexName
            attempt_id = $AttemptId
            live_trading_locked = $true
            can_trade = $false
        }
        Write-TradingOSJsonFileAtomic -Path $BusyStatusPath -Payload $BusyStatus -Depth 4
        throw "TradingOS runtime startup is already in progress."
    }

    $AttemptReservation = Enter-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $AttemptId -NewInvocation
    $AttemptReserved = $true

    if (Test-Path -LiteralPath $ShutdownSentinelPath) {
        if ($InvocationMode -ne "Explicit") {
            $ShutdownBlockedStatus = [ordered]@{
                ts = (Get-Date).ToUniversalTime().ToString("o")
                status = "blocked_runtime_shutdown_requested"
                root = $Root
                attempt_id = $AttemptId
                invocation_mode = $InvocationMode
                shutdown_sentinel = $ShutdownSentinelPath
                live_trading_locked = $true
                can_trade = $false
            }
            Write-TradingOSJsonFileAtomic -Path $StatusPath -Payload $ShutdownBlockedStatus -Depth 5
            throw "TradingOS runtime remains explicitly stopped; $InvocationMode cannot clear the shutdown sentinel."
        }
        Write-TradingOSJsonFileAtomic -Path $ShutdownStartMarkerPath -Payload ([ordered]@{
            schema_version = 1
            generated_at = (Get-Date).ToUniversalTime().ToString('o')
            attempt_id = $AttemptId
            invocation_id = [string]$AttemptReservation.invocation_id
            root = $Root
            live_trading_locked = $true
            can_trade = $false
        }) -Depth 5
        $ShutdownStartBypassAcquired = $true
    }

$LoopStdout = Join-Path $LogDir "forward_paper_feed\forward_loop_stdout.log"
$LoopStderr = Join-Path $LogDir "forward_paper_feed\forward_loop_stderr.log"
$WatchdogProcessStdout = Join-Path $LogDir "forward_paper_feed\forward_runtime_watchdog_process_stdout.log"
$WatchdogProcessStderr = Join-Path $LogDir "forward_paper_feed\forward_runtime_watchdog_process_stderr.log"
$CrowdFadeStdout = Join-Path $LogDir "forward_paper_feed\crowd_fade_loop_process_stdout.log"
$CrowdFadeStderr = Join-Path $LogDir "forward_paper_feed\crowd_fade_loop_process_stderr.log"
$BackupStdout = Join-Path $LogDir "runtime_backup\daily_backup_loop_stdout.log"
$BackupStderr = Join-Path $LogDir "runtime_backup\daily_backup_loop_stderr.log"
$CrossVenueStdout = Join-Path $LogDir "cross_venue_data\cross_venue_loop_stdout.log"
$CrossVenueStderr = Join-Path $LogDir "cross_venue_data\cross_venue_loop_stderr.log"
$MicrostructureStdout = Join-Path $LogDir "cross_venue_microstructure\microstructure_loop_stdout.log"
$MicrostructureStderr = Join-Path $LogDir "cross_venue_microstructure\microstructure_loop_stderr.log"
$MicrostructureBookStdout = Join-Path $LogDir "cross_venue_microstructure\microstructure_book_process_stdout.log"
$MicrostructureBookStderr = Join-Path $LogDir "cross_venue_microstructure\microstructure_book_process_stderr.log"
$MicrostructureWatchdogStdout = Join-Path $LogDir "cross_venue_microstructure\microstructure_watchdog_process_stdout.log"
$MicrostructureWatchdogStderr = Join-Path $LogDir "cross_venue_microstructure\microstructure_watchdog_process_stderr.log"
$BinanceSpotPerpAggressorFlowStdout = Join-Path $LogDir "binance_spot_perp_aggressor_flow\binance_spot_perp_aggressor_flow_process_stdout.log"
$BinanceSpotPerpAggressorFlowStderr = Join-Path $LogDir "binance_spot_perp_aggressor_flow\binance_spot_perp_aggressor_flow_process_stderr.log"
$BybitAllLiquidationWatchdogStdout = Join-Path $LogDir "liquidation_bybit\bybit_all_liquidation_watchdog_process_stdout.log"
$BybitAllLiquidationWatchdogStderr = Join-Path $LogDir "liquidation_bybit\bybit_all_liquidation_watchdog_process_stderr.log"
$LiquidationForceOrderWatchdogStdout = Join-Path $LogDir "liquidation_force_order\liquidation_force_order_watchdog_process_stdout.log"
$LiquidationForceOrderWatchdogStderr = Join-Path $LogDir "liquidation_force_order\liquidation_force_order_watchdog_process_stderr.log"
$LiquidationForceOrderTransportContinuityStdout = Join-Path $LogDir "liquidation_force_order\liquidation_force_order_transport_continuity_process_stdout.log"
$LiquidationForceOrderTransportContinuityStderr = Join-Path $LogDir "liquidation_force_order\liquidation_force_order_transport_continuity_process_stderr.log"
$CrossStackReplicationTransitionStdout = Join-Path $LogDir "cross_stack_replication\cross_stack_replication_transition_process_stdout.log"
$CrossStackReplicationTransitionStderr = Join-Path $LogDir "cross_stack_replication\cross_stack_replication_transition_process_stderr.log"
$RealEdgeObserverProcessStdout = Join-Path $LogDir "real_edge_observer\real_edge_observer_process_stdout.log"
$RealEdgeObserverProcessStderr = Join-Path $LogDir "real_edge_observer\real_edge_observer_process_stderr.log"
$CexDexFundingCollectorStdout = Join-Path $LogDir "cex_dex_funding\cex_dex_funding_collector_process_stdout.log"
$CexDexFundingCollectorStderr = Join-Path $LogDir "cex_dex_funding\cex_dex_funding_collector_process_stderr.log"
$CexFundingFreshnessWatchdogStdout = Join-Path $LogDir "cex_dex_funding\cex_dex_funding_freshness_watchdog_process_stdout.log"
$CexFundingFreshnessWatchdogStderr = Join-Path $LogDir "cex_dex_funding\cex_dex_funding_freshness_watchdog_process_stderr.log"
$BitunixWO105V3R4Stdout = Join-Path $LogDir "bitunix_wo105_v3r4\bitunix_wo105_v3r4_forward_process_stdout.log"
$BitunixWO105V3R4Stderr = Join-Path $LogDir "bitunix_wo105_v3r4\bitunix_wo105_v3r4_forward_process_stderr.log"
$PostFillMarkoutForwardStdout = Join-Path $LogDir "post_fill_markout_forward\post_fill_markout_forward_process_stdout.log"
$PostFillMarkoutForwardStderr = Join-Path $LogDir "post_fill_markout_forward\post_fill_markout_forward_process_stderr.log"
$PanelScript = Join-Path $Root "ops\autostart\Start-TradingOSControlPanel.ps1"
$LoopScript = Join-Path $Root "ops\autostart\Run-ForwardPaperLoop.ps1"
$WatchdogScript = Join-Path $Root "ops\autostart\Run-ForwardRuntimeWatchdogLoop.ps1"
$CrowdFadeScript = Join-Path $Root "ops\autostart\Run-CrowdFadeObserverLoop.ps1"
$BackupScript = Join-Path $Root "ops\local_runtime\Run-DailyRuntimeBackupLoop.ps1"
$CrossVenueScript = Join-Path $Root "ops\autostart\Run-CrossVenueDataLoop.ps1"
$MicrostructureScript = Join-Path $Root "ops\autostart\Run-CrossVenueMicrostructureLoop.ps1"
$MicrostructureBookScript = Join-Path $Root "ops\autostart\Run-CrossVenueMicrostructureBookLoop.ps1"
$MicrostructureWatchdogScript = Join-Path $Root "ops\autostart\Run-CrossVenueMicrostructureWatchdogLoop.ps1"
$BinanceSpotPerpAggressorFlowScript = Join-Path $Root "ops\autostart\Run-BinanceSpotPerpAggressorFlowLoop.ps1"
$BybitAllLiquidationWatchdogStartScript = Join-Path $Root "ops\autostart\Start-BybitAllLiquidationWatchdogLoop.ps1"
$BybitAllLiquidationCollectorStartScript = Join-Path $Root "ops\autostart\Start-BybitAllLiquidationCollectorLoop.ps1"
$LiquidationForceOrderStartScript = Join-Path $Root "ops\autostart\Start-LiquidationForceOrderCollectorLoop.ps1"
$LiquidationForceOrderWatchdogScript = Join-Path $Root "ops\autostart\Run-LiquidationForceOrderWatchdogLoop.ps1"
$LiquidationForceOrderTransportContinuityScript = Join-Path $Root "ops\autostart\Run-LiquidationForceOrderTransportContinuityLoop.ps1"
$CrossStackReplicationTransitionScript = Join-Path $Root "ops\autostart\Run-CrossStackReplicationTransitionMonitorLoop.ps1"
$RealEdgeObserverScript = Join-Path $Root "ops\autostart\Run-RealEdgeObserverPulseLoop.ps1"
$MicrostructureUnblockStatusStartScript = Join-Path $Root "ops\autostart\Start-MicrostructureUnblockStatusLoop.ps1"
$ResearchRuntimeSupervisorStartScript = Join-Path $Root "ops\autostart\Start-ResearchRuntimeSupervisor.ps1"
$DeribitOptionsResearchComponentStartScript = Join-Path $Root "ops\autostart\Start-DeribitOptionsResearchComponent.ps1"
$DeribitOptionsSkewForwardStartScript = Join-Path $Root "ops\autostart\Start-DeribitOptionsSkewForwardObserver.ps1"
$CexDexFundingCollectorScript = Join-Path $Root "ops\autostart\Run-CexDexFundingCollectorLoop.ps1"
$CexFundingFreshnessWatchdogScript = Join-Path $Root "ops\autostart\Run-CexFundingFreshnessWatchdogLoop.ps1"
$BitunixWO105V3R4Script = Join-Path $Root "ops\autostart\Run-BitunixWO105V3R4ForwardLoop.ps1"
$PostFillMarkoutForwardScript = Join-Path $Root "ops\autostart\Run-PostFillMarkoutForwardLoop.ps1"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $LoopStdout -Parent) | Out-Null

function Normalize-ProcessEnvironment {
    # Some launchers inject both Path and PATH; Start-Process rejects that duplicate key.
    $CurrentPath = [Environment]::GetEnvironmentVariable("Path", "Process")
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $CurrentPath, "Process")
}

Normalize-ProcessEnvironment

$RuntimeManifestForLaunch = Get-TradingOSRuntimeManifest -Root $Root
$LaunchDispositions = New-Object System.Collections.Generic.List[object]
function Add-WrapperLaunchDisposition {
    param($WrapperResult)
    if ($WrapperResult -and $WrapperResult.launch_disposition) { $LaunchDispositions.Add($WrapperResult.launch_disposition) | Out-Null }
}
& $PanelScript -Port $ControlPanelPort -LaunchAttemptId $AttemptId | Out-Null

$ResearchRuntimeSupervisorEligible = $false
$ResearchRuntimeSupervisorStartResult = [pscustomobject]@{ status = "separate_explicit_research_lifecycle"; extra = $null }
$DeribitOptionsSurfaceCollectorEligible = $false
$DeribitOptionsSurfaceCollectorStartResult = [pscustomobject]@{ status = "separate_explicit_research_lifecycle"; extra = $null }
$DeribitOptionsSurfaceCollectorAlive = $false
$DeribitOptionsReadinessEligible = $false
$DeribitOptionsReadinessStartResult = [pscustomobject]@{ status = "separate_explicit_research_lifecycle"; extra = $null }
$DeribitOptionsReadinessAlive = $false
$DeribitOptionsSkewForwardEligible = $false
$DeribitOptionsSkewForwardStartResult = [pscustomobject]@{ status = "separate_explicit_research_lifecycle"; extra = $null }

$MicrostructureUnblockStatusEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $MicrostructureUnblockStatusStartScript)
$MicrostructureUnblockStatusStartResult = $null
if ($MicrostructureUnblockStatusEligible) {
    try {
        $MicrostructureUnblockStatusRaw = & $MicrostructureUnblockStatusStartScript -SleepSeconds $MicrostructureUnblockStatusSleepSeconds -LaunchAttemptId $AttemptId
        $MicrostructureUnblockStatusStartResult = $MicrostructureUnblockStatusRaw | ConvertFrom-Json
        Add-WrapperLaunchDisposition -WrapperResult $MicrostructureUnblockStatusStartResult
    } catch {
        $MicrostructureUnblockStatusStartResult = [pscustomobject]@{
            status = "launcher_error"
            loop_pid = $null
            loop_alive = $false
            error = $_.Exception.GetType().Name
        }
    }
}

$BybitAllLiquidationCollectorEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $BybitAllLiquidationCollectorStartScript)
$BybitAllLiquidationCollectorStartResult = $null
if ($BybitAllLiquidationCollectorEligible) {
    try {
        $BybitAllLiquidationCollectorRaw = & $BybitAllLiquidationCollectorStartScript -LaunchAttemptId $AttemptId
        $BybitAllLiquidationCollectorStartResult = $BybitAllLiquidationCollectorRaw | ConvertFrom-Json
        Add-WrapperLaunchDisposition -WrapperResult $BybitAllLiquidationCollectorStartResult
    } catch {
        $BybitAllLiquidationCollectorStartResult = [pscustomobject]@{ status = "launcher_error"; loop_pid = $null; loop_alive = $false; error = $_.Exception.GetType().Name }
    }
}

$BybitAllLiquidationWatchdogEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $BybitAllLiquidationWatchdogStartScript)
$BybitAllLiquidationWatchdogStartResult = $null
if ($BybitAllLiquidationWatchdogEligible) {
    try {
        $BybitAllLiquidationWatchdogRaw = & $BybitAllLiquidationWatchdogStartScript -SleepSeconds $BybitAllLiquidationWatchdogSleepSeconds -LaunchAttemptId $AttemptId
        $BybitAllLiquidationWatchdogStartResult = $BybitAllLiquidationWatchdogRaw | ConvertFrom-Json
        Add-WrapperLaunchDisposition -WrapperResult $BybitAllLiquidationWatchdogStartResult
    } catch {
        $BybitAllLiquidationWatchdogStartResult = [pscustomobject]@{
            status = "launcher_error"
            loop_pid = $null
            loop_alive = $false
            error = $_.Exception.GetType().Name
        }
    }
}

$LoopLockPath = Join-Path $Root "logs\forward_paper_feed\forward_scheduler_loop.lock.json"
$WatchdogLockPath = Join-Path $Root "logs\forward_paper_feed\forward_runtime_watchdog_loop.lock.json"
$CrowdFadeLockPath = Join-Path $Root "logs\forward_paper_feed\crowd_fade_observer_loop.lock.json"
$BackupLockPath = Join-Path $Root "logs\runtime_backup\daily_drive_backup_loop.lock.json"
$CrossVenueLockPath = Join-Path $Root "logs\cross_venue_data\cross_venue_data_loop.lock.json"
$MicrostructureLockPath = Join-Path $Root "logs\cross_venue_microstructure\microstructure_loop.lock.json"
$MicrostructureBookLockPath = Join-Path $Root "logs\cross_venue_microstructure\microstructure_book_loop.lock.json"
$MicrostructureWatchdogLockPath = Join-Path $Root "logs\cross_venue_microstructure\microstructure_watchdog_loop.lock.json"
$BinanceSpotPerpAggressorFlowLockPath = Join-Path $Root "logs\binance_spot_perp_aggressor_flow\binance_spot_perp_aggressor_flow_loop.lock.json"
$LiquidationForceOrderWatchdogLockPath = Join-Path $Root "logs\liquidation_force_order\liquidation_force_order_watchdog_loop.lock.json"
$CrossStackReplicationTransitionLockPath = Join-Path $Root "logs\cross_stack_replication\cross_stack_replication_transition_loop.lock.json"
$RealEdgeObserverLockPath = Join-Path $Root "logs\real_edge_observer\real_edge_observer_pulse_loop.lock.json"
$CexDexFundingCollectorLockPath = Join-Path $Root "logs\cex_dex_funding\cex_dex_funding_collector_loop.lock.json"
$CexFundingFreshnessWatchdogLockPath = Join-Path $Root "logs\cex_dex_funding\cex_dex_funding_freshness_watchdog_loop.lock.json"
$BitunixWO105V3R4LockPath = Join-Path $Root "logs\bitunix_wo105_v3r4\bitunix_wo105_v3r4_forward_loop.lock.json"
$PostFillMarkoutForwardLockPath = Join-Path $Root "logs\post_fill_markout_forward\post_fill_markout_forward_loop.lock.json"
$LoopAlreadyRunning = $false
if (Test-Path -LiteralPath $LoopLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LoopLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $LoopAlreadyRunning = $true
        }
    } catch {
        $LoopAlreadyRunning = $false
    }
}

$WatchdogAlreadyRunning = $false
if (Test-Path -LiteralPath $WatchdogLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $WatchdogLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $WatchdogAlreadyRunning = $true
        }
    } catch {
        $WatchdogAlreadyRunning = $false
    }
}

$CrowdFadeAlreadyRunning = $false
if (Test-Path -LiteralPath $CrowdFadeLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $CrowdFadeLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $CrowdFadeAlreadyRunning = $true
        }
    } catch {
        $CrowdFadeAlreadyRunning = $false
    }
}

$BackupEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $BackupScript)
$BackupAlreadyRunning = $false
if ($BackupEligible -and (Test-Path -LiteralPath $BackupLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $BackupLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $BackupAlreadyRunning = $true
        }
    } catch {
        $BackupAlreadyRunning = $false
    }
}

$CrossVenueEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $CrossVenueScript)
$CrossVenueAlreadyRunning = $false
if ($CrossVenueEligible -and (Test-Path -LiteralPath $CrossVenueLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $CrossVenueLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $CrossVenueAlreadyRunning = $true
        }
    } catch {
        $CrossVenueAlreadyRunning = $false
    }
}

$MicrostructureEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $MicrostructureScript)
$MicrostructureAlreadyRunning = $false
if ($MicrostructureEligible -and (Test-Path -LiteralPath $MicrostructureLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $MicrostructureLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $MicrostructureAlreadyRunning = $true
        }
    } catch {
        $MicrostructureAlreadyRunning = $false
    }
}

$MicrostructureBookEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $MicrostructureBookScript)
$MicrostructureBookAlreadyRunning = $false
if ($MicrostructureBookEligible -and (Test-Path -LiteralPath $MicrostructureBookLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $MicrostructureBookLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $MicrostructureBookAlreadyRunning = $true
        }
    } catch {
        $MicrostructureBookAlreadyRunning = $false
    }
}

$MicrostructureWatchdogEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $MicrostructureWatchdogScript)
$MicrostructureWatchdogAlreadyRunning = $false
if ($MicrostructureWatchdogEligible -and (Test-Path -LiteralPath $MicrostructureWatchdogLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $MicrostructureWatchdogLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $MicrostructureWatchdogAlreadyRunning = $true
        }
    } catch {
        $MicrostructureWatchdogAlreadyRunning = $false
    }
}

$BinanceSpotPerpAggressorFlowEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $BinanceSpotPerpAggressorFlowScript)
$BinanceSpotPerpAggressorFlowAlreadyRunning = $false
if ($BinanceSpotPerpAggressorFlowEligible -and (Test-Path -LiteralPath $BinanceSpotPerpAggressorFlowLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $BinanceSpotPerpAggressorFlowLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $BinanceSpotPerpAggressorFlowAlreadyRunning = $true
        }
    } catch {
        $BinanceSpotPerpAggressorFlowAlreadyRunning = $false
    }
}

$LiquidationForceOrderEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $LiquidationForceOrderStartScript)
$LiquidationForceOrderStarted = $false
if ($LiquidationForceOrderEligible) {
    try {
        $LiquidationForceOrderRaw = & $LiquidationForceOrderStartScript -CycleSeconds $LiquidationForceOrderCycleSeconds -LaunchAttemptId $AttemptId
        $LiquidationForceOrderStartResult = $LiquidationForceOrderRaw | ConvertFrom-Json
        Add-WrapperLaunchDisposition -WrapperResult $LiquidationForceOrderStartResult
        $LiquidationForceOrderStarted = $LiquidationForceOrderStartResult.status -in @('started', 'already_running')
    } catch {
        $LiquidationForceOrderStarted = $false
    }
}

$LiquidationForceOrderWatchdogEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $LiquidationForceOrderWatchdogScript)
$LiquidationForceOrderWatchdogAlreadyRunning = $false
if ($LiquidationForceOrderWatchdogEligible -and (Test-Path -LiteralPath $LiquidationForceOrderWatchdogLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $LiquidationForceOrderWatchdogLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $LiquidationForceOrderWatchdogAlreadyRunning = $true
        }
    } catch {
        $LiquidationForceOrderWatchdogAlreadyRunning = $false
    }
}

$LiquidationForceOrderTransportContinuityEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $LiquidationForceOrderTransportContinuityScript)
$LiquidationForceOrderTransportContinuityAlreadyRunning = $false

$CrossStackReplicationTransitionEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $CrossStackReplicationTransitionScript)
$CrossStackReplicationTransitionAlreadyRunning = $false
if ($CrossStackReplicationTransitionEligible -and (Test-Path -LiteralPath $CrossStackReplicationTransitionLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $CrossStackReplicationTransitionLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $CrossStackReplicationTransitionAlreadyRunning = $true
        }
    } catch {
        $CrossStackReplicationTransitionAlreadyRunning = $false
    }
}

$RealEdgeObserverEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $RealEdgeObserverScript)
$RealEdgeObserverAlreadyRunning = $false
if ($RealEdgeObserverEligible -and (Test-Path -LiteralPath $RealEdgeObserverLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $RealEdgeObserverLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $RealEdgeObserverAlreadyRunning = $true
        }
    } catch {
        $RealEdgeObserverAlreadyRunning = $false
    }
}

$CexDexFundingCollectorEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $CexDexFundingCollectorScript)
$CexDexFundingCollectorAlreadyRunning = $false
if ($CexDexFundingCollectorEligible -and (Test-Path -LiteralPath $CexDexFundingCollectorLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $CexDexFundingCollectorLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $CexDexFundingCollectorAlreadyRunning = $true
        }
    } catch {
        $CexDexFundingCollectorAlreadyRunning = $false
    }
}

$CexFundingFreshnessWatchdogEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $CexFundingFreshnessWatchdogScript)
$CexFundingFreshnessWatchdogAlreadyRunning = $false
if ($CexFundingFreshnessWatchdogEligible -and (Test-Path -LiteralPath $CexFundingFreshnessWatchdogLockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $CexFundingFreshnessWatchdogLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $CexFundingFreshnessWatchdogAlreadyRunning = $true
        }
    } catch {
        $CexFundingFreshnessWatchdogAlreadyRunning = $false
    }
}

$BitunixWO105V3R4Eligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $BitunixWO105V3R4Script)
$BitunixWO105V3R4AlreadyRunning = $false
if ($BitunixWO105V3R4Eligible -and (Test-Path -LiteralPath $BitunixWO105V3R4LockPath)) {
    try {
        $Existing = Get-Content -LiteralPath $BitunixWO105V3R4LockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            $BitunixWO105V3R4AlreadyRunning = $true
        }
    } catch {
        $BitunixWO105V3R4AlreadyRunning = $false
    }
}

$PostFillMarkoutForwardEligible = $Root -notmatch "\\My Drive(\\|$)" -and (Test-Path -LiteralPath $PostFillMarkoutForwardScript)
$PostFillMarkoutForwardAlreadyRunning = $false

# Re-resolve every directly launched component through the manifest verifier.
# This deliberately overrides the legacy PID-only booleans above: a reused PID
# or an unowned matching process blocks launch, while dead/invalid locks are
# preserved under a quarantine name before a replacement process is started.
function Test-ComponentStartBlocked {
    param([Parameter(Mandatory = $true)][string]$ComponentId)

    $Disposition = Get-TradingOSRuntimeLaunchDisposition -Root $Root -Manifest $RuntimeManifestForLaunch -ComponentId $ComponentId -AttemptId $AttemptId
    $LaunchDispositions.Add($Disposition) | Out-Null
    return -not [bool]$Disposition.should_start
}

$LoopAlreadyRunning = Test-ComponentStartBlocked -ComponentId "forward_paper"
$WatchdogAlreadyRunning = Test-ComponentStartBlocked -ComponentId "forward_runtime_watchdog"
$CrowdFadeAlreadyRunning = Test-ComponentStartBlocked -ComponentId "crowd_fade_observer"
if ($BackupEligible) { $BackupAlreadyRunning = Test-ComponentStartBlocked -ComponentId "daily_runtime_backup" }
if ($CrossVenueEligible) { $CrossVenueAlreadyRunning = Test-ComponentStartBlocked -ComponentId "cross_venue_data" }
if ($MicrostructureEligible) { $MicrostructureAlreadyRunning = Test-ComponentStartBlocked -ComponentId "cross_venue_microstructure" }
if ($MicrostructureBookEligible) { $MicrostructureBookAlreadyRunning = Test-ComponentStartBlocked -ComponentId "cross_venue_microstructure_book" }
if ($MicrostructureWatchdogEligible) { $MicrostructureWatchdogAlreadyRunning = Test-ComponentStartBlocked -ComponentId "cross_venue_microstructure_watchdog" }
if ($BinanceSpotPerpAggressorFlowEligible) { $BinanceSpotPerpAggressorFlowAlreadyRunning = Test-ComponentStartBlocked -ComponentId "binance_spot_perp_aggressor_flow" }
if ($LiquidationForceOrderWatchdogEligible) { $LiquidationForceOrderWatchdogAlreadyRunning = Test-ComponentStartBlocked -ComponentId "liquidation_force_order_watchdog" }
if ($LiquidationForceOrderTransportContinuityEligible) { $LiquidationForceOrderTransportContinuityAlreadyRunning = Test-ComponentStartBlocked -ComponentId "liquidation_force_order_transport_continuity" }
if ($CrossStackReplicationTransitionEligible) { $CrossStackReplicationTransitionAlreadyRunning = Test-ComponentStartBlocked -ComponentId "cross_stack_replication_transition" }
if ($RealEdgeObserverEligible) { $RealEdgeObserverAlreadyRunning = Test-ComponentStartBlocked -ComponentId "real_edge_observer" }
if ($CexDexFundingCollectorEligible) { $CexDexFundingCollectorAlreadyRunning = Test-ComponentStartBlocked -ComponentId "cex_dex_funding_collector" }
if ($CexFundingFreshnessWatchdogEligible) { $CexFundingFreshnessWatchdogAlreadyRunning = Test-ComponentStartBlocked -ComponentId "cex_funding_freshness_watchdog" }
if ($BitunixWO105V3R4Eligible) { $BitunixWO105V3R4AlreadyRunning = Test-ComponentStartBlocked -ComponentId "bitunix_wo105_v3r4_forward" }
if ($PostFillMarkoutForwardEligible) { $PostFillMarkoutForwardAlreadyRunning = Test-ComponentStartBlocked -ComponentId "post_fill_markout_forward" }

if (-not $LoopAlreadyRunning) {
    $LoopArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$LoopScript`" -SleepSeconds $ForwardSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "forward_paper" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $LoopArgs -WorkingDirectory $Root -ExpectedScriptPath $LoopScript -StdoutPath $LoopStdout -StderrPath $LoopStderr | Out-Null
}

if (-not $WatchdogAlreadyRunning) {
    $WatchdogArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$WatchdogScript`" -SleepSeconds $WatchdogSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "forward_runtime_watchdog" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $WatchdogArgs -WorkingDirectory $Root -ExpectedScriptPath $WatchdogScript -StdoutPath $WatchdogProcessStdout -StderrPath $WatchdogProcessStderr | Out-Null
}

if (-not $CrowdFadeAlreadyRunning) {
    $CrowdFadeArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$CrowdFadeScript`" -SleepSeconds $CrowdFadeSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "crowd_fade_observer" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $CrowdFadeArgs -WorkingDirectory $Root -ExpectedScriptPath $CrowdFadeScript -StdoutPath $CrowdFadeStdout -StderrPath $CrowdFadeStderr | Out-Null
}

if ($BackupEligible -and -not $BackupAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $BackupStdout -Parent) | Out-Null
    $BackupArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$BackupScript`" -SleepSeconds $BackupSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "daily_runtime_backup" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $BackupArgs -WorkingDirectory $Root -ExpectedScriptPath $BackupScript -StdoutPath $BackupStdout -StderrPath $BackupStderr | Out-Null
}

if ($CrossVenueEligible -and -not $CrossVenueAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $CrossVenueStdout -Parent) | Out-Null
    $CrossVenueArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$CrossVenueScript`" -SleepSeconds $CrossVenueSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "cross_venue_data" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $CrossVenueArgs -WorkingDirectory $Root -ExpectedScriptPath $CrossVenueScript -StdoutPath $CrossVenueStdout -StderrPath $CrossVenueStderr | Out-Null
}

if ($MicrostructureEligible -and -not $MicrostructureAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $MicrostructureStdout -Parent) | Out-Null
    $MicrostructureArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$MicrostructureScript`" -SleepSeconds $CrossVenueMicrostructureSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "cross_venue_microstructure" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $MicrostructureArgs -WorkingDirectory $Root -ExpectedScriptPath $MicrostructureScript -StdoutPath $MicrostructureStdout -StderrPath $MicrostructureStderr | Out-Null
}

if ($MicrostructureBookEligible -and -not $MicrostructureBookAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $MicrostructureBookStdout -Parent) | Out-Null
    $MicrostructureBookArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$MicrostructureBookScript`" -SleepSeconds $CrossVenueMicrostructureBookSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "cross_venue_microstructure_book" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $MicrostructureBookArgs -WorkingDirectory $Root -ExpectedScriptPath $MicrostructureBookScript -StdoutPath $MicrostructureBookStdout -StderrPath $MicrostructureBookStderr | Out-Null
}

if ($MicrostructureWatchdogEligible -and -not $MicrostructureWatchdogAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $MicrostructureWatchdogStdout -Parent) | Out-Null
    $MicrostructureWatchdogArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$MicrostructureWatchdogScript`" -SleepSeconds $CrossVenueMicrostructureWatchdogSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "cross_venue_microstructure_watchdog" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $MicrostructureWatchdogArgs -WorkingDirectory $Root -ExpectedScriptPath $MicrostructureWatchdogScript -StdoutPath $MicrostructureWatchdogStdout -StderrPath $MicrostructureWatchdogStderr | Out-Null
}

if ($BinanceSpotPerpAggressorFlowEligible -and -not $BinanceSpotPerpAggressorFlowAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $BinanceSpotPerpAggressorFlowStdout -Parent) | Out-Null
    $BinanceSpotPerpAggressorFlowArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$BinanceSpotPerpAggressorFlowScript`" -SleepSeconds $BinanceSpotPerpAggressorFlowSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "binance_spot_perp_aggressor_flow" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $BinanceSpotPerpAggressorFlowArgs -WorkingDirectory $Root -ExpectedScriptPath $BinanceSpotPerpAggressorFlowScript -StdoutPath $BinanceSpotPerpAggressorFlowStdout -StderrPath $BinanceSpotPerpAggressorFlowStderr | Out-Null
}

if ($LiquidationForceOrderWatchdogEligible -and -not $LiquidationForceOrderWatchdogAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $LiquidationForceOrderWatchdogStdout -Parent) | Out-Null
    $LiquidationForceOrderWatchdogArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$LiquidationForceOrderWatchdogScript`" -SleepSeconds $LiquidationForceOrderWatchdogSleepSeconds -CycleSeconds $LiquidationForceOrderCycleSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "liquidation_force_order_watchdog" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $LiquidationForceOrderWatchdogArgs -WorkingDirectory $Root -ExpectedScriptPath $LiquidationForceOrderWatchdogScript -StdoutPath $LiquidationForceOrderWatchdogStdout -StderrPath $LiquidationForceOrderWatchdogStderr | Out-Null
}

if ($LiquidationForceOrderTransportContinuityEligible -and -not $LiquidationForceOrderTransportContinuityAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $LiquidationForceOrderTransportContinuityStdout -Parent) | Out-Null
    $LiquidationForceOrderTransportContinuityArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$LiquidationForceOrderTransportContinuityScript`" -SleepSeconds $LiquidationForceOrderTransportContinuitySleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "liquidation_force_order_transport_continuity" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $LiquidationForceOrderTransportContinuityArgs -WorkingDirectory $Root -ExpectedScriptPath $LiquidationForceOrderTransportContinuityScript -StdoutPath $LiquidationForceOrderTransportContinuityStdout -StderrPath $LiquidationForceOrderTransportContinuityStderr | Out-Null
}

if ($CrossStackReplicationTransitionEligible -and -not $CrossStackReplicationTransitionAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $CrossStackReplicationTransitionStdout -Parent) | Out-Null
    $CrossStackReplicationTransitionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$CrossStackReplicationTransitionScript`" -SleepSeconds $CrossStackReplicationTransitionSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "cross_stack_replication_transition" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $CrossStackReplicationTransitionArgs -WorkingDirectory $Root -ExpectedScriptPath $CrossStackReplicationTransitionScript -StdoutPath $CrossStackReplicationTransitionStdout -StderrPath $CrossStackReplicationTransitionStderr | Out-Null
}

if ($RealEdgeObserverEligible -and -not $RealEdgeObserverAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $RealEdgeObserverProcessStdout -Parent) | Out-Null
    $RealEdgeObserverArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$RealEdgeObserverScript`" -SleepSeconds $RealEdgeObserverPulseSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "real_edge_observer" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $RealEdgeObserverArgs -WorkingDirectory $Root -ExpectedScriptPath $RealEdgeObserverScript -StdoutPath $RealEdgeObserverProcessStdout -StderrPath $RealEdgeObserverProcessStderr | Out-Null
}

if ($CexDexFundingCollectorEligible -and -not $CexDexFundingCollectorAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $CexDexFundingCollectorStdout -Parent) | Out-Null
    $CexDexFundingCollectorArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$CexDexFundingCollectorScript`" -SleepSeconds $CexDexFundingCollectorSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "cex_dex_funding_collector" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $CexDexFundingCollectorArgs -WorkingDirectory $Root -ExpectedScriptPath $CexDexFundingCollectorScript -StdoutPath $CexDexFundingCollectorStdout -StderrPath $CexDexFundingCollectorStderr | Out-Null
}

if ($CexFundingFreshnessWatchdogEligible -and -not $CexFundingFreshnessWatchdogAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $CexFundingFreshnessWatchdogStdout -Parent) | Out-Null
    $CexFundingFreshnessWatchdogArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$CexFundingFreshnessWatchdogScript`" -SleepSeconds $CexFundingFreshnessWatchdogSleepSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "cex_funding_freshness_watchdog" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $CexFundingFreshnessWatchdogArgs -WorkingDirectory $Root -ExpectedScriptPath $CexFundingFreshnessWatchdogScript -StdoutPath $CexFundingFreshnessWatchdogStdout -StderrPath $CexFundingFreshnessWatchdogStderr | Out-Null
}

if ($BitunixWO105V3R4Eligible -and -not $BitunixWO105V3R4AlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $BitunixWO105V3R4Stdout -Parent) | Out-Null
    $BitunixWO105V3R4Args = "-NoProfile -ExecutionPolicy Bypass -File `"$BitunixWO105V3R4Script`" -RestCadenceSeconds $BitunixWO105V3R4RestCadenceSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "bitunix_wo105_v3r4_forward" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $BitunixWO105V3R4Args -WorkingDirectory $Root -ExpectedScriptPath $BitunixWO105V3R4Script -StdoutPath $BitunixWO105V3R4Stdout -StderrPath $BitunixWO105V3R4Stderr | Out-Null
}

if ($PostFillMarkoutForwardEligible -and -not $PostFillMarkoutForwardAlreadyRunning) {
    New-Item -ItemType Directory -Force -Path (Split-Path $PostFillMarkoutForwardStdout -Parent) | Out-Null
    $PostFillMarkoutForwardArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PostFillMarkoutForwardScript`" -PulseSeconds $PostFillMarkoutForwardPulseSeconds -LaunchAttemptId `"$AttemptId`""
    Start-TradingOSRuntimeJobProcess -Root $Root -ComponentId "post_fill_markout_forward" -AttemptId $AttemptId -FilePath "powershell.exe" -Arguments $PostFillMarkoutForwardArgs -WorkingDirectory $Root -ExpectedScriptPath $PostFillMarkoutForwardScript -StdoutPath $PostFillMarkoutForwardStdout -StderrPath $PostFillMarkoutForwardStderr | Out-Null
}

$RuntimeManifest = Get-TradingOSRuntimeManifest -Root $Root
$VerificationDeadline = (Get-Date).AddSeconds(15)
do {
    $RuntimeComponentStates = @(Get-TradingOSRuntimeStates -Root $Root -Manifest $RuntimeManifest)
    $MissingRequiredComponents = @($RuntimeComponentStates | Where-Object { $_.required -and -not $_.job_contained })
    $ControlPanelState = Get-TradingOSControlPanelOwnershipState -Root $Root -Port $ControlPanelPort
    if (($MissingRequiredComponents.Count -eq 0 -and $ControlPanelState.job_contained) -or (Get-Date) -ge $VerificationDeadline) { break }
    Start-Sleep -Milliseconds 500
} while ($true)
$RuntimeVerified = $MissingRequiredComponents.Count -eq 0 -and $ControlPanelState.job_contained
$RuntimeStatus = if ($RuntimeVerified) { "completed" } else { "degraded" }

$RuntimeReport = [ordered]@{
    ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    status = $RuntimeStatus
    root = $Root
    attempt_id = $AttemptId
    invocation_id = [string]$AttemptReservation.invocation_id
    invocation_mode = $InvocationMode
    startup_mutex = $StartMutexName
    shutdown_sentinel_cleared = $ShutdownSentinelCleared
    runtime_components_expected = $RuntimeComponentStates.Count
    runtime_components_healthy = @($RuntimeComponentStates | Where-Object { $_.job_contained }).Count
    runtime_components_failed = @(@($MissingRequiredComponents | ForEach-Object { $_.id }) + $(if (-not $ControlPanelState.job_contained) { @("control_panel") } else { @() }))
    runtime_component_states = @($RuntimeComponentStates | ForEach-Object {
        [ordered]@{
            id = $_.id
            decision = $_.decision
            ownership_decision = $_.ownership_decision
            job_decision = $_.job_decision
            job_contained = $_.job_contained
            matching_script_pids = $_.matching_script_pids
            pid = $_.pid
            working_set_mb = $_.working_set_mb
            private_mb = $_.private_mb
        }
    })
    launch_dispositions = $LaunchDispositions
    control_panel_port = $ControlPanelPort
    control_panel_decision = $ControlPanelState.ownership_decision
    control_panel_job_decision = $ControlPanelState.job_decision
    control_panel_job_contained = $ControlPanelState.job_contained
    control_panel_exact_script_pids = $ControlPanelState.exact_script_pids
    control_panel_pid = $ControlPanelState.pid
    forward_loop_already_running = $LoopAlreadyRunning
    forward_sleep_seconds = $ForwardSleepSeconds
    watchdog_loop_already_running = $WatchdogAlreadyRunning
    watchdog_sleep_seconds = $WatchdogSleepSeconds
    crowd_fade_loop_already_running = $CrowdFadeAlreadyRunning
    crowd_fade_sleep_seconds = $CrowdFadeSleepSeconds
    daily_backup_eligible = $BackupEligible
    daily_backup_already_running = $BackupAlreadyRunning
    daily_backup_sleep_seconds = $BackupSleepSeconds
    cross_venue_data_eligible = $CrossVenueEligible
    cross_venue_data_already_running = $CrossVenueAlreadyRunning
    cross_venue_data_sleep_seconds = $CrossVenueSleepSeconds
    cross_venue_microstructure_eligible = $MicrostructureEligible
    cross_venue_microstructure_already_running = $MicrostructureAlreadyRunning
    cross_venue_microstructure_sleep_seconds = $CrossVenueMicrostructureSleepSeconds
    cross_venue_microstructure_book_eligible = $MicrostructureBookEligible
    cross_venue_microstructure_book_already_running = $MicrostructureBookAlreadyRunning
    cross_venue_microstructure_book_sleep_seconds = $CrossVenueMicrostructureBookSleepSeconds
    cross_venue_microstructure_watchdog_eligible = $MicrostructureWatchdogEligible
    cross_venue_microstructure_watchdog_already_running = $MicrostructureWatchdogAlreadyRunning
    cross_venue_microstructure_watchdog_sleep_seconds = $CrossVenueMicrostructureWatchdogSleepSeconds
    binance_spot_perp_aggressor_flow_eligible = $BinanceSpotPerpAggressorFlowEligible
    binance_spot_perp_aggressor_flow_already_running = $BinanceSpotPerpAggressorFlowAlreadyRunning
    binance_spot_perp_aggressor_flow_sleep_seconds = $BinanceSpotPerpAggressorFlowSleepSeconds
    binance_spot_perp_aggressor_flow_collector_only = $true
    binance_spot_perp_aggressor_flow_can_trade = $false
    bybit_all_liquidation_watchdog_eligible = $BybitAllLiquidationWatchdogEligible
    bybit_all_liquidation_watchdog_status = if ($BybitAllLiquidationWatchdogStartResult) { $BybitAllLiquidationWatchdogStartResult.status } else { "not_eligible" }
    bybit_all_liquidation_watchdog_pid = if ($BybitAllLiquidationWatchdogStartResult -and $BybitAllLiquidationWatchdogStartResult.loop_pid) { $BybitAllLiquidationWatchdogStartResult.loop_pid } elseif ($BybitAllLiquidationWatchdogStartResult) { $BybitAllLiquidationWatchdogStartResult.pid } else { $null }
    bybit_all_liquidation_watchdog_alive = [bool]($BybitAllLiquidationWatchdogStartResult -and ($BybitAllLiquidationWatchdogStartResult.status -eq "already_running" -or $BybitAllLiquidationWatchdogStartResult.loop_alive))
    bybit_all_liquidation_watchdog_sleep_seconds = $BybitAllLiquidationWatchdogSleepSeconds
    bybit_all_liquidation_watchdog_public_data_only = $true
    liquidation_force_order_eligible = $LiquidationForceOrderEligible
    liquidation_force_order_started_or_already_running = $LiquidationForceOrderStarted
    liquidation_force_order_cycle_seconds = $LiquidationForceOrderCycleSeconds
    liquidation_force_order_watchdog_eligible = $LiquidationForceOrderWatchdogEligible
    liquidation_force_order_watchdog_already_running = $LiquidationForceOrderWatchdogAlreadyRunning
    liquidation_force_order_watchdog_sleep_seconds = $LiquidationForceOrderWatchdogSleepSeconds
    liquidation_force_order_transport_continuity_eligible = $LiquidationForceOrderTransportContinuityEligible
    liquidation_force_order_transport_continuity_already_running = $LiquidationForceOrderTransportContinuityAlreadyRunning
    liquidation_force_order_transport_continuity_sleep_seconds = $LiquidationForceOrderTransportContinuitySleepSeconds
    liquidation_force_order_transport_continuity_audit_only = $true
    cross_stack_replication_transition_eligible = $CrossStackReplicationTransitionEligible
    cross_stack_replication_transition_already_running = $CrossStackReplicationTransitionAlreadyRunning
    cross_stack_replication_transition_sleep_seconds = $CrossStackReplicationTransitionSleepSeconds
    real_edge_observer_eligible = $RealEdgeObserverEligible
    real_edge_observer_already_running = $RealEdgeObserverAlreadyRunning
    real_edge_observer_sleep_seconds = $RealEdgeObserverPulseSleepSeconds
    cex_dex_funding_collector_eligible = $CexDexFundingCollectorEligible
    cex_dex_funding_collector_already_running = $CexDexFundingCollectorAlreadyRunning
    cex_dex_funding_collector_sleep_seconds = $CexDexFundingCollectorSleepSeconds
    cex_dex_funding_collector_only = $true
    cex_funding_freshness_watchdog_eligible = $CexFundingFreshnessWatchdogEligible
    cex_funding_freshness_watchdog_already_running = $CexFundingFreshnessWatchdogAlreadyRunning
    cex_funding_freshness_watchdog_sleep_seconds = $CexFundingFreshnessWatchdogSleepSeconds
    cex_funding_freshness_watchdog_only = $true
    bitunix_wo105_v3r4_forward_eligible = $BitunixWO105V3R4Eligible
    bitunix_wo105_v3r4_forward_already_running = $BitunixWO105V3R4AlreadyRunning
    bitunix_wo105_v3r4_rest_cadence_seconds = $BitunixWO105V3R4RestCadenceSeconds
    bitunix_wo105_v3r4_public_shadow_only = $true
    post_fill_markout_forward_eligible = $PostFillMarkoutForwardEligible
    post_fill_markout_forward_already_running = $PostFillMarkoutForwardAlreadyRunning
    post_fill_markout_forward_pulse_seconds = $PostFillMarkoutForwardPulseSeconds
    post_fill_markout_forward_demo_only = $true
    post_fill_markout_forward_signed_read_endpoint_allowlist = @("/fapi/v1/userTrades")
    post_fill_markout_forward_orders_allowed = $false
    microstructure_unblock_status_eligible = $MicrostructureUnblockStatusEligible
    microstructure_unblock_status = if ($MicrostructureUnblockStatusStartResult) { $MicrostructureUnblockStatusStartResult.status } else { "not_eligible" }
    microstructure_unblock_status_pid = if ($MicrostructureUnblockStatusStartResult -and $MicrostructureUnblockStatusStartResult.loop_pid) { $MicrostructureUnblockStatusStartResult.loop_pid } elseif ($MicrostructureUnblockStatusStartResult) { $MicrostructureUnblockStatusStartResult.pid } else { $null }
    microstructure_unblock_status_alive = [bool]($MicrostructureUnblockStatusStartResult -and ($MicrostructureUnblockStatusStartResult.status -eq "already_running" -or $MicrostructureUnblockStatusStartResult.loop_alive))
    microstructure_unblock_status_sleep_seconds = $MicrostructureUnblockStatusSleepSeconds
    microstructure_unblock_status_observability_only = $true
    research_runtime_supervisor_eligible = $ResearchRuntimeSupervisorEligible
    research_runtime_supervisor_status = if ($ResearchRuntimeSupervisorStartResult) { $ResearchRuntimeSupervisorStartResult.status } else { "not_eligible" }
    research_runtime_supervisor_pid = if ($ResearchRuntimeSupervisorStartResult -and $ResearchRuntimeSupervisorStartResult.extra) { $ResearchRuntimeSupervisorStartResult.extra.pid } else { $null }
    research_runtime_supervisor_alive = [bool]($ResearchRuntimeSupervisorStartResult -and $ResearchRuntimeSupervisorStartResult.status -in @("already_running", "started"))
    research_runtime_supervisor_sleep_seconds = $ResearchRuntimeSupervisorSleepSeconds
    research_runtime_supervisor_automatic_restart_allowed = $false
    deribit_options_surface_collector_eligible = $DeribitOptionsSurfaceCollectorEligible
    deribit_options_surface_collector_status = if ($DeribitOptionsSurfaceCollectorStartResult) { $DeribitOptionsSurfaceCollectorStartResult.status } else { "not_eligible" }
    deribit_options_surface_collector_pid = if ($DeribitOptionsSurfaceCollectorStartResult -and $DeribitOptionsSurfaceCollectorStartResult.extra) { $DeribitOptionsSurfaceCollectorStartResult.extra.pid } else { $null }
    deribit_options_surface_collector_alive = $DeribitOptionsSurfaceCollectorAlive
    deribit_options_surface_collector_sleep_seconds = $DeribitOptionsSurfaceCollectorSleepSeconds
    deribit_options_surface_collector_automatic_restart_allowed = $false
    deribit_options_readiness_eligible = $DeribitOptionsReadinessEligible
    deribit_options_readiness_status = if ($DeribitOptionsReadinessStartResult) { $DeribitOptionsReadinessStartResult.status } else { "not_eligible" }
    deribit_options_readiness_pid = if ($DeribitOptionsReadinessStartResult -and $DeribitOptionsReadinessStartResult.extra) { $DeribitOptionsReadinessStartResult.extra.pid } else { $null }
    deribit_options_readiness_alive = $DeribitOptionsReadinessAlive
    deribit_options_readiness_sleep_seconds = $DeribitOptionsReadinessSleepSeconds
    deribit_options_readiness_automatic_restart_allowed = $false
    deribit_options_skew_forward_eligible = $DeribitOptionsSkewForwardEligible
    deribit_options_skew_forward_status = if ($DeribitOptionsSkewForwardStartResult) { $DeribitOptionsSkewForwardStartResult.status } else { "not_eligible" }
    deribit_options_skew_forward_pid = if ($DeribitOptionsSkewForwardStartResult -and $DeribitOptionsSkewForwardStartResult.extra) { $DeribitOptionsSkewForwardStartResult.extra.pid } else { $null }
    deribit_options_skew_forward_alive = [bool]($DeribitOptionsSkewForwardStartResult -and $DeribitOptionsSkewForwardStartResult.status -in @("already_running", "started"))
    deribit_options_skew_forward_sleep_seconds = $DeribitOptionsSkewForwardSleepSeconds
    deribit_options_skew_forward_automatic_restart_allowed = $false
    live_trading_locked = $true
    can_trade = $false
}
if (-not $RuntimeVerified) {
    Write-TradingOSJsonFileAtomic -Path $StatusPath -Payload $RuntimeReport -Depth 6
    $FailedStartupComponents = @($MissingRequiredComponents | ForEach-Object { $_.id })
    if (-not $ControlPanelState.job_contained) { $FailedStartupComponents += "control_panel" }
    throw "TradingOS runtime startup degraded: $($FailedStartupComponents -join ', ')"
}
$AttemptCommit = Complete-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $AttemptId
$RuntimeReport | Add-Member -NotePropertyName attempt_commit -NotePropertyValue $AttemptCommit -Force
$StartCommitted = $true
$RuntimeHealthy = $true
if ($ShutdownStartBypassAcquired) {
    try {
        Remove-Item -LiteralPath $ShutdownStartMarkerPath -Force -ErrorAction Stop
        Remove-Item -LiteralPath $ShutdownSentinelPath -Force -ErrorAction Stop
        $ShutdownSentinelCleared = -not (Test-Path -LiteralPath $ShutdownSentinelPath)
    } catch {
        $RuntimeReport | Add-Member -NotePropertyName shutdown_sentinel_cleanup_warning -NotePropertyValue $_.Exception.Message -Force
        Write-Warning "Runtime committed, but the explicit shutdown sentinel cleanup was incomplete: $($_.Exception.Message)"
    }
    $RuntimeReport.shutdown_sentinel_cleared = $ShutdownSentinelCleared
}
try {
    Write-TradingOSJsonFileAtomic -Path $StatusPath -Payload $RuntimeReport -Depth 8
} catch {
    # The durable attempt reservation is authoritative. Reporting failure after
    # commit must not surface as a failed launch or reopen rollback.
    Write-Warning "Runtime committed successfully, but the startup status report could not be updated: $($_.Exception.Message)"
}
} finally {
    $RollbackFailureMessage = $null
    if (-not $StartCommitted -and $AttemptReserved -and $StartMutexAcquired) {
        try {
            $RollbackResult = Undo-TradingOSRuntimeLaunchAttempt `
                -Root $Root `
                -AttemptId $AttemptId `
                -LaunchDispositions $(if (Get-Variable -Name LaunchDispositions -ErrorAction SilentlyContinue) { $LaunchDispositions } else { @() })
            Write-TradingOSJsonFileAtomic -Path (Join-Path $LogDir "runtime_startup_rollback_status.json") -Payload $RollbackResult -Depth 8
            if (-not $RollbackResult.success) { $RollbackFailureMessage = "Runtime startup rollback could not prove complete cleanup." }
        } catch {
            $RollbackFailureMessage = "Runtime startup rollback failed: $($_.Exception.Message)"
        }
    }
    if (-not $StartCommitted -and $ShutdownStartBypassAcquired -and (Test-Path -LiteralPath $ShutdownStartMarkerPath)) {
        Remove-Item -LiteralPath $ShutdownStartMarkerPath -Force -ErrorAction SilentlyContinue
    }
    if ($StartMutexAcquired) {
        try { $StartMutex.ReleaseMutex() } catch {}
    }
    $StartMutex.Dispose()
    if ($RollbackFailureMessage) { throw $RollbackFailureMessage }
}
