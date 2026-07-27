param(
    [string]$ForwardFloor = "2026-07-14T14:00:00Z",
    [int]$RestCadenceSeconds = 300,
    [int]$RestOffsetSeconds = 2,
    [double]$CaptureMinutes = 30.2,
    [string]$PythonPath = "",
    [string]$LaunchAttemptId = "",
    [string]$RuntimeTag = "bitunix_wo105_v3",
    [string]$LockRelativePath = "configs\BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3_2026-07-14.json",
    [string]$AssemblerScriptRelativePath = "tools\bitunix_wo105_packet_assembler_v3.py",
    [string]$ShadowTag = "bitunix_wo105_shadow_v3",
    [string]$CohortLabel = "BITUNIX_WO105_V3",
    [string]$ReportDate = "2026-07-14",
    [string]$ManagedScriptPath = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ShutdownGateScript = Join-Path $Root "ops\autostart\TradingOSRuntimeShutdownGate.ps1"
try {
    if (-not (Test-Path -LiteralPath $ShutdownGateScript -PathType Leaf)) { throw "Runtime shutdown gate is unavailable." }
    . $ShutdownGateScript
    $null = Get-Command Test-TradingOSRuntimeShutdownRequested -CommandType Function -ErrorAction Stop
} catch {
    throw "Runtime shutdown gate failed to load: $($_.Exception.Message)"
}

if ($LaunchAttemptId) {
    try { $LaunchAttemptId = ([guid]$LaunchAttemptId).ToString() }
    catch { throw "LaunchAttemptId must be a valid non-empty GUID." }
    if ([guid]$LaunchAttemptId -eq [guid]::Empty) { throw "LaunchAttemptId must be a valid non-empty GUID." }
}

function Test-ShutdownRequested {
    $ShutdownRequested = $true
    try {
        $Result = Test-TradingOSRuntimeShutdownRequested -Root $Root -AllowedAttemptId $LaunchAttemptId
        if ($Result -is [bool] -and -not $Result) { $ShutdownRequested = $false }
    } catch { $ShutdownRequested = $true }
    return $ShutdownRequested
}

if (Test-ShutdownRequested) { exit 1 }
if ($Root -match "\\My Drive(\\|$)") { exit 2 }
if ($RestCadenceSeconds -lt 60 -or $RestOffsetSeconds -lt 1 -or $RestOffsetSeconds -gt 5) { exit 2 }
if ($CaptureMinutes -lt 30.1 -or $CaptureMinutes -gt 60) { exit 2 }
if ($RuntimeTag -notmatch '^[a-z0-9_]+$' -or $ShadowTag -notmatch '^[a-z0-9_]+$' -or $CohortLabel -notmatch '^[A-Z0-9_]+$') { exit 2 }
if ($ReportDate -notmatch '^\d{4}-\d{2}-\d{2}$') { exit 2 }

$RestRelative = "data\forward\${RuntimeTag}_rest"
$CaptureRelative = "data\forward\${RuntimeTag}_ws"
$WsIntakeRelative = "_dl\${RuntimeTag}_ws_intake"
$ShadowRelative = "_dl\$ShadowTag"
$StatusPrefixRelative = "docs\${CohortLabel}_STATUS_${ReportDate}"
$BlindGatePrefixRelative = "docs\${CohortLabel}_BLIND_REVIEW_GATE_${ReportDate}"
$FirstCyclePrefixRelative = "docs\${CohortLabel}_FIRST_CYCLE_GATE_${ReportDate}"
$LogDir = Join-Path $Root "logs\$RuntimeTag"
$LoopLockPath = Join-Path $LogDir "${RuntimeTag}_forward_loop.lock.json"
$LoopStatusPath = Join-Path $LogDir "${RuntimeTag}_forward_loop_status.json"
$MilestoneJournalPath = Join-Path $LogDir "${RuntimeTag}_first_cycle_milestones.jsonl"
$StdoutPath = Join-Path $LogDir "${RuntimeTag}_forward_loop_stdout.log"
$StderrPath = Join-Path $LogDir "${RuntimeTag}_forward_loop_stderr.log"
$RestRoot = Join-Path $Root $RestRelative
$CaptureRoot = Join-Path $Root $CaptureRelative
New-Item -ItemType Directory -Force -Path $LogDir, $RestRoot, $CaptureRoot | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    foreach ($Candidate in @(
        $Requested,
        $env:TRADING_OS_PYTHON,
        (Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe")
    )) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) { return (Resolve-Path -LiteralPath $Candidate).Path }
    }
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCommand) { return $PythonCommand.Source }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

