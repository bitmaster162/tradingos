param(
    [string]$TaskPrefix = "TradingOS",
    [ValidateRange(1024, 65535)][int]$ControlPanelPort = 8765,
    [int]$MemoryMaintenanceMinutes = 15,
    [int]$MinimumTrimSleepSeconds = 600
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1")
$TaskPrefix = Assert-TradingOSTaskPrefix -TaskPrefix $TaskPrefix
if ($MemoryMaintenanceMinutes -lt 5 -or $MemoryMaintenanceMinutes -gt 1440) { throw "MemoryMaintenanceMinutes must be between 5 and 1440." }
if ($MinimumTrimSleepSeconds -lt 30 -or $MinimumTrimSleepSeconds -gt 604800) { throw "MinimumTrimSleepSeconds must be between 30 and 604800." }
$StartupInstaller = Join-Path $Root "ops\autostart\Install-TradingOSStartupFolder.ps1"
$OptimizerScript = Join-Path $Root "ops\autostart\Optimize-TradingOSRuntime.ps1"
$StatusScript = Join-Path $Root "ops\autostart\Get-TradingOSAutostartStatus.ps1"

if (-not (Test-Path -LiteralPath $StartupInstaller)) { throw "Missing $StartupInstaller" }
if (-not (Test-Path -LiteralPath $OptimizerScript)) { throw "Missing $OptimizerScript" }
if (-not (Test-Path -LiteralPath $StatusScript)) { throw "Missing $StatusScript" }

$AutostartMutex = New-Object System.Threading.Mutex($false, (Get-TradingOSAutostartMutexName -Root $Root))
$AutostartMutexAcquired = $false
try { $AutostartMutexAcquired = $AutostartMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $AutostartMutexAcquired = $true }
if (-not $AutostartMutexAcquired) {
    $AutostartMutex.Dispose()
    throw 'TradingOS autostart mutation is already in progress.'
}

try {
# Compatibility entrypoint: install and verify the replacement first. Legacy
# tasks are removed only after the new startup path and maintenance task succeed.
& $OptimizerScript -TaskPrefix $TaskPrefix -ControlPanelPort $ControlPanelPort -MemoryMaintenanceMinutes $MemoryMaintenanceMinutes -MinimumTrimSleepSeconds $MinimumTrimSleepSeconds | Out-Null
$ReceiptPath = Join-Path $Root "logs\runtime_autostart_receipt.json"
$Receipt = Get-Content -LiteralPath $ReceiptPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
$ReceiptTask = Get-ScheduledTask -TaskName ([string]$Receipt.maintenance_task_name) -TaskPath ([string]$Receipt.maintenance_task_path) -ErrorAction Stop
$ReceiptStartupContent = [System.IO.File]::ReadAllText([string]$Receipt.startup_path)
$ReceiptValid = [int]$Receipt.schema_version -eq 1 -and [string]$Receipt.task_prefix -eq $TaskPrefix -and
    [System.IO.Path]::GetFullPath([string]$Receipt.root).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase) -and
    [string]$Receipt.startup_sha256 -eq (Get-TradingOSFileSha256 -Path ([string]$Receipt.startup_path)) -and
    (Test-TradingOSManagedStartupContent -Content $ReceiptStartupContent -Root $Root -TaskPrefix $TaskPrefix) -and
    (Test-TradingOSManagedMaintenanceTask -Task $ReceiptTask -Root $Root -TaskPrefix $TaskPrefix) -and
    [string]$ReceiptTask.Actions[0].Execute -eq [string]$Receipt.maintenance_action_execute -and
    [string]$ReceiptTask.Actions[0].Arguments -eq [string]$Receipt.maintenance_action_arguments -and
    [string]$ReceiptTask.Actions[0].WorkingDirectory -eq [string]$Receipt.maintenance_working_directory -and
    [string]$ReceiptTask.Triggers[0].Repetition.Interval -eq [string]$Receipt.maintenance_interval
if (-not $ReceiptValid) { throw "Autostart ownership receipt failed verification: $ReceiptPath" }

$LegacyTasks = New-Object System.Collections.Generic.List[object]
$LegacyTaskKinds = [ordered]@{
    "${TaskPrefix}_ControlPanel_Logon" = 'ControlPanel'
    "${TaskPrefix}_ForwardPaper_4H" = 'ForwardPaper4H'
}
$RootTaskInventory = @(Get-ScheduledTask -TaskPath "\" -ErrorAction Stop)
foreach ($LegacyTaskName in $LegacyTaskKinds.Keys) {
    $LegacyMatches = @($RootTaskInventory | Where-Object { [string]$_.TaskName -eq $LegacyTaskName })
    if ($LegacyMatches.Count -eq 0) { continue }
    if ($LegacyMatches.Count -ne 1) { throw "Legacy task inventory is ambiguous: $LegacyTaskName" }
    $LegacyTask = $LegacyMatches[0]
    if (-not (Test-TradingOSManagedLegacyTask -Task $LegacyTask -Root $Root -TaskPrefix $TaskPrefix -Kind ([string]$LegacyTaskKinds[$LegacyTaskName]))) {
        throw "Refusing to remove an unowned legacy task: $LegacyTaskName"
    }
    $LegacyXml = Export-ScheduledTask -TaskName $LegacyTaskName -TaskPath "\" -ErrorAction Stop
    $LegacyTasks.Add([pscustomobject]@{ name = $LegacyTaskName; path = "\"; xml = $LegacyXml }) | Out-Null
}
$RemovedLegacyTasks = New-Object System.Collections.Generic.List[object]
try {
    foreach ($LegacyTask in $LegacyTasks) {
        Unregister-ScheduledTask -TaskName $LegacyTask.name -TaskPath $LegacyTask.path -Confirm:$false -ErrorAction Stop
        $RemovedLegacyTasks.Add($LegacyTask) | Out-Null
    }
} catch {
    $RemovalError = $_
    $RollbackErrors = New-Object System.Collections.Generic.List[string]
    foreach ($LegacyTask in $RemovedLegacyTasks) {
        try {
            $RollbackInventory = @(Get-ScheduledTask -TaskPath $LegacyTask.path -ErrorAction Stop)
            if (@($RollbackInventory | Where-Object { [string]$_.TaskName -eq [string]$LegacyTask.name }).Count -ne 0) {
                throw "task name was recreated before rollback"
            }
            Register-ScheduledTask -TaskName $LegacyTask.name -TaskPath $LegacyTask.path -Xml $LegacyTask.xml -ErrorAction Stop | Out-Null
        } catch {
            $RollbackErrors.Add("$($LegacyTask.name):$($_.Exception.Message)") | Out-Null
        }
    }
    if ($RollbackErrors.Count -gt 0) { throw "Legacy task cleanup failed and rollback was incomplete. cleanup_error=$($RemovalError.Exception.Message); rollback_errors=$($RollbackErrors -join ' | ')" }
    throw $RemovalError
}
& $StatusScript -TaskPrefix $TaskPrefix -ControlPanelPort $ControlPanelPort -MemoryMaintenanceMinutes $MemoryMaintenanceMinutes
} finally {
    if ($AutostartMutexAcquired) {
        try { $AutostartMutex.ReleaseMutex() } catch {}
    }
    $AutostartMutex.Dispose()
}
