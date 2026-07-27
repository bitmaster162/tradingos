param(
    [string]$PythonPath = "",
    [int]$LockStaleMinutes = 180,
    [switch]$SkipDataQualityCollector,
    [switch]$SkipAltBreadthRefresh,
    [switch]$SkipHealthCheck,
    [int]$DataQualityPages = 5,
    [int]$DataQualityFundingPages = 2,
    [int]$DataQualityKlinePages = 1
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LogDir = Join-Path $Root "logs\forward_paper_feed"
$LockPath = Join-Path $LogDir "forward_scheduler.lock.json"
$StatusPath = Join-Path $LogDir "scheduled_task_last_run.json"
$StdoutPath = Join-Path $LogDir "scheduled_task_stdout.log"
$StderrPath = Join-Path $LogDir "scheduled_task_stderr.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

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

function Write-Status {
    param(
        [string]$Status,
        [int]$ExitCode,
        [string]$Message,
        [object]$Extra = $null
    )
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        message = $Message
        root = $Root
        stdout = $StdoutPath
        stderr = $StderrPath
        live_trading_locked = $true
        extra = $Extra
    }
    $Json = $Payload | ConvertTo-Json -Depth 5
    for ($Attempt = 1; $Attempt -le 10; $Attempt++) {
        try {
            $Json | Set-Content -LiteralPath $StatusPath -Encoding UTF8 -ErrorAction Stop
            return
        } catch {
            if ($Attempt -lt 10) { Start-Sleep -Milliseconds (100 * $Attempt) }
        }
    }
}

if (Test-Path -LiteralPath $LockPath) {
    $AgeMinutes = ((Get-Date) - (Get-Item -LiteralPath $LockPath).LastWriteTime).TotalMinutes
    $LockOwnerAlive = $false
    $LockOwnerPid = $null
    try {
        $ExistingLock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        $LockOwnerPid = [int]$ExistingLock.pid
        $LockOwnerAlive = $null -ne (Get-Process -Id $LockOwnerPid -ErrorAction SilentlyContinue)
    } catch {
        $LockOwnerAlive = $false
    }
    if ($LockOwnerAlive -and $AgeMinutes -lt $LockStaleMinutes) {
        Write-Status -Status "skipped_lock_active" -ExitCode 0 -Message "Previous scheduler lock is still fresh." -Extra @{ lock_age_minutes = [math]::Round($AgeMinutes, 2) }
        exit 0
    }
    Remove-Item -LiteralPath $LockPath -Force
}

$Lock = [ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
}
$Lock | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $LockPath -Encoding UTF8