function Write-LoopStatus {
    param([string]$Status, [object]$Extra = $null)
    [ordered]@{
        ts = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        pid = $PID
        root = $Root
        runtime_tag = $RuntimeTag
        forward_floor = $ForwardFloor
        rest_cadence_seconds = $RestCadenceSeconds
        rest_offset_seconds = $RestOffsetSeconds
        capture_minutes = $CaptureMinutes
        receipt_selection = "earliest_received_record_per_close_ms"
        event_continuation = $true
        public_data_only = $true
        credentials_allowed = $false
        signals_allowed = $false
        paper_entries_allowed = $false
        orders_allowed = $false
        capital_permission = "DENY"
        can_trade = $false
        extra = $Extra
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $LoopStatusPath -Encoding UTF8
}

function Test-PidAlive {
    param([int]$PidValue)
    return $PidValue -gt 0 -and [bool](Get-Process -Id $PidValue -ErrorAction SilentlyContinue)
}

function Invoke-PublicTool {
    param([string[]]$Arguments)
    Push-Location $Root
    try {
        $PreviousErrorActionPreference = $ErrorActionPreference
        try {
            # PowerShell 5 turns native stderr into ErrorRecord objects. Keep
            # those records non-terminating so the loop can inspect exit code.
            $ErrorActionPreference = "Continue"
            & $Python @Arguments >> $StdoutPath 2>> $StderrPath
            $ExitCode = $(if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE })
        } finally {
            $ErrorActionPreference = $PreviousErrorActionPreference
        }
    } finally { Pop-Location }
    return $ExitCode
}

