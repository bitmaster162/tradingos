param(
    [string]$ForwardFloor = "2026-07-14T12:00:00Z",
    [int]$RestCadenceSeconds = 300,
    [int]$RestOffsetSeconds = 2,
    [double]$CaptureMinutes = 30,
    [string]$PythonPath = "",
    [string]$LaunchAttemptId = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ShutdownGateScript = Join-Path $Root "ops\autostart\TradingOSRuntimeShutdownGate.ps1"
$PreviousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Stop"
    if (-not (Test-Path -LiteralPath $ShutdownGateScript -PathType Leaf -ErrorAction Stop)) { throw "Runtime shutdown gate is unavailable." }
    . $ShutdownGateScript
    $null = Get-Command Test-TradingOSRuntimeShutdownRequested -CommandType Function -ErrorAction Stop
} catch {
    throw "Runtime shutdown gate failed to load: $($_.Exception.Message)"
} finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}
if ($LaunchAttemptId) {
    try { $LaunchAttemptId = ([guid]$LaunchAttemptId).ToString() }
    catch { throw "LaunchAttemptId must be a valid non-empty GUID." }
    if ([guid]$LaunchAttemptId -eq [guid]::Empty) { throw "LaunchAttemptId must be a valid non-empty GUID." }
}
$ShutdownRequested = $true
try {
    $ShutdownGateResult = Test-TradingOSRuntimeShutdownRequested -Root $Root -AllowedAttemptId $LaunchAttemptId
    if ($ShutdownGateResult -is [bool] -and -not $ShutdownGateResult) { $ShutdownRequested = $false }
} catch { $ShutdownRequested = $true }
if ($ShutdownRequested) { exit 1 }
$LogDir = Join-Path $Root "logs\bitunix_wo105_v2"
$LoopLockPath = Join-Path $LogDir "bitunix_wo105_v2_forward_loop.lock.json"
$LoopStatusPath = Join-Path $LogDir "bitunix_wo105_v2_forward_loop_status.json"
$MilestoneJournalPath = Join-Path $LogDir "bitunix_wo105_v2_first_cycle_milestones.jsonl"
$StdoutPath = Join-Path $LogDir "bitunix_wo105_v2_forward_loop_stdout.log"
$StderrPath = Join-Path $LogDir "bitunix_wo105_v2_forward_loop_stderr.log"
$CaptureRoot = Join-Path $Root "data\forward\bitunix_wo105_ws"
New-Item -ItemType Directory -Force -Path $LogDir, $CaptureRoot | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    foreach ($Candidate in @(
        $Requested,
        $env:TRADING_OS_PYTHON,
        (Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe")
    )) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) { return $Python.Source }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

function Write-LoopStatus {
    param(
        [string]$Status,
        [object]$Extra = $null
    )
    [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        pid = $PID
        root = $Root
        forward_floor = $ForwardFloor
        rest_cadence_seconds = $RestCadenceSeconds
        rest_offset_seconds = $RestOffsetSeconds
        capture_minutes = $CaptureMinutes
        public_data_only = $true
        credentials_allowed = $false
        signals_allowed = $false
        paper_entries_allowed = $false
        orders_allowed = $false
        capital_permission = "DENY"
        can_trade = $false
        extra = $Extra
    } | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $LoopStatusPath -Encoding UTF8
}

function Test-PidAlive {
    param([int]$PidValue)
    return $PidValue -gt 0 -and [bool](Get-Process -Id $PidValue -ErrorAction SilentlyContinue)
}

function Invoke-PublicTool {
    param([string[]]$Arguments)
    Push-Location $Root
    try {
        & $Python @Arguments >> $StdoutPath 2>> $StderrPath
        return $(if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE })
    } finally {
        Pop-Location
    }
}

