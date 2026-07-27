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

$CoreScript = Join-Path $Root "ops\autostart\Run-BitunixWO105V3ForwardLoop.ps1"
if (-not (Test-Path -LiteralPath $CoreScript -PathType Leaf)) { throw "WO105 forward-loop core is missing." }

& $CoreScript `
    -ForwardFloor "2026-07-15T04:00:00Z" `
    -RestCadenceSeconds $RestCadenceSeconds `
    -PythonPath $PythonPath `
    -LaunchAttemptId $LaunchAttemptId `
    -RuntimeTag "bitunix_wo105_v3r4" `
    -LockRelativePath "configs\BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json" `
    -AssemblerScriptRelativePath "tools\bitunix_wo105_packet_assembler_v6.py" `
    -ShadowTag "bitunix_wo105_shadow_v3r4" `
    -CohortLabel "BITUNIX_WO105_V3R4" `
    -ReportDate "2026-07-15" `
    -ManagedScriptPath $MyInvocation.MyCommand.Path
exit $LASTEXITCODE
