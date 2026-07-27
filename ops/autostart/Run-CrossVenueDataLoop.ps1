param(
    [int]$SleepSeconds = 86400,
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
$LogDir = Join-Path $Root "logs\cross_venue_data"
$LockPath = Join-Path $LogDir "cross_venue_data_loop.lock.json"
$StatusPath = Join-Path $LogDir "cross_venue_data_loop_status.json"
$OnceScript = Join-Path $Root "ops\autostart\Run-CrossVenueDataOnce.ps1"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-LoopStatus {
    param([string]$Status, [object]$Extra = $null)
    [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        data_collection_only = $true
        opens_hypothesis_cycle = $false
        sends_orders = $false
        can_trade = $false
        extra = $Extra
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

if ($SleepSeconds -lt 3600) { throw "SleepSeconds must be at least 3600." }
if (Test-Path -LiteralPath $LockPath) {
    try {
        $Existing = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        if (Get-Process -Id ([int]$Existing.pid) -ErrorAction SilentlyContinue) {
            Write-LoopStatus -Status "skipped_existing_loop" -Extra @{ existing_pid = [int]$Existing.pid }
            exit 0
        }
    } catch {}
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

[ordered]@{
    pid = $PID
    started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
} | ConvertTo-Json | Set-Content -LiteralPath $LockPath -Encoding UTF8

function Invoke-Refresh {
    Write-LoopStatus -Status "running_once"
    & $OnceScript -PythonPath $PythonPath -Hours 24 -RetentionHours 744 | Out-Null
    $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
    Write-LoopStatus -Status "sleeping" -Extra @{ last_refresh_exit_code = $ExitCode; next_run_after_seconds = $SleepSeconds }
}

try {
    if (-not $NoImmediateRun) { Invoke-Refresh }
    while ($true) {
        Start-Sleep -Seconds $SleepSeconds
        Invoke-Refresh
    }
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Write-LoopStatus -Status "stopped"
}