function Write-FirstCycleMilestone {
    param(
        [Parameter(Mandatory = $true)][ValidateSet(
            "loop_transitioned_after_floor",
            "post_floor_rest_snapshot",
            "post_floor_ws_independently_accepted",
            "post_floor_packet_assembler_ran"
        )][string]$Name,
        [object]$Evidence = $null
    )
    if ([DateTimeOffset]::UtcNow -lt $Floor) { return }
    if (Test-Path -LiteralPath $MilestoneJournalPath) {
        foreach ($Line in Get-Content -LiteralPath $MilestoneJournalPath -ErrorAction SilentlyContinue) {
            try {
                $ExistingMilestone = $Line | ConvertFrom-Json -ErrorAction Stop
                if ([string]$ExistingMilestone.milestone -eq $Name -and [string]$ExistingMilestone.cohort_id -eq $CohortId) { return }
            } catch {}
        }
    }
    [ordered]@{
        schema_version = 1
        observed_at = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        milestone = $Name
        cohort_id = $CohortId
        forward_start_at = $ForwardFloor
        evidence = $Evidence
        public_data_only = $true
        credentials_allowed = $false
        signals_allowed = $false
        paper_entries_allowed = $false
        orders_allowed = $false
        capital_permission = "DENY"
        can_trade = $false
    } | ConvertTo-Json -Depth 7 -Compress | Add-Content -LiteralPath $MilestoneJournalPath -Encoding UTF8
}

function Update-FirstCycleGate {
    [void](Invoke-PublicTool -Arguments $FirstCycleGateArgs)
}

function Get-NextRestAt {
    $NowSeconds = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $Boundary = ([math]::Floor($NowSeconds / $RestCadenceSeconds) + 1) * $RestCadenceSeconds
    return [DateTimeOffset]::FromUnixTimeSeconds([int64]($Boundary + $RestOffsetSeconds))
}

if ($Root -match "\\My Drive(\\|$)") {
    Write-LoopStatus -Status "blocked_google_drive_runtime"
    exit 2
}
if ($RestCadenceSeconds -lt 60 -or $RestOffsetSeconds -lt 1 -or $RestOffsetSeconds -gt 5) {
    Write-LoopStatus -Status "blocked_invalid_rest_schedule"
    exit 2
}
if ($CaptureMinutes -lt 30 -or $CaptureMinutes -gt 60) {
    Write-LoopStatus -Status "blocked_invalid_capture_duration"
    exit 2
}

if (Test-Path -LiteralPath $LoopLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LoopLockPath -Raw | ConvertFrom-Json
        if (Test-PidAlive -PidValue ([int]$Existing.pid)) {
            Write-LoopStatus -Status "skipped_existing_bitunix_wo105_v2_forward_loop" -Extra @{ existing_pid = [int]$Existing.pid }
            exit 0
        }
    } catch {}
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
}

