from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOSTART = ROOT / "ops" / "autostart"
MANIFEST = ROOT / "configs" / "TRADING_OS_RUNTIME_COMPONENTS.json"


def read(name: str) -> str:
    return (AUTOSTART / name).read_text(encoding="utf-8-sig")


def test_runtime_manifest_is_complete_and_unique() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    components = payload["components"]

    assert payload["schema_version"] == 1
    assert payload["live_trading_locked"] is True
    assert len(components) == 21
    assert len({item["id"] for item in components}) == 21
    assert len({item["lock_path"] for item in components}) == 21

    for item in components:
        assert item["required"] is True
        assert (ROOT / item["script"]).is_file()
        assert item["lock_path"].endswith(".lock.json")
        assert item["status_path"].endswith(".json")


def test_hot_loops_are_never_working_set_trim_targets() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    hot = [item for item in payload["components"] if item["default_sleep_seconds"] < 300]

    assert hot
    assert all(item["trim_working_set"] is False for item in hot)


def test_start_is_mutex_guarded_and_post_start_verified() -> None:
    source = read("Start-TradingOSRuntime.ps1")

    assert "Get-TradingOSRuntimeMutexName" in source
    assert "startup_already_in_progress" in source
    assert "Get-TradingOSRuntimeStates" in source
    assert 'status = $RuntimeStatus' in source
    assert '"degraded"' in source
    assert 'ComponentId "forward_paper"' in source
    assert "Get-TradingOSRuntimeLaunchDisposition" in source
    assert "launch_dispositions" in source
    assert "TradingOS runtime startup degraded" in source
    assert 'ValidateSet("Explicit", "Autostart", "AutomaticRepair")' in source
    assert "blocked_runtime_shutdown_requested" in source
    assert "ShutdownStartBypassAcquired" in source
    assert "runtime_shutdown.starting.json" in read("TradingOSRuntimeShutdownGate.ps1")


def test_bitunix_wo105_v3r4_forward_loop_is_public_shadow_only_and_predecessors_are_retired() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    component = next(item for item in payload["components"] if item["id"] == "bitunix_wo105_v3r4_forward")
    retired_ids = {item["id"] for item in payload["shutdown_only_components"]}
    source = read("Run-BitunixWO105V3ForwardLoop.ps1")
    wrapper = read("Run-BitunixWO105V3R4ForwardLoop.ps1")
    runtime = read("Start-TradingOSRuntime.ps1")

    assert component["required"] is True
    assert component["trim_working_set"] is False
    assert {
        "bitunix_wo105_v3_forward",
        "bitunix_wo105_v3r1_forward",
        "bitunix_wo105_v3r2_forward",
        "bitunix_wo105_v3r3_forward",
    } <= retired_ids
    assert "bitunix_wo104_public_capture_runner.py" in source
    assert "bitunix_wo105_public_rest_collector.py" in source
    assert "bitunix_wo105_packet_assembler_v3.py" in source
    assert "AssemblerScriptRelativePath" in source
    assert "packet_assembler_sha256" in source
    assert "WO105 packet assembler does not match the frozen path/hash binding" in source
    assert 'Write-LoopStatus -Status "public_ws_capture_running"' in source
    assert "$Capture.WaitForExit()" in source
    assert "$CaptureExit = [int]$Capture.ExitCode" in source
    assert "bitunix_wo105_v2_first_cycle_gate.py" in source
    assert '"${RuntimeTag}_first_cycle_milestones.jsonl"' in source
    assert 'receipt_selection = "earliest_received_record_per_close_ms"' in source
    assert "event_continuation = $true" in source
    assert 'credentials_allowed = $false' in source
    assert 'signals_allowed = $false' in source
    assert 'orders_allowed = $false' in source
    assert 'can_trade = $false' in source
    assert 'ForwardFloor "2026-07-15T04:00:00Z"' in wrapper
    assert 'RuntimeTag "bitunix_wo105_v3r4"' in wrapper
    assert 'LockRelativePath "configs\\BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json"' in wrapper
    assert 'AssemblerScriptRelativePath "tools\\bitunix_wo105_packet_assembler_v6.py"' in wrapper
    assert "Run-BitunixWO105V3R4ForwardLoop.ps1" in runtime


def test_repair_skips_recursive_startup() -> None:
    source = read("Repair-TradingOSRuntime.ps1")

    assert "Test-TradingOSRuntimeStartInProgress" in source
    assert "skipped_startup_in_progress" in source
    assert "blocked_live_component_requires_targeted_restart" in source
    assert "restart_verified" in source


