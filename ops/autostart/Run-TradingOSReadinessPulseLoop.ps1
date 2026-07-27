param(
    [int]$SleepSeconds = 900,
    [switch]$NoImmediateRun,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LogDir = Join-Path $Root "logs\readiness_pulse"
$LoopLockPath = Join-Path $LogDir "readiness_pulse_loop.lock.json"
$LoopStatusPath = Join-Path $LogDir "readiness_pulse_loop_status.json"
$StdoutPath = Join-Path $LogDir "readiness_pulse_stdout.log"
$StderrPath = Join-Path $LogDir "readiness_pulse_stderr.log"
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

function Write-LoopStatus {
    param([string]$Status, [int]$ExitCode = 0, [object]$Extra = $null)
    [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        live_trading_locked = $true
        observability_only = $true
        extra = $Extra
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $LoopStatusPath -Encoding UTF8
}

if (Test-Path -LiteralPath $LoopLockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LoopLockPath -Raw | ConvertFrom-Json
        $ExistingPid = [int]$Existing.pid
        if ($ExistingPid -gt 0 -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
            Write-LoopStatus -Status "skipped_existing_readiness_pulse_loop" -Extra @{ existing_pid = $ExistingPid }
            exit 0
        }
    } catch {}
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
}

[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $LoopLockPath -Encoding UTF8

$Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
$Args = @()
$Args += $Python.Prefix
$Args += @(
    "tools\tradingos_readiness_pulse.py",
    "--out-prefix", "docs\TRADINGOS_READINESS_PULSE_2026-06-30"
)

try {
    if (-not $NoImmediateRun) {
        Push-Location $Root
        try {
            Write-LoopStatus -Status "running_pulse" -Extra @{ python = $Python.Exe }
            & $Python.Exe @Args >> $StdoutPath 2>> $StderrPath
            $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            Write-LoopStatus -Status "ran_pulse" -ExitCode $ExitCode -Extra @{ python = $Python.Exe }
        } finally {
            Pop-Location
        }
    }
    while ($true) {
        Write-LoopStatus -Status "sleeping" -Extra @{ next_run_after_seconds = $SleepSeconds; python = $Python.Exe }
        Start-Sleep -Seconds $SleepSeconds
        Push-Location $Root
        try {
            Write-LoopStatus -Status "running_pulse" -Extra @{ python = $Python.Exe }
            & $Python.Exe @Args >> $StdoutPath 2>> $StderrPath
            $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
            Write-LoopStatus -Status "ran_pulse" -ExitCode $ExitCode -Extra @{ python = $Python.Exe }
        } finally {
            Pop-Location
        }
    }
} finally {
    Remove-Item -LiteralPath $LoopLockPath -Force -ErrorAction SilentlyContinue
    Write-LoopStatus -Status "stopped"
}
