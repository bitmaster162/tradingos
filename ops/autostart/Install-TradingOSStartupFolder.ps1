param(
    [string]$ShortcutName = "TradingOS_Autostart.cmd",
    [string]$TaskPrefix = "TradingOS",
    [int]$MemoryMaintenanceMinutes = 15,
    [int]$MinimumTrimSleepSeconds = 600,
    [ValidateRange(1024, 65535)][int]$ControlPanelPort = 8765
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1")
$TaskPrefix = Assert-TradingOSTaskPrefix -TaskPrefix $TaskPrefix
$ShortcutName = Assert-TradingOSStartupFileName -FileName $ShortcutName
if ($MemoryMaintenanceMinutes -lt 5 -or $MemoryMaintenanceMinutes -gt 1440) { throw "MemoryMaintenanceMinutes must be between 5 and 1440." }
if ($MinimumTrimSleepSeconds -lt 30 -or $MinimumTrimSleepSeconds -gt 604800) { throw "MinimumTrimSleepSeconds must be between 30 and 604800." }
$EffectiveMemoryMaintenanceMinutes = $MemoryMaintenanceMinutes
$StartupDir = [Environment]::GetFolderPath("Startup")
$StartupCmd = Join-Path $StartupDir $ShortcutName
$RuntimeScript = Join-Path $Root "ops\autostart\Optimize-TradingOSRuntime.ps1"
$ReceiptPath = Join-Path $Root "logs\runtime_autostart_receipt.json"
$ExistingStartupContent = $null
$ExistingReceipt = $null

if (-not (Test-Path -LiteralPath $RuntimeScript)) {
    throw "Missing $RuntimeScript"
}

$AutostartMutex = New-Object System.Threading.Mutex($false, (Get-TradingOSAutostartMutexName -Root $Root))
$AutostartMutexAcquired = $false
try { $AutostartMutexAcquired = $AutostartMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $AutostartMutexAcquired = $true }
if (-not $AutostartMutexAcquired) {
    $AutostartMutex.Dispose()
    throw 'TradingOS autostart mutation is already in progress.'
}

try {
if (Test-Path -LiteralPath $StartupCmd) {
    $ExistingStartupContent = [System.IO.File]::ReadAllText($StartupCmd)
    if (-not (Test-TradingOSManagedStartupContent -Content $ExistingStartupContent -Root $Root -TaskPrefix $TaskPrefix)) {
        throw "Refusing to overwrite an unowned startup command: $StartupCmd"
    }
    if (Test-Path -LiteralPath $ReceiptPath) {
        try {
            $ExistingReceipt = Get-Content -LiteralPath $ReceiptPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            $ReceiptOwned = [int]$ExistingReceipt.schema_version -eq 1 -and [string]$ExistingReceipt.task_prefix -eq $TaskPrefix -and
                [System.IO.Path]::GetFullPath([string]$ExistingReceipt.root).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase) -and
                [System.IO.Path]::GetFullPath([string]$ExistingReceipt.startup_path).Equals([System.IO.Path]::GetFullPath($StartupCmd), [System.StringComparison]::OrdinalIgnoreCase) -and
                [string]$ExistingReceipt.startup_sha256 -eq (Get-TradingOSFileSha256 -Path $StartupCmd)
        } catch { $ReceiptOwned = $false }
        if (-not $ReceiptOwned) { throw "Startup receipt does not own the current command: $ReceiptPath" }
    }
}

$Lines = @(
    "@echo off",
    "rem TradingOS managed startup; canonical local runtime; live trading locked",
    "cd /d `"$Root`"",
    "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$RuntimeScript`" -TaskPrefix `"$TaskPrefix`" -MemoryMaintenanceMinutes $EffectiveMemoryMaintenanceMinutes -MinimumTrimSleepSeconds $MinimumTrimSleepSeconds -ControlPanelPort $ControlPanelPort"
)
try {
    Write-TradingOSTextFileAtomic -Path $StartupCmd -Content $Lines -Encoding ASCII
    if ($ExistingReceipt) {
        $ExistingReceipt.generated_at = (Get-Date).ToUniversalTime().ToString("o")
        $ExistingReceipt.startup_path = $StartupCmd
        $ExistingReceipt.startup_sha256 = Get-TradingOSFileSha256 -Path $StartupCmd
        Write-TradingOSJsonFileAtomic -Path $ReceiptPath -Payload $ExistingReceipt -Depth 6
    }
} catch {
    $StartupWriteError = $_
    if ($null -ne $ExistingStartupContent) {
        try { Write-TradingOSTextFileAtomic -Path $StartupCmd -Content $ExistingStartupContent -Encoding ASCII } catch {}
    } else {
        Remove-Item -LiteralPath $StartupCmd -Force -ErrorAction SilentlyContinue
    }
    throw $StartupWriteError
}

[ordered]@{
    ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    status = "installed"
    startup_cmd = $StartupCmd
    runtime_script = $RuntimeScript
    startup_mode = "verified_runtime_start_and_memory_maintenance"
    task_prefix = $TaskPrefix
    memory_maintenance_minutes = $EffectiveMemoryMaintenanceMinutes
    minimum_trim_sleep_seconds = $MinimumTrimSleepSeconds
    control_panel_port = $ControlPanelPort
    root = $Root
    receipt_path = $ReceiptPath
    live_trading_locked = $true
} | ConvertTo-Json -Depth 5
} finally {
    if ($AutostartMutexAcquired) {
        try { $AutostartMutex.ReleaseMutex() } catch {}
    }
    $AutostartMutex.Dispose()
}