def test_stop_uses_manifest_identity_and_verified_job_containment() -> None:
    source = read("Stop-TradingOSRuntime.ps1")

    assert "Get-TradingOSRuntimeManifest" in source
    assert 'decision -eq "running_verified"' in source
    assert "Get-TradingOSRuntimeJobReceiptState" in source
    assert "running_verified_job_contained" in source
    assert "Test-TradingOSRuntimeJobContainsProcess" in source
    assert "Stop-TradingOSRuntimeJobReceipt" in source
    assert 'containment = "named_windows_job_objects"' in source
    assert "safe_to_remove_lock" in source
    assert "unsafe_lock_retained_for_manual_review" in source
    assert "process_creation_utc" in read("TradingOSRuntimeLifecycle.ps1")
    assert "panel_identity_mismatch_not_stopped" in source
    assert ".Kill()" not in source
    assert "runtime_shutdown.request.json" in read("TradingOSRuntimeShutdownGate.ps1")
    assert "unowned_matching_process_without_lock_not_stopped" in source
    assert "request_parent_process_id" in source
    assert "request_parent_command_line" in source
    assert "loops_only = [bool]$LoopsOnly" in source


def test_optimizer_only_trims_verified_long_sleep_loops() -> None:
    source = read("Optimize-TradingOSRuntime.ps1")

    assert "EmptyWorkingSet" in source
    assert 'decision -ne "running_verified"' in source
    assert "MinimumTrimSleepSeconds" in source
    assert "skipped_active_child_process" in source
    assert "actual_sleep_seconds" in source
    assert "remaining_sleep_seconds" in source
    assert "skipped_not_verified_sleep_phase" in source
    assert "runtime_memory_maintenance_whatif_status.json" in source
    assert "Get-VerifiedLoopSleepWindow" in source
    assert "trim_result_unverified_process_changed" in source
    assert "Write-TradingOSJsonFileAtomic" in source
    assert "maintenance_status" in source
    assert '"${TaskPrefix}_RuntimeMemoryMaintenance_${EffectiveIntervalMinutes}M"' in source
    assert "Register-ScheduledTask" in source


def test_startup_folder_runs_verified_optimizer() -> None:
    source = read("Install-TradingOSStartupFolder.ps1")

    assert "Optimize-TradingOSRuntime.ps1" in source
    assert "-NonInteractive" in source
    assert "verified_runtime_start_and_memory_maintenance" in source
    assert "TaskPrefix" in source
    assert "MemoryMaintenanceMinutes" in source
    assert "Write-TradingOSTextFileAtomic" in source


def test_manifest_paths_are_contained_and_legacy_tasks_are_retired() -> None:
    lifecycle = read("TradingOSRuntimeLifecycle.ps1")
    installer = read("Install-TradingOSAutostart.ps1")
    uninstaller = read("Uninstall-TradingOSAutostart.ps1")

    assert "Runtime path escapes the canonical root" in lifecycle
    assert "Runtime component lock must stay under Root\\logs" in lifecycle
    assert "runtime_root_policy" in lifecycle
    assert "Unregister-ScheduledTask" in installer
    assert "TaskNamePattern" in uninstaller
    assert "[regex]::Escape($TaskPrefix)" in uninstaller
    assert "Assert-TradingOSTaskPrefix" in uninstaller


def test_repair_is_serialized_atomic_and_attempt_owned() -> None:
    source = read("Repair-TradingOSRuntime.ps1")

    assert "blocked_invalid_repair_state" in source
    assert "skipped_repair_in_progress" in source
    assert "Write-TradingOSJsonFileAtomic" in source
    assert "active_attempt_id" in source
    assert "-AttemptId $AttemptId" in source
    assert "restart_runtime_verified_health_refresh_pending" in source
    assert "Test-HealthGatesPassed" in source
    assert "blocked_unresolved_repair_attempt" in source
    assert "blocked_shutdown_requested" in source
    assert "-InvocationMode AutomaticRepair" in source


