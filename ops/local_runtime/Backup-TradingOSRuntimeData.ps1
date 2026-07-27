param(
    [string]$BackupRoot = "$env:USERPROFILE\My Drive\04_PRODUCT_SHELLS\Trade\_runtime_backups\TradingOS_ACTIVE"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BackupRoot = [System.IO.Path]::GetFullPath($BackupRoot)
if ($Root -match "\\My Drive(\\|$)") { throw "Daily runtime backup must run from the non-synced local runtime." }
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

$Copies = New-Object System.Collections.Generic.List[object]
foreach ($Relative in @("logs", "_dl", "data\cache", "data\runtime", "data\research_snapshots", "docs")) {
    $Source = Join-Path $Root $Relative
    if (-not (Test-Path -LiteralPath $Source)) { continue }
    $Destination = Join-Path $BackupRoot $Relative
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy.exe $Source $Destination /E /XO /COPY:DAT /DCOPY:DAT /R:3 /W:2 /XJ /FFT /NP /NFL /NDL /XF *.pyc *.pyo | Out-Null
    $Code = [int]$LASTEXITCODE
    $Copies.Add([ordered]@{ path = $Relative; exit_code = $Code }) | Out-Null
    if ($Code -gt 7) { throw "Runtime backup failed for $Relative with exit code $Code" }
}

$StatusDir = Join-Path $Root "logs\runtime_backup"
New-Item -ItemType Directory -Force -Path $StatusDir | Out-Null
[ordered]@{
    ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    status = "completed"
    source_root = $Root
    backup_root = $BackupRoot
    copies = $Copies
    source_code_and_secrets_excluded = $true
    live_trading_locked = $true
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $StatusDir "daily_drive_backup_last_run.json") -Encoding UTF8
