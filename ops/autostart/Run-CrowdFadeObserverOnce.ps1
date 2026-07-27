param(
    [string]$PythonPath = "",
    [int]$LockStaleMinutes = 45,
    [int]$DataPages = 1,
    [int]$CrowdPages = 1
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LogDir = Join-Path $Root "logs\forward_paper_feed"
$LockPath = Join-Path $LogDir "crowd_fade_refresh.lock.json"
$StatusPath = Join-Path $LogDir "crowd_fade_refresh_last_run.json"
$StdoutPath = Join-Path $LogDir "crowd_fade_refresh_stdout.log"
$StderrPath = Join-Path $LogDir "crowd_fade_refresh_stderr.log"
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
    param([string]$Status, [int]$ExitCode, [string]$Message, [object]$Extra = $null)
    [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        message = $Message
        root = $Root
        stdout = $StdoutPath
        stderr = $StderrPath
        live_trading_locked = $true
        creates_paper_entry_intents = $false
        sends_orders = $false
        extra = $Extra
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

if (Test-Path -LiteralPath $LockPath) {
    $AgeMinutes = ((Get-Date) - (Get-Item -LiteralPath $LockPath).LastWriteTime).TotalMinutes
    if ($AgeMinutes -lt $LockStaleMinutes) {
        Write-Status -Status "skipped_lock_active" -ExitCode 0 -Message "Previous crowd-fade refresh is still active." -Extra @{ lock_age_minutes = [math]::Round($AgeMinutes, 2) }
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
        "tools\crowd_fade_refresh_pack.py",
        "--data-pages", [string]$DataPages,
        "--crowd-pages", [string]$CrowdPages,
        "--out-prefix", "docs\CROWD_FADE_REFRESH_PACK_2026-06-19"
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
        Write-Status -Status "completed_observer_only" -ExitCode 0 -Message "Crowd-fade data, observer, scoreboard and notification check completed." -Extra @{ python = $Python.Exe; data_pages = $DataPages; crowd_pages = $CrowdPages }
    } else {
        Write-Status -Status "failed" -ExitCode $ExitCode -Message "Crowd-fade refresh pack returned non-zero." -Extra @{ python = $Python.Exe }
    }
    exit $ExitCode
} catch {
    Write-Status -Status "exception" -ExitCode 1 -Message $_.Exception.Message
    exit 1
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}
