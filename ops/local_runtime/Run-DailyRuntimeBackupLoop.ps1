param(
    [int]$SleepSeconds = 86400,
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
$LogDir = Join-Path $Root "logs\runtime_backup"
$LockPath = Join-Path $LogDir "daily_drive_backup_loop.lock.json"
$StatusPath = Join-Path $LogDir "daily_drive_backup_loop_status.json"
$BackupScript = Join-Path $Root "ops\local_runtime\Backup-TradingOSRuntimeData.ps1"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
if ($Root -match "\\My Drive(\\|$)") { exit 0 }

if (Test-Path -LiteralPath $LockPath) {
    try {
        $Lock = Get-Content -Raw -LiteralPath $LockPath | ConvertFrom-Json
        if (Get-Process -Id ([int]$Lock.pid) -ErrorAction SilentlyContinue) { exit 0 }
    } catch {}
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}
[ordered]@{ pid = $PID; started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"); root = $Root } |
    ConvertTo-Json | Set-Content -LiteralPath $LockPath -Encoding UTF8

function Write-Status([string]$Status, [int]$ExitCode = 0) {
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        exit_code = $ExitCode
        pid = $PID
        sleep_seconds = $SleepSeconds
        live_trading_locked = $true
    } | ConvertTo-Json
    for ($Attempt = 1; $Attempt -le 10; $Attempt++) {
        try { $Payload | Set-Content -LiteralPath $StatusPath -Encoding UTF8 -ErrorAction Stop; return } catch {
            if ($Attempt -lt 10) { Start-Sleep -Milliseconds (100 * $Attempt) }
        }
    }
}

function Invoke-RuntimeBackup {
    Write-Status "running_backup" 0
    try {
        & $BackupScript | Out-Null
        # Robocopy uses 0..7 for successful outcomes. The backup script throws
        # only for real failures, so the loop-level result is unambiguously 0.
        Write-Status "sleeping" 0
    } catch {
        Write-Status "backup_failed" 1
        throw
    }
}

try {
    if (-not $NoImmediateRun) {
        Invoke-RuntimeBackup
    }
    while ($true) {
        Start-Sleep -Seconds $SleepSeconds
        Invoke-RuntimeBackup
    }
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Write-Status "stopped"
}
