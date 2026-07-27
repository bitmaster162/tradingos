if (-not ("TradingOSCommandLineNative" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class TradingOSCommandLineNative {
    [DllImport("shell32.dll", SetLastError = true)]
    private static extern IntPtr CommandLineToArgvW([MarshalAs(UnmanagedType.LPWStr)] string commandLine, out int argc);
    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr pointer);
    public static string[] Split(string commandLine) {
        int argc;
        IntPtr argv = CommandLineToArgvW(commandLine, out argc);
        if (argv == IntPtr.Zero) throw new Win32Exception();
        try {
            string[] values = new string[argc];
            for (int i = 0; i < argc; i++) values[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv, i * IntPtr.Size));
            return values;
        } finally { LocalFree(argv); }
    }
}
"@ -ErrorAction Stop
}

function Get-TradingOSCommandLineArguments {
    param([Parameter(Mandatory = $true)][string]$CommandLine)
    try { return @([TradingOSCommandLineNative]::Split($CommandLine)) } catch { return @() }
}

function Test-TradingOSCommandLineExactArgument {
    param(
        [Parameter(Mandatory = $true)][string]$CommandLine,
        [Parameter(Mandatory = $true)][string]$ExpectedArgument
    )
    foreach ($Argument in @(Get-TradingOSCommandLineArguments -CommandLine $CommandLine)) {
        if ([string]$Argument -and ([string]$Argument).Equals($ExpectedArgument, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Assert-TradingOSTaskPrefix {
    param([Parameter(Mandatory = $true)][string]$TaskPrefix)
    if ($TaskPrefix -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
        throw "Invalid TradingOS task prefix: $TaskPrefix"
    }
    return $TaskPrefix
}

function Assert-TradingOSStartupFileName {
    param([Parameter(Mandatory = $true)][string]$FileName)
    if ($FileName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.cmd$' -or [System.IO.Path]::GetFileName($FileName) -ne $FileName) {
        throw "Invalid TradingOS startup file name: $FileName"
    }
    return $FileName
}

function Get-TradingOSFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}

function Test-TradingOSTrustedPowerShellTaskExecutable {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Executable)

    if ($Executable.Equals('powershell.exe', [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    if (-not [System.IO.Path]::IsPathRooted($Executable)) { return $false }
    try { $Candidate = [System.IO.Path]::GetFullPath($Executable) } catch { return $false }

    $AllowedExecutables = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($Allowed in @(
        (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'),
        (Join-Path $env:SystemRoot 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe')
    )) {
        try { $null = $AllowedExecutables.Add([System.IO.Path]::GetFullPath($Allowed)) } catch {}
    }
    return $AllowedExecutables.Contains($Candidate)
}

function Test-TradingOSManagedStartupContent {
    param(
        [Parameter(Mandatory = $true)][string]$Content,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$TaskPrefix
    )
    $Normalized = $Content.Replace("`r`n", "`n").TrimEnd("`n")
    $Lines = @($Normalized -split "`n")
    $RuntimeScript = Join-Path ([System.IO.Path]::GetFullPath($Root)) "ops\autostart\Optimize-TradingOSRuntime.ps1"
    if ($Lines.Count -notin @(3, 4) -or $Lines[0] -ne "@echo off" -or $Lines[-2] -ne "cd /d `"$([System.IO.Path]::GetFullPath($Root))`"") { return $false }
    $PrefixToken = [regex]::Escape($TaskPrefix)
    $ScriptToken = [regex]::Escape($RuntimeScript)
    if ($Lines.Count -eq 3) {
        return $Lines[2] -match "^powershell\.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ScriptToken`" -TaskPrefix `"$PrefixToken`"$"
    }
    if ($Lines[1] -ne "rem TradingOS managed startup; canonical local runtime; live trading locked") { return $false }
    return $Lines[3] -match "^powershell\.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ScriptToken`" -TaskPrefix `"$PrefixToken`" -MemoryMaintenanceMinutes [0-9]+ -MinimumTrimSleepSeconds [0-9]+ -ControlPanelPort [0-9]+$"
}

function Test-TradingOSManagedMaintenanceTask {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$TaskPrefix
    )
    $ExpectedDescription = 'TradingOS safe working-set maintenance for verified long-sleep PowerShell loops. No trading actions.'
    if (-not $Task -or [string]$Task.TaskPath -ne '\' -or [string]$Task.Description -ne $ExpectedDescription -or
        @($Task.Actions).Count -ne 1 -or @($Task.Triggers).Count -ne 1) { return $false }
    $NamePattern = '^' + [regex]::Escape($TaskPrefix) + '_RuntimeMemoryMaintenance_([0-9]+)M$'
    $NameMatch = [regex]::Match([string]$Task.TaskName, $NamePattern)
    if (-not $NameMatch.Success) { return $false }
    $IntervalMinutes = [int]$NameMatch.Groups[1].Value
    if ($IntervalMinutes -lt 5 -or $IntervalMinutes -gt 1440 -or $NameMatch.Groups[1].Value -ne [string]$IntervalMinutes) { return $false }
    $Action = $Task.Actions[0]
    if (-not (Test-TradingOSTrustedPowerShellTaskExecutable -Executable ([string]$Action.Execute))) { return $false }
    try {
        if (-not [System.IO.Path]::GetFullPath([string]$Action.WorkingDirectory).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    } catch { return $false }
    $OptimizerScript = Join-Path ([System.IO.Path]::GetFullPath($Root)) "ops\autostart\Optimize-TradingOSRuntime.ps1"
    $ExpectedArgumentsPattern = '^-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + [regex]::Escape($OptimizerScript) + '" -MemoryOnly -MinimumTrimSleepSeconds (?<sleep>[0-9]+) -TaskPrefix "' + [regex]::Escape($TaskPrefix) + '" -SkipMemoryMaintenanceInstall$'
    $ArgumentMatch = [regex]::Match([string]$Action.Arguments, $ExpectedArgumentsPattern)
    if (-not $ArgumentMatch.Success) { return $false }
    $MinimumSleepSeconds = 0
    if (-not [int]::TryParse($ArgumentMatch.Groups['sleep'].Value, [ref]$MinimumSleepSeconds) -or
        $MinimumSleepSeconds -lt 30 -or $MinimumSleepSeconds -gt 604800 -or
        $ArgumentMatch.Groups['sleep'].Value -ne [string]$MinimumSleepSeconds) { return $false }
    $ExpectedInterval = [System.Xml.XmlConvert]::ToString((New-TimeSpan -Minutes $IntervalMinutes))
    $Trigger = $Task.Triggers[0]
    return [string]$Trigger.CimClass.CimClassName -eq 'MSFT_TaskTimeTrigger' -and
        [string]$Trigger.Repetition.Interval -eq $ExpectedInterval -and
        [string]$Trigger.Repetition.Duration -eq 'P3650D' -and
        [bool]$Trigger.Repetition.StopAtDurationEnd
}

function Test-TradingOSMaintenanceTaskReceiptOwnership {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)]$Receipt,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$TaskPrefix
    )
    if (-not $Receipt -or -not (Test-TradingOSManagedMaintenanceTask -Task $Task -Root $Root -TaskPrefix $TaskPrefix)) { return $false }
    try {
        $InstallId = [guid][string]$Receipt.install_id
        if ($InstallId -eq [guid]::Empty) { return $false }
        if ([int]$Receipt.schema_version -ne 1 -or [string]$Receipt.task_prefix -ne $TaskPrefix -or
            -not [System.IO.Path]::GetFullPath([string]$Receipt.root).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase) -or
            [string]$Receipt.maintenance_task_name -ne [string]$Task.TaskName -or
            [string]$Receipt.maintenance_task_path -ne [string]$Task.TaskPath -or
            [string]$Receipt.maintenance_action_execute -ne [string]$Task.Actions[0].Execute -or
            [string]$Receipt.maintenance_action_arguments -ne [string]$Task.Actions[0].Arguments -or
            [string]$Receipt.maintenance_working_directory -ne [string]$Task.Actions[0].WorkingDirectory -or
            [string]$Receipt.maintenance_interval -ne [string]$Task.Triggers[0].Repetition.Interval -or
            -not ($Receipt.live_trading_locked -is [bool]) -or -not [bool]$Receipt.live_trading_locked -or
            -not ($Receipt.can_trade -is [bool]) -or [bool]$Receipt.can_trade) { return $false }
    } catch { return $false }
    return $true
}

function Test-TradingOSManagedLegacyTask {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$TaskPrefix,
        [Parameter(Mandatory = $true)][ValidateSet('ControlPanel', 'ForwardPaper4H')][string]$Kind
    )
    if (-not $Task -or [string]$Task.TaskPath -ne '\' -or @($Task.Actions).Count -ne 1 -or @($Task.Triggers).Count -ne 1) { return $false }
    $Action = $Task.Actions[0]
    if ($Kind -eq 'ControlPanel') {
        if ([string]$Task.TaskName -ne "${TaskPrefix}_ControlPanel_Logon" -or
            [string]$Task.Description -ne 'Trading OS safe local control panel autostart. No trading orders.' -or
            -not (Test-TradingOSTrustedPowerShellTaskExecutable -Executable ([string]$Action.Execute)) -or
            [string]$Task.Triggers[0].CimClass.CimClassName -ne 'MSFT_TaskLogonTrigger') { return $false }
        try {
            if (-not [System.IO.Path]::GetFullPath([string]$Action.WorkingDirectory).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        } catch { return $false }
        $CommandLine = "`"$([string]$Action.Execute)`" $([string]$Action.Arguments)"
        $Arguments = @(Get-TradingOSCommandLineArguments -CommandLine $CommandLine)
        if ($Arguments.Count -ne 8 -or [string]$Arguments[1] -ne '-NoProfile' -or
            [string]$Arguments[2] -ne '-ExecutionPolicy' -or [string]$Arguments[3] -ne 'Bypass' -or
            [string]$Arguments[4] -ne '-File' -or [string]$Arguments[6] -ne '-Port') { return $false }
        try {
            if (-not [System.IO.Path]::GetFullPath([string]$Arguments[5]).Equals([System.IO.Path]::GetFullPath((Join-Path $Root 'ops\autostart\Start-TradingOSControlPanel.ps1')), [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
            $Port = [int]$Arguments[7]
            return $Port -ge 1 -and $Port -le 65535
        } catch { return $false }
    }

    $ExpectedLegacyRoot = Join-Path $env:USERPROFILE 'My Drive\04_PRODUCT_SHELLS\Trade\MAX_BitEvo_ALL_IN_ONE_UNIFIED_20260323'
    $ExpectedPython = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\python.exe'
    $ExpectedScript = Join-Path $ExpectedLegacyRoot 'tools\strategy_mix_forward_scheduler.py'
    $ExpectedArguments = "`"$ExpectedScript`" --cycles 1 --with-spot --out-prefix docs\STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08"
    if ([string]$Task.TaskName -ne "${TaskPrefix}_ForwardPaper_4H" -or
        [string]$Task.Description -ne 'Trading OS forward paper monitor: public data only, no orders.' -or
        -not ([string]$Action.Execute).Equals($ExpectedPython, [System.StringComparison]::OrdinalIgnoreCase) -or
        [string]$Action.Arguments -ne $ExpectedArguments -or
        -not ([string]$Action.WorkingDirectory).Equals($ExpectedLegacyRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        [string]$Task.Triggers[0].CimClass.CimClassName -ne 'MSFT_TaskTimeTrigger' -or
        [string]$Task.Triggers[0].Repetition.Interval -ne 'PT4H' -or
        [string]$Task.Triggers[0].Repetition.Duration -ne 'P3650D' -or
        -not [bool]$Task.Triggers[0].Repetition.StopAtDurationEnd) { return $false }
    $CommandLine = "`"$([string]$Action.Execute)`" $([string]$Action.Arguments)"
    $Arguments = @(Get-TradingOSCommandLineArguments -CommandLine $CommandLine)
    if ($Arguments.Count -ne 7 -or [string]$Arguments[2] -ne '--cycles' -or [string]$Arguments[3] -ne '1' -or
        [string]$Arguments[4] -ne '--with-spot' -or [string]$Arguments[5] -ne '--out-prefix' -or
        [string]$Arguments[6] -ne 'docs\STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08') { return $false }
    try {
        if (-not [System.IO.Path]::GetFullPath([string]$Arguments[0]).Equals([System.IO.Path]::GetFullPath([string]$Action.Execute), [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        if (-not [System.IO.Path]::GetFullPath([string]$Action.Execute).Equals([System.IO.Path]::GetFullPath($ExpectedPython), [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        if (-not [System.IO.Path]::GetFullPath([string]$Arguments[1]).Equals([System.IO.Path]::GetFullPath($ExpectedScript), [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        if (-not [System.IO.Path]::GetFullPath([string]$Action.WorkingDirectory).Equals([System.IO.Path]::GetFullPath($ExpectedLegacyRoot), [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    } catch { return $false }
    return $true
}

function Move-TradingOSFileAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$TemporaryPath,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )
    $LastError = $null
    $BackupPath = "$DestinationPath.atomic-backup.$PID.$([guid]::NewGuid().ToString('N'))"
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        try {
            if (Test-Path -LiteralPath $DestinationPath) {
                [System.IO.File]::Replace($TemporaryPath, $DestinationPath, $BackupPath, $true)
                Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
            } else {
                [System.IO.File]::Move($TemporaryPath, $DestinationPath)
            }
            return
        } catch {
            $LastError = $_.Exception
            if ($Attempt -lt 5) { Start-Sleep -Milliseconds (50 * $Attempt) }
        }
    }
    Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
    throw $LastError
}

function Write-TradingOSJsonFileAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Payload,
        [int]$Depth = 8
    )
    $Parent = Split-Path $Path -Parent
    if ($Parent) { New-Item -ItemType Directory -Force -Path $Parent | Out-Null }
    $TemporaryPath = "$Path.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    try {
        $Payload | ConvertTo-Json -Depth $Depth | Set-Content -LiteralPath $TemporaryPath -Encoding UTF8 -ErrorAction Stop
        Move-TradingOSFileAtomic -TemporaryPath $TemporaryPath -DestinationPath $Path
    } finally {
        Remove-Item -LiteralPath $TemporaryPath -Force -ErrorAction SilentlyContinue
    }
}

function Write-TradingOSTextFileAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Content,
        [ValidateSet('ASCII', 'UTF8', 'Unicode')][string]$Encoding = 'UTF8'
    )
    $Parent = Split-Path $Path -Parent
    if ($Parent) { New-Item -ItemType Directory -Force -Path $Parent | Out-Null }
    $TemporaryPath = "$Path.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    try {
        $Content | Set-Content -LiteralPath $TemporaryPath -Encoding $Encoding -ErrorAction Stop
        Move-TradingOSFileAtomic -TemporaryPath $TemporaryPath -DestinationPath $Path
    } finally {
        Remove-Item -LiteralPath $TemporaryPath -Force -ErrorAction SilentlyContinue
    }
}

$ShutdownGateScript = Join-Path $PSScriptRoot "TradingOSRuntimeShutdownGate.ps1"
if (-not (Test-Path -LiteralPath $ShutdownGateScript -PathType Leaf -ErrorAction Stop)) {
    throw "Missing TradingOS runtime shutdown gate: $ShutdownGateScript"
}
. $ShutdownGateScript
if (-not (Get-Command Test-TradingOSRuntimeShutdownRequested -CommandType Function -ErrorAction Stop)) {
    throw "TradingOS runtime shutdown gate did not load."
}

function Get-TradingOSAllowedPowerShellExecutables {
    $AllowedExecutables = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($Candidate in @(
        (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"),
        (Join-Path $env:SystemRoot "SysWOW64\WindowsPowerShell\v1.0\powershell.exe")
    )) {
        try { $null = $AllowedExecutables.Add([System.IO.Path]::GetFullPath($Candidate)) } catch {}
    }
    foreach ($CommandName in @("powershell.exe", "pwsh.exe")) {
        $Resolved = Get-Command $CommandName -ErrorAction SilentlyContinue
        if ($Resolved -and $Resolved.Source) {
            try { $null = $AllowedExecutables.Add([System.IO.Path]::GetFullPath([string]$Resolved.Source)) } catch {}
        }
    }
    return ,$AllowedExecutables
}

function Test-TradingOSPowerShellFileCommand {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$ProcessName,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$ExecutablePath,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$CommandLine,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath,
        [System.Collections.Generic.HashSet[string]]$AllowedPowerShellExecutables
    )

    if ($ProcessName -notmatch '^(?:powershell|pwsh)(?:\.exe)?$' -or -not $ExecutablePath -or -not $CommandLine) { return $false }
    try {
        $ExpectedScript = [System.IO.Path]::GetFullPath($ExpectedScriptPath)
        $Executable = [System.IO.Path]::GetFullPath($ExecutablePath)
        $Arguments = @(Get-TradingOSCommandLineArguments -CommandLine $CommandLine)
        if ($Arguments.Count -lt 3) { return $false }
        if (-not ([System.IO.Path]::GetFullPath([string]$Arguments[0])).Equals($Executable, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        if ($null -eq $AllowedPowerShellExecutables) {
            $AllowedPowerShellExecutables = Get-TradingOSAllowedPowerShellExecutables
        }
        if (-not $AllowedPowerShellExecutables.Contains($Executable)) { return $false }
    } catch { return $false }

    $NoValueHostSwitches = @('-NoProfile', '-NonInteractive', '-NoLogo', '-Sta', '-Mta')
    $ValueHostSwitches = @('-ExecutionPolicy', '-WindowStyle', '-InputFormat', '-OutputFormat', '-ConfigurationName')
    for ($Index = 1; $Index -lt $Arguments.Count; $Index++) {
        $Token = [string]$Arguments[$Index]
        if ($Token -match '^(?:-File|-F)$') {
            if ($Index + 1 -ge $Arguments.Count -or -not [System.IO.Path]::IsPathRooted([string]$Arguments[$Index + 1])) { return $false }
            try { $CandidateScript = [System.IO.Path]::GetFullPath([string]$Arguments[$Index + 1]) } catch { return $false }
            return $CandidateScript.Equals($ExpectedScript, [System.StringComparison]::OrdinalIgnoreCase)
        }
        if ($Token -in $NoValueHostSwitches) { continue }
        if ($Token -in $ValueHostSwitches) {
            if ($Index + 1 -ge $Arguments.Count) { return $false }
            $Index++
            continue
        }
        # Unknown or abbreviated host switches (including /Command and -Com)
        # fail closed; after -Command all remaining tokens are command text.
        return $false
    }
    return $false
}

function Get-TradingOSRuntimeManifest {
    param([Parameter(Mandatory = $true)][string]$Root)

    $ManifestPath = Join-Path $Root "configs\TRADING_OS_RUNTIME_COMPONENTS.json"
    if (-not (Test-Path -LiteralPath $ManifestPath)) {
        throw "Missing runtime component manifest: $ManifestPath"
    }
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if (-not ($Manifest.schema_version -is [int]) -or [int]$Manifest.schema_version -ne 1 -or
        -not $Manifest.components -or [string]$Manifest.runtime_root_policy -ne "local_outside_google_drive" -or
        -not ($Manifest.live_trading_locked -is [bool]) -or -not [bool]$Manifest.live_trading_locked) {
        throw "Unsupported or empty runtime component manifest: $ManifestPath"
    }
    if ([System.IO.Path]::GetFullPath($Root) -match '\\My Drive(\\|$)') {
        throw "Runtime manifest policy forbids execution from Google Drive: $Root"
    }
    $SeenIds = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $SeenLocks = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $SeenStatuses = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $LogsRoot = [System.IO.Path]::GetFullPath((Join-Path $Root "logs")).TrimEnd('\') + '\'
    $InventoryComponents = @($Manifest.components) + @($Manifest.shutdown_only_components)
    foreach ($Component in $InventoryComponents) {
        $Id = [string]$Component.id
        $null = Assert-TradingOSRuntimeComponentId -ComponentId $Id
        if (-not $Id -or -not $SeenIds.Add($Id)) {
            throw "Duplicate or empty runtime component id: $Id"
        }
        foreach ($Property in @('script', 'lock_path', 'status_path', 'start_owner', 'required')) {
            if ($Component.PSObject.Properties.Name -notcontains $Property) { throw "Runtime component is missing ${Property}: $Id" }
        }
        if (-not ($Component.required -is [bool])) { throw "Runtime component required must be Boolean: $Id" }
        $IsShutdownOnly = @($Manifest.shutdown_only_components | Where-Object { [string]$_.id -eq $Id }).Count -eq 1
        if ($IsShutdownOnly) {
            if ([bool]$Component.required -or [string]$Component.start_owner -ne 'control_panel') { throw "Shutdown-only component must be optional and control_panel-owned: $Id" }
        } else {
            if ([string]$Component.start_owner -notin @('runtime', 'runtime_wrapper', 'bybit_watchdog')) { throw "Unsupported runtime component start_owner: $Id" }
            if (-not ($Component.trim_working_set -is [bool]) -or -not ($Component.default_sleep_seconds -is [int]) -or [int]$Component.default_sleep_seconds -lt 1 -or [int]$Component.default_sleep_seconds -gt 604800) {
                throw "Runtime component trim/sleep fields failed strict validation: $Id"
            }
        }
        $ScriptPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.script)
        $LockPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.lock_path)
        $StatusPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.status_path)
        if (-not $ScriptPath.EndsWith('.ps1', [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Runtime component script must be a PowerShell file: $Id"
        }
        if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) { throw "Runtime component script is missing: $Id ($ScriptPath)" }
        if (-not $LockPath.StartsWith($LogsRoot, [System.StringComparison]::OrdinalIgnoreCase) -or -not $LockPath.EndsWith('.lock.json', [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Runtime component lock must stay under Root\logs and end with .lock.json: $Id"
        }
        if (-not $SeenLocks.Add($LockPath)) {
            throw "Duplicate runtime component lock path: $LockPath"
        }
        if (-not $StatusPath.StartsWith($LogsRoot, [System.StringComparison]::OrdinalIgnoreCase) -or -not $StatusPath.EndsWith('.json', [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Runtime component status must stay under Root\logs and end with .json: $Id"
        }
        if (-not $SeenStatuses.Add($StatusPath)) {
            throw "Duplicate runtime component status path: $StatusPath"
        }
    }
    return $Manifest
}

function Resolve-TradingOSRuntimePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $RootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $Candidate = if ([System.IO.Path]::IsPathRooted($Path)) {
        [System.IO.Path]::GetFullPath($Path)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $RootFull ($Path -replace '/', '\')))
    }
    if (-not $Candidate.Equals($RootFull, [System.StringComparison]::OrdinalIgnoreCase) -and
        -not $Candidate.StartsWith(($RootFull + '\'), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Runtime path escapes the canonical root: $Path"
    }
    return $Candidate
}

function Get-TradingOSProcessSnapshot {
    $Rows = @{}
    foreach ($Process in @(Get-CimInstance Win32_Process -ErrorAction Stop)) {
        $Rows[[int]$Process.ProcessId] = $Process
    }
    if (-not $Rows.ContainsKey([int]$PID)) { throw "Win32_Process inventory did not contain the current process and is not trustworthy." }
    return $Rows
}

function Get-TradingOSControlPanelState {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [ValidateRange(1024, 65535)][int]$Port = 8765
    )

    $State = [ordered]@{
        decision = "missing_listener"
        listening = $false
        identity_valid = $false
        pid = $null
        candidate_pids = @()
        api_root_valid = $false
        command_line = $null
        process_creation_utc = $null
        executable_path = $null
        script_argument = $null
    }
    $CandidatePids = @()
    try {
        $CandidatePids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        $PrimaryError = $_.Exception.Message
        try {
            $NetstatPath = Join-Path $env:SystemRoot 'System32\netstat.exe'
            if (-not (Test-Path -LiteralPath $NetstatPath -PathType Leaf)) { throw 'netstat.exe is unavailable' }
            $NetstatRows = @(& $NetstatPath -ano -p tcp 2>&1)
            if ($LASTEXITCODE -ne 0) { throw "netstat exited with code $LASTEXITCODE" }
            $CandidatePids = @($NetstatRows | ForEach-Object {
                if ([string]$_ -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(?<pid>[0-9]+)\s*$") { [int]$Matches.pid }
            } | Sort-Object -Unique)
        } catch {
            throw "Control panel listener inventory failed closed (Get-NetTCPConnection: $PrimaryError; netstat: $($_.Exception.Message))"
        }
    }
    $State.candidate_pids = $CandidatePids
    $State.listening = $CandidatePids.Count -gt 0
    if (-not $State.listening) { return [pscustomobject]$State }

    try {
        $PanelStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 5 -ErrorAction Stop
        $State.api_root_valid = [System.IO.Path]::GetFullPath([string]$PanelStatus.root).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase)
    } catch {}
    if (-not $State.api_root_valid) {
        $State.decision = "listener_api_root_mismatch"
        return [pscustomobject]$State
    }

    $ExpectedScript = [System.IO.Path]::GetFullPath((Join-Path $Root "ops\control_panel\control_panel.py"))
    $LegacyScript = 'ops\control_panel\control_panel.py'
    $Snapshot = Get-TradingOSProcessSnapshot
    $Verified = New-Object System.Collections.Generic.List[object]
    foreach ($CandidatePid in $CandidatePids) {
        if (-not $Snapshot.ContainsKey([int]$CandidatePid)) { continue }
        $Process = $Snapshot[[int]$CandidatePid]
        $Arguments = @(Get-TradingOSCommandLineArguments -CommandLine ([string]$Process.CommandLine))
        if ($Arguments.Count -lt 4 -or [string]$Process.Name -notmatch '^python(?:w)?(?:\.exe)?$' -or -not $Process.ExecutablePath) { continue }
        try {
            $ExecutableMatches = ([System.IO.Path]::GetFullPath([string]$Arguments[0])).Equals([System.IO.Path]::GetFullPath([string]$Process.ExecutablePath), [System.StringComparison]::OrdinalIgnoreCase)
        } catch { $ExecutableMatches = $false }
        $ScriptArgument = [string]$Arguments[1]
        # The legacy relative entrypoint is accepted only in argv[1] and only
        # after /api/status proved that this server owns the canonical root.
        $ScriptMatches = $ScriptArgument.Equals($ExpectedScript, [System.StringComparison]::OrdinalIgnoreCase) -or
            $ScriptArgument.Equals($LegacyScript, [System.StringComparison]::OrdinalIgnoreCase)
        $PortMatches = $false
        for ($Index = 1; $Index -lt $Arguments.Count - 1; $Index++) {
            if ([string]$Arguments[$Index] -eq '--port' -and [string]$Arguments[$Index + 1] -eq [string]$Port) { $PortMatches = $true; break }
        }
        if ($ExecutableMatches -and $ScriptMatches -and $PortMatches) { $Verified.Add($Process) | Out-Null }
    }
    if ($Verified.Count -ne 1) {
        $State.decision = if ($Verified.Count -gt 1) { "ambiguous_verified_listeners" } else { "listener_process_identity_mismatch" }
        return [pscustomobject]$State
    }
    $State.decision = "running_verified"
    $State.identity_valid = $true
    $State.pid = [int]$Verified[0].ProcessId
    $State.command_line = [string]$Verified[0].CommandLine
    $State.executable_path = [string]$Verified[0].ExecutablePath
    $VerifiedArguments = @(Get-TradingOSCommandLineArguments -CommandLine ([string]$Verified[0].CommandLine))
    $State.script_argument = [string]$VerifiedArguments[1]
    try { $State.process_creation_utc = ([datetime]$Verified[0].CreationDate).ToUniversalTime().ToString("o") } catch { $State.process_creation_utc = [string]$Verified[0].CreationDate }
    return [pscustomobject]$State
}

function Get-TradingOSRuntimeComponentState {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$Component,
        [hashtable]$ProcessSnapshot,
        [System.Collections.Generic.HashSet[string]]$AllowedPowerShellExecutables
    )

    if (-not $ProcessSnapshot) {
        $ProcessSnapshot = Get-TradingOSProcessSnapshot
    }

    $LockPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.lock_path)
    $ScriptPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.script)
    $MatchingScriptPids = @($ProcessSnapshot.Values | Where-Object {
        Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath $ScriptPath -AllowedPowerShellExecutables $AllowedPowerShellExecutables
    } | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
    $State = [ordered]@{
        id = [string]$Component.id
        required = [bool]$Component.required
        script = [string]$Component.script
        lock_path = [string]$Component.lock_path
        status_path = [string]$Component.status_path
        default_sleep_seconds = [int]$Component.default_sleep_seconds
        trim_working_set = [bool]$Component.trim_working_set
        lock_present = Test-Path -LiteralPath $LockPath
        lock_valid = $false
        pid = $null
        process_alive = $false
        identity_valid = $false
        process_creation_utc = $null
        command_line = $null
        working_set_mb = 0.0
        private_mb = 0.0
        matching_script_pids = $MatchingScriptPids
        matching_script_process_count = $MatchingScriptPids.Count
        decision = "missing_lock"
    }

    if (-not $State.lock_present) {
        return [pscustomobject]$State
    }

    try {
        $Lock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        $PidValue = [int]$Lock.pid
        $State.pid = $PidValue
        $State.lock_valid = $PidValue -gt 0
    } catch {
        $State.decision = "invalid_lock"
        return [pscustomobject]$State
    }

    if (-not $State.lock_valid) {
        $State.decision = "invalid_lock"
        return [pscustomobject]$State
    }
    if (-not $ProcessSnapshot.ContainsKey([int]$State.pid)) {
        $State.decision = "stale_lock_dead_pid"
        return [pscustomobject]$State
    }

    $CimProcess = $ProcessSnapshot[[int]$State.pid]
    $State.process_alive = $true
    $State.command_line = [string]$CimProcess.CommandLine
    try {
        $State.process_creation_utc = ([datetime]$CimProcess.CreationDate).ToUniversalTime().ToString("o")
    } catch {
        $State.process_creation_utc = [string]$CimProcess.CreationDate
    }
    $State.identity_valid = Test-TradingOSManagedScriptProcess -CimProcess $CimProcess -ExpectedScriptPath $ScriptPath -AllowedPowerShellExecutables $AllowedPowerShellExecutables

    $NativeProcess = Get-Process -Id ([int]$State.pid) -ErrorAction SilentlyContinue
    if ($NativeProcess) {
        $State.working_set_mb = [math]::Round($NativeProcess.WorkingSet64 / 1MB, 1)
        $State.private_mb = [math]::Round($NativeProcess.PrivateMemorySize64 / 1MB, 1)
    }

    $State.decision = if ($State.identity_valid) { "running_verified" } else { "pid_identity_mismatch" }
    return [pscustomobject]$State
}

function Get-TradingOSRuntimeStates {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        $Manifest
    )

    if (-not $Manifest) {
        $Manifest = Get-TradingOSRuntimeManifest -Root $Root
    }
    $Snapshot = Get-TradingOSProcessSnapshot
    $AllowedPowerShellExecutables = Get-TradingOSAllowedPowerShellExecutables
    return @($Manifest.components | ForEach-Object {
        $Component = $_
        $State = Get-TradingOSRuntimeComponentState -Root $Root -Component $Component -ProcessSnapshot $Snapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
        $ScriptPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.script)
        $JobState = $null
        try {
            $JobState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId ([string]$Component.id) -ExpectedScriptPath $ScriptPath -ProcessSnapshot $Snapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
            $SingleExactRoot = $State.matching_script_process_count -eq 1 -and [int]$State.matching_script_pids[0] -eq [int]$State.pid
            $Contained = $State.decision -eq 'running_verified' -and $SingleExactRoot -and $JobState.decision -eq 'running_verified_job_contained' -and [int]$JobState.receipt.pid -eq [int]$State.pid
            $State | Add-Member -NotePropertyName job_decision -NotePropertyValue ([string]$JobState.decision) -Force
            $State | Add-Member -NotePropertyName job_contained -NotePropertyValue ([bool]$Contained) -Force
            $State | Add-Member -NotePropertyName ownership_decision -NotePropertyValue $(if ($Contained) { 'running_verified_job_contained' } elseif ($State.decision -eq 'running_verified' -and -not $SingleExactRoot) { 'blocked_duplicate_exact_processes' } else { "unowned_$($State.decision)_$($JobState.decision)" }) -Force
        } catch {
            $State | Add-Member -NotePropertyName job_decision -NotePropertyValue 'job_receipt_verification_failed' -Force
            $State | Add-Member -NotePropertyName job_contained -NotePropertyValue $false -Force
            $State | Add-Member -NotePropertyName ownership_decision -NotePropertyValue 'job_receipt_verification_failed' -Force
            $State | Add-Member -NotePropertyName ownership_error -NotePropertyValue $_.Exception.Message -Force
        } finally {
            if ($JobState -and $JobState.process) { try { $JobState.process.Dispose() } catch {} }
        }
        $State
    })
}

function Get-TradingOSRuntimeLaunchDisposition {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [string]$AttemptId = ""
    )

    if ($AttemptId) { $AttemptId = ([guid]$AttemptId).ToString() }
    $InventoryComponents = @($Manifest.components) + @($Manifest.shutdown_only_components)
    $Component = @($InventoryComponents | Where-Object { [string]$_.id -eq $ComponentId } | Select-Object -First 1)
    if (-not $Component) {
        throw "Unknown runtime component id: $ComponentId"
    }

    $Snapshot = Get-TradingOSProcessSnapshot
    $AllowedPowerShellExecutables = Get-TradingOSAllowedPowerShellExecutables
    $State = Get-TradingOSRuntimeComponentState -Root $Root -Component $Component[0] -ProcessSnapshot $Snapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
    $ScriptPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component[0].script)
    $MatchingScriptPids = @($Snapshot.Values | Where-Object {
        Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath $ScriptPath -AllowedPowerShellExecutables $AllowedPowerShellExecutables
    } | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
    $LockPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component[0].lock_path)
    $Result = [ordered]@{
        component = $ComponentId
        state_before = [string]$State.decision
        should_start = $false
        decision = "blocked_unknown_state"
        lock_action = "none"
        blocking_pids = $MatchingScriptPids
        pid = $State.pid
        quarantine_path = $null
        job_decision = 'not_checked'
        job_contained = $false
        attempt_id = $AttemptId
    }

    if (Test-TradingOSRuntimeShutdownRequested -Root $Root -AllowedAttemptId $AttemptId) {
        $Result.decision = "blocked_runtime_shutdown_requested"
        return [pscustomobject]$Result
    }

    if ($State.decision -eq "running_verified") {
        if ($MatchingScriptPids.Count -ne 1 -or [int]$MatchingScriptPids[0] -ne [int]$State.pid) {
            $Result.decision = 'blocked_duplicate_exact_processes'
            return [pscustomobject]$Result
        }
        $JobState = $null
        try {
            $JobState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ScriptPath -ProcessSnapshot $Snapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
            $Result.job_decision = [string]$JobState.decision
            $Result.job_contained = $JobState.decision -eq 'running_verified_job_contained' -and [int]$JobState.receipt.pid -eq [int]$State.pid
        } catch {
            $Result.job_decision = 'job_receipt_verification_failed'
        } finally {
            if ($JobState -and $JobState.process) { try { $JobState.process.Dispose() } catch {} }
        }
        if (-not $Result.job_contained) {
            $Result.decision = 'blocked_uncontained_legacy_runtime'
            return [pscustomobject]$Result
        }
        $Result.decision = "already_running_verified"
        return [pscustomobject]$Result
    }
    $RecoverablePidReuseLock = $false
    if ($State.decision -eq "pid_identity_mismatch") {
        $PidReuseJobState = $null
        try {
            $PidReuseJobState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ScriptPath -ProcessSnapshot $Snapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
            $Result.job_decision = [string]$PidReuseJobState.decision
            $RecoverablePidReuseLock = $MatchingScriptPids.Count -eq 0 -and $PidReuseJobState.decision -in @('missing_receipt', 'stale_receipt_process_absent', 'stale_receipt_pid_reused', 'reserved_receipt_no_active_job')
        } catch {
            $Result.job_decision = 'job_receipt_verification_failed'
        } finally {
            if ($PidReuseJobState -and $PidReuseJobState.process) { try { $PidReuseJobState.process.Dispose() } catch {} }
        }
        if (-not $RecoverablePidReuseLock) {
            $Result.decision = "blocked_pid_identity_mismatch"
            return [pscustomobject]$Result
        }
    }
    if ($MatchingScriptPids.Count -gt 0) {
        $Result.decision = "blocked_unowned_matching_process"
        return [pscustomobject]$Result
    }
    if ($State.decision -eq "missing_lock") {
        $Result.should_start = $true
        $Result.decision = "start_missing_lock_no_matching_process"
        if ($AttemptId) { Register-TradingOSRuntimeLaunchDisposition -Root $Root -AttemptId $AttemptId -Component $Component[0] -Disposition ([pscustomobject]$Result) | Out-Null }
        return [pscustomobject]$Result
    }
    if ($State.decision -in @("stale_lock_dead_pid", "invalid_lock") -or $RecoverablePidReuseLock) {
        $Stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssfffZ")
        $AttemptToken = if ($AttemptId) { ([guid]$AttemptId).ToString('N') } else { 'untracked' }
        $QuarantinePath = "$LockPath.quarantine.$AttemptToken.$Stamp.$([guid]::NewGuid().ToString('N'))"
        try {
            Move-TradingOSFileAtomic -TemporaryPath $LockPath -DestinationPath $QuarantinePath
            $Result.should_start = $true
            $Result.decision = if ($RecoverablePidReuseLock) { "start_after_pid_reuse_lock_quarantine" } else { "start_after_stale_lock_quarantine" }
            $Result.lock_action = "quarantined"
            $Result.quarantine_path = $QuarantinePath
            if ($AttemptId) { Register-TradingOSRuntimeLaunchDisposition -Root $Root -AttemptId $AttemptId -Component $Component[0] -Disposition ([pscustomobject]$Result) | Out-Null }
        } catch {
            $QuarantineError = $_.Exception.Message
            if ((Test-Path -LiteralPath $QuarantinePath) -and -not (Test-Path -LiteralPath $LockPath)) {
                try { Move-TradingOSFileAtomic -TemporaryPath $QuarantinePath -DestinationPath $LockPath } catch { throw "Stale lock quarantine registration failed and rollback could not restore the lock: $QuarantineError; $($_.Exception.Message)" }
            }
            $Result.should_start = $false
            $Result.decision = "blocked_stale_lock_quarantine_failed"
            $Result.lock_action = "quarantine_failed"
            $Result.quarantine_path = $null
            $Result['error'] = $QuarantineError
        }
        return [pscustomobject]$Result
    }

    return [pscustomobject]$Result
}

function Get-TradingOSDescendantProcessIds {
    param(
        [Parameter(Mandatory = $true)][int]$RootPid,
        [hashtable]$ProcessSnapshot
    )

    if (-not $ProcessSnapshot) {
        $ProcessSnapshot = Get-TradingOSProcessSnapshot
    }
    $ChildrenByParent = @{}
    foreach ($Row in $ProcessSnapshot.Values) {
        $ParentPid = [int]$Row.ParentProcessId
        if (-not $ChildrenByParent.ContainsKey($ParentPid)) {
            $ChildrenByParent[$ParentPid] = New-Object System.Collections.Generic.List[int]
        }
        $ChildrenByParent[$ParentPid].Add([int]$Row.ProcessId)
    }

    $Queue = New-Object System.Collections.Generic.Queue[int]
    $Seen = New-Object 'System.Collections.Generic.HashSet[int]'
    $Queue.Enqueue($RootPid)
    while ($Queue.Count -gt 0) {
        $Parent = $Queue.Dequeue()
        if (-not $ChildrenByParent.ContainsKey($Parent)) { continue }
        foreach ($Child in $ChildrenByParent[$Parent]) {
            if ($Seen.Add($Child)) {
                $Queue.Enqueue($Child)
            }
        }
    }
    return @($Seen)
}

function Get-TradingOSRuntimeMutexName {
    param([Parameter(Mandatory = $true)][string]$Root)

    $Bytes = [System.Text.Encoding]::UTF8.GetBytes(([System.IO.Path]::GetFullPath($Root)).ToLowerInvariant())
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Hash = -join ($Hasher.ComputeHash($Bytes)[0..7] | ForEach-Object { $_.ToString('x2') })
    } finally {
        $Hasher.Dispose()
    }
    return "Local\TradingOS_Runtime_Start_$Hash"
}

function Get-TradingOSAutostartMutexName {
    param([Parameter(Mandatory = $true)][string]$Root)

    $Bytes = [System.Text.Encoding]::UTF8.GetBytes(([System.IO.Path]::GetFullPath($Root)).ToLowerInvariant())
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Hash = -join ($Hasher.ComputeHash($Bytes)[0..7] | ForEach-Object { $_.ToString('x2') })
    } finally {
        $Hasher.Dispose()
    }
    return "Local\TradingOS_Autostart_Mutation_$Hash"
}

function Test-TradingOSRuntimeStartInProgress {
    param([Parameter(Mandatory = $true)][string]$Root)

    $Mutex = New-Object System.Threading.Mutex($false, (Get-TradingOSRuntimeMutexName -Root $Root))
    $Acquired = $false
    try {
        try {
            $Acquired = $Mutex.WaitOne(0)
        } catch [System.Threading.AbandonedMutexException] {
            $Acquired = $true
        }
        return -not $Acquired
    } finally {
        if ($Acquired) {
            try { $Mutex.ReleaseMutex() } catch {}
        }
        $Mutex.Dispose()
    }
}

if (-not ("TradingOSRuntimeJobNative" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class TradingOSRuntimeJobNative {
    private const UInt32 CREATE_SUSPENDED = 0x00000004;
    private const UInt32 CREATE_NO_WINDOW = 0x08000000;
    private const UInt32 EXTENDED_STARTUPINFO_PRESENT = 0x00080000;
    private const UInt32 STARTF_USESHOWWINDOW = 0x00000001;
    private const UInt32 STARTF_USESTDHANDLES = 0x00000100;
    private const UInt16 SW_HIDE = 0;
    private const UInt32 GENERIC_READ = 0x80000000;
    private const UInt32 GENERIC_WRITE = 0x40000000;
    private const UInt32 FILE_SHARE_ALL = 0x00000007;
    private const UInt32 CREATE_ALWAYS = 2;
    private const UInt32 OPEN_EXISTING = 3;
    private const UInt32 FILE_ATTRIBUTE_NORMAL = 0x00000080;
    private const UInt32 JOB_OBJECT_QUERY = 0x0004;
    private const UInt32 JOB_OBJECT_TERMINATE = 0x0008;
    private const UInt32 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    private const Int32 ERROR_FILE_NOT_FOUND = 2;
    private const Int32 ERROR_ALREADY_EXISTS = 183;
    private static readonly IntPtr PROC_THREAD_ATTRIBUTE_HANDLE_LIST = new IntPtr(0x00020002);
    private static readonly IntPtr PROC_THREAD_ATTRIBUTE_JOB_LIST = new IntPtr(0x0002000D);
    private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);
    private static readonly List<IntPtr> HeldDirectJobHandles = new List<IntPtr>();

    [StructLayout(LayoutKind.Sequential)]
    private struct SECURITY_ATTRIBUTES {
        public Int32 nLength;
        public IntPtr lpSecurityDescriptor;
        public Int32 bInheritHandle;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO {
        public Int32 cb;
        public IntPtr lpReserved;
        public IntPtr lpDesktop;
        public IntPtr lpTitle;
        public UInt32 dwX;
        public UInt32 dwY;
        public UInt32 dwXSize;
        public UInt32 dwYSize;
        public UInt32 dwXCountChars;
        public UInt32 dwYCountChars;
        public UInt32 dwFillAttribute;
        public UInt32 dwFlags;
        public UInt16 wShowWindow;
        public UInt16 cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct STARTUPINFOEX {
        public STARTUPINFO StartupInfo;
        public IntPtr lpAttributeList;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION {
        public IntPtr hProcess;
        public IntPtr hThread;
        public UInt32 dwProcessId;
        public UInt32 dwThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public UIntPtr Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_ACCOUNTING_INFORMATION {
        public Int64 TotalUserTime;
        public Int64 TotalKernelTime;
        public Int64 ThisPeriodTotalUserTime;
        public Int64 ThisPeriodTotalKernelTime;
        public UInt32 TotalPageFaultCount;
        public UInt32 TotalProcesses;
        public UInt32 ActiveProcesses;
        public UInt32 TotalTerminatedProcesses;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObjectW(ref SECURITY_ATTRIBUTES attributes, string name);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr OpenJobObjectW(UInt32 desiredAccess, bool inheritHandle, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, Int32 infoClass, ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION info, UInt32 length);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool QueryInformationJobObject(IntPtr job, Int32 infoClass, ref JOBOBJECT_BASIC_ACCOUNTING_INFORMATION info, UInt32 length, IntPtr returnLength);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateJobObject(IntPtr job, UInt32 exitCode);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateProcessW(string applicationName, StringBuilder commandLine, IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, UInt32 creationFlags, IntPtr environment, string currentDirectory, ref STARTUPINFOEX startupInfo, out PROCESS_INFORMATION processInformation);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFileW(string fileName, UInt32 desiredAccess, UInt32 shareMode, ref SECURITY_ATTRIBUTES attributes, UInt32 creationDisposition, UInt32 flags, IntPtr templateFile);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern UInt32 ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateProcess(IntPtr process, UInt32 exitCode);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool InitializeProcThreadAttributeList(IntPtr attributeList, Int32 attributeCount, Int32 flags, ref IntPtr size);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool UpdateProcThreadAttribute(IntPtr attributeList, UInt32 flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previousValue, IntPtr returnSize);
    [DllImport("kernel32.dll")]
    private static extern void DeleteProcThreadAttributeList(IntPtr attributeList);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern UInt32 WaitForSingleObject(IntPtr handle, UInt32 milliseconds);
    [DllImport("kernel32.dll")]
    private static extern IntPtr GetCurrentProcess();

    private static void ThrowLast(string operation) {
        throw new Win32Exception(Marshal.GetLastWin32Error(), operation);
    }

    public static string QuoteArgument(string value) {
        if (value == null) return "\"\"";
        if (value.Length > 0 && value.IndexOfAny(new char[] { ' ', '\t', '\n', '\v', '"' }) < 0) return value;
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') {
                result.Append('\\', slashes * 2 + 1);
                result.Append('"');
                slashes = 0;
                continue;
            }
            result.Append('\\', slashes);
            slashes = 0;
            result.Append(c);
        }
        result.Append('\\', slashes * 2);
        result.Append('"');
        return result.ToString();
    }

    public sealed class SuspendedJobProcess : IDisposable {
        private IntPtr processHandle;
        private IntPtr threadHandle;
        private IntPtr jobHandle;
        private bool resumed;
        private bool terminated;
        public UInt32 ProcessId { get; private set; }
        public string JobName { get; private set; }

        internal SuspendedJobProcess(IntPtr process, IntPtr thread, IntPtr job, UInt32 pid, string name) {
            processHandle = process;
            threadHandle = thread;
            jobHandle = job;
            ProcessId = pid;
            JobName = name;
        }

        public void Resume() {
            if (terminated) throw new InvalidOperationException("Cannot resume a terminated runtime process.");
            if (resumed) throw new InvalidOperationException("Runtime process was already resumed.");
            if (threadHandle == IntPtr.Zero || ResumeThread(threadHandle) == UInt32.MaxValue) ThrowLast("ResumeThread");
            resumed = true;
            CloseHandle(threadHandle);
            threadHandle = IntPtr.Zero;
        }

        public void Terminate(UInt32 exitCode) {
            if (terminated) return;
            bool terminatedJob = jobHandle != IntPtr.Zero && TerminateJobObject(jobHandle, exitCode);
            if (!terminatedJob && processHandle != IntPtr.Zero && !TerminateProcess(processHandle, exitCode)) ThrowLast("Terminate runtime job/process");
            if (processHandle != IntPtr.Zero) WaitForSingleObject(processHandle, 10000);
            terminated = true;
        }

        public void Dispose() {
            try {
                if (!resumed && !terminated) Terminate(1);
            } finally {
                if (threadHandle != IntPtr.Zero) { CloseHandle(threadHandle); threadHandle = IntPtr.Zero; }
                if (processHandle != IntPtr.Zero) { CloseHandle(processHandle); processHandle = IntPtr.Zero; }
                if (jobHandle != IntPtr.Zero) { CloseHandle(jobHandle); jobHandle = IntPtr.Zero; }
            }
        }
    }

    public static SuspendedJobProcess CreateSuspendedInJob(string applicationPath, string argumentLine, string workingDirectory, string stdoutPath, string stderrPath, string jobName) {
        SECURITY_ATTRIBUTES jobAttributes = new SECURITY_ATTRIBUTES();
        jobAttributes.nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES));
        // The child keeps one inherited job handle alive after the launcher exits.
        jobAttributes.bInheritHandle = 1;
        SECURITY_ATTRIBUTES inheritable = new SECURITY_ATTRIBUTES();
        inheritable.nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES));
        inheritable.bInheritHandle = 1;
        IntPtr job = IntPtr.Zero;
        IntPtr stdoutHandle = IntPtr.Zero;
        IntPtr stderrHandle = IntPtr.Zero;
        IntPtr stdinHandle = IntPtr.Zero;
        IntPtr attributeList = IntPtr.Zero;
        IntPtr handleList = IntPtr.Zero;
        PROCESS_INFORMATION pi = new PROCESS_INFORMATION();
        bool processCreated = false;
        try {
            job = CreateJobObjectW(ref jobAttributes, jobName);
            if (job == IntPtr.Zero) ThrowLast("CreateJobObjectW");
            if (Marshal.GetLastWin32Error() == ERROR_ALREADY_EXISTS) throw new Win32Exception(ERROR_ALREADY_EXISTS, "Runtime job name already exists.");

            string outTarget = String.IsNullOrEmpty(stdoutPath) ? "NUL" : stdoutPath;
            string errTarget = String.IsNullOrEmpty(stderrPath) ? "NUL" : stderrPath;
            stdoutHandle = CreateFileW(outTarget, GENERIC_WRITE, FILE_SHARE_ALL, ref inheritable, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, IntPtr.Zero);
            if (stdoutHandle == INVALID_HANDLE_VALUE) ThrowLast("CreateFileW(stdout)");
            stderrHandle = CreateFileW(errTarget, GENERIC_WRITE, FILE_SHARE_ALL, ref inheritable, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, IntPtr.Zero);
            if (stderrHandle == INVALID_HANDLE_VALUE) ThrowLast("CreateFileW(stderr)");
            stdinHandle = CreateFileW("NUL", GENERIC_READ, FILE_SHARE_ALL, ref inheritable, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, IntPtr.Zero);
            if (stdinHandle == INVALID_HANDLE_VALUE) ThrowLast("CreateFileW(stdin)");

            IntPtr attributeBytes = IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref attributeBytes);
            attributeList = Marshal.AllocHGlobal(attributeBytes);
            if (!InitializeProcThreadAttributeList(attributeList, 2, 0, ref attributeBytes)) ThrowLast("InitializeProcThreadAttributeList");
            handleList = Marshal.AllocHGlobal(IntPtr.Size * 4);
            Marshal.WriteIntPtr(handleList, 0, stdinHandle);
            Marshal.WriteIntPtr(handleList, IntPtr.Size, stdoutHandle);
            Marshal.WriteIntPtr(handleList, IntPtr.Size * 2, stderrHandle);
            Marshal.WriteIntPtr(handleList, IntPtr.Size * 3, job);
            if (!UpdateProcThreadAttribute(attributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handleList, new IntPtr(IntPtr.Size * 4), IntPtr.Zero, IntPtr.Zero)) ThrowLast("UpdateProcThreadAttribute(HANDLE_LIST)");
            IntPtr jobList = IntPtr.Add(handleList, IntPtr.Size * 3);
            if (!UpdateProcThreadAttribute(attributeList, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST, jobList, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero)) ThrowLast("UpdateProcThreadAttribute(JOB_LIST)");

            STARTUPINFOEX startup = new STARTUPINFOEX();
            startup.StartupInfo.cb = Marshal.SizeOf(typeof(STARTUPINFOEX));
            startup.StartupInfo.dwFlags = STARTF_USESHOWWINDOW | STARTF_USESTDHANDLES;
            startup.StartupInfo.wShowWindow = SW_HIDE;
            startup.StartupInfo.hStdInput = stdinHandle;
            startup.StartupInfo.hStdOutput = stdoutHandle;
            startup.StartupInfo.hStdError = stderrHandle;
            startup.lpAttributeList = attributeList;
            string command = QuoteArgument(applicationPath) + (String.IsNullOrEmpty(argumentLine) ? "" : " " + argumentLine);
            if (!CreateProcessW(applicationPath, new StringBuilder(command), IntPtr.Zero, IntPtr.Zero, true, CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT, IntPtr.Zero, workingDirectory, ref startup, out pi)) ThrowLast("CreateProcessW");
            processCreated = true;
            bool atomicallyAssigned;
            if (!IsProcessInJob(pi.hProcess, job, out atomicallyAssigned)) ThrowLast("IsProcessInJob(post-create)");
            if (!atomicallyAssigned) throw new InvalidOperationException("Process was not atomically assigned to the runtime job.");
            SuspendedJobProcess result = new SuspendedJobProcess(pi.hProcess, pi.hThread, job, pi.dwProcessId, jobName);
            pi.hProcess = IntPtr.Zero;
            pi.hThread = IntPtr.Zero;
            job = IntPtr.Zero;
            return result;
        } catch {
            if (processCreated && pi.hProcess != IntPtr.Zero) {
                bool killed = job != IntPtr.Zero && TerminateJobObject(job, 1);
                if (!killed) TerminateProcess(pi.hProcess, 1);
                WaitForSingleObject(pi.hProcess, 10000);
            }
            throw;
        } finally {
            if (pi.hThread != IntPtr.Zero) CloseHandle(pi.hThread);
            if (pi.hProcess != IntPtr.Zero) CloseHandle(pi.hProcess);
            if (handleList != IntPtr.Zero) Marshal.FreeHGlobal(handleList);
            if (attributeList != IntPtr.Zero) { DeleteProcThreadAttributeList(attributeList); Marshal.FreeHGlobal(attributeList); }
            if (stdinHandle != IntPtr.Zero && stdinHandle != INVALID_HANDLE_VALUE) CloseHandle(stdinHandle);
            if (stderrHandle != IntPtr.Zero && stderrHandle != INVALID_HANDLE_VALUE) CloseHandle(stderrHandle);
            if (stdoutHandle != IntPtr.Zero && stdoutHandle != INVALID_HANDLE_VALUE) CloseHandle(stdoutHandle);
            if (job != IntPtr.Zero) CloseHandle(job);
        }
    }

    public static bool IsCurrentProcessInAnyJob() {
        bool result;
        if (!IsProcessInJob(GetCurrentProcess(), IntPtr.Zero, out result)) ThrowLast("IsProcessInJob(current)");
        return result;
    }

    public static void JoinCurrentProcess(string jobName) {
        if (IsCurrentProcessInAnyJob()) return;
        SECURITY_ATTRIBUTES attributes = new SECURITY_ATTRIBUTES();
        attributes.nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES));
        IntPtr job = CreateJobObjectW(ref attributes, jobName);
        if (job == IntPtr.Zero) ThrowLast("CreateJobObjectW(current)");
        try {
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if (!SetInformationJobObject(job, 9, ref limits, (UInt32)Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION)))) ThrowLast("SetInformationJobObject");
            if (!AssignProcessToJobObject(job, GetCurrentProcess())) ThrowLast("AssignProcessToJobObject(current)");
            lock (HeldDirectJobHandles) { HeldDirectJobHandles.Add(job); }
            job = IntPtr.Zero;
        } finally {
            if (job != IntPtr.Zero) CloseHandle(job);
        }
    }

    public static bool IsProcessInNamedJob(IntPtr processHandle, string jobName) {
        IntPtr job = OpenJobObjectW(JOB_OBJECT_QUERY, false, jobName);
        if (job == IntPtr.Zero) {
            int error = Marshal.GetLastWin32Error();
            if (error == ERROR_FILE_NOT_FOUND) return false;
            throw new Win32Exception(error, "OpenJobObjectW(query membership)");
        }
        try {
            bool result;
            if (!IsProcessInJob(processHandle, job, out result)) ThrowLast("IsProcessInJob(named)");
            return result;
        } finally { CloseHandle(job); }
    }

    public static Int32 GetActiveProcessCount(string jobName) {
        IntPtr job = OpenJobObjectW(JOB_OBJECT_QUERY, false, jobName);
        if (job == IntPtr.Zero) {
            int error = Marshal.GetLastWin32Error();
            if (error == ERROR_FILE_NOT_FOUND) return -1;
            throw new Win32Exception(error, "OpenJobObjectW(query accounting)");
        }
        try {
            JOBOBJECT_BASIC_ACCOUNTING_INFORMATION info = new JOBOBJECT_BASIC_ACCOUNTING_INFORMATION();
            if (!QueryInformationJobObject(job, 1, ref info, (UInt32)Marshal.SizeOf(typeof(JOBOBJECT_BASIC_ACCOUNTING_INFORMATION)), IntPtr.Zero)) ThrowLast("QueryInformationJobObject");
            return (Int32)info.ActiveProcesses;
        } finally { CloseHandle(job); }
    }

    public static bool TerminateNamedJob(string jobName, UInt32 exitCode) {
        IntPtr job = OpenJobObjectW(JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE, false, jobName);
        if (job == IntPtr.Zero) {
            int error = Marshal.GetLastWin32Error();
            if (error == ERROR_FILE_NOT_FOUND) return false;
            throw new Win32Exception(error, "OpenJobObjectW(terminate)");
        }
        try {
            if (!TerminateJobObject(job, exitCode)) ThrowLast("TerminateJobObject");
            return true;
        } finally { CloseHandle(job); }
    }
}
"@ -ErrorAction Stop
}

function Assert-TradingOSRuntimeComponentId {
    param([Parameter(Mandatory = $true)][string]$ComponentId)
    if ($ComponentId -notmatch '^[A-Za-z0-9][A-Za-z0-9_]{0,95}$') { throw "Invalid runtime component id: $ComponentId" }
    return $ComponentId
}

function Get-TradingOSRuntimeJobRootHash {
    param([Parameter(Mandatory = $true)][string]$Root)
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes(([System.IO.Path]::GetFullPath($Root)).ToLowerInvariant())
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try { return -join ($Hasher.ComputeHash($Bytes)[0..7] | ForEach-Object { $_.ToString('x2') }) } finally { $Hasher.Dispose() }
}

function Get-TradingOSRuntimeJobReceiptPath {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$ComponentId)
    $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId $ComponentId
    return Join-Path ([System.IO.Path]::GetFullPath($Root)) "logs\runtime_jobs\$ComponentId.json"
}

function Get-TradingOSRuntimeJobName {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [string]$Generation = ""
    )
    $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId $ComponentId
    if (-not $Generation) { $Generation = [guid]::NewGuid().ToString('N') }
    if ($Generation -notmatch '^[0-9a-f]{32}$') { throw "Invalid runtime job generation." }
    return "Local\TradingOS_Runtime_Job_$(Get-TradingOSRuntimeJobRootHash -Root $Root)_${ComponentId}_$Generation"
}

function Get-TradingOSRuntimeAttemptDirectory {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$AttemptId)
    $AttemptToken = ([guid]$AttemptId).ToString('N')
    return Join-Path ([System.IO.Path]::GetFullPath($Root)) "logs\runtime_attempts\$AttemptToken"
}

function Write-TradingOSJsonFileCreateNew {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$Payload, [int]$Depth = 8)
    $Parent = Split-Path $Path -Parent
    if ($Parent) { New-Item -ItemType Directory -Force -Path $Parent | Out-Null }
    $Json = $Payload | ConvertTo-Json -Depth $Depth
    $Stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    try {
        $Writer = New-Object System.IO.StreamWriter($Stream, (New-Object System.Text.UTF8Encoding($false)))
        try { $Writer.Write($Json); $Writer.Flush(); $Stream.Flush($true) } finally { $Writer.Dispose() }
    } finally {
        if ($Stream) { $Stream.Dispose() }
    }
}

function Get-TradingOSCurrentProcessCreationUtc {
    $Current = Get-Process -Id $PID -ErrorAction Stop
    try { return $Current.StartTime.ToUniversalTime().ToString('o') } finally { $Current.Dispose() }
}

function Enter-TradingOSRuntimeLaunchAttempt {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AttemptId,
        [switch]$NewInvocation
    )
    $AttemptId = ([guid]$AttemptId).ToString()
    if ([guid]$AttemptId -eq [guid]::Empty) { throw 'Runtime launch attempt id cannot be empty.' }
    $CanonicalRoot = [System.IO.Path]::GetFullPath($Root)
    $AttemptDir = Get-TradingOSRuntimeAttemptDirectory -Root $Root -AttemptId $AttemptId
    $ReservationPath = Join-Path $AttemptDir 'reservation.json'
    $OwnerCreation = Get-TradingOSCurrentProcessCreationUtc
    if (Test-Path -LiteralPath $ReservationPath) {
        $Existing = Get-Content -LiteralPath $ReservationPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $SameOwner = ($Existing.owner_pid -is [int]) -and [int]$Existing.owner_pid -eq $PID -and [string]$Existing.owner_process_creation_utc -eq $OwnerCreation
        try {
            $InvocationGuid = [guid][string]$Existing.invocation_id
            $null = [datetimeoffset]::ParseExact([string]$Existing.generated_at, 'o', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
            $null = [datetimeoffset]::ParseExact([string]$Existing.owner_process_creation_utc, 'o', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
            $Valid = ($Existing.schema_version -is [int]) -and [int]$Existing.schema_version -eq 1 -and
            ([guid][string]$Existing.attempt_id).ToString() -eq $AttemptId -and
            $InvocationGuid -ne [guid]::Empty -and ($Existing.session_id -is [int]) -and [int]$Existing.session_id -ge 0 -and
            [System.IO.Path]::GetFullPath([string]$Existing.root).Equals($CanonicalRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
            [string]$Existing.state -in @('reserved', 'committed') -and
            ($Existing.live_trading_locked -is [bool]) -and [bool]$Existing.live_trading_locked -and
            ($Existing.can_trade -is [bool]) -and -not [bool]$Existing.can_trade
            if ([string]$Existing.state -eq 'committed') {
                $null = [datetimeoffset]::ParseExact([string]$Existing.committed_at, 'o', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
                $Valid = $Valid -and ($Existing.journal_count -is [int]) -and [int]$Existing.journal_count -ge 0
            }
        } catch { $Valid = $false }
        if (-not $Valid) { throw "Runtime launch attempt reservation failed strict validation: $ReservationPath" }
        if ($NewInvocation) { throw "Runtime launch attempt id has already been used and cannot be replayed: $AttemptId" }
        if (-not $SameOwner) { throw "Runtime launch attempt id was already reserved by another invocation: $AttemptId" }
        return $Existing
    }

    $ReceiptDir = Join-Path $CanonicalRoot 'logs\runtime_jobs'
    if (Test-Path -LiteralPath $ReceiptDir) {
        foreach ($ReceiptFile in @(Get-ChildItem -LiteralPath $ReceiptDir -Filter '*.json' -File -ErrorAction Stop)) {
            try {
                $Raw = Get-Content -LiteralPath $ReceiptFile.FullName -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
                if (([guid][string]$Raw.attempt_id).ToString() -eq $AttemptId) { throw "Runtime launch attempt id is already present in a live/stale receipt: $($ReceiptFile.Name)" }
            } catch {
                throw "Runtime launch attempt receipt inventory failed closed for $($ReceiptFile.Name): $($_.Exception.Message)"
            }
        }
    }
    $Reservation = [ordered]@{
        schema_version = 1
        generated_at = (Get-Date).ToUniversalTime().ToString('o')
        attempt_id = $AttemptId
        invocation_id = [guid]::NewGuid().ToString()
        root = $CanonicalRoot
        owner_pid = [int]$PID
        owner_process_creation_utc = $OwnerCreation
        session_id = $(
            $OwnerProcess = Get-Process -Id $PID -ErrorAction Stop
            try { [int]$OwnerProcess.SessionId } finally { $OwnerProcess.Dispose() }
        )
        state = 'reserved'
        live_trading_locked = $true
        can_trade = $false
    }
    try { Write-TradingOSJsonFileCreateNew -Path $ReservationPath -Payload $Reservation -Depth 6 } catch [System.IO.IOException] {
        throw "Runtime launch attempt reservation raced with another invocation: $AttemptId"
    }
    return [pscustomobject]$Reservation
}

function Get-TradingOSRuntimeAttemptJournalPath {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$AttemptId, [Parameter(Mandatory = $true)][string]$ComponentId)
    $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId $ComponentId
    return Join-Path (Get-TradingOSRuntimeAttemptDirectory -Root $Root -AttemptId $AttemptId) "$ComponentId.json"
}

function Register-TradingOSRuntimeLaunchDisposition {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AttemptId,
        [Parameter(Mandatory = $true)]$Component,
        [Parameter(Mandatory = $true)]$Disposition
    )
    $Reservation = Enter-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $AttemptId
    if ([string]$Reservation.state -eq 'committed') { throw "Refusing to register work against a committed runtime launch attempt: $AttemptId" }
    $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId ([string]$Component.id)
    $JournalPath = Get-TradingOSRuntimeAttemptJournalPath -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
    if (Test-Path -LiteralPath $JournalPath) { return Read-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId }
    $Journal = [ordered]@{
        schema_version = 1
        generated_at = (Get-Date).ToUniversalTime().ToString('o')
        updated_at = (Get-Date).ToUniversalTime().ToString('o')
        attempt_id = ([guid]$AttemptId).ToString()
        invocation_id = [string]$Reservation.invocation_id
        root = [System.IO.Path]::GetFullPath($Root)
        component = $ComponentId
        expected_script_path = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.script)
        lock_path = if ([string]$Component.lock_path) { Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component.lock_path) } else { $null }
        disposition_decision = [string]$Disposition.decision
        state_before = [string]$Disposition.state_before
        quarantine_path = if ($Disposition.quarantine_path) { [string]$Disposition.quarantine_path } else { $null }
        job_name = $null
        pid = 0
        process_creation_utc = $null
        state = 'disposition_reserved'
        live_trading_locked = $true
        can_trade = $false
    }
    try { Write-TradingOSJsonFileCreateNew -Path $JournalPath -Payload $Journal -Depth 8 } catch [System.IO.IOException] {
        $Existing = Read-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
        if ([string]$Existing.invocation_id -ne [string]$Reservation.invocation_id) { throw "Runtime attempt component journal belongs to another invocation: $ComponentId" }
        return $Existing
    }
    return [pscustomobject]$Journal
}

