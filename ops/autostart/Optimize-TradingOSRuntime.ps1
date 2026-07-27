param(
    [ValidateRange(1024, 65535)][int]$ControlPanelPort = 8765,
    [int]$ForwardSleepSeconds = 14400,
    [int]$CrowdFadeSleepSeconds = 3600,
    [int]$BybitAllLiquidationWatchdogSleepSeconds = 300,
    [int]$MicrostructureUnblockStatusSleepSeconds = 900,
    [int]$ResearchRuntimeSupervisorSleepSeconds = 300,
    [int]$DeribitOptionsSurfaceCollectorSleepSeconds = 300,
    [int]$DeribitOptionsReadinessSleepSeconds = 300,
    [int]$DeribitOptionsSkewForwardSleepSeconds = 300,
    [int]$DeribitOptionsV3CollectorSleepSeconds = 300,
    [int]$DeribitOptionsV3ReadinessSleepSeconds = 300,
    [switch]$RunOneShot,
    [switch]$MemoryOnly,
    [switch]$MemoryWhatIf,
    [int]$MinimumTrimSleepSeconds = 600,
    [int]$MemoryMaintenanceMinutes = 15,
    [string]$TaskPrefix = "TradingOS",
    [switch]$SkipMemoryMaintenanceInstall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $Root "ops\autostart\TradingOSRuntimeLifecycle.ps1")
$TaskPrefix = Assert-TradingOSTaskPrefix -TaskPrefix $TaskPrefix
if ($MemoryMaintenanceMinutes -lt 5 -or $MemoryMaintenanceMinutes -gt 1440) { throw "MemoryMaintenanceMinutes must be between 5 and 1440." }
if ($MinimumTrimSleepSeconds -lt 30 -or $MinimumTrimSleepSeconds -gt 604800) { throw "MinimumTrimSleepSeconds must be between 30 and 604800." }
$StatusPath = Join-Path $Root $(if ($MemoryOnly -and $MemoryWhatIf) { "logs\runtime_memory_maintenance_whatif_status.json" } elseif ($MemoryOnly) { "logs\runtime_memory_maintenance_status.json" } else { "logs\runtime_optimizer_status.json" })
$ReceiptPath = Join-Path $Root "logs\runtime_autostart_receipt.json"
$StartupInstaller = Join-Path $Root "ops\autostart\Install-TradingOSStartupFolder.ps1"
$RuntimeScript = Join-Path $Root "ops\autostart\Start-TradingOSRuntime.ps1"
$OnceScript = Join-Path $Root "ops\autostart\Run-ForwardPaperOnce.ps1"
$LoopLockPath = Join-Path $Root "logs\forward_paper_feed\forward_scheduler_loop.lock.json"
$WatchdogLockPath = Join-Path $Root "logs\forward_paper_feed\forward_runtime_watchdog_loop.lock.json"
$CrowdFadeLockPath = Join-Path $Root "logs\forward_paper_feed\crowd_fade_observer_loop.lock.json"
$LiquidationForceOrderWatchdogLockPath = Join-Path $Root "logs\liquidation_force_order\liquidation_force_order_watchdog_loop.lock.json"
$BybitAllLiquidationWatchdogLockPath = Join-Path $Root "logs\liquidation_bybit\bybit_all_liquidation_watchdog_loop.lock.json"
$MicrostructureUnblockStatusLockPath = Join-Path $Root "logs\cross_venue_microstructure\microstructure_unblock_status_loop.lock.json"
$ResearchRuntimeSupervisorStatusPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260712_research_runtime_supervisor\runtime\loop_status.json"
$DeribitOptionsSurfaceCollectorStatusPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260711_deribit_options_surface_collector\runtime_v2\loop_status.json"
$DeribitOptionsReadinessStatusPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260711_deribit_options_readiness_guard\runtime\loop_status.json"
$DeribitOptionsSkewForwardStatusPath = Join-Path $Root "HANDOFF\INCOMING\codex\20260712_deribit_options_skew_forward\runtime\loop_status.json"
$DeribitOptionsV3Launcher = Join-Path $Root "ops\autostart\Start-DeribitOptionsV3DataLayer.ps1"
$DeribitOptionsV3CollectorStatusPath = Join-Path $Root "data\forward\deribit_options_surface_v3\loop_status.json"
$DeribitOptionsV3ReadinessStatusPath = Join-Path $Root "data\forward\deribit_options_readiness_v2\loop_status.json"
$DeribitOptionsV3CollectorLauncherStatusPath = Join-Path $Root "logs\deribit_options_v3_collector_launcher.json"
$DeribitOptionsV3ReadinessLauncherStatusPath = Join-Path $Root "logs\deribit_options_v3_readiness_launcher.json"
$IsolatedResearchRegistryLauncher = Join-Path $Root "ops\autostart\Start-IsolatedResearchRegistryComponents.ps1"
$IsolatedResearchRegistryStatusPath = Join-Path $Root "logs\isolated_research_registry_components_launcher_status.json"
New-Item -ItemType Directory -Force -Path (Split-Path $StatusPath -Parent) | Out-Null

$Actions = New-Object System.Collections.Generic.List[object]

function Add-Action {
    param([string]$Name, [string]$Status, [object]$Extra = $null)
    $Actions.Add([ordered]@{ name = $Name; status = $Status; extra = $Extra }) | Out-Null
}

function Test-Port {
    param([int]$Port)
    try {
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    } catch {
        return [bool](netstat -ano | Select-String ":$Port" | Select-String "LISTENING")
    }
}

function Test-LoopAlive {
    param([string]$Path = $LoopLockPath)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    try {
        $Lock = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        $PidValue = [int]$Lock.pid
        return [bool](Get-Process -Id $PidValue -ErrorAction SilentlyContinue)
    } catch {
        return $false
    }
}

