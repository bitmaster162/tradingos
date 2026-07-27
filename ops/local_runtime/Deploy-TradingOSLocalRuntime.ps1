param(
    [string]$TargetRoot = "$env:USERPROFILE\TradingOS\Active",
    [switch]$Activate,
    [switch]$InitialSeed
)

$ErrorActionPreference = "Stop"
$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$TargetRoot = [System.IO.Path]::GetFullPath($TargetRoot)
$StatusPath = Join-Path $SourceRoot "docs\LOCAL_RUNTIME_DEPLOY_2026-06-22.json"

if ($TargetRoot -like "$SourceRoot*") { throw "TargetRoot must not be inside SourceRoot." }
if ($TargetRoot -match "\\My Drive(\\|$)") { throw "TargetRoot must not be inside Google Drive." }
New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null

$TargetWasEmpty = -not (Get-ChildItem -LiteralPath $TargetRoot -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
$SeedRuntimeData = [bool]$InitialSeed -or $TargetWasEmpty
if ($Activate) {
    $TargetStop = Join-Path $TargetRoot "ops\autostart\Stop-TradingOSRuntime.ps1"
    if (Test-Path -LiteralPath $TargetStop) { & $TargetStop | Out-Null }
    & (Join-Path $SourceRoot "ops\autostart\Stop-TradingOSRuntime.ps1") | Out-Null
}
$Args = @(
    $SourceRoot,
    $TargetRoot,
    "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:3", "/W:2", "/XJ", "/FFT", "/NP", "/NFL", "/NDL",
    "/XD", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules",
    "/XF", "*.pyc", "*.pyo", "*.zip"
)
if (-not $SeedRuntimeData) {
    $Args += "/XO"
    $Args += @(
        "/XD",
        (Join-Path $SourceRoot "logs"),
        (Join-Path $SourceRoot "_dl"),
        (Join-Path $SourceRoot "data\cache"),
        (Join-Path $SourceRoot "data\runtime")
    )
    # Runtime reports are derived from the local journals. Never replace them
    # with newer-but-empty reports generated in the curated source tree.
    $Args += @(
        "/XF",
        "ACTIVE_STRATEGY_RUNTIME_MAP_*.json", "ACTIVE_STRATEGY_RUNTIME_MAP_*.md",
        "FORWARD_RUNTIME_HEALTH_*.json", "FORWARD_RUNTIME_HEALTH_*.md",
        "STRATEGY_MIX_FORWARD_SCHEDULER_*.json", "STRATEGY_MIX_FORWARD_SCHEDULER_*.md",
        "STRATEGY_MIX_FORWARD_SCOREBOARD_*.json", "STRATEGY_MIX_FORWARD_SCOREBOARD_*.md",
        "OI_GUARD_PROMOTION_GATE_*.json", "OI_GUARD_PROMOTION_GATE_*.md",
        "RANGE_REFINED_OBSERVER_SCOREBOARD_*.json", "RANGE_REFINED_OBSERVER_SCOREBOARD_*.md",
        "RANGE_REFINED_PROMOTION_GATE_*.json", "RANGE_REFINED_PROMOTION_GATE_*.md",
        "EDGE_FORWARD_RANGE_SCOREBOARD_*.json", "EDGE_FORWARD_RANGE_SCOREBOARD_*.md",
        "EDGE_FORWARD_PROMOTION_GATE_*.json", "EDGE_FORWARD_PROMOTION_GATE_*.md",
        "CROWD_FADE_POSITIONING_*.json", "CROWD_FADE_POSITIONING_*.md",
        "FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_*.json", "FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_*.md",
        "FORWARD_EVIDENCE_LIFECYCLE_*.json", "FORWARD_EVIDENCE_LIFECYCLE_*.md",
        "RUNTIME_BACKUP_RESTORE_DRILL_*.json", "RUNTIME_BACKUP_RESTORE_DRILL_*.md"
    )
}

& robocopy.exe @Args | Out-Null
$CopyExitCode = [int]$LASTEXITCODE
if ($CopyExitCode -gt 7) { throw "Robocopy failed with exit code $CopyExitCode" }

$Activation = "not_requested"
if ($Activate) {
    & (Join-Path $TargetRoot "ops\autostart\Install-TradingOSStartupFolder.ps1") | Out-Null
    & (Join-Path $TargetRoot "ops\autostart\Start-TradingOSRuntime.ps1") | Out-Null
    $Activation = "started_local_runtime"
}

$Report = [ordered]@{
    ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    status = "completed"
    source_root = $SourceRoot
    target_root = $TargetRoot
    target_outside_google_drive = $true
    seed_runtime_data = $SeedRuntimeData
    robocopy_exit_code = $CopyExitCode
    activation = $Activation
    source_deleted = $false
    live_trading_locked = $true
    next_action = if ($Activate) { "Use the local runtime; keep the Drive tree as source/backup only." } else { "Run again with -Activate after reviewing the local copy." }
}
$Report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
$Report | ConvertTo-Json -Depth 6