function Update-TradingOSRuntimeAttemptJournal {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AttemptId,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [Parameter(Mandatory = $true)][string]$State,
        [string]$JobName = '',
        [int]$ProcessId = 0,
        [string]$ProcessCreationUtc = '',
        [string]$ErrorMessage = '',
        [switch]$AllowCommittedReservation
    )
    $Reservation = Enter-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $AttemptId
    if ([string]$Reservation.state -eq 'committed' -and -not $AllowCommittedReservation) { throw "Refusing to mutate a committed runtime launch attempt: $AttemptId" }
    $Path = Get-TradingOSRuntimeAttemptJournalPath -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
    if (-not (Test-Path -LiteralPath $Path)) { throw "Missing runtime attempt component journal: $ComponentId" }
    $Journal = Read-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
    $Journal.state = $State
    $Journal.updated_at = (Get-Date).ToUniversalTime().ToString('o')
    if ($JobName) { $Journal.job_name = $JobName }
    if ($ProcessId -gt 0) { $Journal.pid = [int]$ProcessId }
    if ($ProcessCreationUtc) { $Journal.process_creation_utc = $ProcessCreationUtc }
    if ($ErrorMessage) { $Journal | Add-Member -NotePropertyName error -NotePropertyValue $ErrorMessage -Force }
    $Journal.PSObject.Properties.Remove('validated_lock_path')
    $Journal.PSObject.Properties.Remove('validated_receipt_path')
    Write-TradingOSJsonFileAtomic -Path $Path -Payload $Journal -Depth 8
    return $Journal
}

