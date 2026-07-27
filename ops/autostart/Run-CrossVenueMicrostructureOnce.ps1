param(
    [string]$PythonPath = "",
    [int]$TradeLimit = 1000,
    [int]$RetentionHours = 168,
    [int]$LockStaleMinutes = 5
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LogDir = Join-Path $Root "logs\cross_venue_microstructure"
$LockPath = Join-Path $LogDir "microstructure_refresh.lock.json"
$StatusPath = Join-Path $LogDir "microstructure_refresh_last_run.json"
$StdoutPath = Join-Path $LogDir "microstructure_refresh_stdout.log"
$StderrPath = Join-Path $LogDir "microstructure_refresh_stderr.log"
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
        public_data_only = $true
        data_collection_only = $true
        signals_allowed = $false
        sends_orders = $false
        can_trade = $false
        extra = $Extra
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

if ($TradeLimit -lt 1 -or $TradeLimit -gt 1000) { throw "TradeLimit must be within [1, 1000]." }
if ($RetentionHours -lt 1 -or $RetentionHours -gt 744) { throw "RetentionHours must be within [1, 744]." }
if (Test-Path -LiteralPath $LockPath) {
    $LockOwnerAlive = $false
    $LockOwnerPid = $null
    try {
        $ExistingLock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        $LockOwnerPid = [int]$ExistingLock.pid
        $LockOwnerAlive = $null -ne (Get-Process -Id $LockOwnerPid -ErrorAction SilentlyContinue)
    } catch {
        $AgeMinutes = ((Get-Date) - (Get-Item -LiteralPath $LockPath).LastWriteTime).TotalMinutes
        if ($AgeMinutes -lt $LockStaleMinutes) {
            Write-Status -Status "skipped_lock_active" -ExitCode 0 -Message "Microstructure refresh lock exists but ownership cannot yet be verified."
            exit 0
        }
    }
    if ($LockOwnerAlive) {
        Write-Status -Status "skipped_lock_active" -ExitCode 0 -Message "Previous microstructure refresh is still active." -Extra @{ lock_owner_pid = $LockOwnerPid }
        exit 0
    }
    Remove-Item -LiteralPath $LockPath -Force
}

[ordered]@{ pid = $PID; started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"); root = $Root } |
    ConvertTo-Json | Set-Content -LiteralPath $LockPath -Encoding UTF8

try {
    $Python = Get-PreferredPython -Requested $(if ($PythonPath) { $PythonPath } else { $env:TRADING_OS_PYTHON })
    $Args = @()
    $Args += $Python.Prefix
    $Args += @(
        "tools\cross_venue_microstructure_sqlite_collector.py",
        "--trade-limit", [string]$TradeLimit,
        "--retention-hours", [string]$RetentionHours,
        "--min-research-hours", "168",
        "--out-dir", "data\cross_venue_microstructure",
        "--report-prefix", "docs\CROSS_VENUE_MICROSTRUCTURE_DATA_QUALITY_2026-06-24"
    )
    Push-Location $Root
    try {
        $env:BOT_ENV = "demo"
        & $Python.Exe @Args > $StdoutPath 2> $StderrPath
        $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
    } finally { Pop-Location }
    if ($ExitCode -eq 0) {
        Write-Status -Status "completed_data_only" -ExitCode 0 -Message "Cross-venue trades and top-of-book refreshed." -Extra @{ python = $Python.Exe; trade_limit = $TradeLimit; retention_hours = $RetentionHours }
    } else {
        Write-Status -Status "failed" -ExitCode $ExitCode -Message "Microstructure collector returned non-zero." -Extra @{ python = $Python.Exe }
    }
    exit $ExitCode
} catch {
    Write-Status -Status "exception" -ExitCode 1 -Message $_.Exception.Message
    exit 1
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}