def test_command_line_identity_is_argv_aware() -> None:
    lifecycle = (AUTOSTART / "TradingOSRuntimeLifecycle.ps1").as_posix()
    script = (AUTOSTART / "Run-ForwardPaperLoop.ps1").as_posix().replace("/", "\\")
    command = rf"""
. '{lifecycle}'
$exe = (Get-Command powershell.exe).Source
$expected = '{script}'
$cases = @(
    [pscustomobject]@{{ expected = $true; command = ('"' + $exe + '" -NoProfile -File "' + $expected + '"') }},
    [pscustomobject]@{{ expected = $true; command = ('"' + $exe + '" -NonInteractive -ExecutionPolicy Bypass -F "' + $expected + '"') }},
    [pscustomobject]@{{ expected = $false; command = ('"' + $exe + '" -Command "Write-Output ''' + $expected + '''"') }},
    [pscustomobject]@{{ expected = $false; command = ('"' + $exe + '" /Command "' + $expected + '"') }},
    [pscustomobject]@{{ expected = $false; command = ('"' + $exe + '" -Com "' + $expected + '"') }},
    [pscustomobject]@{{ expected = $false; command = ('"' + $exe + '" -File "' + $expected + '.bak"') }},
    [pscustomobject]@{{ expected = $false; command = ('"' + $exe + '" -File "C:\\Windows\\Temp\\other.ps1" "' + $expected + '"') }}
)
$actual = @($cases | ForEach-Object {{
    Test-TradingOSPowerShellFileCommand -ProcessName 'powershell.exe' -ExecutablePath $exe -CommandLine $_.command -ExpectedScriptPath $expected
}})
for ($i = 0; $i -lt $cases.Count; $i++) {{ if ([bool]$actual[$i] -ne [bool]$cases[$i].expected) {{ exit 1 }} }}
if (Test-TradingOSPowerShellFileCommand -ProcessName 'System' -ExecutablePath '' -CommandLine '' -ExpectedScriptPath $expected) {{ exit 2 }}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_task_prefix_and_startup_leaf_validation_fail_closed() -> None:
    lifecycle = (AUTOSTART / "TradingOSRuntimeLifecycle.ps1").as_posix()
    command = rf"""
. '{lifecycle}'
$badPrefixes = @('*', '?', '[x]', 'bad"quote', "bad`nline", 'bad%value')
foreach ($value in $badPrefixes) {{
    $threw = $false
    try {{ $null = Assert-TradingOSTaskPrefix -TaskPrefix $value }} catch {{ $threw = $true }}
    if (-not $threw) {{ exit 1 }}
}}
if ((Assert-TradingOSTaskPrefix -TaskPrefix 'TradingOS.safe-1') -ne 'TradingOS.safe-1') {{ exit 2 }}
$threw = $false
try {{ $null = Assert-TradingOSStartupFileName -FileName '..\\victim.cmd' }} catch {{ $threw = $true }}
if (-not $threw) {{ exit 3 }}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_every_loop_launcher_honors_verified_lifecycle_or_shutdown() -> None:
    launchers = sorted(AUTOSTART.glob("Start-*Loop.ps1"))

    assert launchers
    for path in launchers:
        source = path.read_text(encoding="utf-8-sig")
        assert "TradingOSRuntimeLifecycle.ps1" in source, path.name
        assert (
            "Get-TradingOSRuntimeLaunchDisposition" in source
            or "Test-TradingOSRuntimeShutdownRequested" in source
        ), path.name


def test_every_loop_launcher_holds_lifecycle_mutex_through_job_launch() -> None:
    launchers = sorted(AUTOSTART.glob("Start-*Loop.ps1"))

    assert launchers
    for path in launchers:
        source = path.read_text(encoding="utf-8-sig")
        assert "Get-TradingOSRuntimeMutexName" in source, path.name
        assert "RuntimeOperationMutexAcquired" in source, path.name
        assert "Start-TradingOSRuntimeJobProcess" in source, path.name
        has_receipt_stop = "Stop-TradingOSRuntimeJobReceipt" in source
        has_journal_rollback = "Undo-TradingOSRuntimeComponentLaunch" in source
        assert has_receipt_stop or has_journal_rollback, path.name
        if has_journal_rollback:
            assert "Get-TradingOSRuntimeComponentLaunchConfirmation" in source, path.name
            assert ".confirmed" in source, path.name
            assert "start_unconfirmed" in source, path.name