try {
    $Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
    $Args = @()
    $Args += $Python.Prefix
    $Args += @(
        "tools\strategy_mix_forward_scheduler.py",
        "--source-report", "docs\STRATEGY_MIX_FORWARD_LOCKED_CANDIDATE_2026-06-29_4H_GUARDED_SHORT.json",
        "--cycles", "1",
        "--with-spot",
        "--out-prefix", "docs\STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08"
    )

    Push-Location $Root
    try {
        & $Python.Exe @Args > $StdoutPath 2> $StderrPath
        $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        $DataQualityExitCode = $null
        $DerivativesSqueezeExitCode = $null
        $AltSpotTailExitCode = $null
        $AltFuturesTailExitCode = $null
        $AltBreadthExitCode = $null
        $HealthExitCode = $null
        $InventoryExitCode = $null
        $FrontierExitCode = $null
        $WaitingBoardExitCode = $null
        if ($ExitCode -eq 0 -and -not $SkipDataQualityCollector) {
            $CollectorArgs = @()
            $CollectorArgs += $Python.Prefix
            $CollectorArgs += @(
                "tools\oi_funding_data_quality_collector.py",
                "--pages", [string]$DataQualityPages,
                "--funding-pages", [string]$DataQualityFundingPages,
                "--kline-pages", [string]$DataQualityKlinePages,
                "--out-prefix", "docs\OI_FUNDING_DATA_QUALITY_2026-06-15"
            )
            & $Python.Exe @CollectorArgs >> $StdoutPath 2>> $StderrPath
            $DataQualityExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        }
        if ($ExitCode -eq 0 -and ($null -eq $DataQualityExitCode -or $DataQualityExitCode -eq 0)) {
            $DerivativesSqueezeArgs = @()
            $DerivativesSqueezeArgs += $Python.Prefix
            $DerivativesSqueezeArgs += @(
                "tools\derivatives_squeeze_disagreement_forward_observer.py",
                "--out-prefix", "docs\DERIVATIVES_SQUEEZE_DISAGREEMENT_FORWARD_OBSERVER_2026-07-03"
            )
            & $Python.Exe @DerivativesSqueezeArgs >> $StdoutPath 2>> $StderrPath
            $DerivativesSqueezeExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        }
        if ($ExitCode -eq 0 -and -not $SkipAltBreadthRefresh) {
            $AltSpotTailArgs = @()
            $AltSpotTailArgs += $Python.Prefix
            $AltSpotTailArgs += @(
                "tools\binance_rest_kline_tail_gap_filler.py",
                "--market", "spot",
                "--symbols", "ETHUSDT,SOLUSDT,BCHUSDT",
                "--interval", "1h",
                "--out-prefix", "docs\ALT_BREADTH_SPOT_TAIL_REFRESH_2026-07-12"
            )
            & $Python.Exe @AltSpotTailArgs >> $StdoutPath 2>> $StderrPath
            $AltSpotTailExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }

            $AltFuturesTailArgs = @()
            $AltFuturesTailArgs += $Python.Prefix
            $AltFuturesTailArgs += @(
                "tools\binance_rest_kline_tail_gap_filler.py",
                "--market", "futures",
                "--symbols", "ETHUSDT,SOLUSDT,BCHUSDT",
                "--interval", "1h",
                "--out-prefix", "docs\ALT_BREADTH_FUTURES_TAIL_REFRESH_2026-07-12"
            )
            & $Python.Exe @AltFuturesTailArgs >> $StdoutPath 2>> $StderrPath
            $AltFuturesTailExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        }
        if ($ExitCode -eq 0 -and $AltSpotTailExitCode -eq 0 -and $AltFuturesTailExitCode -eq 0) {
            $AltBreadthArgs = @()
            $AltBreadthArgs += $Python.Prefix
            $AltBreadthArgs += @(
                "tools\alt_breadth_dislocation_forward_observer.py",
                "--out-prefix", "docs\ALT_BREADTH_DISLOCATION_FORWARD_OBSERVER_2026-07-03"
            )
            & $Python.Exe @AltBreadthArgs >> $StdoutPath 2>> $StderrPath
            $AltBreadthExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        }
        if ($ExitCode -eq 0 -and -not $SkipHealthCheck) {
            Write-Status -Status "health_check_running" -ExitCode 0 -Message "Forward paper scheduler completed; running runtime health check." -Extra @{
                python = $Python.Exe
                data_quality_exit_code = $DataQualityExitCode
                data_quality_skipped = [bool]$SkipDataQualityCollector
                derivatives_squeeze_exit_code = $DerivativesSqueezeExitCode
                alt_spot_tail_exit_code = $AltSpotTailExitCode
                alt_futures_tail_exit_code = $AltFuturesTailExitCode
                alt_breadth_exit_code = $AltBreadthExitCode
                alt_breadth_refresh_skipped = [bool]$SkipAltBreadthRefresh
                health_exit_code = $null
                health_skipped = $false
            }
            $HealthArgs = @()
            $HealthArgs += $Python.Prefix
            $HealthArgs += @(
                "tools\forward_runtime_health_check.py",
                "--out-prefix", "docs\FORWARD_RUNTIME_HEALTH_2026-06-16"
            )
            & $Python.Exe @HealthArgs >> $StdoutPath 2>> $StderrPath
            $HealthExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        }
        if ($ExitCode -eq 0) {
            $InventoryArgs = @()
            $InventoryArgs += $Python.Prefix
            $InventoryArgs += @(
                "tools\active_strategy_runtime_inventory.py",
                "--out-prefix", "docs\ACTIVE_STRATEGY_RUNTIME_MAP_2026-06-22"
            )
            & $Python.Exe @InventoryArgs >> $StdoutPath 2>> $StderrPath
            $InventoryExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        }
        if ($ExitCode -eq 0) {
            $FrontierArgs = @()
            $FrontierArgs += $Python.Prefix
            $FrontierArgs += @(
                "tools\strategy_research_frontier_matrix.py",
                "--observer-max-age-hours", "26",
                "--out-prefix", "docs\STRATEGY_RESEARCH_FRONTIER_MATRIX_2026-07-03_AFTER_OBSERVER_PULSE"
            )
            & $Python.Exe @FrontierArgs >> $StdoutPath 2>> $StderrPath
            $FrontierExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        }
        if ($ExitCode -eq 0 -and $FrontierExitCode -eq 0) {
            $WaitingBoardArgs = @()
            $WaitingBoardArgs += $Python.Prefix
            $WaitingBoardArgs += @(
                "tools\edge_waiting_board.py",
                "--strategy-frontier", "docs\STRATEGY_RESEARCH_FRONTIER_MATRIX_2026-07-03_AFTER_OBSERVER_PULSE.json",
                "--derivatives-squeeze", "docs\DERIVATIVES_SQUEEZE_DISAGREEMENT_FORWARD_OBSERVER_2026-07-03.json",
                "--alt-breadth", "docs\ALT_BREADTH_DISLOCATION_FORWARD_OBSERVER_2026-07-03.json",
                "--out-prefix", "docs\EDGE_WAITING_BOARD_2026-07-03_AFTER_OBSERVER_PULSE"
            )
            & $Python.Exe @WaitingBoardArgs >> $StdoutPath 2>> $StderrPath
            $WaitingBoardExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        }
    } finally {
        Pop-Location
    }

    if ($ExitCode -eq 0) {
        $StatusName = "completed"
        $Message = "Forward paper scheduler completed."
        if ($null -ne $DataQualityExitCode -and $DataQualityExitCode -ne 0) {
            $StatusName = "completed_data_quality_warning"
            $Message = "Forward paper scheduler completed, but data-quality collector returned non-zero."
        }
        if ($null -ne $DerivativesSqueezeExitCode -and $DerivativesSqueezeExitCode -ne 0) {
            $StatusName = "completed_derivatives_squeeze_warning"
            $Message = "Forward paper scheduler completed, but derivatives-squeeze observer returned non-zero."
        }
        if (($null -ne $AltSpotTailExitCode -and $AltSpotTailExitCode -ne 0) -or ($null -ne $AltFuturesTailExitCode -and $AltFuturesTailExitCode -ne 0)) {
            $StatusName = "completed_alt_breadth_data_warning"
            $Message = "Forward paper scheduler completed, but an alt-breadth public-data refresh returned non-zero."
        }
        if ($null -ne $AltBreadthExitCode -and $AltBreadthExitCode -ne 0) {
            $StatusName = "completed_alt_breadth_observer_warning"
            $Message = "Forward paper scheduler completed, but alt-breadth observer returned non-zero."
        }
        if ($null -ne $HealthExitCode -and $HealthExitCode -ne 0) {
            $StatusName = "completed_health_warning"
            $Message = "Forward paper scheduler completed, but runtime health check returned non-zero."
        }
        if ($null -ne $InventoryExitCode -and $InventoryExitCode -ne 0) {
            $StatusName = "completed_strategy_inventory_warning"
            $Message = "Forward paper scheduler completed, but active strategy inventory found a degraded family."
        }
        if ($null -ne $FrontierExitCode -and $FrontierExitCode -ne 0) {
            $StatusName = "completed_strategy_frontier_warning"
            $Message = "Forward paper scheduler completed, but strategy frontier refresh returned non-zero."
        }
        if ($null -ne $WaitingBoardExitCode -and $WaitingBoardExitCode -ne 0) {
            $StatusName = "completed_edge_waiting_board_warning"
            $Message = "Forward paper scheduler completed, but edge waiting board refresh returned non-zero."
        }
        Write-Status -Status $StatusName -ExitCode 0 -Message $Message -Extra @{
            python = $Python.Exe
            data_quality_exit_code = $DataQualityExitCode
            data_quality_skipped = [bool]$SkipDataQualityCollector
            derivatives_squeeze_exit_code = $DerivativesSqueezeExitCode
            alt_spot_tail_exit_code = $AltSpotTailExitCode
            alt_futures_tail_exit_code = $AltFuturesTailExitCode
            alt_breadth_exit_code = $AltBreadthExitCode
            alt_breadth_refresh_skipped = [bool]$SkipAltBreadthRefresh
            health_exit_code = $HealthExitCode
            health_skipped = [bool]$SkipHealthCheck
            strategy_inventory_exit_code = $InventoryExitCode
            strategy_frontier_exit_code = $FrontierExitCode
            edge_waiting_board_exit_code = $WaitingBoardExitCode
        }
    } else {
        Write-Status -Status "failed" -ExitCode $ExitCode -Message "Forward paper scheduler returned non-zero exit." -Extra @{
            python = $Python.Exe
            data_quality_exit_code = $DataQualityExitCode
            data_quality_skipped = [bool]$SkipDataQualityCollector
            derivatives_squeeze_exit_code = $DerivativesSqueezeExitCode
            alt_spot_tail_exit_code = $AltSpotTailExitCode
            alt_futures_tail_exit_code = $AltFuturesTailExitCode
            alt_breadth_exit_code = $AltBreadthExitCode
            alt_breadth_refresh_skipped = [bool]$SkipAltBreadthRefresh
            health_exit_code = $HealthExitCode
            health_skipped = [bool]$SkipHealthCheck
            strategy_inventory_exit_code = $InventoryExitCode
            strategy_frontier_exit_code = $FrontierExitCode
            edge_waiting_board_exit_code = $WaitingBoardExitCode
        }
    }
    exit $ExitCode
} catch {
    Write-Status -Status "exception" -ExitCode 1 -Message $_.Exception.Message
    exit 1
} finally {
    if (Test-Path -LiteralPath $LockPath) {
        Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    }
}
