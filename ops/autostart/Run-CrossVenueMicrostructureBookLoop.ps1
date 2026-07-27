param(
    [int]$SleepSeconds = 20,
    [string]$PythonPath = "",
    [switch]$NoImmediateRun,
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
$LogDir = Join-Path $Root "logs\cross_venue_microstructure"
$LockPath = Join-Path $LogDir "microstructure_book_loop.lock.json"
$StatusPath = Join-Path $LogDir "microstructure_book_loop_status.json"
$StdoutPath = Join-Path $LogDir "microstructure_book_loop_stdout.log"
$StderrPath = Join-Path $LogDir "microstructure_book_loop_stderr.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-PreferredPython {
    param([string]$Requested)
    if ($Requested -and (Test-Path -LiteralPath $Requested)) { return @{ Exe = $Requested; Prefix = @() } }
    $HermesPython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $HermesPython) { return @{ Exe = $HermesPython; Prefix = @() } }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) { return @{ Exe = $Python.Source; Prefix = @() } }
    $Py = Get-Command py -ErrorAction SilentlyContinue
    if ($Py) { return @{ Exe = $Py.Source; Prefix = @("-3") } }
    throw "No Python runtime found. Set TRADING_OS_PYTHON or pass -PythonPath."
}

function Write-JsonFileSafe {
    param([string]$Path, [object]$Payload)
    $Json = $Payload | ConvertTo-Json -Depth 6
    $LastErrorMessage = $null
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        $TempPath = "$Path.tmp.$PID.$Attempt"
        try {
            $Json | Set-Content -LiteralPath $TempPath -Encoding UTF8
            Move-Item -LiteralPath $TempPath -Destination $Path -Force
            return
        } catch {
            $LastErrorMessage = $_.Exception.Message
            Start-Sleep -Milliseconds (100 * $Attempt)
        } finally {
            Remove-Item -LiteralPath $TempPath -Force -ErrorAction SilentlyContinue
        }
    }
    try {
        [ordered]@{
            ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            target = $Path
            error = $LastErrorMessage
            can_trade = $false
        } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath "$Path.write_failed.$PID.json" -Encoding UTF8
    } catch {}
}

function Write-LoopStatus {
    param([string]$Status, [int]$ExitCode = 0, [object]$Extra = $null)
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        public_data_only = $true
        book_snapshots_only = $true
        signals_allowed = $false
        paper_entries_allowed = $false
        orders_allowed = $false
        can_trade = $false
        extra = $Extra
    }
    Write-JsonFileSafe -Path $StatusPath -Payload $Payload
}

function Move-CycleCaptureToLog {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        return [pscustomobject]@{ decision = "capture_missing"; spill_path = $null; bytes = 0 }
    }
    $Bytes = [System.IO.File]::ReadAllBytes($SourcePath)
    if ($Bytes.Length -eq 0) {
        Remove-Item -LiteralPath $SourcePath -Force -ErrorAction SilentlyContinue
        return [pscustomobject]@{ decision = "empty_capture_discarded"; spill_path = $null; bytes = 0 }
    }

    $LastErrorMessage = $null
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        $Stream = $null
        try {
            $Stream = [System.IO.File]::Open(
                $DestinationPath,
                [System.IO.FileMode]::Append,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::ReadWrite
            )
            $Stream.Write($Bytes, 0, $Bytes.Length)
            $Stream.Flush($true)
            $Stream.Dispose()
            $Stream = $null
            Remove-Item -LiteralPath $SourcePath -Force -ErrorAction SilentlyContinue
            return [pscustomobject]@{ decision = "capture_appended"; spill_path = $null; bytes = $Bytes.Length }
        } catch {
            $LastErrorMessage = $_.Exception.Message
            Start-Sleep -Milliseconds (100 * $Attempt)
        } finally {
            if ($Stream) { try { $Stream.Dispose() } catch {} }
        }
    }

    $SpillPath = "$DestinationPath.spill.$PID.$([guid]::NewGuid().ToString('N')).log"
    try {
        Move-Item -LiteralPath $SourcePath -Destination $SpillPath -Force -ErrorAction Stop
        return [pscustomobject]@{
            decision = "capture_spilled_after_append_contention"
            spill_path = $SpillPath
            bytes = $Bytes.Length
            error = $LastErrorMessage
        }
    } catch {
        return [pscustomobject]@{
            decision = "capture_preserved_at_source_after_append_failure"
            spill_path = $SourcePath
            bytes = $Bytes.Length
            error = "$LastErrorMessage; spill_failed:$($_.Exception.Message)"
        }
    }
}

if ($SleepSeconds -lt 5) { throw "SleepSeconds must be at least 5." }
if (Test-Path -LiteralPath $LockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            [ordered]@{
                status = "skipped_existing_book_loop"
                existing_pid = $ExistingPid
                shared_status_preserved = $true
                can_trade = $false
            } | ConvertTo-Json -Depth 3
            return
        }
    } catch {}
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
    public_data_only = $true
    book_snapshots_only = $true
    can_trade = $false
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LockPath -Encoding UTF8

$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
$PythonArgs = @()
$PythonArgs += $Python.Prefix
$PythonArgs += @(
    "tools\cross_venue_microstructure_book_only_collector.py",
    "--report-prefix", "docs\CROSS_VENUE_MICROSTRUCTURE_BOOK_ONLY_COLLECTOR_2026-07-03_LOOP"
)

function Invoke-BookCycle {
    Push-Location $Root
    try {
        $env:BOT_ENV = "demo"
        Write-LoopStatus -Status "running_book_cycle" -Extra @{ python = $Python.Exe }
        $CaptureId = [guid]::NewGuid().ToString("N")
        $CycleStdoutPath = Join-Path $LogDir "microstructure_book_loop_stdout.$PID.$CaptureId.tmp"
        $CycleStderrPath = Join-Path $LogDir "microstructure_book_loop_stderr.$PID.$CaptureId.tmp"
        $PreviousCycleErrorActionPreference = $ErrorActionPreference
        try {
            # Windows PowerShell 5.1 promotes native stderr to NativeCommandError.
            # A transient API failure must fail this cycle, not terminate the loop.
            $ErrorActionPreference = "Continue"
            & $Python.Exe @PythonArgs > $CycleStdoutPath 2> $CycleStderrPath
            $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        } finally {
            $ErrorActionPreference = $PreviousCycleErrorActionPreference
            $StdoutFlush = Move-CycleCaptureToLog -SourcePath $CycleStdoutPath -DestinationPath $StdoutPath
            $StderrFlush = Move-CycleCaptureToLog -SourcePath $CycleStderrPath -DestinationPath $StderrPath
        }
        $CycleStatus = if ($ExitCode -eq 0) { "ran_book_cycle" } else { "book_cycle_failed" }
        Write-LoopStatus -Status $CycleStatus -ExitCode $ExitCode -Extra @{
            python = $Python.Exe
            next_run_after_seconds = $SleepSeconds
            stdout_log_decision = $StdoutFlush.decision
            stdout_log_spill_path = $StdoutFlush.spill_path
            stderr_log_decision = $StderrFlush.decision
            stderr_log_spill_path = $StderrFlush.spill_path
        }
    } finally {
        Pop-Location
    }
}

try {
    if (-not $NoImmediateRun) { Invoke-BookCycle }
    while ($true) {
        Start-Sleep -Seconds $SleepSeconds
        Invoke-BookCycle
    }
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Write-LoopStatus -Status "stopped"
}
