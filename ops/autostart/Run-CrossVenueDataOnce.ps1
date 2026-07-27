param(
    [string]$PythonPath = "",
    [int]$Hours = 24,
    [int]$RetentionHours = 744,
    [int]$LockStaleMinutes = 30
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LogDir = Join-Path $Root "logs\cross_venue_data"
$LockPath = Join-Path $LogDir "cross_venue_refresh.lock.json"
$StatusPath = Join-Path $LogDir "cross_venue_refresh_last_run.json"
$StdoutPath = Join-Path $LogDir "cross_venue_refresh_stdout.log"
$StderrPath = Join-Path $LogDir "cross_venue_refresh_stderr.log"
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

function Write-Status {
    param([string]$Status, [int]$ExitCode, [string]$Message, [object]$Extra = $null)
    [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        message = $Message
        root = $Root
        stdout = $StdoutPath
        stderr = $StderrPath
        data_collection_only = $true
        opens_hypothesis_cycle = $false
        sends_orders = $false
        can_trade = $false
        extra = $Extra
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

if ($Hours -lt 1 -or $Hours -gt 744) { throw "Hours must be within [1, 744]." }
if ($RetentionHours -lt $Hours -or $RetentionHours -gt 744) { throw "RetentionHours must be within [Hours, 744]." }

if (Test-Path -LiteralPath $LockPath) {
    $AgeMinutes = ((Get-Date) - (Get-Item -LiteralPath $LockPath).LastWriteTime).TotalMinutes
    if ($AgeMinutes -lt $LockStaleMinutes) {
        Write-Status -Status "skipped_lock_active" -ExitCode 0 -Message "Previous cross-venue refresh is still active." -Extra @{ lock_age_minutes = [math]::Round($AgeMinutes, 2) }
        exit 0
    }
    Remove-Item -LiteralPath $LockPath -Force
}

[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $LockPath -Encoding UTF8

try {
    $Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
    $Args = @()
    $Args += $Python.Prefix
    $Args += @(
        "tools\cross_venue_spot_data_collector.py",
        "--interval", "1m",
        "--hours", [string]$Hours,
        "--retention-hours", [string]$RetentionHours,
        "--coinbase-product", "BTC-USD",
        "--out-dir", "data\cross_venue_spot",
        "--report-prefix", "docs\CROSS_VENUE_SPOT_DATA_QUALITY_2026-06-24"
    )
    Push-Location $Root
    try {
        $env:BOT_ENV = "demo"
        & $Python.Exe @Args > $StdoutPath 2> $StderrPath
        $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
    } finally {
        Pop-Location
    }
    if ($ExitCode -eq 0) {
        Write-Status -Status "completed_data_only" -ExitCode 0 -Message "Cross-venue candles refreshed and merged." -Extra @{ python = $Python.Exe; hours = $Hours; retention_hours = $RetentionHours }
    } else {
        Write-Status -Status "failed" -ExitCode $ExitCode -Message "Cross-venue collector returned non-zero." -Extra @{ python = $Python.Exe }
    }
    exit $ExitCode
} catch {
    Write-Status -Status "exception" -ExitCode 1 -Message $_.Exception.Message
    exit 1
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}