$Floor = [DateTimeOffset]::Parse($ForwardFloor).ToUniversalTime()
$Python = Get-PreferredPython -Requested $PythonPath
$V2LockPath = Join-Path $Root "configs\BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json"
try {
    $V2Lock = Get-Content -LiteralPath $V2LockPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    $CohortId = [string]$V2Lock.cohort_id
} catch { throw "WO105 V2 lock failed to load: $($_.Exception.Message)" }
if (-not $CohortId -or [string]$V2Lock.forward_start_at -ne $ForwardFloor -or $V2Lock.can_trade -ne $false) {
    throw "WO105 V2 runtime arguments do not match the frozen no-trade lock."
}
[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    script = $MyInvocation.MyCommand.Path
    forward_floor = $ForwardFloor
    public_data_only = $true
    credentials_allowed = $false
    orders_allowed = $false
    can_trade = $false
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LoopLockPath -Encoding UTF8

$StatusArgs = @(
    "tools\bitunix_wo105_v2_status.py",
    "--out-prefix", "docs\BITUNIX_WO105_V2_STATUS_2026-07-14"
)
$RestArgs = @(
    "tools\bitunix_wo105_public_rest_collector.py",
    "--forward-floor", $ForwardFloor
)
$AssemblerArgs = @("tools\bitunix_wo105_packet_assembler.py")
$FirstCycleGateArgs = @(
    "tools\bitunix_wo105_v2_first_cycle_gate.py",
    "--out-prefix", "docs\BITUNIX_WO105_V2_FIRST_CYCLE_GATE_2026-07-14",
    "--milestone-journal", "logs\bitunix_wo105_v2\bitunix_wo105_v2_first_cycle_milestones.jsonl"
)

try {
    [void](Invoke-PublicTool -Arguments $StatusArgs)
    while ([DateTimeOffset]::UtcNow -lt $Floor) {
        $Remaining = [math]::Max(1, [math]::Ceiling(($Floor - [DateTimeOffset]::UtcNow).TotalSeconds))
        Write-LoopStatus -Status "waiting_forward_floor" -Extra @{ remaining_seconds = $Remaining; python = $Python }
        Start-Sleep -Seconds ([int][math]::Min(60, $Remaining))
    }

    Write-FirstCycleMilestone -Name "loop_transitioned_after_floor" -Evidence @{ status = "forward_floor_reached" }
    Update-FirstCycleGate

    while ($true) {
        $CycleId = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
        $CaptureStdout = Join-Path $LogDir ("capture_" + $CycleId + ".stdout.log")
        $CaptureStderr = Join-Path $LogDir ("capture_" + $CycleId + ".stderr.log")
        $CaptureArgs = @(
            "tools\bitunix_wo104_public_capture_runner.py",
            "--outbase", "data\forward\bitunix_wo105_ws",
            "--minutes", ([string]$CaptureMinutes)
        )
        Write-LoopStatus -Status "starting_public_ws_capture" -Extra @{ cycle_id = $CycleId; python = $Python }
        $Capture = Start-Process `
            -FilePath $Python `
            -ArgumentList $CaptureArgs `
            -WorkingDirectory $Root `
            -RedirectStandardOutput $CaptureStdout `
            -RedirectStandardError $CaptureStderr `
            -WindowStyle Hidden `
            -PassThru

        $NextRestAt = Get-NextRestAt
        while (-not $Capture.HasExited) {
            if ([DateTimeOffset]::UtcNow -ge $NextRestAt) {
                Write-LoopStatus -Status "collecting_causal_rest_snapshot" -Extra @{ cycle_id = $CycleId; capture_pid = $Capture.Id; scheduled_at = $NextRestAt.ToString("o") }
                $RestExit = Invoke-PublicTool -Arguments $RestArgs
                if ($RestExit -eq 0) {
                    Write-FirstCycleMilestone -Name "post_floor_rest_snapshot" -Evidence @{ cycle_id = $CycleId; scheduled_at = $NextRestAt.ToString("o") }
                    Update-FirstCycleGate
                }
                Write-LoopStatus -Status "public_ws_and_rest_collecting" -Extra @{ cycle_id = $CycleId; capture_pid = $Capture.Id; rest_exit_code = $RestExit }
                $NextRestAt = Get-NextRestAt
            }
            Start-Sleep -Seconds 1
            $Capture.Refresh()
        }

        $CaptureExit = $Capture.ExitCode
        Write-LoopStatus -Status "assembling_shadow_packet" -Extra @{ cycle_id = $CycleId; capture_exit_code = $CaptureExit }
        $AssemblerExit = Invoke-PublicTool -Arguments $AssemblerArgs
        if ($AssemblerExit -eq 0) {
            $WsIntakePath = Join-Path $Root "_dl\bitunix_wo105_ws_intake\WS_INTAKE_MANIFEST.json"
            $PacketStatusPath = Join-Path $Root "_dl\bitunix_wo105_shadow_v2\PACKET_ASSEMBLY_STATUS.json"
            try {
                $WsIntake = Get-Content -LiteralPath $WsIntakePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
                if ([string]$WsIntake.decision -eq "bitunix_wo105_ws_intake_ready" -and [int]$WsIntake.accepted_runs -ge 1 -and $WsIntake.can_trade -eq $false) {
                    Write-FirstCycleMilestone -Name "post_floor_ws_independently_accepted" -Evidence @{ cycle_id = $CycleId; accepted_runs = [int]$WsIntake.accepted_runs }
                }
            } catch {}
            try {
                $PacketStatus = Get-Content -LiteralPath $PacketStatusPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
                if ([int]$PacketStatus.rest_eligible_runs -ge 1 -and [int]$PacketStatus.ws_accepted_runs -ge 1 -and $PacketStatus.can_trade -eq $false) {
                    Write-FirstCycleMilestone -Name "post_floor_packet_assembler_ran" -Evidence @{ cycle_id = $CycleId; decision = [string]$PacketStatus.decision }
                }
            } catch {}
            Update-FirstCycleGate
        }
        $StatusExit = Invoke-PublicTool -Arguments $StatusArgs
        Write-LoopStatus -Status $(if ($CaptureExit -eq 0 -and $AssemblerExit -eq 0 -and $StatusExit -eq 0) { "cycle_complete_shadow_only" } else { "cycle_failed_closed" }) -Extra @{
            cycle_id = $CycleId
            capture_exit_code = $CaptureExit
            assembler_exit_code = $AssemblerExit
            status_exit_code = $StatusExit
        }
        if ($CaptureExit -ne 0) { Start-Sleep -Seconds 60 }
    }
} finally {
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
    Write-LoopStatus -Status "stopped"
}