function Write-Milestone {
    param(
        [ValidateSet("loop_transitioned_after_floor", "post_floor_rest_snapshot", "post_floor_ws_independently_accepted", "post_floor_packet_assembler_ran")]
        [string]$Name,
        [object]$Evidence = $null
    )
    if ([DateTimeOffset]::UtcNow -lt $Floor) { return }
    if (Test-Path -LiteralPath $MilestoneJournalPath) {
        foreach ($Line in Get-Content -LiteralPath $MilestoneJournalPath -ErrorAction SilentlyContinue) {
            try {
                $Existing = $Line | ConvertFrom-Json -ErrorAction Stop
                if ([string]$Existing.milestone -eq $Name -and [string]$Existing.cohort_id -eq $CohortId) { return }
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
    } | ConvertTo-Json -Depth 8 -Compress | Add-Content -LiteralPath $MilestoneJournalPath -Encoding UTF8
}

function Get-NextRestAt {
    $NowSeconds = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $Boundary = ([math]::Floor($NowSeconds / $RestCadenceSeconds) + 1) * $RestCadenceSeconds
    return [DateTimeOffset]::FromUnixTimeSeconds([int64]($Boundary + $RestOffsetSeconds))
}

if (Test-Path -LiteralPath $LoopLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LoopLockPath -Raw | ConvertFrom-Json
        if (Test-PidAlive -PidValue ([int]$Existing.pid)) {
            Write-LoopStatus -Status "skipped_existing_${RuntimeTag}_forward_loop" -Extra @{ existing_pid = [int]$Existing.pid }
            exit 0
        }
    } catch {}
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
}

$Floor = [DateTimeOffset]::Parse($ForwardFloor).ToUniversalTime()
$Python = Get-PreferredPython -Requested $PythonPath
$FrozenLockPath = Join-Path $Root $LockRelativePath
$FrozenLock = Get-Content -LiteralPath $FrozenLockPath -Raw | ConvertFrom-Json
$CohortId = [string]$FrozenLock.cohort_id
if (-not $CohortId -or [string]$FrozenLock.forward_start_at -ne $ForwardFloor -or $FrozenLock.can_trade -ne $false) {
    throw "WO105 runtime arguments do not match the frozen no-trade lock."
}
$ExpectedAssemblerRelativePath = [string]$FrozenLock.bindings.packet_assembler
$ExpectedAssemblerSha256 = ([string]$FrozenLock.bindings.packet_assembler_sha256).ToLowerInvariant()
$AssemblerScriptPath = [IO.Path]::GetFullPath((Join-Path $Root $AssemblerScriptRelativePath))
$ExpectedAssemblerPath = [IO.Path]::GetFullPath((Join-Path $Root $ExpectedAssemblerRelativePath))
if (
    -not $ExpectedAssemblerRelativePath -or
    -not $ExpectedAssemblerSha256 -or
    -not (Test-Path -LiteralPath $AssemblerScriptPath -PathType Leaf) -or
    $AssemblerScriptPath -ine $ExpectedAssemblerPath -or
    (Get-FileHash -Algorithm SHA256 -LiteralPath $AssemblerScriptPath).Hash.ToLowerInvariant() -ne $ExpectedAssemblerSha256
) {
    throw "WO105 packet assembler does not match the frozen path/hash binding."
}
$ReceiptScriptPath = if ($ManagedScriptPath) { (Resolve-Path -LiteralPath $ManagedScriptPath).Path } else { $MyInvocation.MyCommand.Path }

[ordered]@{
    pid = $PID
    started_at = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    script = $ReceiptScriptPath
    runtime_tag = $RuntimeTag
    forward_floor = $ForwardFloor
    public_data_only = $true
    credentials_allowed = $false
    orders_allowed = $false
    can_trade = $false
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LoopLockPath -Encoding UTF8

$RestArgs = @(
    "tools\bitunix_wo105_public_rest_collector.py",
    "--forward-floor", $ForwardFloor,
    "--outbase", $RestRelative
)
$AssemblerArgs = @(
    $AssemblerScriptRelativePath,
    "--lock", $LockRelativePath,
    "--rest-root", $RestRelative,
    "--ws-capture-root", $CaptureRelative,
    "--ws-intake-dir", $WsIntakeRelative,
    "--out-dir", $ShadowRelative
)
$StatusArgs = @(
    "tools\bitunix_wo105_v2_status.py",
    "--lock", $LockRelativePath,
    "--tombstone", "docs\BITUNIX_WO105_V2_PRE_FLOOR_RUNTIME_TOMBSTONE_2026-07-14.json",
    "--packet-status", "$ShadowRelative\PACKET_ASSEMBLY_STATUS.json",
    "--ws-status", "$WsIntakeRelative\WS_INTAKE_MANIFEST.json",
    "--ledger", "$ShadowRelative\EVENT_LEDGER.jsonl",
    "--out-prefix", $StatusPrefixRelative,
    "--blind-gate-prefix", $BlindGatePrefixRelative
)
$FirstCycleArgs = @(
    "tools\bitunix_wo105_v2_first_cycle_gate.py",
    "--lock", $LockRelativePath,
    "--loop-status", "logs\$RuntimeTag\${RuntimeTag}_forward_loop_status.json",
    "--rest-root", $RestRelative,
    "--ws-intake", "$WsIntakeRelative\WS_INTAKE_MANIFEST.json",
    "--packet-status", "$ShadowRelative\PACKET_ASSEMBLY_STATUS.json",
    "--milestone-journal", "logs\$RuntimeTag\${RuntimeTag}_first_cycle_milestones.jsonl",
    "--out-prefix", $FirstCyclePrefixRelative
)

try {
    [void](Invoke-PublicTool -Arguments $StatusArgs)
    [void](Invoke-PublicTool -Arguments $FirstCycleArgs)
    while ([DateTimeOffset]::UtcNow -lt $Floor) {
        if (Test-ShutdownRequested) { exit 0 }
        $Remaining = [math]::Max(1, [math]::Ceiling(($Floor - [DateTimeOffset]::UtcNow).TotalSeconds))
        Write-LoopStatus -Status "waiting_forward_floor" -Extra @{ remaining_seconds = $Remaining; python = $Python }
        Start-Sleep -Seconds ([int][math]::Min(60, $Remaining))
    }
    Write-Milestone -Name "loop_transitioned_after_floor" -Evidence @{ status = "forward_floor_reached" }
    [void](Invoke-PublicTool -Arguments $FirstCycleArgs)

    while (-not (Test-ShutdownRequested)) {
        $CycleId = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
        $CaptureStdout = Join-Path $LogDir ("capture_" + $CycleId + ".stdout.log")
        $CaptureStderr = Join-Path $LogDir ("capture_" + $CycleId + ".stderr.log")
        $CaptureArgs = @(
            "tools\bitunix_wo104_public_capture_runner.py",
            "--outbase", $CaptureRelative,
            "--minutes", ([string]$CaptureMinutes)
        )
        Write-LoopStatus -Status "starting_public_ws_capture" -Extra @{ cycle_id = $CycleId; python = $Python }
        $Capture = Start-Process -FilePath $Python -ArgumentList $CaptureArgs -WorkingDirectory $Root `
            -RedirectStandardOutput $CaptureStdout -RedirectStandardError $CaptureStderr -WindowStyle Hidden -PassThru
        $NextRestAt = Get-NextRestAt
        while (-not $Capture.HasExited) {
            if (Test-ShutdownRequested) {
                Stop-Process -Id $Capture.Id -Force -ErrorAction SilentlyContinue
                exit 0
            }
            if ([DateTimeOffset]::UtcNow -ge $NextRestAt) {
                Write-LoopStatus -Status "collecting_earliest_causal_rest_snapshot" -Extra @{ cycle_id = $CycleId; scheduled_at = $NextRestAt.ToString("o") }
                $RestExit = Invoke-PublicTool -Arguments $RestArgs
                if ($RestExit -eq 0) {
                    Write-Milestone -Name "post_floor_rest_snapshot" -Evidence @{ cycle_id = $CycleId; scheduled_at = $NextRestAt.ToString("o") }
                }
                $NextRestAt = Get-NextRestAt
                Write-LoopStatus -Status "public_ws_capture_running" -Extra @{
                    cycle_id = $CycleId
                    last_rest_exit_code = $RestExit
                    next_rest_at = $NextRestAt.ToString("o")
                }
            }
            Start-Sleep -Seconds 1
            $Capture.Refresh()
        }

        $Capture.WaitForExit()
        $Capture.Refresh()
        $CaptureExit = [int]$Capture.ExitCode
        Write-LoopStatus -Status "assembling_and_continuing_shadow_events" -Extra @{ cycle_id = $CycleId; capture_exit_code = $CaptureExit }
        $AssemblerExit = Invoke-PublicTool -Arguments $AssemblerArgs
        try {
            $WsIntake = Get-Content -LiteralPath (Join-Path $Root "$WsIntakeRelative\WS_INTAKE_MANIFEST.json") -Raw | ConvertFrom-Json
            if ([string]$WsIntake.decision -eq "bitunix_wo105_ws_intake_ready" -and [int]$WsIntake.accepted_runs -ge 1) {
                Write-Milestone -Name "post_floor_ws_independently_accepted" -Evidence @{ cycle_id = $CycleId; accepted_runs = [int]$WsIntake.accepted_runs }
            }
        } catch {}
        try {
            $PacketStatus = Get-Content -LiteralPath (Join-Path $Root "$ShadowRelative\PACKET_ASSEMBLY_STATUS.json") -Raw | ConvertFrom-Json
            if ([int]$PacketStatus.rest_eligible_runs -ge 1 -and [int]$PacketStatus.ws_accepted_runs -ge 1 -and $PacketStatus.can_trade -eq $false) {
                Write-Milestone -Name "post_floor_packet_assembler_ran" -Evidence @{ cycle_id = $CycleId; decision = [string]$PacketStatus.decision }
            }
        } catch {}
        $StatusExit = Invoke-PublicTool -Arguments $StatusArgs
        $GateExit = Invoke-PublicTool -Arguments $FirstCycleArgs
        Write-LoopStatus -Status $(if ($CaptureExit -eq 0 -and $AssemblerExit -eq 0 -and $StatusExit -eq 0 -and $GateExit -eq 0) { "cycle_complete_shadow_only" } else { "cycle_failed_closed" }) -Extra @{
            cycle_id = $CycleId
            capture_exit_code = $CaptureExit
            assembler_exit_code = $AssemblerExit
            status_exit_code = $StatusExit
            first_cycle_gate_exit_code = $GateExit
        }
        if ($CaptureExit -ne 0) { Start-Sleep -Seconds 60 }
    }
} finally {
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
    Write-LoopStatus -Status "stopped"
}