function Get-VerifiedLoopSleepWindow {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$ExpectedProcessId,
        [Parameter(Mandatory = $true)][int]$MinimumSleepSeconds
    )

    $Result = [ordered]@{
        valid = $false
        reason = "missing_or_invalid_status"
        loop_status = "missing_status"
        actual_sleep_seconds = 0
        remaining_sleep_seconds = -1
        status_pid_matches = $false
        status_root_matches = $false
        status_ts_utc = $null
    }
    $LoopStatus = $null
    try { $LoopStatus = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch { return [pscustomobject]$Result }
    try { $Result.actual_sleep_seconds = [int]$LoopStatus.sleep_seconds } catch {}
    $Result.loop_status = [string]$LoopStatus.status
    try { $Result.status_pid_matches = [int]$LoopStatus.pid -eq $ExpectedProcessId } catch {}
    try {
        $Result.status_root_matches = -not $LoopStatus.root -or [System.IO.Path]::GetFullPath([string]$LoopStatus.root).Equals(
            [System.IO.Path]::GetFullPath($Root),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } catch {}
    try {
        $StatusUtc = ([datetime]::Parse([string]$LoopStatus.ts)).ToUniversalTime()
        $Result.status_ts_utc = $StatusUtc.ToString("o")
        $ElapsedSeconds = ((Get-Date).ToUniversalTime() - $StatusUtc).TotalSeconds
        if ($ElapsedSeconds -lt -5) {
            $Result.reason = "status_timestamp_in_future"
            return [pscustomobject]$Result
        }
        $Result.remaining_sleep_seconds = [math]::Floor($Result.actual_sleep_seconds - [math]::Max(0, $ElapsedSeconds))
    } catch {
        $Result.reason = "invalid_status_timestamp"
        return [pscustomobject]$Result
    }

    if ($Result.actual_sleep_seconds -lt $MinimumSleepSeconds) { $Result.reason = "sleep_below_minimum"; return [pscustomobject]$Result }
    if ($Result.loop_status -notmatch '^(sleeping|ran_)') { $Result.reason = "not_sleep_phase"; return [pscustomobject]$Result }
    if (-not $Result.status_pid_matches) { $Result.reason = "status_pid_mismatch"; return [pscustomobject]$Result }
    if (-not $Result.status_root_matches) { $Result.reason = "status_root_mismatch"; return [pscustomobject]$Result }
    if ($Result.remaining_sleep_seconds -lt 30) { $Result.reason = "insufficient_remaining_sleep"; return [pscustomobject]$Result }
    $Result.valid = $true
    $Result.reason = "verified_sleep_window"
    return [pscustomobject]$Result
}

function Invoke-RuntimeWorkingSetTrim {
    param(
        [int]$MinimumSleepSeconds = 600,
        [switch]$WhatIf
    )

    if (-not ("TradingOSRuntimeWorkingSetNative" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class TradingOSRuntimeWorkingSetNative {
    [DllImport("psapi.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool EmptyWorkingSet(IntPtr processHandle);
}
"@
    }

    $Manifest = Get-TradingOSRuntimeManifest -Root $Root
    $States = @(Get-TradingOSRuntimeStates -Root $Root -Manifest $Manifest)
    # Resolve the fail-closed PowerShell executable allowlist once for this
    # maintenance pass. Component rechecks still use independent CIM snapshots.
    $AllowedPowerShellExecutables = Get-TradingOSAllowedPowerShellExecutables
    $Rows = New-Object System.Collections.Generic.List[object]
    $BeforeTotal = 0.0
    $AfterTotal = 0.0

    foreach ($State in $States) {
        if ($State.decision -ne "running_verified" -or -not $State.job_contained -or -not $State.trim_working_set) {
            continue
        }

        $Process = Get-Process -Id ([int]$State.pid) -ErrorAction SilentlyContinue
        if (-not $Process) { continue }
        $BeforeMb = [math]::Round($Process.WorkingSet64 / 1MB, 1)
        $BeforeTotal += $BeforeMb

        $StatusPathForComponent = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$State.status_path)
        $SleepWindow = Get-VerifiedLoopSleepWindow -Path $StatusPathForComponent -ExpectedProcessId ([int]$State.pid) -MinimumSleepSeconds $MinimumSleepSeconds
        $ActualSleepSeconds = $SleepWindow.actual_sleep_seconds
        $LoopPhase = $SleepWindow.loop_status
        $RemainingSleepSeconds = $SleepWindow.remaining_sleep_seconds
        if (-not $SleepWindow.valid) {
            $Rows.Add([ordered]@{
                id = $State.id
                pid = $State.pid
                status = "skipped_not_verified_sleep_phase"
                reason = $SleepWindow.reason
                loop_status = $LoopPhase
                actual_sleep_seconds = $ActualSleepSeconds
                remaining_sleep_seconds = $RemainingSleepSeconds
                status_pid_matches = $SleepWindow.status_pid_matches
                status_root_matches = $SleepWindow.status_root_matches
                before_mb = $BeforeMb
                after_mb = $BeforeMb
                saved_mb = 0.0
            }) | Out-Null
            $AfterTotal += $BeforeMb
            continue
        }

        $Component = @($Manifest.components | Where-Object { [string]$_.id -eq [string]$State.id } | Select-Object -First 1)[0]
        $FreshSnapshot = Get-TradingOSProcessSnapshot
        $FreshState = Get-TradingOSRuntimeComponentState -Root $Root -Component $Component -ProcessSnapshot $FreshSnapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
        if ($FreshState.decision -ne "running_verified" -or [string]$FreshState.process_creation_utc -ne [string]$State.process_creation_utc) {
            $Rows.Add([ordered]@{ id = $State.id; pid = $State.pid; status = "skipped_identity_changed_before_trim"; actual_sleep_seconds = $ActualSleepSeconds; remaining_sleep_seconds = $RemainingSleepSeconds; before_mb = $BeforeMb; after_mb = $BeforeMb; saved_mb = 0.0 }) | Out-Null
            $AfterTotal += $BeforeMb
            continue
        }
        $Descendants = @(Get-TradingOSDescendantProcessIds -RootPid ([int]$State.pid) -ProcessSnapshot $FreshSnapshot |
            Where-Object {
                $Child = $FreshSnapshot[[int]$_]
                $Child -and [string]$Child.Name -notmatch '^(conhost|OpenConsole)\.exe$' -and
                    (Get-Process -Id $_ -ErrorAction SilentlyContinue)
            })
        if ($Descendants.Count -gt 0) {
            $Rows.Add([ordered]@{
                id = $State.id
                pid = $State.pid
                status = "skipped_active_child_process"
                child_pids = $Descendants
                loop_status = $LoopPhase
                actual_sleep_seconds = $ActualSleepSeconds
                remaining_sleep_seconds = $RemainingSleepSeconds
                before_mb = $BeforeMb
                after_mb = $BeforeMb
                saved_mb = 0.0
            }) | Out-Null
            $AfterTotal += $BeforeMb
            continue
        }

        # Re-read the loop's sleep lease after the child scan, then refresh both
        # identity and descendants once more immediately before touching memory.
        $FinalSleepWindow = Get-VerifiedLoopSleepWindow -Path $StatusPathForComponent -ExpectedProcessId ([int]$State.pid) -MinimumSleepSeconds $MinimumSleepSeconds
        if (-not $FinalSleepWindow.valid) {
            $Rows.Add([ordered]@{
                id = $State.id
                pid = $State.pid
                status = "skipped_sleep_phase_changed_before_trim"
                reason = $FinalSleepWindow.reason
                loop_status = $FinalSleepWindow.loop_status
                actual_sleep_seconds = $FinalSleepWindow.actual_sleep_seconds
                remaining_sleep_seconds = $FinalSleepWindow.remaining_sleep_seconds
                before_mb = $BeforeMb
                after_mb = $BeforeMb
                saved_mb = 0.0
            }) | Out-Null
            $AfterTotal += $BeforeMb
            continue
        }
        $TrimSnapshot = Get-TradingOSProcessSnapshot
        $TrimState = Get-TradingOSRuntimeComponentState -Root $Root -Component $Component -ProcessSnapshot $TrimSnapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
        $TrimDescendants = @()
        if ($TrimState.decision -eq "running_verified" -and [string]$TrimState.process_creation_utc -eq [string]$State.process_creation_utc) {
            $TrimDescendants = @(Get-TradingOSDescendantProcessIds -RootPid ([int]$State.pid) -ProcessSnapshot $TrimSnapshot |
                Where-Object {
                    $Child = $TrimSnapshot[[int]$_]
                    $Child -and [string]$Child.Name -notmatch '^(conhost|OpenConsole)\.exe$' -and
                        (Get-Process -Id $_ -ErrorAction SilentlyContinue)
                })
        }
        if ($TrimState.decision -ne "running_verified" -or [string]$TrimState.process_creation_utc -ne [string]$State.process_creation_utc) {
            $Rows.Add([ordered]@{ id = $State.id; pid = $State.pid; status = "skipped_identity_changed_immediately_before_trim"; actual_sleep_seconds = $FinalSleepWindow.actual_sleep_seconds; remaining_sleep_seconds = $FinalSleepWindow.remaining_sleep_seconds; before_mb = $BeforeMb; after_mb = $BeforeMb; saved_mb = 0.0 }) | Out-Null
            $AfterTotal += $BeforeMb
            continue
        }
        if ($TrimDescendants.Count -gt 0) {
            $Rows.Add([ordered]@{ id = $State.id; pid = $State.pid; status = "skipped_child_appeared_before_trim"; child_pids = $TrimDescendants; actual_sleep_seconds = $FinalSleepWindow.actual_sleep_seconds; remaining_sleep_seconds = $FinalSleepWindow.remaining_sleep_seconds; before_mb = $BeforeMb; after_mb = $BeforeMb; saved_mb = 0.0 }) | Out-Null
            $AfterTotal += $BeforeMb
            continue
        }

        if ($WhatIf) {
            $Rows.Add([ordered]@{
                id = $State.id
                pid = $State.pid
                status = "would_trim"
                loop_status = $FinalSleepWindow.loop_status
                actual_sleep_seconds = $FinalSleepWindow.actual_sleep_seconds
                remaining_sleep_seconds = $FinalSleepWindow.remaining_sleep_seconds
                before_mb = $BeforeMb
                after_mb = $BeforeMb
                saved_mb = 0.0
            }) | Out-Null
            $AfterTotal += $BeforeMb
            continue
        }

        $Trimmed = $false
        $NativeError = 0
        try {
            $ProcessForTrim = Get-Process -Id ([int]$State.pid) -ErrorAction Stop
            $Trimmed = [TradingOSRuntimeWorkingSetNative]::EmptyWorkingSet($ProcessForTrim.Handle)
            if (-not $Trimmed) { $NativeError = [Runtime.InteropServices.Marshal]::GetLastWin32Error() }
        } catch {
            $NativeError = $_.Exception.HResult
        }
        Start-Sleep -Milliseconds 100
        $PostSnapshot = Get-TradingOSProcessSnapshot
        $PostState = Get-TradingOSRuntimeComponentState -Root $Root -Component $Component -ProcessSnapshot $PostSnapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
        $PostIdentityValid = $PostState.decision -eq "running_verified" -and [string]$PostState.process_creation_utc -eq [string]$State.process_creation_utc
        $AfterProcess = if ($PostIdentityValid) { Get-Process -Id ([int]$State.pid) -ErrorAction SilentlyContinue } else { $null }
        $AfterMb = if ($AfterProcess) { [math]::Round($AfterProcess.WorkingSet64 / 1MB, 1) } else { $BeforeMb }
        $AfterTotal += $AfterMb
        $RowStatus = if (-not $PostIdentityValid) { "trim_result_unverified_process_changed" } elseif ($Trimmed) { "trimmed" } else { "trim_failed" }
        $Rows.Add([ordered]@{
            id = $State.id
            pid = $State.pid
            status = $RowStatus
            win32_error = $NativeError
            loop_status = $FinalSleepWindow.loop_status
            actual_sleep_seconds = $FinalSleepWindow.actual_sleep_seconds
            remaining_sleep_seconds = $FinalSleepWindow.remaining_sleep_seconds
            before_mb = $BeforeMb
            after_mb = $AfterMb
            saved_mb = if ($PostIdentityValid -and $Trimmed) { [math]::Round([math]::Max(0.0, $BeforeMb - $AfterMb), 1) } else { 0.0 }
        }) | Out-Null
    }

    $SavedTotal = 0.0
    $TrimmedCount = 0
    foreach ($Row in $Rows) {
        try { $SavedTotal += [double]$Row.saved_mb } catch {}
        if ([string]$Row.status -eq "trimmed") { $TrimmedCount++ }
    }
    return [ordered]@{
        minimum_sleep_seconds = $MinimumSleepSeconds
        what_if = [bool]$WhatIf
        eligible_components = $Rows.Count
        trimmed_components = $TrimmedCount
        before_mb = [math]::Round($BeforeTotal, 1)
        after_mb = [math]::Round($AfterTotal, 1)
        saved_mb = [math]::Round($SavedTotal, 1)
        components = $Rows
    }
}

function Install-RuntimeMemoryMaintenanceTask {
    param(
        [int]$IntervalMinutes = 15,
        [int]$MinimumSleepSeconds = 600,
        [Parameter(Mandatory = $true)][string]$StartupPath,
        [Parameter(Mandatory = $true)][string]$OwnershipReceiptPath
    )

    $EffectiveIntervalMinutes = $IntervalMinutes
    $TaskName = "${TaskPrefix}_RuntimeMemoryMaintenance_${EffectiveIntervalMinutes}M"
    $TaskNamePattern = '^' + [regex]::Escape($TaskPrefix) + '_RuntimeMemoryMaintenance_[0-9]+M$'
    $MatchingTasks = @(Get-ScheduledTask -TaskPath "\" -ErrorAction Stop | Where-Object {
        $_.TaskPath -eq '\' -and $_.TaskName -match $TaskNamePattern
    })
    $ReceiptOriginallyPresent = Test-Path -LiteralPath $OwnershipReceiptPath
    $OwnershipReceiptRaw = $null
    $OwnershipReceipt = $null
    if ($ReceiptOriginallyPresent) {
        try {
            $OwnershipReceiptRaw = Get-Content -LiteralPath $OwnershipReceiptPath -Raw -ErrorAction Stop
            $OwnershipReceipt = $OwnershipReceiptRaw | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "Refusing to mutate maintenance tasks with an invalid ownership receipt: $OwnershipReceiptPath"
        }
    }
    if ($MatchingTasks.Count -gt 0) {
        if (-not $OwnershipReceipt) {
            throw "Refusing to replace maintenance task(s) without an ownership receipt: $(@($MatchingTasks.TaskName) -join ', ')"
        }
        foreach ($MatchingTask in $MatchingTasks) {
            if (-not (Test-TradingOSMaintenanceTaskReceiptOwnership -Task $MatchingTask -Receipt $OwnershipReceipt -Root $Root -TaskPrefix $TaskPrefix)) {
                throw "Refusing to overwrite or remove an unowned maintenance task: $($MatchingTask.TaskName)"
            }
        }
    } elseif ($OwnershipReceipt) {
        throw "Refusing to create a maintenance task while its ownership receipt references a missing task: $OwnershipReceiptPath"
    }
    if (-not (Test-Path -LiteralPath $StartupPath)) { throw "Managed startup command is missing before task installation: $StartupPath" }
    $StartupContent = [System.IO.File]::ReadAllText($StartupPath)
    if (-not (Test-TradingOSManagedStartupContent -Content $StartupContent -Root $Root -TaskPrefix $TaskPrefix)) {
        throw "Managed startup command failed exact verification before task installation: $StartupPath"
    }
    $StartupSha256 = Get-TradingOSFileSha256 -Path $StartupPath
    if ($OwnershipReceipt) {
        try {
            $ReceiptOwnsStartup = [int]$OwnershipReceipt.schema_version -eq 1 -and [string]$OwnershipReceipt.task_prefix -eq $TaskPrefix -and
                [System.IO.Path]::GetFullPath([string]$OwnershipReceipt.root).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase) -and
                [System.IO.Path]::GetFullPath([string]$OwnershipReceipt.startup_path).Equals([System.IO.Path]::GetFullPath($StartupPath), [System.StringComparison]::OrdinalIgnoreCase) -and
                [string]$OwnershipReceipt.startup_sha256 -eq $StartupSha256
        } catch { $ReceiptOwnsStartup = $false }
        if (-not $ReceiptOwnsStartup) { throw "Ownership receipt does not own the managed startup command: $OwnershipReceiptPath" }
    }

    $PriorTasks = @($MatchingTasks | Where-Object { $_.TaskName -ne $TaskName })
    $ExistingSameTask = @($MatchingTasks | Where-Object { $_.TaskName -eq $TaskName } | Select-Object -First 1)
    if ($ExistingSameTask.Count -gt 0) { $ExistingSameTask = $ExistingSameTask[0] } else { $ExistingSameTask = $null }
    $ExistingSameTaskXml = if ($ExistingSameTask) { Export-ScheduledTask -TaskName $TaskName -TaskPath "\" -ErrorAction Stop } else { $null }
    $PriorTaskBackups = @($PriorTasks | ForEach-Object {
        $PriorName = $_.TaskName
        $PriorPath = $_.TaskPath
        $PriorXml = Export-ScheduledTask -TaskName $PriorName -TaskPath $PriorPath -ErrorAction Stop
        [pscustomobject]@{ task_name = $PriorName; task_path = $PriorPath; xml = $PriorXml }
    })
    $User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    $Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSCommandPath`" -MemoryOnly -MinimumTrimSleepSeconds $MinimumSleepSeconds -TaskPrefix `"$TaskPrefix`" -SkipMemoryMaintenanceInstall"
    $TaskDescription = 'TradingOS safe working-set maintenance for verified long-sleep PowerShell loops. No trading actions.'
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $Root
    $StartAt = (Get-Date).AddMinutes([math]::Max(1, $EffectiveIntervalMinutes))
    $Trigger = New-ScheduledTaskTrigger -Once -At $StartAt `
        -RepetitionInterval (New-TimeSpan -Minutes $EffectiveIntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $RemovedPriorTasks = New-Object System.Collections.Generic.List[object]
    try {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -TaskPath "\" `
            -Action $Action `
            -Trigger $Trigger `
            -Principal $Principal `
            -Settings $Settings `
            -Description $TaskDescription `
            -Force `
            -ErrorAction Stop | Out-Null
        $RegisteredTask = Get-ScheduledTask -TaskName $TaskName -TaskPath "\" -ErrorAction Stop
        if (-not (Test-TradingOSManagedMaintenanceTask -Task $RegisteredTask -Root $Root -TaskPrefix $TaskPrefix) -or
            [string]$RegisteredTask.TaskName -ne $TaskName -or [string]$RegisteredTask.TaskPath -ne '\' -or
            [string]$RegisteredTask.Description -ne $TaskDescription -or
            [string]$RegisteredTask.Actions[0].Execute -ne 'powershell.exe' -or
            [string]$RegisteredTask.Actions[0].Arguments -ne $Arguments -or
            -not [System.IO.Path]::GetFullPath([string]$RegisteredTask.Actions[0].WorkingDirectory).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Registered memory-maintenance task action failed exact verification: $TaskName"
        }
        $ExpectedInterval = [System.Xml.XmlConvert]::ToString((New-TimeSpan -Minutes $EffectiveIntervalMinutes))
        if (@($RegisteredTask.Triggers).Count -ne 1 -or [string]$RegisteredTask.Triggers[0].Repetition.Interval -ne $ExpectedInterval) {
            throw "Registered memory-maintenance task interval failed exact verification: $TaskName"
        }
        foreach ($PriorTask in $PriorTasks) {
            Unregister-ScheduledTask -TaskName $PriorTask.TaskName -TaskPath $PriorTask.TaskPath -Confirm:$false -ErrorAction Stop
            $RemovedPriorTasks.Add($PriorTask) | Out-Null
        }

        $InstallId = [guid]::NewGuid().ToString()
        if ($OwnershipReceipt -and [string]$OwnershipReceipt.install_id) {
            try { $InstallId = ([guid][string]$OwnershipReceipt.install_id).ToString() } catch { throw "Ownership receipt has an invalid install_id: $OwnershipReceiptPath" }
        }
        $UpdatedReceipt = [ordered]@{
            schema_version = 1
            generated_at = (Get-Date).ToUniversalTime().ToString('o')
            install_id = $InstallId
            root = $Root
            task_prefix = $TaskPrefix
            startup_path = $StartupPath
            startup_sha256 = $StartupSha256
            maintenance_task_name = $TaskName
            maintenance_task_path = '\'
            maintenance_action_execute = [string]$RegisteredTask.Actions[0].Execute
            maintenance_action_arguments = [string]$RegisteredTask.Actions[0].Arguments
            maintenance_working_directory = [string]$RegisteredTask.Actions[0].WorkingDirectory
            maintenance_interval = [string]$RegisteredTask.Triggers[0].Repetition.Interval
            live_trading_locked = $true
            can_trade = $false
        }
        Write-TradingOSJsonFileAtomic -Path $OwnershipReceiptPath -Payload $UpdatedReceipt -Depth 6
        $CommittedReceipt = Get-Content -LiteralPath $OwnershipReceiptPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if (-not (Test-TradingOSMaintenanceTaskReceiptOwnership -Task $RegisteredTask -Receipt $CommittedReceipt -Root $Root -TaskPrefix $TaskPrefix) -or
            [string]$CommittedReceipt.startup_sha256 -ne (Get-TradingOSFileSha256 -Path $StartupPath)) {
            throw "Committed autostart ownership receipt failed exact verification: $OwnershipReceiptPath"
        }
        return $TaskName
    } catch {
        $InstallError = $_
        $RollbackErrors = New-Object System.Collections.Generic.List[string]
        try {
            if ($ExistingSameTaskXml) {
                Register-ScheduledTask -TaskName $TaskName -TaskPath "\" -Xml $ExistingSameTaskXml -Force -ErrorAction Stop | Out-Null
            } else {
                $RollbackInventory = @(Get-ScheduledTask -TaskPath "\" -ErrorAction Stop)
                if (@($RollbackInventory | Where-Object { [string]$_.TaskName -eq $TaskName }).Count -gt 0) {
                    Unregister-ScheduledTask -TaskName $TaskName -TaskPath "\" -Confirm:$false -ErrorAction Stop
                }
            }
        } catch { $RollbackErrors.Add("target:$($_.Exception.Message)") | Out-Null }
        foreach ($RemovedPriorTask in $RemovedPriorTasks) {
            $Backup = @($PriorTaskBackups | Where-Object { [string]$_.task_name -eq [string]$RemovedPriorTask.TaskName -and [string]$_.task_path -eq [string]$RemovedPriorTask.TaskPath } | Select-Object -First 1)
            if ($Backup.Count -ne 1) {
                $RollbackErrors.Add("$($RemovedPriorTask.TaskName):missing rollback backup") | Out-Null
                continue
            }
            $Backup = $Backup[0]
            try {
                $RollbackInventory = @(Get-ScheduledTask -TaskPath $Backup.task_path -ErrorAction Stop)
                if (@($RollbackInventory | Where-Object { [string]$_.TaskName -eq [string]$Backup.task_name }).Count -ne 0) {
                    throw "task name was recreated before rollback"
                }
                Register-ScheduledTask -TaskName $Backup.task_name -TaskPath $Backup.task_path -Xml $Backup.xml -ErrorAction Stop | Out-Null
            } catch { $RollbackErrors.Add("$($Backup.task_name):$($_.Exception.Message)") | Out-Null }
        }
        try {
            if ($ReceiptOriginallyPresent) {
                Write-TradingOSTextFileAtomic -Path $OwnershipReceiptPath -Content $OwnershipReceiptRaw -Encoding UTF8
            } elseif (Test-Path -LiteralPath $OwnershipReceiptPath) {
                Remove-Item -LiteralPath $OwnershipReceiptPath -Force -ErrorAction Stop
            }
        } catch { $RollbackErrors.Add("receipt:$($_.Exception.Message)") | Out-Null }
        if ($RollbackErrors.Count -gt 0) {
            throw "Memory-maintenance task installation failed and rollback was incomplete. install_error=$($InstallError.Exception.Message); rollback_errors=$($RollbackErrors -join ' | ')"
        }
        throw $InstallError
    }
}

$AutostartMutex = $null
$AutostartMutexAcquired = $false
if (-not $MemoryOnly) {
    $AutostartMutex = New-Object System.Threading.Mutex($false, (Get-TradingOSAutostartMutexName -Root $Root))
    try { $AutostartMutexAcquired = $AutostartMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $AutostartMutexAcquired = $true }
    if (-not $AutostartMutexAcquired) {
        $AutostartMutex.Dispose()
        throw 'TradingOS autostart mutation is already in progress.'
    }
}

try {
if ($MemoryOnly) {
    $MemoryResult = Invoke-RuntimeWorkingSetTrim -MinimumSleepSeconds $MinimumTrimSleepSeconds -WhatIf:$MemoryWhatIf
    $MemoryFailures = @($MemoryResult.components | Where-Object { $_.status -in @("trim_failed", "trim_result_unverified_process_changed") })
    $MemorySummary = [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = if ($MemoryFailures.Count -eq 0) { "memory_maintenance_completed" } else { "memory_maintenance_degraded" }
        root = $Root
        memory_only = $true
        memory_what_if = [bool]$MemoryWhatIf
        working_set_trim = $MemoryResult
        failed_components = @($MemoryFailures | ForEach-Object { $_.id })
        live_trading_locked = $true
        can_trade = $false
    }
    Write-TradingOSJsonFileAtomic -Path $StatusPath -Payload $MemorySummary -Depth 8
    $MemorySummary | ConvertTo-Json -Depth 8
    if ($MemoryFailures.Count -gt 0) {
        throw "TradingOS memory maintenance degraded: $(@($MemoryFailures | ForEach-Object { $_.id }) -join ', ')"
    }
    return
}

$StartupInstallResult = $null
try {
    $StartupInstallRaw = & $StartupInstaller -TaskPrefix $TaskPrefix -MemoryMaintenanceMinutes $MemoryMaintenanceMinutes -MinimumTrimSleepSeconds $MinimumTrimSleepSeconds -ControlPanelPort $ControlPanelPort
    $StartupInstallResult = $StartupInstallRaw | ConvertFrom-Json -ErrorAction Stop
    Add-Action -Name "startup_folder_install" -Status "ok" -Extra @{ startup_cmd = $StartupInstallResult.startup_cmd }
} catch {
    Add-Action -Name "startup_folder_install" -Status "failed" -Extra @{ error = $_.Exception.Message }
}

$PanelStateBefore = Get-TradingOSControlPanelOwnershipState -Root $Root -Port $ControlPanelPort
$PanelListeningBefore = [bool]$PanelStateBefore.listening
$PanelHealthyBefore = [bool]$PanelStateBefore.job_contained
$RuntimeManifest = Get-TradingOSRuntimeManifest -Root $Root
$RuntimeComponentStatesBefore = @(Get-TradingOSRuntimeStates -Root $Root -Manifest $RuntimeManifest)
$RuntimeMissingBefore = @($RuntimeComponentStatesBefore | Where-Object { $_.required -and -not $_.job_contained })
$LoopAliveBefore = Test-LoopAlive -Path $LoopLockPath
$WatchdogAliveBefore = Test-LoopAlive -Path $WatchdogLockPath
$CrowdFadeAliveBefore = Test-LoopAlive -Path $CrowdFadeLockPath
$LiquidationForceOrderWatchdogAliveBefore = Test-LoopAlive -Path $LiquidationForceOrderWatchdogLockPath
$BybitAllLiquidationWatchdogAliveBefore = Test-LoopAlive -Path $BybitAllLiquidationWatchdogLockPath
$MicrostructureUnblockStatusAliveBefore = Test-LoopAlive -Path $MicrostructureUnblockStatusLockPath
$ResearchRuntimeSupervisorAliveBefore = Test-LoopAlive -Path $ResearchRuntimeSupervisorStatusPath
$DeribitOptionsSurfaceCollectorAliveBefore = Test-LoopAlive -Path $DeribitOptionsSurfaceCollectorStatusPath
$DeribitOptionsReadinessAliveBefore = Test-LoopAlive -Path $DeribitOptionsReadinessStatusPath
$DeribitOptionsSkewForwardAliveBefore = Test-LoopAlive -Path $DeribitOptionsSkewForwardStatusPath
$DeribitOptionsV3CollectorAliveBefore = Test-LoopAlive -Path $DeribitOptionsV3CollectorStatusPath
$DeribitOptionsV3ReadinessAliveBefore = Test-LoopAlive -Path $DeribitOptionsV3ReadinessStatusPath
if (-not $PanelHealthyBefore -or 
    $RuntimeMissingBefore.Count -gt 0 -or
    -not $BybitAllLiquidationWatchdogAliveBefore -or
    -not $MicrostructureUnblockStatusAliveBefore
) {
    try {
        & $RuntimeScript -InvocationMode Autostart -ControlPanelPort $ControlPanelPort -ForwardSleepSeconds $ForwardSleepSeconds -CrowdFadeSleepSeconds $CrowdFadeSleepSeconds -BybitAllLiquidationWatchdogSleepSeconds $BybitAllLiquidationWatchdogSleepSeconds -MicrostructureUnblockStatusSleepSeconds $MicrostructureUnblockStatusSleepSeconds -ResearchRuntimeSupervisorSleepSeconds $ResearchRuntimeSupervisorSleepSeconds -DeribitOptionsSurfaceCollectorSleepSeconds $DeribitOptionsSurfaceCollectorSleepSeconds -DeribitOptionsReadinessSleepSeconds $DeribitOptionsReadinessSleepSeconds -DeribitOptionsSkewForwardSleepSeconds $DeribitOptionsSkewForwardSleepSeconds | Out-Null
        Add-Action -Name "start_runtime" -Status "ok" -Extra @{ panel_before = $PanelListeningBefore; panel_decision_before = $PanelStateBefore.decision; missing_required_before = @($RuntimeMissingBefore | ForEach-Object { $_.id }); loop_before = $LoopAliveBefore; watchdog_before = $WatchdogAliveBefore; crowd_fade_before = $CrowdFadeAliveBefore; liquidation_force_order_watchdog_before = $LiquidationForceOrderWatchdogAliveBefore; bybit_all_liquidation_watchdog_before = $BybitAllLiquidationWatchdogAliveBefore; microstructure_unblock_status_before = $MicrostructureUnblockStatusAliveBefore; research_runtime_supervisor_before = $ResearchRuntimeSupervisorAliveBefore; deribit_options_surface_collector_before = $DeribitOptionsSurfaceCollectorAliveBefore; deribit_options_readiness_before = $DeribitOptionsReadinessAliveBefore; deribit_options_skew_forward_before = $DeribitOptionsSkewForwardAliveBefore }
    } catch {
        Add-Action -Name "start_runtime" -Status "failed" -Extra @{ error = $_.Exception.Message; panel_before = $PanelListeningBefore; panel_decision_before = $PanelStateBefore.decision; missing_required_before = @($RuntimeMissingBefore | ForEach-Object { $_.id }); loop_before = $LoopAliveBefore; watchdog_before = $WatchdogAliveBefore; liquidation_force_order_watchdog_before = $LiquidationForceOrderWatchdogAliveBefore; bybit_all_liquidation_watchdog_before = $BybitAllLiquidationWatchdogAliveBefore }
    }
} else {
    Add-Action -Name "start_runtime" -Status "skipped_already_running" -Extra @{ optional_research_runtime_supervisor_alive = $ResearchRuntimeSupervisorAliveBefore; optional_deribit_options_surface_collector_alive = $DeribitOptionsSurfaceCollectorAliveBefore; optional_deribit_options_readiness_alive = $DeribitOptionsReadinessAliveBefore; optional_deribit_options_skew_forward_alive = $DeribitOptionsSkewForwardAliveBefore }
}

# V3 is a separate public-data successor. Startup may launch it once, but neither
# this optimizer nor its launcher may stop or automatically restart a process.
if (-not (Test-Path -LiteralPath $DeribitOptionsV3Launcher)) {
    Add-Action -Name "start_deribit_options_v3_collector" -Status "failed" -Extra @{ reason = "launcher_missing" }
    Add-Action -Name "start_deribit_options_v3_readiness" -Status "failed" -Extra @{ reason = "launcher_missing" }
} else {
    if (-not $DeribitOptionsV3CollectorAliveBefore) {
        try {
            & $DeribitOptionsV3Launcher -Component collector -SleepSeconds $DeribitOptionsV3CollectorSleepSeconds | Out-Null
        } catch {
            Add-Action -Name "start_deribit_options_v3_collector" -Status "failed" -Extra @{ error = $_.Exception.Message }
        }
    }
    $DeribitOptionsV3CollectorAlive = Test-LoopAlive -Path $DeribitOptionsV3CollectorStatusPath
    $DeribitOptionsV3CollectorLauncherStatus = $null
    try { $DeribitOptionsV3CollectorLauncherStatus = Get-Content -LiteralPath $DeribitOptionsV3CollectorLauncherStatusPath -Raw | ConvertFrom-Json } catch {}
    if (-not ($Actions | Where-Object { $_.name -eq "start_deribit_options_v3_collector" })) {
        Add-Action -Name "start_deribit_options_v3_collector" -Status $(if ($DeribitOptionsV3CollectorAlive) { "ok" } else { "failed" }) -Extra @{
            alive_before = $DeribitOptionsV3CollectorAliveBefore
            alive_after = $DeribitOptionsV3CollectorAlive
            launcher_status = if ($DeribitOptionsV3CollectorLauncherStatus) { $DeribitOptionsV3CollectorLauncherStatus.status } else { $null }
            automatic_restart_allowed = $false
        }
    }

    if ($DeribitOptionsV3CollectorAlive -and -not $DeribitOptionsV3ReadinessAliveBefore) {
        try {
            & $DeribitOptionsV3Launcher -Component readiness -SleepSeconds $DeribitOptionsV3ReadinessSleepSeconds | Out-Null
        } catch {
            Add-Action -Name "start_deribit_options_v3_readiness" -Status "failed" -Extra @{ error = $_.Exception.Message }
        }
    }
    $DeribitOptionsV3ReadinessAlive = Test-LoopAlive -Path $DeribitOptionsV3ReadinessStatusPath
    $DeribitOptionsV3ReadinessLauncherStatus = $null
    try { $DeribitOptionsV3ReadinessLauncherStatus = Get-Content -LiteralPath $DeribitOptionsV3ReadinessLauncherStatusPath -Raw | ConvertFrom-Json } catch {}
    if (-not ($Actions | Where-Object { $_.name -eq "start_deribit_options_v3_readiness" })) {
        Add-Action -Name "start_deribit_options_v3_readiness" -Status $(if ($DeribitOptionsV3ReadinessAlive) { "ok" } else { "failed" }) -Extra @{
            upstream_collector_alive = $DeribitOptionsV3CollectorAlive
            alive_before = $DeribitOptionsV3ReadinessAliveBefore
            alive_after = $DeribitOptionsV3ReadinessAlive
            launcher_status = if ($DeribitOptionsV3ReadinessLauncherStatus) { $DeribitOptionsV3ReadinessLauncherStatus.status } else { $null }
            automatic_restart_allowed = $false
        }
    }
}

# Restore the six public-data, orderless research components once per login or
# explicit optimizer run. The launcher is idempotent and never stops/restarts a
# process; the 15-minute memory-only task returns before reaching this block.
$IsolatedResearchRegistryStatus = $null
if (-not (Test-Path -LiteralPath $IsolatedResearchRegistryLauncher)) {
    Add-Action -Name "start_isolated_research_registry_components" -Status "failed" -Extra @{ reason = "launcher_missing" }
} else {
    try {
        & $IsolatedResearchRegistryLauncher -Component all | Out-Null
        try { $IsolatedResearchRegistryStatus = Get-Content -LiteralPath $IsolatedResearchRegistryStatusPath -Raw | ConvertFrom-Json } catch {}
        $IsolatedResearchRegistryReady = [bool](
            $IsolatedResearchRegistryStatus -and
            $IsolatedResearchRegistryStatus.status -eq "research_registry_components_ready" -and
            $IsolatedResearchRegistryStatus.can_trade -eq $false
        )
        Add-Action -Name "start_isolated_research_registry_components" -Status $(if ($IsolatedResearchRegistryReady) { "ok" } else { "failed" }) -Extra @{
            launcher_status = if ($IsolatedResearchRegistryStatus) { $IsolatedResearchRegistryStatus.status } else { $null }
            managed_components = if ($IsolatedResearchRegistryStatus) { @($IsolatedResearchRegistryStatus.results).Count } else { 0 }
            startup_launch_only = $true
            automatic_restart_allowed = $false
            can_trade = $false
        }
    } catch {
        Add-Action -Name "start_isolated_research_registry_components" -Status "failed" -Extra @{
            error = $_.Exception.Message
            startup_launch_only = $true
            automatic_restart_allowed = $false
            can_trade = $false
        }
    }
}

if ($RunOneShot) {
    try {
        & $OnceScript | Out-Null
        Add-Action -Name "run_forward_once" -Status "ok"
    } catch {
        Add-Action -Name "run_forward_once" -Status "failed" -Extra @{ error = $_.Exception.Message }
    }
}

$VerificationDeadline = (Get-Date).AddSeconds(15)
do {
    $RuntimeComponentStates = @(Get-TradingOSRuntimeStates -Root $Root -Manifest $RuntimeManifest)
    $RuntimeMissing = @($RuntimeComponentStates | Where-Object { $_.required -and -not $_.job_contained })
    $ControlPanelState = Get-TradingOSControlPanelOwnershipState -Root $Root -Port $ControlPanelPort
    if (($RuntimeMissing.Count -eq 0 -and $ControlPanelState.job_contained) -or (Get-Date) -ge $VerificationDeadline) { break }
    Start-Sleep -Milliseconds 500
} while ($true)
$RuntimeHealthy = $RuntimeMissing.Count -eq 0 -and [bool]$ControlPanelState.job_contained
Add-Action -Name "verify_runtime_components" -Status $(if ($RuntimeHealthy) { "ok" } else { "degraded" }) -Extra @{
    expected = $RuntimeComponentStates.Count
    healthy = @($RuntimeComponentStates | Where-Object { $_.job_contained }).Count
    failed = @(@($RuntimeMissing | ForEach-Object { $_.id }) + $(if (-not $ControlPanelState.job_contained) { @("control_panel") } else { @() }))
    control_panel_decision = $ControlPanelState.ownership_decision
}

$WorkingSetTrim = Invoke-RuntimeWorkingSetTrim -MinimumSleepSeconds $MinimumTrimSleepSeconds -WhatIf:$MemoryWhatIf
Add-Action -Name "trim_idle_loop_working_sets" -Status "completed" -Extra $WorkingSetTrim

$MemoryMaintenanceTask = $null
if (-not $SkipMemoryMaintenanceInstall) {
    if ($StartupInstallResult) {
        try {
            $MemoryMaintenanceTask = Install-RuntimeMemoryMaintenanceTask `
                -IntervalMinutes $MemoryMaintenanceMinutes `
                -MinimumSleepSeconds $MinimumTrimSleepSeconds `
                -StartupPath ([string]$StartupInstallResult.startup_cmd) `
                -OwnershipReceiptPath $ReceiptPath
            Add-Action -Name "install_memory_maintenance_task" -Status "ok" -Extra @{ task_name = $MemoryMaintenanceTask; interval_minutes = $MemoryMaintenanceMinutes }
        } catch {
            Add-Action -Name "install_memory_maintenance_task" -Status "failed" -Extra @{ error = $_.Exception.Message }
        }
    } else {
        Add-Action -Name "install_memory_maintenance_task" -Status "failed" -Extra @{ error = 'startup_folder_install_not_verified' }
    }
}

$TrimFailures = @($WorkingSetTrim.components | Where-Object { $_.status -in @("trim_failed", "trim_result_unverified_process_changed") })
if ($StartupInstallResult -and $MemoryMaintenanceTask) {
    try {
        $ExpectedTaskName = "${TaskPrefix}_RuntimeMemoryMaintenance_${MemoryMaintenanceMinutes}M"
        $ReceiptTask = Get-ScheduledTask -TaskName $ExpectedTaskName -TaskPath "\" -ErrorAction Stop
        $Receipt = Get-Content -LiteralPath $ReceiptPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if (-not (Test-TradingOSMaintenanceTaskReceiptOwnership -Task $ReceiptTask -Receipt $Receipt -Root $Root -TaskPrefix $TaskPrefix)) { throw "Maintenance task failed receipt-ownership verification: $ExpectedTaskName" }
        $StartupPath = [string]$StartupInstallResult.startup_cmd
        $StartupContent = [System.IO.File]::ReadAllText($StartupPath)
        if (-not (Test-TradingOSManagedStartupContent -Content $StartupContent -Root $Root -TaskPrefix $TaskPrefix)) {
            throw "Startup command failed managed-ownership verification: $StartupPath"
        }
        if ([string]$Receipt.startup_sha256 -ne (Get-TradingOSFileSha256 -Path $StartupPath)) { throw "Startup command hash does not match its committed ownership receipt: $StartupPath" }
        Add-Action -Name "write_autostart_ownership_receipt" -Status "ok" -Extra @{ receipt_path = $ReceiptPath; install_id = [string]$Receipt.install_id }
    } catch {
        Add-Action -Name "write_autostart_ownership_receipt" -Status "failed" -Extra @{ error = $_.Exception.Message }
    }
} else {
    Add-Action -Name "write_autostart_ownership_receipt" -Status "skipped_autostart_provisioning_failed"
}
Add-Action -Name "legacy_task_cleanup" -Status "deferred_to_verified_installer"

$ActionFailures = @($Actions | Where-Object { [string]$_.status -in @("failed", "degraded") })
$MaintenanceHealthy = $TrimFailures.Count -eq 0 -and $ActionFailures.Count -eq 0
$OverallHealthy = $RuntimeHealthy -and $MaintenanceHealthy

$Summary = [ordered]@{
    ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    status = if ($OverallHealthy) { "completed" } else { "degraded" }
    runtime_status = if ($RuntimeHealthy) { "healthy" } else { "degraded" }
    maintenance_status = if ($MaintenanceHealthy) { "healthy" } else { "degraded" }
    root = $Root
    memory_only = $false
    runtime_components_expected = $RuntimeComponentStates.Count
    runtime_components_healthy = @($RuntimeComponentStates | Where-Object { $_.job_contained }).Count
    runtime_components_failed = @($RuntimeMissing | ForEach-Object { $_.id })
    working_set_trim = $WorkingSetTrim
    memory_maintenance_task = $MemoryMaintenanceTask
    autostart_receipt = $ReceiptPath
    maintenance_failed_actions = @($ActionFailures | ForEach-Object { $_.name })
    maintenance_failed_components = @($TrimFailures | ForEach-Object { $_.id })
    live_trading_locked = $true
    can_trade = $false
    control_panel_listening = [bool]$ControlPanelState.listening
    control_panel_decision = $ControlPanelState.ownership_decision
    control_panel_pid = $ControlPanelState.pid
    forward_loop_alive = Test-LoopAlive -Path $LoopLockPath
    watchdog_loop_alive = Test-LoopAlive -Path $WatchdogLockPath
    crowd_fade_loop_alive = Test-LoopAlive -Path $CrowdFadeLockPath
    liquidation_force_order_watchdog_loop_alive = Test-LoopAlive -Path $LiquidationForceOrderWatchdogLockPath
    bybit_all_liquidation_watchdog_loop_alive = Test-LoopAlive -Path $BybitAllLiquidationWatchdogLockPath
    bybit_all_liquidation_watchdog_sleep_seconds = $BybitAllLiquidationWatchdogSleepSeconds
    bybit_all_liquidation_watchdog_public_data_only = $true
    microstructure_unblock_status_loop_alive = Test-LoopAlive -Path $MicrostructureUnblockStatusLockPath
    microstructure_unblock_status_sleep_seconds = $MicrostructureUnblockStatusSleepSeconds
    microstructure_unblock_status_observability_only = $true
    research_runtime_supervisor_loop_alive = Test-LoopAlive -Path $ResearchRuntimeSupervisorStatusPath
    research_runtime_supervisor_sleep_seconds = $ResearchRuntimeSupervisorSleepSeconds
    research_runtime_supervisor_automatic_restart_allowed = $false
    deribit_options_surface_collector_loop_alive = Test-LoopAlive -Path $DeribitOptionsSurfaceCollectorStatusPath
    deribit_options_surface_collector_sleep_seconds = $DeribitOptionsSurfaceCollectorSleepSeconds
    deribit_options_surface_collector_automatic_restart_allowed = $false
    deribit_options_readiness_loop_alive = Test-LoopAlive -Path $DeribitOptionsReadinessStatusPath
    deribit_options_readiness_sleep_seconds = $DeribitOptionsReadinessSleepSeconds
    deribit_options_readiness_automatic_restart_allowed = $false
    deribit_options_skew_forward_loop_alive = Test-LoopAlive -Path $DeribitOptionsSkewForwardStatusPath
    deribit_options_skew_forward_sleep_seconds = $DeribitOptionsSkewForwardSleepSeconds
    deribit_options_skew_forward_automatic_restart_allowed = $false
    deribit_options_v3_collector_loop_alive = Test-LoopAlive -Path $DeribitOptionsV3CollectorStatusPath
    deribit_options_v3_collector_sleep_seconds = $DeribitOptionsV3CollectorSleepSeconds
    deribit_options_v3_collector_startup_launch_only = $true
    deribit_options_v3_collector_automatic_restart_allowed = $false
    deribit_options_v3_readiness_loop_alive = Test-LoopAlive -Path $DeribitOptionsV3ReadinessStatusPath
    deribit_options_v3_readiness_sleep_seconds = $DeribitOptionsV3ReadinessSleepSeconds
    deribit_options_v3_readiness_startup_launch_only = $true
    deribit_options_v3_readiness_automatic_restart_allowed = $false
    isolated_research_registry_components_status = if ($IsolatedResearchRegistryStatus) { $IsolatedResearchRegistryStatus.status } else { "missing_status" }
    isolated_research_registry_components_managed = if ($IsolatedResearchRegistryStatus) { @($IsolatedResearchRegistryStatus.results).Count } else { 0 }
    isolated_research_registry_components_startup_launch_only = $true
    isolated_research_registry_components_automatic_restart_allowed = $false
    run_one_shot = [bool]$RunOneShot
    actions = $Actions
}

Write-TradingOSJsonFileAtomic -Path $StatusPath -Payload $Summary -Depth 8
$Summary | ConvertTo-Json -Depth 8
if (-not $OverallHealthy) {
    $FailureNames = @(@($RuntimeMissing | ForEach-Object { $_.id }) + @($ActionFailures | ForEach-Object { $_.name }) + @($TrimFailures | ForEach-Object { $_.id }))
    if (-not $ControlPanelState.job_contained) { $FailureNames += "control_panel" }
    throw "TradingOS runtime optimization degraded: $($FailureNames -join ', ')"
}
} finally {
    if ($AutostartMutexAcquired) {
        try { $AutostartMutex.ReleaseMutex() } catch {}
    }
    if ($AutostartMutex) { $AutostartMutex.Dispose() }
}