def test_job_process_is_suspended_until_contained_and_receipted() -> None:
    lifecycle = read("TradingOSRuntimeLifecycle.ps1")

    native_create = lifecycle.split(
        "public static SuspendedJobProcess CreateSuspendedInJob", 1
    )[1].split("public static bool IsCurrentProcessInAnyJob", 1)[0]
    resume_method = lifecycle.split("public void Resume()", 1)[1].split(
        "public void Terminate", 1
    )[0]
    launcher = lifecycle.split("function Start-TradingOSRuntimeJobProcess", 1)[1].split(
        "function Stop-TradingOSRuntimeJobReceipt", 1
    )[0]

    job_attribute_at = native_create.index("UpdateProcThreadAttribute(JOB_LIST)")
    create_at = native_create.index("CreateProcessW(applicationPath")
    verify_atomic_assignment_at = native_create.index("IsProcessInJob(post-create)")
    wrap_at = native_create.index("new SuspendedJobProcess")
    identity_at = launcher.index("Test-TradingOSManagedScriptProcess")
    containment_at = launcher.index("IsProcessInNamedJob", identity_at)
    receipt_at = launcher.index(
        "Write-TradingOSJsonFileAtomic -Path $ReceiptPath", containment_at
    )
    suspended_journal_at = launcher.index(
        "-State 'suspended_assigned_receipted'", receipt_at
    )
    resume_at = launcher.index("$SuspendedProcess.Resume()")
    running_state_at = launcher.index("$Receipt['launch_state'] = 'running'", resume_at)
    running_receipt_at = launcher.index(
        "Write-TradingOSJsonFileAtomic -Path $ReceiptPath", running_state_at
    )
    verify_at = launcher.index(
        "Get-TradingOSRuntimeJobReceiptState", running_receipt_at
    )

    assert "CREATE_SUSPENDED | CREATE_NO_WINDOW" in native_create
    assert job_attribute_at < create_at < verify_atomic_assignment_at < wrap_at
    assert "PROC_THREAD_ATTRIBUTE_JOB_LIST" in native_create
    assert "InitializeProcThreadAttributeList(IntPtr.Zero, 2" in native_create
    assert "AssignProcessToJobObject(job, pi.hProcess)" not in native_create
    assert "jobAttributes.bInheritHandle = 1" in native_create
    assert "Marshal.WriteIntPtr(handleList, IntPtr.Size * 3, job)" in native_create
    assert "new IntPtr(IntPtr.Size * 4)" in native_create
    assert "ResumeThread(threadHandle)" in resume_method
    assert "CreateSuspendedInJob" in launcher
    assert "StartSuspendedInJob" not in launcher
    assert "Resolve-TradingOSTrustedRuntimeExecutable" in launcher
    assert "else { $FilePath }" in launcher
    assert "Test-TradingOSManagedScriptProcess -CimProcess $IdentityProcess" in launcher
    assert (
        identity_at
        < containment_at
        < receipt_at
        < suspended_journal_at
        < resume_at
        < running_state_at
        < running_receipt_at
        < verify_at
    )
    assert "launch_state = 'suspended_assigned'" in launcher
    assert "running_verified_job_contained" in launcher
    assert "$SuspendedProcess.Terminate(1)" in launcher
    assert "$SuspendedProcess.Dispose()" in launcher
    assert "process_creation_utc" in launcher
    assert "attempt_id" in launcher
    assert "remaining_receipts" in lifecycle


def test_shutdown_intent_remains_durable_until_start_commit() -> None:
    source = read("Start-TradingOSRuntime.ps1")

    rollback_at = source.index("Undo-TradingOSRuntimeLaunchAttempt")
    marker_cleanup_at = source.index(
        "if (-not $StartCommitted -and $ShutdownStartBypassAcquired"
    )
    commit_at = source.index("Complete-TradingOSRuntimeLaunchAttempt")
    marker_success_remove_at = source.index(
        "Remove-Item -LiteralPath $ShutdownStartMarkerPath", commit_at
    )
    sentinel_remove_at = source.index(
        "Remove-Item -LiteralPath $ShutdownSentinelPath", marker_success_remove_at
    )
    assert "[guid]::NewGuid().ToString()" in source
    assert rollback_at < marker_cleanup_at
    assert commit_at < marker_success_remove_at < sentinel_remove_at
    assert "runtime_startup_rollback_status.json" in source
    assert "failed_rollback" in read("TradingOSRuntimeLifecycle.ps1")


