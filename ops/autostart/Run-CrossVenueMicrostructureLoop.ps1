param(
    [int]$SleepSeconds = 15,
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
$LockPath = Join-Path $LogDir "microstructure_loop.lock.json"
$StatusPath = Join-Path $LogDir "microstructure_loop_status.json"
$OnceScript = Join-Path $Root "ops\autostart\Run-CrossVenueMicrostructureOnce.ps1"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-JsonFileSafe {
    param([string]$Path, [object]$Payload)
    $Json = $Payload | ConvertTo-Json -Depth 5
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
        $FallbackPath = "$Path.write_failed.$PID.json"
        [ordered]@{
            ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            target = $Path
            error = $LastErrorMessage
            can_trade = $false
        } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $FallbackPath -Encoding UTF8
    } catch {}
}

function Write-LoopStatus {
    param([string]$Status, [object]$Extra = $null)
    $Payload = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        pid = $PID
        root = $Root
        sleep_seconds = $SleepSeconds
        public_data_only = $true
        data_collection_only = $true
        signals_allowed = $false
        sends_orders = $false
        can_trade = $false
        extra = $Extra
    }
    Write-JsonFileSafe -Path $StatusPath -Payload $Payload
}

if ($SleepSeconds -lt 10) { throw "SleepSeconds must be at least 10." }
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

[ordered]@{ pid = $PID; started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"); root = $Root } |
    ConvertTo-Json | Set-Content -LiteralPath $LockPath -Encoding UTF8

function Invoke-Refresh {
    Write-LoopStatus -Status "running_once"
    & $OnceScript -PythonPath $PythonPath -TradeLimit 1000 -RetentionHours 168 | Out-Null
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