function Read-TradingOSRuntimeAttemptJournal {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AttemptId,
        [Parameter(Mandatory = $true)][string]$ComponentId
    )
    $AttemptId = ([guid]$AttemptId).ToString()
    $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId $ComponentId
    $Reservation = Enter-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $AttemptId
    $Path = Get-TradingOSRuntimeAttemptJournalPath -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing runtime attempt component journal: $ComponentId" }
    try { $Journal = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch { throw "Invalid runtime attempt component journal: $Path" }
    $CanonicalRoot = [System.IO.Path]::GetFullPath($Root)
    $Manifest = Get-TradingOSRuntimeManifest -Root $Root
    $Inventory = @($Manifest.components) + @($Manifest.shutdown_only_components)
    $Component = @($Inventory | Where-Object { [string]$_.id -eq $ComponentId } | Select-Object -First 1)
    if ($Component) {
        $ExpectedScript = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component[0].script)
        $ExpectedLock = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component[0].lock_path)
    } elseif ($ComponentId -match '^control_panel_(?<port>[0-9]{1,5})$' -and [int]$Matches.port -ge 1024 -and [int]$Matches.port -le 65535) {
        $ExpectedScript = [System.IO.Path]::GetFullPath((Join-Path $Root 'ops\control_panel\control_panel.py'))
        $ExpectedLock = $null
    } else { throw "Attempt journal references an unmanaged component: $ComponentId" }
    $ReceiptPath = Get-TradingOSRuntimeJobReceiptPath -Root $Root -ComponentId $ComponentId
    $AttemptToken = ([guid]$AttemptId).ToString('N')
    $JobPattern = '^Local\\TradingOS_Runtime_Job_' + [regex]::Escape((Get-TradingOSRuntimeJobRootHash -Root $Root)) + '_' + [regex]::Escape($ComponentId) + '_[0-9a-f]{32}$'
    try {
        $Generated = [datetimeoffset]::ParseExact([string]$Journal.generated_at, 'o', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
        $Updated = [datetimeoffset]::ParseExact([string]$Journal.updated_at, 'o', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
        $Valid = ($Journal.schema_version -is [int]) -and [int]$Journal.schema_version -eq 1 -and
            ($Journal.pid -is [int]) -and [int]$Journal.pid -ge 0 -and
            ($Journal.live_trading_locked -is [bool]) -and [bool]$Journal.live_trading_locked -and
            ($Journal.can_trade -is [bool]) -and -not [bool]$Journal.can_trade -and
            [string]$Journal.component -ceq $ComponentId -and ([guid][string]$Journal.attempt_id).ToString() -eq $AttemptId -and
            [string]$Journal.invocation_id -ceq [string]$Reservation.invocation_id -and
            [System.IO.Path]::GetFullPath([string]$Journal.root).Equals($CanonicalRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
            [System.IO.Path]::GetFullPath([string]$Journal.expected_script_path).Equals($ExpectedScript, [System.StringComparison]::OrdinalIgnoreCase) -and
            [string]$Journal.state -in @('disposition_reserved', 'native_launch_reserved', 'suspended_assigned_receipted', 'running_verified_job_contained', 'launch_failed_cleanup_degraded', 'rolled_back', 'committed') -and
            $Generated.Year -ge 2020 -and $Updated.Year -ge 2020
        if ($ExpectedLock) { $Valid = $Valid -and [System.IO.Path]::GetFullPath([string]$Journal.lock_path).Equals($ExpectedLock, [System.StringComparison]::OrdinalIgnoreCase) }
        else { $Valid = $Valid -and [string]::IsNullOrEmpty([string]$Journal.lock_path) }
        if ([string]$Journal.job_name) { $Valid = $Valid -and [string]$Journal.job_name -match $JobPattern }
        if ([int]$Journal.pid -gt 0) {
            $null = [datetimeoffset]::ParseExact([string]$Journal.process_creation_utc, 'o', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
        }
        if ([string]$Journal.quarantine_path) {
            $Valid = $Valid -and $ExpectedLock -and [System.IO.Path]::GetFullPath([string]$Journal.quarantine_path).StartsWith("$ExpectedLock.quarantine.$AttemptToken.", [System.StringComparison]::OrdinalIgnoreCase)
        }
        if ([string]$Journal.stale_receipt_quarantine_path) {
            $Valid = $Valid -and [System.IO.Path]::GetFullPath([string]$Journal.stale_receipt_quarantine_path).StartsWith("$ReceiptPath.stale.$AttemptToken.", [System.StringComparison]::OrdinalIgnoreCase)
        }
    } catch { $Valid = $false }
    if (-not $Valid) { throw "Runtime attempt component journal failed strict validation: $Path" }
    $Journal | Add-Member -NotePropertyName validated_lock_path -NotePropertyValue $ExpectedLock -Force
    $Journal | Add-Member -NotePropertyName validated_receipt_path -NotePropertyValue $ReceiptPath -Force
    return $Journal
}

function Complete-TradingOSRuntimeLaunchAttempt {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AttemptId
    )
    $AttemptId = ([guid]$AttemptId).ToString()
    $Reservation = Enter-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $AttemptId
    if ([string]$Reservation.state -eq 'committed') {
        return [pscustomobject]@{
            success = $true
            decision = 'already_committed'
            attempt_id = $AttemptId
            invocation_id = [string]$Reservation.invocation_id
            journal_count = [int]$Reservation.journal_count
            warnings = @()
        }
    }
    if ([string]$Reservation.state -ne 'reserved') { throw "Runtime launch attempt is not committable: $AttemptId ($($Reservation.state))" }

    $AttemptDir = Get-TradingOSRuntimeAttemptDirectory -Root $Root -AttemptId $AttemptId
    $JournalFiles = @(Get-ChildItem -LiteralPath $AttemptDir -Filter '*.json' -File -ErrorAction Stop | Where-Object { $_.Name -ne 'reservation.json' })
    $Journals = New-Object System.Collections.Generic.List[object]
    $CommitSnapshot = Get-TradingOSProcessSnapshot
    $AllowedPowerShellExecutables = Get-TradingOSAllowedPowerShellExecutables
    foreach ($Path in $JournalFiles) {
        $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId ([System.IO.Path]::GetFileNameWithoutExtension($Path.Name))
        $Journal = Read-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
        if ([string]$Journal.state -eq 'disposition_reserved') {
            if ([string]$Journal.disposition_decision -notin @('already_running_verified')) {
                throw "Runtime launch attempt has an unfinished component journal: $ComponentId ($($Journal.disposition_decision))"
            }
        } elseif ([string]$Journal.state -eq 'running_verified_job_contained') {
            $ReceiptState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath ([string]$Journal.expected_script_path) -ProcessSnapshot $CommitSnapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
            try {
                if ($ReceiptState.decision -ne 'running_verified_job_contained' -or
                    [int]$ReceiptState.receipt.pid -ne [int]$Journal.pid -or
                    ([guid][string]$ReceiptState.receipt.attempt_id).ToString() -ne $AttemptId) {
                    throw "Runtime launch attempt component lost verified job ownership: $ComponentId ($($ReceiptState.decision))"
                }
            } finally {
                if ($ReceiptState -and $ReceiptState.process) { try { $ReceiptState.process.Dispose() } catch {} }
            }
        } elseif ([string]$Journal.state -ne 'committed') {
            throw "Runtime launch attempt has a non-committable component journal: $ComponentId ($($Journal.state))"
        }
        $Journals.Add($Journal) | Out-Null
    }

    # The reservation is the authoritative commit record. Once this durable write
    # succeeds rollback must never terminate the verified runtime. Journal updates
    # below are audit decoration only and therefore cannot reopen rollback.
    $Reservation.state = 'committed'
    $Reservation | Add-Member -NotePropertyName committed_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
    $Reservation | Add-Member -NotePropertyName journal_count -NotePropertyValue ([int]$Journals.Count) -Force
    $ReservationPath = Join-Path $AttemptDir 'reservation.json'
    Write-TradingOSJsonFileAtomic -Path $ReservationPath -Payload $Reservation -Depth 6

    $Warnings = New-Object System.Collections.Generic.List[string]
    foreach ($Journal in $Journals) {
        if ([string]$Journal.state -eq 'committed') { continue }
        try {
            Update-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId ([string]$Journal.component) -State 'committed' -JobName ([string]$Journal.job_name) -ProcessId ([int]$Journal.pid) -ProcessCreationUtc ([string]$Journal.process_creation_utc) -AllowCommittedReservation | Out-Null
        } catch {
            $Warnings.Add("journal_commit_annotation_failed:$($Journal.component):$($_.Exception.Message)") | Out-Null
        }
    }
    return [pscustomobject]@{
        success = $true
        decision = 'committed'
        attempt_id = $AttemptId
        invocation_id = [string]$Reservation.invocation_id
        journal_count = $Journals.Count
        warnings = $Warnings.ToArray()
    }
}

function Join-TradingOSRuntimeJob {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$ComponentId)
    $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId $ComponentId
    if (-not [TradingOSRuntimeJobNative]::IsCurrentProcessInAnyJob()) {
        [TradingOSRuntimeJobNative]::JoinCurrentProcess((Get-TradingOSRuntimeJobName -Root $Root -ComponentId $ComponentId -Generation ('0' * 32)))
        return [pscustomobject]@{ component = $ComponentId; decision = 'joined_direct_runtime_job' }
    }
    return [pscustomobject]@{ component = $ComponentId; decision = 'already_job_contained' }
}

function Test-TradingOSPythonFileCommand {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$ExecutablePath,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$CommandLine,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath
    )
    if (-not $ExecutablePath -or -not $CommandLine) { return $false }
    try {
        $Executable = [System.IO.Path]::GetFullPath($ExecutablePath)
        $ExpectedScript = [System.IO.Path]::GetFullPath($ExpectedScriptPath)
        $Arguments = @(Get-TradingOSCommandLineArguments -CommandLine $CommandLine)
        if ($Arguments.Count -lt 2 -or -not [System.IO.Path]::GetFullPath([string]$Arguments[0]).Equals($Executable, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        $ScriptIndex = 1
        if ([string]$Arguments[1] -eq '-3') { $ScriptIndex = 2 }
        if ($ScriptIndex -ge $Arguments.Count) { return $false }
        return [System.IO.Path]::GetFullPath([string]$Arguments[$ScriptIndex]).Equals($ExpectedScript, [System.StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Test-TradingOSManagedScriptProcess {
    param(
        [Parameter(Mandatory = $true)]$CimProcess,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath,
        [System.Collections.Generic.HashSet[string]]$AllowedPowerShellExecutables
    )
    if ($ExpectedScriptPath.EndsWith('.ps1', [System.StringComparison]::OrdinalIgnoreCase)) {
        return Test-TradingOSPowerShellFileCommand -ProcessName ([string]$CimProcess.Name) -ExecutablePath ([string]$CimProcess.ExecutablePath) -CommandLine ([string]$CimProcess.CommandLine) -ExpectedScriptPath $ExpectedScriptPath -AllowedPowerShellExecutables $AllowedPowerShellExecutables
    }
    if ($ExpectedScriptPath.EndsWith('.py', [System.StringComparison]::OrdinalIgnoreCase)) {
        return Test-TradingOSPythonFileCommand -ExecutablePath ([string]$CimProcess.ExecutablePath) -CommandLine ([string]$CimProcess.CommandLine) -ExpectedScriptPath $ExpectedScriptPath
    }
    return $false
}

function Read-TradingOSRuntimeJobReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [string]$ExpectedScriptPath = ""
    )
    $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId $ComponentId
    $ReceiptPath = Get-TradingOSRuntimeJobReceiptPath -Root $Root -ComponentId $ComponentId
    if (-not (Test-Path -LiteralPath $ReceiptPath)) { return $null }
    try { $Receipt = Get-Content -LiteralPath $ReceiptPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch { throw "Invalid runtime job receipt: $ReceiptPath" }
    $CanonicalRoot = [System.IO.Path]::GetFullPath($Root)
    $RootHash = Get-TradingOSRuntimeJobRootHash -Root $Root
    $JobPattern = '^Local\\TradingOS_Runtime_Job_' + [regex]::Escape($RootHash) + '_' + [regex]::Escape($ComponentId) + '_[0-9a-f]{32}$'
    try {
        $ReceiptScript = [System.IO.Path]::GetFullPath([string]$Receipt.expected_script_path)
        $AttemptGuid = [guid][string]$Receipt.attempt_id
        $GeneratedUtc = [datetimeoffset]::ParseExact([string]$Receipt.generated_at, 'o', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
        $RootMatches = [System.IO.Path]::GetFullPath([string]$Receipt.root).Equals($CanonicalRoot, [System.StringComparison]::OrdinalIgnoreCase)
        $ScriptInsideRoot = $ReceiptScript.StartsWith($CanonicalRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)
        $ExpectedMatches = -not $ExpectedScriptPath -or $ReceiptScript.Equals([System.IO.Path]::GetFullPath($ExpectedScriptPath), [System.StringComparison]::OrdinalIgnoreCase)
        $LegacyV1 = ($Receipt.schema_version -is [int]) -and [int]$Receipt.schema_version -eq 1
        $CurrentV2 = ($Receipt.schema_version -is [int]) -and [int]$Receipt.schema_version -eq 2
        $LaunchState = [string]$Receipt.launch_state
        $VersionFieldsValid = $LegacyV1 -or ($CurrentV2 -and ($Receipt.session_id -is [int]) -and [int]$Receipt.session_id -ge 0 -and $LaunchState -in @('reserved', 'suspended_assigned', 'running'))
        $PidFieldsValid = ($Receipt.pid -is [int]) -and $(
            if ($CurrentV2 -and $LaunchState -eq 'reserved') {
                [int]$Receipt.pid -eq 0 -and [string]::IsNullOrEmpty([string]$Receipt.process_creation_utc)
            } else {
                $CreationUtc = [datetimeoffset]::ParseExact([string]$Receipt.process_creation_utc, 'o', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
                [int]$Receipt.pid -gt 0 -and $CreationUtc.Year -ge 2020
            }
        )
        $Valid = $VersionFieldsValid -and
            $PidFieldsValid -and
            ($Receipt.live_trading_locked -is [bool]) -and [bool]$Receipt.live_trading_locked -and
            ($Receipt.can_trade -is [bool]) -and -not [bool]$Receipt.can_trade -and
            [string]$Receipt.component -ceq $ComponentId -and $RootMatches -and $ScriptInsideRoot -and $ExpectedMatches -and
            [string]$Receipt.job_name -match $JobPattern -and $AttemptGuid -ne [guid]::Empty -and $GeneratedUtc.Year -ge 2020 -and
            [System.IO.Path]::IsPathRooted([string]$Receipt.executable_path) -and -not [string]::IsNullOrWhiteSpace([string]$Receipt.command_line)
    } catch { $Valid = $false }
    if (-not $Valid) { throw "Runtime job receipt failed strict validation: $ReceiptPath" }
    if ($LegacyV1) {
        $CurrentProcess = Get-Process -Id $PID -ErrorAction Stop
        try { $CurrentSession = [int]$CurrentProcess.SessionId } finally { $CurrentProcess.Dispose() }
        $Receipt | Add-Member -NotePropertyName session_id -NotePropertyValue $CurrentSession -Force
        $Receipt | Add-Member -NotePropertyName launch_state -NotePropertyValue 'running' -Force
        $Receipt | Add-Member -NotePropertyName legacy_schema -NotePropertyValue $true -Force
    }
    return $Receipt
}

function Get-TradingOSRuntimeJobReceiptState {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [string]$ExpectedScriptPath = "",
        [hashtable]$ProcessSnapshot,
        [System.Collections.Generic.HashSet[string]]$AllowedPowerShellExecutables
    )
    $Receipt = Read-TradingOSRuntimeJobReceipt -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ExpectedScriptPath
    if (-not $Receipt) { return [pscustomobject]@{ decision = 'missing_receipt'; receipt = $null; process = $null; active_processes = -1 } }
    $CurrentSession = [int](Get-Process -Id $PID -ErrorAction Stop).SessionId
    if ([int]$Receipt.session_id -ne $CurrentSession) {
        if ($null -eq $ProcessSnapshot) { $ProcessSnapshot = Get-TradingOSProcessSnapshot }
        if ($null -eq $AllowedPowerShellExecutables) { $AllowedPowerShellExecutables = Get-TradingOSAllowedPowerShellExecutables }
        $ExactPids = @($ProcessSnapshot.Values | Where-Object {
            Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath ([string]$Receipt.expected_script_path) -AllowedPowerShellExecutables $AllowedPowerShellExecutables
        } | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
        $OldSessionProcessCount = @($ProcessSnapshot.Values | Where-Object { [int]$_.SessionId -eq [int]$Receipt.session_id }).Count
        $ReceiptBeforeCurrentBoot = $false
        try {
            $BootUtc = ([datetime](Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime).ToUniversalTime()
            $ReceiptGeneratedUtc = ([datetimeoffset][string]$Receipt.generated_at).UtcDateTime
            $ReceiptBeforeCurrentBoot = $ReceiptGeneratedUtc -lt $BootUtc
        } catch {
            return [pscustomobject]@{ decision = 'receipt_session_verification_failed'; receipt = $Receipt; process = $null; active_processes = $null; error = $_.Exception.Message }
        }
        $SafelyStale = $ExactPids.Count -eq 0 -and ($ReceiptBeforeCurrentBoot -or $OldSessionProcessCount -eq 0)
        return [pscustomobject]@{
            decision = $(if ($SafelyStale) { 'stale_receipt_session_mismatch' } else { 'receipt_session_mismatch' })
            receipt = $Receipt
            process = $null
            active_processes = $null
            exact_script_pids = $ExactPids
            old_session_process_count = $OldSessionProcessCount
            receipt_before_current_boot = $ReceiptBeforeCurrentBoot
        }
    }
    if ([int]$Receipt.pid -eq 0 -and [string]$Receipt.launch_state -eq 'reserved') {
        try { $ReservedActive = [TradingOSRuntimeJobNative]::GetActiveProcessCount([string]$Receipt.job_name) } catch {
            return [pscustomobject]@{ decision = 'job_query_failed'; receipt = $Receipt; process = $null; active_processes = $null; error = $_.Exception.Message }
        }
        return [pscustomobject]@{ decision = $(if ($ReservedActive -gt 0) { 'job_active_preidentity_receipt' } else { 'reserved_receipt_no_active_job' }); receipt = $Receipt; process = $null; active_processes = $ReservedActive }
    }
    $Cim = if ($null -ne $ProcessSnapshot) {
        if ($ProcessSnapshot.ContainsKey([int]$Receipt.pid)) { $ProcessSnapshot[[int]$Receipt.pid] } else { $null }
    } else {
        Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$Receipt.pid)" -ErrorAction SilentlyContinue
    }
    $Native = Get-Process -Id ([int]$Receipt.pid) -ErrorAction SilentlyContinue
    try { $Active = [TradingOSRuntimeJobNative]::GetActiveProcessCount([string]$Receipt.job_name) } catch {
        if ($Native) { try { $Native.Dispose() } catch {} }
        return [pscustomobject]@{ decision = 'job_query_failed'; receipt = $Receipt; process = $null; active_processes = $null; error = $_.Exception.Message }
    }
    if (-not $Cim -or -not $Native) {
        if ($Native) { try { $Native.Dispose() } catch {} }
        return [pscustomobject]@{ decision = $(if ($Active -in @(0, -1)) { 'stale_receipt_process_absent' } else { 'job_active_root_process_absent' }); receipt = $Receipt; process = $null; active_processes = $Active }
    }
    try {
        $ActualCreation = $Native.StartTime.ToUniversalTime()
        $ExpectedCreation = ([datetime][string]$Receipt.process_creation_utc).ToUniversalTime()
        $CreationMatches = [math]::Abs(($ActualCreation - $ExpectedCreation).TotalMilliseconds) -lt 2
        if (-not $CreationMatches) {
            $Decision = if ($Active -gt 0) { 'job_active_root_pid_reused' } elseif ($Active -in @(0, -1)) { 'stale_receipt_pid_reused' } else { 'receipt_process_identity_mismatch' }
            $Native.Dispose()
            $Native = $null
            return [pscustomobject]@{ decision = $Decision; receipt = $Receipt; process = $null; active_processes = $Active; creation_matches = $false; identity_matches = $false; executable_matches = $false; command_line_matches = $false; in_job = $false }
        }
        $IdentityMatches = Test-TradingOSManagedScriptProcess -CimProcess $Cim -ExpectedScriptPath ([string]$Receipt.expected_script_path) -AllowedPowerShellExecutables $AllowedPowerShellExecutables
        $InJob = [TradingOSRuntimeJobNative]::IsProcessInNamedJob($Native.Handle, [string]$Receipt.job_name)
        $ExecutableMatches = [System.IO.Path]::GetFullPath([string]$Cim.ExecutablePath).Equals([System.IO.Path]::GetFullPath([string]$Receipt.executable_path), [System.StringComparison]::OrdinalIgnoreCase)
        $CommandLineMatches = [string]$Cim.CommandLine -ceq [string]$Receipt.command_line
        $Decision = if ($IdentityMatches -and $ExecutableMatches -and $CommandLineMatches -and $InJob -and $Active -ge 1) { 'running_verified_job_contained' } else { 'receipt_process_identity_mismatch' }
        return [pscustomobject]@{ decision = $Decision; receipt = $Receipt; process = $Native; active_processes = $Active; creation_matches = $CreationMatches; identity_matches = $IdentityMatches; executable_matches = $ExecutableMatches; command_line_matches = $CommandLineMatches; in_job = $InJob }
    } catch {
        try { $Native.Dispose() } catch {}
        return [pscustomobject]@{ decision = 'receipt_process_verification_failed'; receipt = $Receipt; process = $null; active_processes = $Active; error = $_.Exception.Message }
    }
}

function Test-TradingOSRuntimeJobContainsProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [string]$ExpectedAttemptId = ""
    )
    try {
        $Receipt = Read-TradingOSRuntimeJobReceipt -Root $Root -ComponentId $ComponentId
        if (-not $Receipt) { return $false }
        if ($ExpectedAttemptId -and ([guid][string]$Receipt.attempt_id).ToString() -ne ([guid]$ExpectedAttemptId).ToString()) { return $false }
        $Process = Get-Process -Id $ProcessId -ErrorAction Stop
        try { return [TradingOSRuntimeJobNative]::IsProcessInNamedJob($Process.Handle, [string]$Receipt.job_name) } finally { $Process.Dispose() }
    } catch { return $false }
}

function Get-TradingOSControlPanelOwnershipState {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [ValidateRange(1024, 65535)][int]$Port = 8765
    )
    $PanelState = Get-TradingOSControlPanelState -Root $Root -Port $Port
    $ComponentId = "control_panel_$Port"
    $PanelScript = [System.IO.Path]::GetFullPath((Join-Path $Root 'ops\control_panel\control_panel.py'))
    $Snapshot = Get-TradingOSProcessSnapshot
    $ExactPids = @($Snapshot.Values | Where-Object { Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath $PanelScript } | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
    $JobState = $null
    try {
        $JobState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $PanelScript
        $AllExactContained = $ExactPids.Count -gt 0
        foreach ($ExactPid in $ExactPids) {
            if (-not (Test-TradingOSRuntimeJobContainsProcess -Root $Root -ComponentId $ComponentId -ProcessId $ExactPid)) { $AllExactContained = $false; break }
        }
        $ListenerContained = $PanelState.decision -eq 'running_verified' -and (Test-TradingOSRuntimeJobContainsProcess -Root $Root -ComponentId $ComponentId -ProcessId ([int]$PanelState.pid))
        $Owned = $PanelState.decision -eq 'running_verified' -and $JobState.decision -eq 'running_verified_job_contained' -and $ListenerContained -and $AllExactContained
        $PanelState | Add-Member -NotePropertyName job_decision -NotePropertyValue ([string]$JobState.decision) -Force
        $PanelState | Add-Member -NotePropertyName job_contained -NotePropertyValue ([bool]$Owned) -Force
        $PanelState | Add-Member -NotePropertyName exact_script_pids -NotePropertyValue $ExactPids -Force
        $PanelState | Add-Member -NotePropertyName all_exact_processes_contained -NotePropertyValue ([bool]$AllExactContained) -Force
        $PanelState | Add-Member -NotePropertyName ownership_decision -NotePropertyValue $(if ($Owned) { 'running_verified_job_contained' } elseif ($PanelState.decision -eq 'running_verified') { 'blocked_uncontained_or_duplicate_panel' } else { [string]$PanelState.decision }) -Force
        return $PanelState
    } catch {
        $PanelState | Add-Member -NotePropertyName job_decision -NotePropertyValue 'job_receipt_verification_failed' -Force
        $PanelState | Add-Member -NotePropertyName job_contained -NotePropertyValue $false -Force
        $PanelState | Add-Member -NotePropertyName exact_script_pids -NotePropertyValue $ExactPids -Force
        $PanelState | Add-Member -NotePropertyName ownership_decision -NotePropertyValue 'job_receipt_verification_failed' -Force
        return $PanelState
    } finally {
        if ($JobState -and $JobState.process) { try { $JobState.process.Dispose() } catch {} }
    }
}

function Get-TradingOSRuntimeJobComponentDefinition {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath
    )
    $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId $ComponentId
    $CanonicalScript = [System.IO.Path]::GetFullPath($ExpectedScriptPath)
    $Manifest = Get-TradingOSRuntimeManifest -Root $Root
    $Known = @(@($Manifest.components) + @($Manifest.shutdown_only_components) | Where-Object { [string]$_.id -eq $ComponentId } | Select-Object -First 1)
    if ($Known) {
        $ManifestScript = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Known[0].script)
        if (-not $ManifestScript.Equals($CanonicalScript, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Runtime job script does not match manifest component: $ComponentId" }
        return $Known[0]
    }
    if ($ComponentId -match '^control_panel_(?<port>[0-9]{1,5})$') {
        $Port = [int]$Matches.port
        $PanelScript = [System.IO.Path]::GetFullPath((Join-Path $Root 'ops\control_panel\control_panel.py'))
        if ($Port -lt 1024 -or $Port -gt 65535 -or -not $PanelScript.Equals($CanonicalScript, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Invalid control panel runtime job identity: $ComponentId" }
        return [pscustomobject]@{ id = $ComponentId; script = $PanelScript; lock_path = ''; status_path = 'logs/control_panel_autostart_status.json'; start_owner = 'control_panel_launcher'; required = $false }
    }
    throw "Runtime job component is not present in the managed inventory: $ComponentId"
}

function Resolve-TradingOSTrustedRuntimeExecutable {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath
    )
    if (-not (Test-Path -LiteralPath $ExpectedScriptPath -PathType Leaf)) { throw "Runtime job script is missing: $ExpectedScriptPath" }
    if ($ExpectedScriptPath.EndsWith('.ps1', [System.StringComparison]::OrdinalIgnoreCase)) {
        $TrustedPowerShell = [System.IO.Path]::GetFullPath((Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'))
        if (-not (Test-Path -LiteralPath $TrustedPowerShell -PathType Leaf)) { throw "Trusted Windows PowerShell executable is missing: $TrustedPowerShell" }
        if ([System.IO.Path]::IsPathRooted($FilePath) -and -not [System.IO.Path]::GetFullPath($FilePath).Equals($TrustedPowerShell, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Untrusted PowerShell runtime executable: $FilePath" }
        if (-not [System.IO.Path]::IsPathRooted($FilePath) -and -not $FilePath.Equals('powershell.exe', [System.StringComparison]::OrdinalIgnoreCase)) { throw "Untrusted relative runtime executable: $FilePath" }
        return $TrustedPowerShell
    }
    if ($ExpectedScriptPath.EndsWith('.py', [System.StringComparison]::OrdinalIgnoreCase)) {
        if (-not [System.IO.Path]::IsPathRooted($FilePath)) { throw "Python runtime executable must be an explicit absolute path." }
        $Python = [System.IO.Path]::GetFullPath($FilePath)
        if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -or [System.IO.Path]::GetExtension($Python) -ne '.exe' -or [System.IO.Path]::GetFileNameWithoutExtension($Python) -notmatch '^(pythonw?|py)$') { throw "Untrusted Python runtime executable: $Python" }
        return $Python
    }
    throw "Unsupported managed runtime script extension: $ExpectedScriptPath"
}

function Start-TradingOSRuntimeJobProcess {
    [CmdletBinding(DefaultParameterSetName = 'ArgumentLine')]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [Parameter(Mandatory = $true)][string]$AttemptId,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ParameterSetName = 'ArgumentLine')][string]$Arguments = "",
        [Parameter(ParameterSetName = 'ArgumentList')][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath,
        [string]$StdoutPath = "",
        [string]$StderrPath = ""
    )
    $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId $ComponentId
    $AttemptId = ([guid]$AttemptId).ToString()
    $CanonicalRoot = [System.IO.Path]::GetFullPath($Root)
    $CanonicalWorking = [System.IO.Path]::GetFullPath($WorkingDirectory)
    if (-not $CanonicalWorking.Equals($CanonicalRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Runtime job working directory must equal the canonical root." }
    $ExpectedScriptPath = [System.IO.Path]::GetFullPath($ExpectedScriptPath)
    if (-not $ExpectedScriptPath.StartsWith($CanonicalRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw "Runtime job script must stay under the canonical root." }
    $Component = Get-TradingOSRuntimeJobComponentDefinition -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ExpectedScriptPath
    $Reservation = Enter-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $AttemptId
    if ([string]$Reservation.state -ne 'reserved') { throw "Refusing to launch work against a committed runtime launch attempt: $AttemptId" }
    $JournalPath = Get-TradingOSRuntimeAttemptJournalPath -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
    if (-not (Test-Path -LiteralPath $JournalPath)) {
        if ($ComponentId -notmatch '^control_panel_[0-9]+$') { throw "Runtime component launch requires a disposition journal before native launch: $ComponentId" }
        $PanelDisposition = [pscustomobject]@{ decision = 'start_verified_panel_port_free'; state_before = 'missing_listener'; quarantine_path = $null }
        Register-TradingOSRuntimeLaunchDisposition -Root $Root -AttemptId $AttemptId -Component $Component -Disposition $PanelDisposition | Out-Null
    }
    $FilePath = Resolve-TradingOSTrustedRuntimeExecutable -FilePath $FilePath -ExpectedScriptPath $ExpectedScriptPath
    if ($PSCmdlet.ParameterSetName -eq 'ArgumentList') {
        $Arguments = (@($ArgumentList) | ForEach-Object { [TradingOSRuntimeJobNative]::QuoteArgument([string]$_) }) -join ' '
    }
    foreach ($Path in @($StdoutPath, $StderrPath)) {
        if (-not $Path) { continue }
        $FullLogPath = [System.IO.Path]::GetFullPath($Path)
        if (-not $FullLogPath.StartsWith((Join-Path $CanonicalRoot 'logs').TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw "Runtime job output must stay under Root\logs." }
        New-Item -ItemType Directory -Force -Path (Split-Path $FullLogPath -Parent) | Out-Null
    }

    $ReceiptPath = Get-TradingOSRuntimeJobReceiptPath -Root $Root -ComponentId $ComponentId
    $StaleQuarantine = $null
    $JobName = ''
    $PidValue = 0
    $SuspendedProcess = $null
    $Native = $null
    try {
    if (Test-Path -LiteralPath $ReceiptPath) {
        $ExistingState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ExpectedScriptPath
        if ($ExistingState.decision -notin @('stale_receipt_process_absent', 'stale_receipt_pid_reused', 'stale_receipt_session_mismatch', 'reserved_receipt_no_active_job')) { throw "Refusing to replace an active or unverifiable runtime job receipt: $ReceiptPath" }
        if ($ExistingState.process) { try { $ExistingState.process.Dispose() } catch {} }
        $StaleQuarantine = "$ReceiptPath.stale.$(([guid]$AttemptId).ToString('N')).$([guid]::NewGuid().ToString('N'))"
        Move-TradingOSFileAtomic -TemporaryPath $ReceiptPath -DestinationPath $StaleQuarantine
        $Journal = Read-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
        $Journal | Add-Member -NotePropertyName stale_receipt_quarantine_path -NotePropertyValue $StaleQuarantine -Force
        $Journal.PSObject.Properties.Remove('validated_lock_path')
        $Journal.PSObject.Properties.Remove('validated_receipt_path')
        Write-TradingOSJsonFileAtomic -Path $JournalPath -Payload $Journal -Depth 8
    }

    $JobName = Get-TradingOSRuntimeJobName -Root $Root -ComponentId $ComponentId
    Update-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId -State 'native_launch_reserved' -JobName $JobName | Out-Null
    $CurrentProcess = Get-Process -Id $PID -ErrorAction Stop
    try { $SessionId = [int]$CurrentProcess.SessionId } finally { $CurrentProcess.Dispose() }
    $ProvisionalCommandLine = [TradingOSRuntimeJobNative]::QuoteArgument($FilePath) + $(if ($Arguments) { " $Arguments" } else { '' })
    $ProvisionalReceipt = [ordered]@{
        schema_version = 2
        generated_at = (Get-Date).ToUniversalTime().ToString('o')
        component = $ComponentId
        root = $CanonicalRoot
        attempt_id = $AttemptId
        job_name = $JobName
        pid = 0
        process_creation_utc = $null
        executable_path = $FilePath
        command_line = $ProvisionalCommandLine
        expected_script_path = $ExpectedScriptPath
        session_id = $SessionId
        launch_state = 'reserved'
        live_trading_locked = $true
        can_trade = $false
    }
    Write-TradingOSJsonFileAtomic -Path $ReceiptPath -Payload $ProvisionalReceipt -Depth 6
        $SuspendedProcess = [TradingOSRuntimeJobNative]::CreateSuspendedInJob($FilePath, $Arguments, $CanonicalWorking, $StdoutPath, $StderrPath, $JobName)
        $PidValue = [int]$SuspendedProcess.ProcessId
        $Deadline = (Get-Date).AddSeconds(5)
        do {
            $Native = Get-Process -Id $PidValue -ErrorAction SilentlyContinue
            $Cim = Get-CimInstance Win32_Process -Filter "ProcessId = $PidValue" -ErrorAction SilentlyContinue
            $IdentityProcess = $null
            if ($Cim) {
                # Win32_Process hides ExecutablePath while CREATE_SUSPENDED is active.
                # FilePath is safe here because Resolve-TradingOSTrustedRuntimeExecutable
                # already bound it to the allowlisted interpreter used by CreateProcessW.
                $IdentityExecutablePath = if ([string]$Cim.ExecutablePath) { [string]$Cim.ExecutablePath } else { $FilePath }
                $IdentityProcess = [pscustomobject]@{
                    Name = if ([string]$Cim.Name) { [string]$Cim.Name } else { [System.IO.Path]::GetFileName($FilePath) }
                    ExecutablePath = $IdentityExecutablePath
                    CommandLine = [string]$Cim.CommandLine
                }
            }
            if ($Native -and $IdentityProcess -and (Test-TradingOSManagedScriptProcess -CimProcess $IdentityProcess -ExpectedScriptPath $ExpectedScriptPath)) { break }
            Start-Sleep -Milliseconds 50
        } while ((Get-Date) -lt $Deadline)
        if (-not $Native -or -not $IdentityProcess -or -not (Test-TradingOSManagedScriptProcess -CimProcess $IdentityProcess -ExpectedScriptPath $ExpectedScriptPath)) { throw "Started process failed exact script identity verification." }
        if (-not [TradingOSRuntimeJobNative]::IsProcessInNamedJob($Native.Handle, $JobName)) { throw "Started process is not contained in its runtime job." }
        $ProcessCreationUtc = $Native.StartTime.ToUniversalTime().ToString('o')
        $Receipt = [ordered]@{
            schema_version = 2
            generated_at = (Get-Date).ToUniversalTime().ToString('o')
            component = $ComponentId
            root = $CanonicalRoot
            attempt_id = $AttemptId
            job_name = $JobName
            pid = $PidValue
            process_creation_utc = $ProcessCreationUtc
            executable_path = [string]$IdentityProcess.ExecutablePath
            command_line = [string]$IdentityProcess.CommandLine
            expected_script_path = $ExpectedScriptPath
            session_id = $SessionId
            launch_state = 'suspended_assigned'
            live_trading_locked = $true
            can_trade = $false
        }
        Write-TradingOSJsonFileAtomic -Path $ReceiptPath -Payload $Receipt -Depth 6
        Update-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId -State 'suspended_assigned_receipted' -JobName $JobName -ProcessId $PidValue -ProcessCreationUtc $ProcessCreationUtc | Out-Null
        $SuspendedProcess.Resume()
        $Receipt['launch_state'] = 'running'
        $Receipt['resumed_at'] = (Get-Date).ToUniversalTime().ToString('o')
        Write-TradingOSJsonFileAtomic -Path $ReceiptPath -Payload $Receipt -Depth 6
        $ReceiptState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ExpectedScriptPath
        if ($ReceiptState.decision -ne 'running_verified_job_contained') { throw "Runtime job receipt post-verification failed: $($ReceiptState.decision)" }
        if ($ReceiptState.process) { try { $ReceiptState.process.Dispose() } catch {} }
        Update-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId -State 'running_verified_job_contained' -JobName $JobName -ProcessId $PidValue -ProcessCreationUtc $ProcessCreationUtc | Out-Null
        if ($Native) { try { $Native.Dispose() } catch {}; $Native = $null }
        return Get-Process -Id $PidValue -ErrorAction Stop
    } catch {
        $LaunchError = $_
        $CleanupFailures = New-Object System.Collections.Generic.List[string]
        if ($SuspendedProcess) {
            try { $SuspendedProcess.Terminate(1) } catch { $CleanupFailures.Add("native_termination_failed:$($_.Exception.Message)") | Out-Null }
        } elseif ($PidValue -gt 0) {
            try { [void][TradingOSRuntimeJobNative]::TerminateNamedJob($JobName, 1) } catch { $CleanupFailures.Add("named_job_termination_failed:$($_.Exception.Message)") | Out-Null }
        }
        try {
            $Journal = Read-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
            $Rollback = Undo-TradingOSRuntimeComponentJournal -Root $Root -AttemptId $AttemptId -Journal $Journal
            if (-not $Rollback.success) { $CleanupFailures.AddRange([string[]]@($Rollback.failures)) }
        } catch { $CleanupFailures.Add("component_rollback_failed:$($_.Exception.Message)") | Out-Null }
        if ($StaleQuarantine -and (Test-Path -LiteralPath $StaleQuarantine)) {
            if (Test-Path -LiteralPath $ReceiptPath) { $CleanupFailures.Add('stale_receipt_restore_destination_occupied') | Out-Null }
            else { try { Move-TradingOSFileAtomic -TemporaryPath $StaleQuarantine -DestinationPath $ReceiptPath } catch { $CleanupFailures.Add("stale_receipt_direct_restore_failed:$($_.Exception.Message)") | Out-Null } }
        }
        if ($CleanupFailures.Count -gt 0) {
            try { Update-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId -State 'launch_failed_cleanup_degraded' -JobName $JobName -ProcessId $PidValue -ErrorMessage ($CleanupFailures -join ';') | Out-Null } catch {}
            throw "Runtime job launch failed ($($LaunchError.Exception.Message)); cleanup degraded: $($CleanupFailures -join '; ')"
        }
        throw $LaunchError
    } finally {
        if ($Native) { try { $Native.Dispose() } catch {} }
        if ($SuspendedProcess) { try { $SuspendedProcess.Dispose() } catch {} }
    }
}

function Stop-TradingOSRuntimeJobReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [string]$ExpectedAttemptId = "",
        [int]$ExpectedProcessId = 0,
        [string]$ExpectedScriptPath = ""
    )
    $ReceiptPath = Get-TradingOSRuntimeJobReceiptPath -Root $Root -ComponentId $ComponentId
    $State = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ExpectedScriptPath
    if (-not $State.receipt) { return [pscustomobject]@{ component = $ComponentId; success = $true; decision = 'receipt_already_absent' } }
    $Receipt = $State.receipt
    if ($ExpectedAttemptId -and ([guid][string]$Receipt.attempt_id).ToString() -ne ([guid]$ExpectedAttemptId).ToString()) { throw "Runtime job receipt attempt mismatch: $ComponentId" }
    if ($ExpectedProcessId -gt 0 -and [int]$Receipt.pid -ne $ExpectedProcessId) { throw "Runtime job receipt PID mismatch: $ComponentId" }
    $BoundProcess = $State.process
    if ($State.decision -in @('running_verified_job_contained', 'job_active_root_process_absent', 'job_active_root_pid_reused', 'job_active_preidentity_receipt')) {
        $TerminationRequested = [TradingOSRuntimeJobNative]::TerminateNamedJob([string]$Receipt.job_name, 1)
        if ($TerminationRequested) {
            $Deadline = (Get-Date).AddSeconds(10)
            do {
                $Active = [TradingOSRuntimeJobNative]::GetActiveProcessCount([string]$Receipt.job_name)
                $BoundExited = $true
                if ($BoundProcess) { try { $BoundProcess.Refresh(); $BoundExited = $BoundProcess.HasExited } catch { $BoundExited = $true } }
                if ($Active -in @(-1, 0) -and $BoundExited) { break }
                Start-Sleep -Milliseconds 100
            } while ((Get-Date) -lt $Deadline)
        }
        # A verified job can exit naturally between the preflight query and the
        # terminate call. Absence is success only when the bound process handle
        # also proves that the original process exited.
        $FinalActive = [TradingOSRuntimeJobNative]::GetActiveProcessCount([string]$Receipt.job_name)
        if ($BoundProcess) { try { $BoundProcess.Refresh(); $BoundExited = $BoundProcess.HasExited } catch { $BoundExited = $true } }
        else { $BoundExited = $true }
        if (-not $BoundExited -or $FinalActive -gt 0) { throw "Runtime job remained active after termination: $ComponentId" }
    } elseif ($State.decision -notin @('stale_receipt_process_absent', 'stale_receipt_pid_reused', 'stale_receipt_session_mismatch', 'reserved_receipt_no_active_job')) {
        throw "Refusing to terminate an unverifiable runtime job: $ComponentId ($($State.decision))"
    }
    if ($BoundProcess) { try { $BoundProcess.Dispose() } catch {} }
    Remove-Item -LiteralPath $ReceiptPath -Force -ErrorAction Stop
    return [pscustomobject]@{ component = $ComponentId; success = $true; decision = 'verified_job_terminated'; pid = [int]$Receipt.pid; attempt_id = [string]$Receipt.attempt_id }
}

function Undo-TradingOSRuntimeComponentJournal {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AttemptId,
        [Parameter(Mandatory = $true)]$Journal
    )
    $AttemptId = ([guid]$AttemptId).ToString()
    $Reservation = Enter-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $AttemptId
    if ([string]$Reservation.state -eq 'committed') { throw "Refusing to roll back a committed runtime launch attempt: $AttemptId" }
    $Failures = New-Object System.Collections.Generic.List[string]
    $Actions = New-Object System.Collections.Generic.List[object]
    $ComponentId = Assert-TradingOSRuntimeComponentId -ComponentId ([string]$Journal.component)
    $Journal = Read-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
    if (([guid][string]$Journal.attempt_id).ToString() -ne $AttemptId -or [string]$Journal.invocation_id -ne [string]$Reservation.invocation_id -or
        -not [System.IO.Path]::GetFullPath([string]$Journal.root).Equals([System.IO.Path]::GetFullPath($Root), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Runtime component journal ownership mismatch: $ComponentId"
    }
    $ExpectedScript = [System.IO.Path]::GetFullPath([string]$Journal.expected_script_path)
    $ComponentDefinition = Get-TradingOSRuntimeJobComponentDefinition -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ExpectedScript
    if ([string]$Journal.state -in @('disposition_reserved', 'rolled_back') -and [string]$Journal.disposition_decision -eq 'already_running_verified') {
        if ([int]$Journal.pid -ne 0 -or [string]$Journal.job_name -or [string]$Journal.quarantine_path -or [string]$Journal.stale_receipt_quarantine_path) {
            return [pscustomobject]@{ component = $ComponentId; success = $false; actions = @(); failures = @("preexisting_runtime_journal_has_mutation_evidence:$ComponentId"); exact_processes_remaining = @() }
        }
        if ([string]$Journal.state -eq 'rolled_back') {
            return [pscustomobject]@{ component = $ComponentId; success = $true; actions = @([pscustomobject]@{ component = $ComponentId; decision = 'preexisting_runtime_rollback_already_recorded' }); failures = @(); exact_processes_remaining = @() }
        }
        $Snapshot = Get-TradingOSProcessSnapshot
        $AllowedPowerShellExecutables = Get-TradingOSAllowedPowerShellExecutables
        $ExistingState = Get-TradingOSRuntimeComponentState -Root $Root -Component $ComponentDefinition -ProcessSnapshot $Snapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
        $ExistingJobState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ExpectedScript -ProcessSnapshot $Snapshot -AllowedPowerShellExecutables $AllowedPowerShellExecutables
        try {
            $StillOwned = $ExistingState.decision -eq 'running_verified' -and
                $ExistingState.matching_script_process_count -eq 1 -and
                [int]$ExistingState.matching_script_pids[0] -eq [int]$ExistingState.pid -and
                $ExistingJobState.decision -eq 'running_verified_job_contained' -and
                [int]$ExistingJobState.receipt.pid -eq [int]$ExistingState.pid
        } finally {
            if ($ExistingJobState.process) { try { $ExistingJobState.process.Dispose() } catch {} }
        }
        if (-not $StillOwned) {
            return [pscustomobject]@{ component = $ComponentId; success = $false; actions = @(); failures = @("preexisting_runtime_revalidation_failed:$ComponentId"); exact_processes_remaining = @($ExistingState.matching_script_pids) }
        }
        Update-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId -State 'rolled_back' | Out-Null
        return [pscustomobject]@{ component = $ComponentId; success = $true; actions = @([pscustomobject]@{ component = $ComponentId; decision = 'preexisting_verified_runtime_left_untouched' }); failures = @(); exact_processes_remaining = @($ExistingState.matching_script_pids) }
    }
    $ReceiptPath = [string]$Journal.validated_receipt_path
    $JobPattern = '^Local\\TradingOS_Runtime_Job_' + [regex]::Escape((Get-TradingOSRuntimeJobRootHash -Root $Root)) + '_' + [regex]::Escape($ComponentId) + '_[0-9a-f]{32}$'
    $JobName = [string]$Journal.job_name
    $AttemptNeverStarted = [string]$Journal.state -eq 'disposition_reserved' -and
        [int]$Journal.pid -eq 0 -and
        -not $JobName -and
        -not [string]$Journal.stale_receipt_quarantine_path
    if ($JobName -and $JobName -notmatch $JobPattern) { $Failures.Add("invalid_journal_job_name:$ComponentId") | Out-Null }

    if ($Failures.Count -eq 0 -and (Test-Path -LiteralPath $ReceiptPath)) {
        try {
            $Receipt = Read-TradingOSRuntimeJobReceipt -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ExpectedScript
            if (([guid][string]$Receipt.attempt_id).ToString() -eq $AttemptId) {
                $Stopped = Stop-TradingOSRuntimeJobReceipt -Root $Root -ComponentId $ComponentId -ExpectedAttemptId $AttemptId -ExpectedProcessId ([int]$Receipt.pid) -ExpectedScriptPath $ExpectedScript
                $Actions.Add($Stopped) | Out-Null
            } elseif ($AttemptNeverStarted) {
                # A pre-existing stale receipt can survive disposition reservation.
                # Leave it untouched only when this attempt has no launch identity;
                # the exact-process inventory below still fails closed on a process.
                $Actions.Add([pscustomobject]@{ component = $ComponentId; decision = 'preexisting_foreign_receipt_left_untouched' }) | Out-Null
            } elseif ([string]$Journal.state -ne 'rolled_back') {
                $Failures.Add("foreign_receipt_blocks_rollback:$ComponentId") | Out-Null
            }
        } catch { $Failures.Add("receipt_rollback_failed:${ComponentId}:$($_.Exception.Message)") | Out-Null }
    }
    if ($Failures.Count -eq 0 -and -not (Test-Path -LiteralPath $ReceiptPath) -and $JobName) {
        try {
            $Active = [TradingOSRuntimeJobNative]::GetActiveProcessCount($JobName)
            if ($Active -gt 0) {
                if (-not [TradingOSRuntimeJobNative]::TerminateNamedJob($JobName, 1)) { throw 'journal job disappeared before termination' }
                $Deadline = (Get-Date).AddSeconds(10)
                do { Start-Sleep -Milliseconds 100; $Active = [TradingOSRuntimeJobNative]::GetActiveProcessCount($JobName) } while ($Active -gt 0 -and (Get-Date) -lt $Deadline)
                if ($Active -gt 0) { throw 'journal job remained active after termination' }
                $Actions.Add([pscustomobject]@{ component = $ComponentId; decision = 'journal_job_terminated_without_receipt' }) | Out-Null
            }
        } catch { $Failures.Add("journal_job_cleanup_failed:${ComponentId}:$($_.Exception.Message)") | Out-Null }
    }

    $Snapshot = Get-TradingOSProcessSnapshot
    $ExactPids = @($Snapshot.Values | Where-Object { Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath $ExpectedScript } | ForEach-Object { [int]$_.ProcessId } | Sort-Object -Unique)
    if ($ExactPids.Count -gt 0) { $Failures.Add("exact_processes_remain:${ComponentId}:$($ExactPids -join ',')") | Out-Null }

    $LockPath = if ([string]$Journal.validated_lock_path) { [string]$Journal.validated_lock_path } else { $null }
    if ([string]$Journal.state -ne 'rolled_back' -and $LockPath -and (Test-Path -LiteralPath $LockPath) -and $ExactPids.Count -eq 0) {
        try {
            $Lock = Get-Content -LiteralPath $LockPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            if ([int]$Journal.pid -le 0 -or [int]$Lock.pid -ne [int]$Journal.pid) { throw 'current lock is not owned by the attempted process' }
            $RollbackQuarantine = "$LockPath.rollback.$(([guid]$AttemptId).ToString('N')).$([guid]::NewGuid().ToString('N'))"
            Move-TradingOSFileAtomic -TemporaryPath $LockPath -DestinationPath $RollbackQuarantine
            Remove-Item -LiteralPath $RollbackQuarantine -Force -ErrorAction Stop
            $Actions.Add([pscustomobject]@{ component = $ComponentId; decision = 'attempt_lock_removed'; pid = [int]$Journal.pid }) | Out-Null
        } catch { $Failures.Add("attempt_lock_cleanup_failed:${ComponentId}:$($_.Exception.Message)") | Out-Null }
    }

    $QuarantinePath = [string]$Journal.quarantine_path
    if ($LockPath -and $QuarantinePath) {
        $ExpectedPrefix = "$LockPath.quarantine.$(([guid]$AttemptId).ToString('N'))."
        if (-not [System.IO.Path]::GetFullPath($QuarantinePath).StartsWith($ExpectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $Failures.Add("invalid_lock_quarantine_path:$ComponentId") | Out-Null
        } elseif (Test-Path -LiteralPath $QuarantinePath) {
            if (Test-Path -LiteralPath $LockPath) { $Failures.Add("lock_restore_destination_occupied:$ComponentId") | Out-Null }
            elseif ($ExactPids.Count -gt 0) { $Failures.Add("lock_restore_blocked_by_exact_process:$ComponentId") | Out-Null }
            else {
                try { Move-TradingOSFileAtomic -TemporaryPath $QuarantinePath -DestinationPath $LockPath; $Actions.Add([pscustomobject]@{ component = $ComponentId; decision = 'original_stale_lock_restored' }) | Out-Null } catch { $Failures.Add("stale_lock_restore_failed:${ComponentId}:$($_.Exception.Message)") | Out-Null }
            }
        } elseif (-not (Test-Path -LiteralPath $LockPath)) { $Failures.Add("original_lock_quarantine_missing:$ComponentId") | Out-Null }
    }

    $StaleReceiptQuarantine = [string]$Journal.stale_receipt_quarantine_path
    if ($StaleReceiptQuarantine -and (Test-Path -LiteralPath $StaleReceiptQuarantine)) {
        if (Test-Path -LiteralPath $ReceiptPath) { $Failures.Add("stale_receipt_restore_destination_occupied:$ComponentId") | Out-Null }
        else {
            try { Move-TradingOSFileAtomic -TemporaryPath $StaleReceiptQuarantine -DestinationPath $ReceiptPath; $Actions.Add([pscustomobject]@{ component = $ComponentId; decision = 'original_stale_receipt_restored' }) | Out-Null } catch { $Failures.Add("stale_receipt_restore_failed:${ComponentId}:$($_.Exception.Message)") | Out-Null }
        }
    }

    if ($Failures.Count -eq 0) {
        Update-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId -State 'rolled_back' -JobName $JobName -ProcessId ([int]$Journal.pid) | Out-Null
    }
    return [pscustomobject]@{ component = $ComponentId; success = $Failures.Count -eq 0; actions = $Actions.ToArray(); failures = $Failures.ToArray(); exact_processes_remaining = $ExactPids }
}

function Undo-TradingOSRuntimeLaunchAttempt {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AttemptId,
        [object[]]$LaunchDispositions = @()
    )
    $AttemptId = ([guid]$AttemptId).ToString()
    $Reservation = Enter-TradingOSRuntimeLaunchAttempt -Root $Root -AttemptId $AttemptId
    if ([string]$Reservation.state -eq 'committed') { throw "Refusing to roll back a committed runtime launch attempt: $AttemptId" }
    $AttemptDir = Get-TradingOSRuntimeAttemptDirectory -Root $Root -AttemptId $AttemptId
    $Results = New-Object System.Collections.Generic.List[object]
    $Failures = New-Object System.Collections.Generic.List[string]
    $JournalFiles = @(Get-ChildItem -LiteralPath $AttemptDir -Filter '*.json' -File -ErrorAction Stop | Where-Object { $_.Name -ne 'reservation.json' })
    foreach ($Path in @($JournalFiles | Sort-Object LastWriteTimeUtc -Descending)) {
        try {
            $FileComponentId = Assert-TradingOSRuntimeComponentId -ComponentId ([System.IO.Path]::GetFileNameWithoutExtension($Path.Name))
            $Journal = Read-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $FileComponentId
            $Result = Undo-TradingOSRuntimeComponentJournal -Root $Root -AttemptId $AttemptId -Journal $Journal
            $Results.Add($Result) | Out-Null
            foreach ($Failure in @($Result.failures)) { $Failures.Add([string]$Failure) | Out-Null }
        } catch { $Failures.Add("journal_rollback_failed:$($Path.Name):$($_.Exception.Message)") | Out-Null }
    }

    $Remaining = New-Object System.Collections.Generic.List[string]
    $ReceiptDir = Join-Path $Root 'logs\runtime_jobs'
    if (Test-Path -LiteralPath $ReceiptDir) {
        foreach ($Path in @(Get-ChildItem -LiteralPath $ReceiptDir -Filter '*.json' -File -ErrorAction Stop)) {
            try {
                $Raw = Get-Content -LiteralPath $Path.FullName -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
                if (([guid][string]$Raw.attempt_id).ToString() -eq $AttemptId) { $Remaining.Add($Path.FullName) | Out-Null }
            } catch { $Failures.Add("receipt_inventory_failed:$($Path.Name):$($_.Exception.Message)") | Out-Null }
        }
    }
    if ($Remaining.Count -gt 0) { $Failures.Add("attempt_receipts_remain:$($Remaining.Count)") | Out-Null }
    foreach ($JournalPath in $JournalFiles) {
        try {
            $FileComponentId = Assert-TradingOSRuntimeComponentId -ComponentId ([System.IO.Path]::GetFileNameWithoutExtension($JournalPath.Name))
            $Journal = Read-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $FileComponentId
            if ([string]$Journal.state -ne 'rolled_back') { $Failures.Add("attempt_journal_not_rolled_back:$($Journal.component):$($Journal.state)") | Out-Null }
            if ([string]$Journal.quarantine_path -and (Test-Path -LiteralPath ([string]$Journal.quarantine_path))) { $Failures.Add("attempt_lock_quarantine_remains:$($Journal.component)") | Out-Null }
        } catch { $Failures.Add("journal_final_verification_failed:$($JournalPath.Name):$($_.Exception.Message)") | Out-Null }
    }
    return [ordered]@{
        ts = (Get-Date).ToUniversalTime().ToString('o')
        status = if ($Failures.Count -eq 0) { 'failed_rolled_back' } else { 'failed_rollback_degraded' }
        success = $Failures.Count -eq 0
        root = [System.IO.Path]::GetFullPath($Root)
        attempt_id = $AttemptId
        invocation_id = [string]$Reservation.invocation_id
        jobs = $Results.ToArray()
        failures = $Failures.ToArray()
        remaining_receipts = $Remaining.ToArray()
        live_trading_locked = $true
        can_trade = $false
    }
}

function Get-TradingOSRuntimeComponentLaunchConfirmation {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)][string]$ComponentId,
        [Parameter(Mandatory = $true)][string]$AttemptId,
        [Parameter(Mandatory = $true)][int]$ExpectedProcessId,
        [int]$StatusProcessId = 0
    )
    $AttemptId = ([guid]$AttemptId).ToString()
    $Inventory = @($Manifest.components) + @($Manifest.shutdown_only_components)
    $Component = @($Inventory | Where-Object { [string]$_.id -eq $ComponentId } | Select-Object -First 1)
    if (-not $Component) { throw "Unknown runtime component id: $ComponentId" }
    $State = Get-TradingOSRuntimeComponentState -Root $Root -Component $Component[0] -ProcessSnapshot (Get-TradingOSProcessSnapshot)
    $ScriptPath = Resolve-TradingOSRuntimePath -Root $Root -Path ([string]$Component[0].script)
    $JobState = $null
    try {
        $JobState = Get-TradingOSRuntimeJobReceiptState -Root $Root -ComponentId $ComponentId -ExpectedScriptPath $ScriptPath
        $AttemptMatches = $JobState.receipt -and ([guid][string]$JobState.receipt.attempt_id).ToString() -eq $AttemptId
        $ExactSingle = $State.matching_script_process_count -eq 1 -and [int]$State.matching_script_pids[0] -eq $ExpectedProcessId
        $Confirmed = $State.decision -eq 'running_verified' -and $ExactSingle -and [int]$State.pid -eq $ExpectedProcessId -and
            ($StatusProcessId -le 0 -or $StatusProcessId -eq $ExpectedProcessId) -and $AttemptMatches -and
            $JobState.decision -eq 'running_verified_job_contained' -and [int]$JobState.receipt.pid -eq $ExpectedProcessId
        return [pscustomobject]@{
            confirmed = [bool]$Confirmed
            component = $ComponentId
            pid = $ExpectedProcessId
            status_pid = $StatusProcessId
            component_decision = [string]$State.decision
            matching_script_pids = $State.matching_script_pids
            job_decision = [string]$JobState.decision
            attempt_matches = [bool]$AttemptMatches
            live_trading_locked = $true
            can_trade = $false
        }
    } finally {
        if ($JobState -and $JobState.process) { try { $JobState.process.Dispose() } catch {} }
    }
}

function Undo-TradingOSRuntimeComponentLaunch {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AttemptId,
        [Parameter(Mandatory = $true)][string]$ComponentId
    )
    $Journal = Read-TradingOSRuntimeAttemptJournal -Root $Root -AttemptId $AttemptId -ComponentId $ComponentId
    return Undo-TradingOSRuntimeComponentJournal -Root $Root -AttemptId $AttemptId -Journal $Journal
}