def test_shutdown_only_control_panel_loops_are_in_stop_inventory() -> None:
    manifest = json.loads((ROOT / "configs" / "TRADING_OS_RUNTIME_COMPONENTS.json").read_text(encoding="utf-8"))
    shutdown_ids = {row["id"] for row in manifest["shutdown_only_components"]}

    assert shutdown_ids == {
        "bitunix_wo105_v3_forward",
        "bitunix_wo105_v3r1_forward",
        "bitunix_wo105_v3r2_forward",
        "bitunix_wo105_v3r3_forward",
        "bybit_forward_gate_pulse",
        "liquidation_real_feed_status_refresh",
        "real_edge_autopilot_guard",
        "strategy_mix_guarded_1h_forward_observer",
    }
    stop = read("Stop-TradingOSRuntime.ps1")
    assert "$Manifest.shutdown_only_components" in stop
    assert "residual_job_receipts" in stop


def test_autostart_ownership_and_task_replacement_are_transactional() -> None:
    lifecycle = read("TradingOSRuntimeLifecycle.ps1")
    optimizer = read("Optimize-TradingOSRuntime.ps1")
    startup = read("Install-TradingOSStartupFolder.ps1")
    installer = read("Install-TradingOSAutostart.ps1")
    uninstaller = read("Uninstall-TradingOSAutostart.ps1")

    assert "Test-TradingOSManagedStartupContent" in lifecycle
    assert "Test-TradingOSManagedMaintenanceTask" in lifecycle
    assert "Test-TradingOSMaintenanceTaskReceiptOwnership" in lifecycle
    assert "Test-TradingOSManagedLegacyTask" in lifecycle
    assert "runtime_autostart_receipt.json" in optimizer
    assert "Refusing to overwrite or remove an unowned maintenance task" in optimizer
    assert "Committed autostart ownership receipt failed exact verification" in optimizer
    assert "skipped_prospective_degraded" not in optimizer
    assert "ExistingSameTaskXml" in optimizer
    assert "Export-ScheduledTask" in optimizer
    assert "Get-TradingOSAutostartMutexName" in optimizer
    assert "Get-TradingOSAutostartMutexName" in startup
    assert "Get-TradingOSAutostartMutexName" in installer
    assert "Get-TradingOSAutostartMutexName" in uninstaller
    assert "Autostart ownership receipt failed verification" in installer
    assert "Test-TradingOSManagedLegacyTask" in installer
    assert "Test-TradingOSManagedLegacyTask" in uninstaller
    assert "Export-ScheduledTask" in uninstaller
    assert "uninstall-quarantine" in uninstaller


