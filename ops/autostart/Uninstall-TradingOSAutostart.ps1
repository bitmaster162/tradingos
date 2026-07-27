param(
    [string]$TaskPrefix = "TradingOS",
    [string]$StartupFileName = "TradingOS_Autostart.cmd"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1")
$TaskPrefix = Assert-TradingOSTaskPrefix -TaskPrefix $TaskPrefix
$StartupFileName = Assert-TradingOSStartupFileName -FileName $StartupFileName
$StartupPath = Join-Path ([Environment]::GetFolderPath("Startup")) $StartupFileName
$ReceiptPath = Join-Path $Root "logs\runtime_autostart_receipt.json"
$Receipt = $null

$AutostartMutex = New-Object System.Threading.Mutex($false, (Get-TradingOSAutostartMutexName -Root $Root))
$AutostartMutexAcquired = $false
try { $AutostartMutexAcquired = $AutostartMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $AutostartMutexAcquired = $true }
if (-not $AutostartMutexAcquired) {
    $AutostartMutex.Dispose()
    throw 'TradingOS autostart mutation is already in progress.'
}

try {
if (Test-Path -LiteralPath $ReceiptPath) {
    try { $Receipt = Get-Content -LiteralPath $ReceiptPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch { throw "Invalid autostart ownership receipt: $ReceiptPath" }
    if ([int]$Receipt.schema_version -ne 1 -or [string]$Receipt.task_prefix -ne $TaskPrefix -or
        -not [System.IO.Path]::GetFullPath([string]$Receipt.root).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [System.IO.Path]::GetFullPath([string]$Receipt.startup_path).Equals([System.IO.Path]::GetFullPath($StartupPath), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Autostart ownership receipt does not match this installation: $ReceiptPath"
    }
}

if (Test-Path -LiteralPath $StartupPath) {
    $ExistingStartupContent = [System.IO.File]::ReadAllText($StartupPath)
    if (-not (Test-TradingOSManagedStartupContent -Content $ExistingStartupContent -Root $Root -TaskPrefix $TaskPrefix)) {
        throw "Refusing to remove an unowned startup command: $StartupPath"
    }
    if ($Receipt -and [string]$Receipt.startup_sha256 -ne (Get-TradingOSFileSha256 -Path $StartupPath)) {
        throw "Startup command hash no longer matches its ownership receipt: $StartupPath"
    }
} elseif ($Receipt) {
    throw "Receipt-owned startup command is unexpectedly missing: $StartupPath"
}

$TaskNamePattern = '^' + [regex]::Escape($TaskPrefix) + '_RuntimeMemoryMaintenance_[0-9]+M$'
$TasksToRemove = New-Object System.Collections.Generic.List[object]
$RootTaskInventory = @(Get-ScheduledTask -TaskPath "\" -ErrorAction Stop)
foreach ($Task in @($RootTaskInventory | Where-Object { $_.TaskName -match $TaskNamePattern })) {
    if (-not (Test-TradingOSManagedMaintenanceTask -Task $Task -Root $Root -TaskPrefix $TaskPrefix)) {
        throw "Refusing to remove an unowned maintenance task: $($Task.TaskName)"
    }
    if ($Receipt -and [string]$Task.TaskName -ne [string]$Receipt.maintenance_task_name) {
        throw "Maintenance task is not listed in the ownership receipt: $($Task.TaskName)"
    }
    if ($Receipt -and (
        [string]$Task.Actions[0].Execute -ne [string]$Receipt.maintenance_action_execute -or
        [string]$Task.Actions[0].Arguments -ne [string]$Receipt.maintenance_action_arguments -or
        [string]$Task.Actions[0].WorkingDirectory -ne [string]$Receipt.maintenance_working_directory -or
        [string]$Task.Triggers[0].Repetition.Interval -ne [string]$Receipt.maintenance_interval
    )) { throw "Maintenance task no longer matches its ownership receipt: $($Task.TaskName)" }
    $TaskXml = Export-ScheduledTask -TaskName $Task.TaskName -TaskPath $Task.TaskPath -ErrorAction Stop
    $TasksToRemove.Add([pscustomobject]@{ name = $Task.TaskName; path = $Task.TaskPath; xml = $TaskXml }) | Out-Null
}

$LegacyTaskKinds = [ordered]@{
    "${TaskPrefix}_ControlPanel_Logon" = 'ControlPanel'
    "${TaskPrefix}_ForwardPaper_4H" = 'ForwardPaper4H'
}
foreach ($LegacyTaskName in $LegacyTaskKinds.Keys) {
    $LegacyMatches = @($RootTaskInventory | Where-Object { [string]$_.TaskName -eq $LegacyTaskName })
    if ($LegacyMatches.Count -eq 0) { continue }
    if ($LegacyMatches.Count -ne 1) { throw "Legacy task inventory is ambiguous: $LegacyTaskName" }
    $LegacyTask = $LegacyMatches[0]
    if (-not (Test-TradingOSManagedLegacyTask -Task $LegacyTask -Root $Root -TaskPrefix $TaskPrefix -Kind ([string]$LegacyTaskKinds[$LegacyTaskName]))) {
        throw "Refusing to remove an unowned legacy task: $LegacyTaskName"
    }
    $LegacyXml = Export-ScheduledTask -TaskName $LegacyTaskName -TaskPath "\" -ErrorAction Stop
    $TasksToRemove.Add([pscustomobject]@{ name = $LegacyTaskName; path = "\"; xml = $LegacyXml }) | Out-Null
}

$StartupQuarantine = $null
$ReceiptQuarantine = $null
$RemovedTasks = New-Object System.Collections.Generic.List[object]
try {
    if (Test-Path -LiteralPath $StartupPath) {
        $StartupQuarantine = "$StartupPath.uninstall-quarantine.$([guid]::NewGuid().ToString('N'))"
        Move-Item -LiteralPath $StartupPath -Destination $StartupQuarantine -ErrorAction Stop
    }
    if (Test-Path -LiteralPath $ReceiptPath) {
        $ReceiptQuarantine = "$ReceiptPath.uninstall-quarantine.$([guid]::NewGuid().ToString('N'))"
        Move-Item -LiteralPath $ReceiptPath -Destination $ReceiptQuarantine -ErrorAction Stop
    }
    foreach ($Task in $TasksToRemove) {
        Unregister-ScheduledTask -TaskName $Task.name -TaskPath $Task.path -Confirm:$false -ErrorAction Stop
        $RemovedTasks.Add($Task) | Out-Null
    }
} catch {
    $UninstallError = $_
    $RollbackErrors = New-Object System.Collections.Generic.List[string]
    foreach ($Task in $RemovedTasks) {
        try {
            $RollbackInventory = @(Get-ScheduledTask -TaskPath $Task.path -ErrorAction Stop)
            if (@($RollbackInventory | Where-Object { [string]$_.TaskName -eq [string]$Task.name }).Count -ne 0) {
                throw "task name was recreated before rollback"
            }
            Register-ScheduledTask -TaskName $Task.name -TaskPath $Task.path -Xml $Task.xml -ErrorAction Stop | Out-Null
        } catch {
            $RollbackErrors.Add("$($Task.name):$($_.Exception.Message)") | Out-Null
        }
    }
    if ($StartupQuarantine -and (Test-Path -LiteralPath $StartupQuarantine) -and -not (Test-Path -LiteralPath $StartupPath)) {
        try { Move-Item -LiteralPath $StartupQuarantine -Destination $StartupPath -ErrorAction Stop } catch { $RollbackErrors.Add("startup:$($_.Exception.Message)") | Out-Null }
    }
    if ($ReceiptQuarantine -and (Test-Path -LiteralPath $ReceiptQuarantine) -and -not (Test-Path -LiteralPath $ReceiptPath)) {
        try { Move-Item -LiteralPath $ReceiptQuarantine -Destination $ReceiptPath -ErrorAction Stop } catch { $RollbackErrors.Add("receipt:$($_.Exception.Message)") | Out-Null }
    }
    if ($RollbackErrors.Count -gt 0) { throw "Autostart uninstall failed and rollback was incomplete. uninstall_error=$($UninstallError.Exception.Message); rollback_errors=$($RollbackErrors -join ' | ')" }
    throw $UninstallError
}
if ($StartupQuarantine) { Remove-Item -LiteralPath $StartupQuarantine -Force -ErrorAction SilentlyContinue }
if ($ReceiptQuarantine) { Remove-Item -LiteralPath $ReceiptQuarantine -Force -ErrorAction SilentlyContinue }

[ordered]@{
    ts = (Get-Date).ToUniversalTime().ToString("o")
    status = "uninstalled"
    root = $Root
    task_prefix = $TaskPrefix
    removed_tasks = @($RemovedTasks | ForEach-Object { $_.name })
    startup_path = $StartupPath
    receipt_path = $ReceiptPath
    live_trading_locked = $true
    can_trade = $false
} | ConvertTo-Json -Depth 5
} finally {
    if ($AutostartMutexAcquired) {
        try { $AutostartMutex.ReleaseMutex() } catch {}
    }
    $AutostartMutex.Dispose()
}
