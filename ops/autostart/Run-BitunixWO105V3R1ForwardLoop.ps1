param(
    [int]$RestCadenceSeconds = 300,
    [string]$PythonPath = "",
    [string]$LaunchAttemptId = ""
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

$ShutdownRequested = $true
try {
    $Result = Test-TradingOSRuntimeShutdownRequested -Root $Root -AllowedAttemptId $LaunchAttemptId
    if ($Result -is [bool] -and -not $Result) { $ShutdownRequested = $false }
} catch { $ShutdownRequested = $true }
if ($ShutdownRequested) { exit 1 }

$ExpectedLockPath = Join-Path $Root "logs\bitunix_wo105_v3r1\bitunix_wo105_v3r1_forward_loop.lock.json"
$CoreScript = Join-Path $Root "ops\autostart\Run-BitunixWO105V3ForwardLoop.ps1"
if (-not (Test-Path -LiteralPath $CoreScript -PathType Leaf)) { throw "WO105 forward-loop core is missing." }

& $CoreScript `
    -ForwardFloor "2026-07-14T17:00:00Z" `
    -RestCadenceSeconds $RestCadenceSeconds `
    -PythonPath $PythonPath `
    -LaunchAttemptId $LaunchAttemptId `
    -RuntimeTag "bitunix_wo105_v3r1" `
    -LockRelativePath "configs\BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R1_2026-07-14.json" `
    -ShadowTag "bitunix_wo105_shadow_v3r1" `
    -CohortLabel "BITUNIX_WO105_V3R1" `
    -ManagedScriptPath $MyInvocation.MyCommand.Path
exit $LASTEXITCODE