def test_scheduled_task_ownership_helpers_fail_closed() -> None:
    lifecycle = (AUTOSTART / "TradingOSRuntimeLifecycle.ps1").as_posix()
    root = ROOT.as_posix().replace("/", "\\")
    command = rf"""
. '{lifecycle}'
$root = '{root}'
$description = 'TradingOS safe working-set maintenance for verified long-sleep PowerShell loops. No trading actions.'
$optimizer = Join-Path $root 'ops\autostart\Optimize-TradingOSRuntime.ps1'
$action = [pscustomobject]@{{ Execute = 'powershell.exe'; Arguments = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $optimizer + '" -MemoryOnly -MinimumTrimSleepSeconds 600 -TaskPrefix "TradingOS" -SkipMemoryMaintenanceInstall'); WorkingDirectory = $root }}
$repetition = [pscustomobject]@{{ Interval = 'PT15M'; Duration = 'P3650D'; StopAtDurationEnd = $true }}
$trigger = [pscustomobject]@{{ CimClass = [pscustomobject]@{{ CimClassName = 'MSFT_TaskTimeTrigger' }}; Repetition = $repetition }}
$task = [pscustomobject]@{{ TaskName = 'TradingOS_RuntimeMemoryMaintenance_15M'; TaskPath = '\'; Description = $description; Actions = @($action); Triggers = @($trigger) }}
$receipt = [pscustomobject]@{{ schema_version = 1; install_id = [guid]::NewGuid().ToString(); root = $root; task_prefix = 'TradingOS'; maintenance_task_name = $task.TaskName; maintenance_task_path = '\'; maintenance_action_execute = $action.Execute; maintenance_action_arguments = $action.Arguments; maintenance_working_directory = $action.WorkingDirectory; maintenance_interval = 'PT15M'; live_trading_locked = $true; can_trade = $false }}
if (-not (Test-TradingOSManagedMaintenanceTask -Task $task -Root $root -TaskPrefix 'TradingOS')) {{ exit 1 }}
if (-not (Test-TradingOSMaintenanceTaskReceiptOwnership -Task $task -Receipt $receipt -Root $root -TaskPrefix 'TradingOS')) {{ exit 2 }}
$action.Execute = 'C:\Temp\powershell.exe'
if (Test-TradingOSManagedMaintenanceTask -Task $task -Root $root -TaskPrefix 'TradingOS') {{ exit 3 }}
$action.Execute = 'powershell.exe'
$task.Description = 'foreign'
if (Test-TradingOSManagedMaintenanceTask -Task $task -Root $root -TaskPrefix 'TradingOS') {{ exit 4 }}
$task.Description = $description
$task.Triggers = @($trigger, $trigger)
if (Test-TradingOSManagedMaintenanceTask -Task $task -Root $root -TaskPrefix 'TradingOS') {{ exit 5 }}
$task.Triggers = @($trigger)
$receipt.maintenance_action_arguments = 'foreign'
if (Test-TradingOSMaintenanceTaskReceiptOwnership -Task $task -Receipt $receipt -Root $root -TaskPrefix 'TradingOS') {{ exit 6 }}

$panelAction = [pscustomobject]@{{ Execute = 'powershell.exe'; Arguments = ('-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $root 'ops\autostart\Start-TradingOSControlPanel.ps1') + '" -Port 8765'); WorkingDirectory = $root }}
$panelTrigger = [pscustomobject]@{{ CimClass = [pscustomobject]@{{ CimClassName = 'MSFT_TaskLogonTrigger' }} }}
$panelTask = [pscustomobject]@{{ TaskName = 'TradingOS_ControlPanel_Logon'; TaskPath = '\'; Description = 'Trading OS safe local control panel autostart. No trading orders.'; Actions = @($panelAction); Triggers = @($panelTrigger) }}
if (-not (Test-TradingOSManagedLegacyTask -Task $panelTask -Root $root -TaskPrefix 'TradingOS' -Kind ControlPanel)) {{ exit 7 }}
$panelAction.Execute = 'C:\Temp\powershell.exe'
if (Test-TradingOSManagedLegacyTask -Task $panelTask -Root $root -TaskPrefix 'TradingOS' -Kind ControlPanel) {{ exit 8 }}

$legacyRoot = Join-Path $env:USERPROFILE 'My Drive\04_PRODUCT_SHELLS\Trade\MAX_BitEvo_ALL_IN_ONE_UNIFIED_20260323'
$python = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\python.exe'
$forwardAction = [pscustomobject]@{{ Execute = $python; Arguments = ('"' + (Join-Path $legacyRoot 'tools\strategy_mix_forward_scheduler.py') + '" --cycles 1 --with-spot --out-prefix docs\STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08'); WorkingDirectory = $legacyRoot }}
$forwardRepeat = [pscustomobject]@{{ Interval = 'PT4H'; Duration = 'P3650D'; StopAtDurationEnd = $true }}
$forwardTrigger = [pscustomobject]@{{ CimClass = [pscustomobject]@{{ CimClassName = 'MSFT_TaskTimeTrigger' }}; Repetition = $forwardRepeat }}
$forwardTask = [pscustomobject]@{{ TaskName = 'TradingOS_ForwardPaper_4H'; TaskPath = '\'; Description = 'Trading OS forward paper monitor: public data only, no orders.'; Actions = @($forwardAction); Triggers = @($forwardTrigger) }}
if (-not (Test-TradingOSManagedLegacyTask -Task $forwardTask -Root $root -TaskPrefix 'TradingOS' -Kind ForwardPaper4H)) {{ exit 9 }}
$forwardAction.WorkingDirectory = $root
if (Test-TradingOSManagedLegacyTask -Task $forwardTask -Root $root -TaskPrefix 'TradingOS' -Kind ForwardPaper4H) {{ exit 10 }}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_modified_powershell_files_parse() -> None:
    files = sorted(path.name for path in AUTOSTART.glob("*.ps1"))
    quoted = ",".join(f"'{(AUTOSTART / name).as_posix()}'" for name in files)
    command = (
        f"$files=@({quoted});"
        "$failed=@();"
        "foreach($file in $files){"
        "$tokens=$null;$errors=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile($file,[ref]$tokens,[ref]$errors)|Out-Null;"
        "if($errors.Count){$failed += ($file + ':' + ($errors.Message -join '|'))}"
        "};"
        "if($failed.Count){$failed|Write-Error;exit 1}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
