from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOSTART = ROOT / "ops" / "autostart"
LIFECYCLE = AUTOSTART / "TradingOSRuntimeLifecycle.ps1"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def run_isolated_powershell(script: str, *, temporary_root: Path) -> dict[str, object]:
    """Run lifecycle-only checks against a pytest temp root, never the live root."""

    env = os.environ.copy()
    env["TRADINGOS_LIFECYCLE_UNDER_TEST"] = str(LIFECYCLE)
    env["TRADINGOS_TEMP_ROOT"] = str(temporary_root)
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output_lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert output_lines, "isolated lifecycle check returned no result"
    return json.loads(output_lines[-1])


def test_attempt_id_replay_and_committed_mutations_fail_closed(tmp_path: Path) -> None:
    temporary_root = tmp_path / "isolated runtime root"
    temporary_root.mkdir()
    actual = run_isolated_powershell(
        r"""
$ErrorActionPreference = 'Stop'
. $env:TRADINGOS_LIFECYCLE_UNDER_TEST
$root = [System.IO.Path]::GetFullPath($env:TRADINGOS_TEMP_ROOT)
$attemptId = [guid]::NewGuid().ToString()

$reservation = Enter-TradingOSRuntimeLaunchAttempt -Root $root -AttemptId $attemptId -NewInvocation
if ([string]$reservation.state -ne 'reserved') { throw 'initial reservation was not reserved' }

$replayBlocked = $false
try {
    $null = Enter-TradingOSRuntimeLaunchAttempt -Root $root -AttemptId $attemptId -NewInvocation
} catch {
    if ($_.Exception.Message -notmatch 'already been used and cannot be replayed') { throw }
    $replayBlocked = $true
}

# Establish a strictly valid committed reservation without starting a process or
# writing anywhere outside this test's temporary root.
$attemptDirectory = Get-TradingOSRuntimeAttemptDirectory -Root $root -AttemptId $attemptId
$reservationPath = Join-Path $attemptDirectory 'reservation.json'
$committed = Get-Content -LiteralPath $reservationPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
$committed.state = 'committed'
$committed | Add-Member -NotePropertyName committed_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
$committed | Add-Member -NotePropertyName journal_count -NotePropertyValue ([int]0) -Force
Write-TradingOSJsonFileAtomic -Path $reservationPath -Payload $committed -Depth 6
$verifiedCommitted = Enter-TradingOSRuntimeLaunchAttempt -Root $root -AttemptId $attemptId
if ([string]$verifiedCommitted.state -ne 'committed') { throw 'committed reservation failed strict validation' }

$undoBlocked = $false
try {
    $null = Undo-TradingOSRuntimeLaunchAttempt -Root $root -AttemptId $attemptId
} catch {
    if ($_.Exception.Message -notmatch 'Refusing to roll back a committed runtime launch attempt') { throw }
    $undoBlocked = $true
}

$updateBlocked = $false
try {
    $null = Update-TradingOSRuntimeAttemptJournal -Root $root -AttemptId $attemptId -ComponentId 'synthetic_component' -State 'rolled_back'
} catch {
    if ($_.Exception.Message -notmatch 'Refusing to mutate a committed runtime launch attempt') { throw }
    $updateBlocked = $true
}

$registerBlocked = $false
$component = [pscustomobject]@{ id = 'synthetic_component'; script = 'ops\autostart\Run-Synthetic.ps1'; lock_path = 'logs\synthetic.lock.json' }
$disposition = [pscustomobject]@{ decision = 'start_new'; state_before = 'missing_lock'; quarantine_path = $null }
try {
    $null = Register-TradingOSRuntimeLaunchDisposition -Root $root -AttemptId $attemptId -Component $component -Disposition $disposition
} catch {
    if ($_.Exception.Message -notmatch 'Refusing to register work against a committed runtime launch attempt') { throw }
    $registerBlocked = $true
}

[ordered]@{
    replay_blocked = $replayBlocked
    undo_blocked = $undoBlocked
    update_blocked = $updateBlocked
    register_blocked = $registerBlocked
    live_trading_locked = $true
    can_trade = $false
} | ConvertTo-Json -Compress
""",
        temporary_root=temporary_root,
    )

    assert actual == {
        "replay_blocked": True,
        "undo_blocked": True,
        "update_blocked": True,
        "register_blocked": True,
        "live_trading_locked": True,
        "can_trade": False,
    }


def test_shutdown_sentinel_bypass_is_attempt_bound_and_canonical(tmp_path: Path) -> None:
    temporary_root = tmp_path / "isolated shutdown runtime"
    temporary_root.mkdir()
    actual = run_isolated_powershell(
        r"""
$ErrorActionPreference = 'Stop'
. $env:TRADINGOS_LIFECYCLE_UNDER_TEST
$root = [System.IO.Path]::GetFullPath($env:TRADINGOS_TEMP_ROOT)
$attemptId = [guid]::NewGuid().ToString()
$otherAttemptId = [guid]::NewGuid().ToString()
$reservation = Enter-TradingOSRuntimeLaunchAttempt -Root $root -AttemptId $attemptId -NewInvocation
$sentinel = Get-TradingOSRuntimeShutdownSentinelPath -Root $root
$marker = Get-TradingOSRuntimeShutdownStartMarkerPath -Root $root
Write-TradingOSJsonFileAtomic -Path $sentinel -Payload ([ordered]@{ request_id = [guid]::NewGuid().ToString(); live_trading_locked = $true; can_trade = $false }) -Depth 4
$blockedWithoutMarker = Test-TradingOSRuntimeShutdownRequested -Root $root -AllowedAttemptId $attemptId
$markerPayload = [ordered]@{
    schema_version = 1
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    attempt_id = $attemptId
    invocation_id = [string]$reservation.invocation_id
    root = $root
    live_trading_locked = $true
    can_trade = $false
}
Write-TradingOSJsonFileAtomic -Path $marker -Payload $markerPayload -Depth 5
$matchingAttemptBypasses = -not (Test-TradingOSRuntimeShutdownRequested -Root $root -AllowedAttemptId $attemptId)
$reservationPath = Get-TradingOSRuntimeShutdownAttemptReservationPath -Root $root -AttemptId $attemptId
$reservationPayload = Get-Content -LiteralPath $reservationPath -Raw | ConvertFrom-Json

$reservationPayload.state = 'committed'
Write-TradingOSJsonFileAtomic -Path $reservationPath -Payload $reservationPayload -Depth 6
$committedReservationBlocked = Test-TradingOSRuntimeShutdownRequested -Root $root -AllowedAttemptId $attemptId
$reservationPayload.state = 'reserved'

$originalInvocationId = [string]$reservationPayload.invocation_id
$reservationPayload.invocation_id = $otherAttemptId
Write-TradingOSJsonFileAtomic -Path $reservationPath -Payload $reservationPayload -Depth 6
$reservationInvocationMismatchBlocked = Test-TradingOSRuntimeShutdownRequested -Root $root -AllowedAttemptId $attemptId
$reservationPayload.invocation_id = $originalInvocationId

$originalOwnerPid = [int]$reservationPayload.owner_pid
$reservationPayload.owner_pid = 2147483647
Write-TradingOSJsonFileAtomic -Path $reservationPath -Payload $reservationPayload -Depth 6
$missingOwnerBlocked = Test-TradingOSRuntimeShutdownRequested -Root $root -AllowedAttemptId $attemptId
$reservationPayload.owner_pid = $originalOwnerPid

$originalOwnerCreation = [string]$reservationPayload.owner_process_creation_utc
$reservationPayload.owner_process_creation_utc = (Get-Date).ToUniversalTime().AddDays(-1).ToString('o')
Write-TradingOSJsonFileAtomic -Path $reservationPath -Payload $reservationPayload -Depth 6
$ownerCreationMismatchBlocked = Test-TradingOSRuntimeShutdownRequested -Root $root -AllowedAttemptId $attemptId
$reservationPayload.owner_process_creation_utc = $originalOwnerCreation
Write-TradingOSJsonFileAtomic -Path $reservationPath -Payload $reservationPayload -Depth 6

$markerPayload.invocation_id = $otherAttemptId
Write-TradingOSJsonFileAtomic -Path $marker -Payload $markerPayload -Depth 5
$markerInvocationMismatchBlocked = Test-TradingOSRuntimeShutdownRequested -Root $root -AllowedAttemptId $attemptId
$markerPayload.invocation_id = $originalInvocationId
Write-TradingOSJsonFileAtomic -Path $marker -Payload $markerPayload -Depth 5
[ordered]@{
    blocked_without_marker = $blockedWithoutMarker
    matching_attempt_bypasses = $matchingAttemptBypasses
    other_attempt_blocked = Test-TradingOSRuntimeShutdownRequested -Root $root -AllowedAttemptId $otherAttemptId
    automatic_blocked = Test-TradingOSRuntimeShutdownRequested -Root $root
    committed_reservation_blocked = $committedReservationBlocked
    reservation_invocation_mismatch_blocked = $reservationInvocationMismatchBlocked
    missing_owner_blocked = $missingOwnerBlocked
    owner_creation_mismatch_blocked = $ownerCreationMismatchBlocked
    marker_invocation_mismatch_blocked = $markerInvocationMismatchBlocked
    canonical_sentinel_remains = Test-Path -LiteralPath $sentinel
    live_trading_locked = $true
    can_trade = $false
} | ConvertTo-Json -Compress
""",
        temporary_root=temporary_root,
    )

    assert actual == {
        "blocked_without_marker": True,
        "matching_attempt_bypasses": True,
        "other_attempt_blocked": True,
        "automatic_blocked": True,
        "committed_reservation_blocked": True,
        "reservation_invocation_mismatch_blocked": True,
        "missing_owner_blocked": True,
        "owner_creation_mismatch_blocked": True,
        "marker_invocation_mismatch_blocked": True,
        "canonical_sentinel_remains": True,
        "live_trading_locked": True,
        "can_trade": False,
    }


def test_cross_session_receipt_is_stale_only_after_fail_closed_process_inventory(
    tmp_path: Path,
) -> None:
    temporary_root = tmp_path / "isolated session runtime"
    (temporary_root / "ops" / "autostart").mkdir(parents=True)
    dummy_script = temporary_root / "ops" / "autostart" / "Run-OldSession.ps1"
    dummy_script.write_text("Start-Sleep -Seconds 60\n", encoding="utf-8")
    actual = run_isolated_powershell(
        r"""
$ErrorActionPreference = 'Stop'
. $env:TRADINGOS_LIFECYCLE_UNDER_TEST
$root = [System.IO.Path]::GetFullPath($env:TRADINGOS_TEMP_ROOT)
$script = Join-Path $root 'ops\autostart\Run-OldSession.ps1'
$componentId = 'old_session_loop'
$attemptId = [guid]::NewGuid().ToString()
$jobName = Get-TradingOSRuntimeJobName -Root $root -ComponentId $componentId
$exe = (Get-Command powershell.exe -ErrorAction Stop).Source
$currentSession = [int](Get-Process -Id $PID -ErrorAction Stop).SessionId
$oldSession = 2147483000
if ($oldSession -eq $currentSession) { $oldSession-- }
$receiptPath = Get-TradingOSRuntimeJobReceiptPath -Root $root -ComponentId $componentId
Write-TradingOSJsonFileAtomic -Path $receiptPath -Payload ([ordered]@{
    schema_version = 2
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    component = $componentId
    root = $root
    attempt_id = $attemptId
    job_name = $jobName
    pid = 2147483001
    process_creation_utc = (Get-Date).ToUniversalTime().ToString('o')
    executable_path = $exe
    command_line = ([TradingOSRuntimeJobNative]::QuoteArgument($exe) + ' -NoProfile -File ' + [TradingOSRuntimeJobNative]::QuoteArgument($script))
    expected_script_path = $script
    session_id = [int]$oldSession
    launch_state = 'running'
    live_trading_locked = $true
    can_trade = $false
}) -Depth 6
$snapshot = Get-TradingOSProcessSnapshot
$state = Get-TradingOSRuntimeJobReceiptState -Root $root -ComponentId $componentId -ExpectedScriptPath $script -ProcessSnapshot $snapshot
$stop = Stop-TradingOSRuntimeJobReceipt -Root $root -ComponentId $componentId -ExpectedAttemptId $attemptId -ExpectedProcessId 2147483001 -ExpectedScriptPath $script
[ordered]@{
    decision = [string]$state.decision
    exact_script_pids = @($state.exact_script_pids).Count
    old_session_process_count = [int]$state.old_session_process_count
    stop_success = [bool]$stop.success
    receipt_remaining = Test-Path -LiteralPath $receiptPath
    snapshot_contains_current_pid = $snapshot.ContainsKey([int]$PID)
    live_trading_locked = $true
    can_trade = $false
} | ConvertTo-Json -Compress
""",
        temporary_root=temporary_root,
    )

    assert actual == {
        "decision": "stale_receipt_session_mismatch",
        "exact_script_pids": 0,
        "old_session_process_count": 0,
        "stop_success": True,
        "receipt_remaining": False,
        "snapshot_contains_current_pid": True,
        "live_trading_locked": True,
        "can_trade": False,
    }


def test_rollback_leaves_preexisting_foreign_receipt_for_unstarted_component(
    tmp_path: Path,
) -> None:
    temporary_root = tmp_path / "isolated reserved rollback"
    (temporary_root / "configs").mkdir(parents=True)
    (temporary_root / "ops" / "autostart").mkdir(parents=True)
    dummy_script = temporary_root / "ops" / "autostart" / "Run-Reserved.ps1"
    dummy_script.write_text("Start-Sleep -Seconds 60\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "runtime_root_policy": "local_outside_google_drive",
        "live_trading_locked": True,
        "components": [
            {
                "id": "reserved_loop",
                "script": "ops/autostart/Run-Reserved.ps1",
                "lock_path": "logs/reserved_loop.lock.json",
                "status_path": "logs/reserved_loop_status.json",
                "start_owner": "runtime",
                "required": True,
                "trim_working_set": False,
                "default_sleep_seconds": 60,
            }
        ],
        "shutdown_only_components": [],
    }
    (temporary_root / "configs" / "TRADING_OS_RUNTIME_COMPONENTS.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    actual = run_isolated_powershell(
        r"""
$ErrorActionPreference = 'Stop'
. $env:TRADINGOS_LIFECYCLE_UNDER_TEST
$root = [System.IO.Path]::GetFullPath($env:TRADINGOS_TEMP_ROOT)
$manifest = Get-TradingOSRuntimeManifest -Root $root
$component = $manifest.components[0]
$componentId = [string]$component.id
$script = Resolve-TradingOSRuntimePath -Root $root -Path ([string]$component.script)
$attemptId = [guid]::NewGuid().ToString()
$foreignAttemptId = [guid]::NewGuid().ToString()
$null = Enter-TradingOSRuntimeLaunchAttempt -Root $root -AttemptId $attemptId -NewInvocation
$receiptPath = Get-TradingOSRuntimeJobReceiptPath -Root $root -ComponentId $componentId
$jobName = Get-TradingOSRuntimeJobName -Root $root -ComponentId $componentId
$exe = (Get-Command powershell.exe -ErrorAction Stop).Source
$sessionId = [int](Get-Process -Id $PID -ErrorAction Stop).SessionId
Write-TradingOSJsonFileAtomic -Path $receiptPath -Payload ([ordered]@{
    schema_version = 2
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    component = $componentId
    root = $root
    attempt_id = $foreignAttemptId
    job_name = $jobName
    pid = 2147483001
    process_creation_utc = (Get-Date).ToUniversalTime().ToString('o')
    executable_path = $exe
    command_line = ([TradingOSRuntimeJobNative]::QuoteArgument($exe) + ' -NoProfile -File ' + [TradingOSRuntimeJobNative]::QuoteArgument($script))
    expected_script_path = $script
    session_id = $sessionId
    launch_state = 'running'
    live_trading_locked = $true
    can_trade = $false
}) -Depth 6
$lockPath = Resolve-TradingOSRuntimePath -Root $root -Path ([string]$component.lock_path)
Write-TradingOSJsonFileAtomic -Path $lockPath -Payload ([ordered]@{ pid = 2147483001; started_at = (Get-Date).ToUniversalTime().ToString('o') }) -Depth 4
$disposition = Get-TradingOSRuntimeLaunchDisposition -Root $root -Manifest $manifest -ComponentId $componentId -AttemptId $attemptId
if (-not $disposition.should_start) { throw "expected recoverable stale disposition: $($disposition.decision)" }
$rollback = Undo-TradingOSRuntimeLaunchAttempt -Root $root -AttemptId $attemptId
$journal = Read-TradingOSRuntimeAttemptJournal -Root $root -AttemptId $attemptId -ComponentId $componentId
$receipt = Read-TradingOSRuntimeJobReceipt -Root $root -ComponentId $componentId -ExpectedScriptPath $script
[ordered]@{
    disposition = [string]$disposition.decision
    rollback_success = [bool]$rollback.success
    rollback_status = [string]$rollback.status
    journal_state = [string]$journal.state
    receipt_attempt_unchanged = ([guid][string]$receipt.attempt_id -eq [guid]$foreignAttemptId)
    lock_restored = Test-Path -LiteralPath $lockPath
    quarantine_remaining = Test-Path -LiteralPath ([string]$disposition.quarantine_path)
    action = [string]$rollback.jobs[0].actions[0].decision
    exact_processes_remaining = @($rollback.jobs[0].exact_processes_remaining).Count
    live_trading_locked = $true
    can_trade = $false
} | ConvertTo-Json -Compress
""",
        temporary_root=temporary_root,
    )

    assert actual == {
        "disposition": "start_after_stale_lock_quarantine",
        "rollback_success": True,
        "rollback_status": "failed_rolled_back",
        "journal_state": "rolled_back",
        "receipt_attempt_unchanged": True,
        "lock_restored": True,
        "quarantine_remaining": False,
        "action": "preexisting_foreign_receipt_left_untouched",
        "exact_processes_remaining": 0,
        "live_trading_locked": True,
        "can_trade": False,
    }


def test_stop_preflights_all_receipts_and_exact_processes_before_termination() -> None:
    source = read(AUTOSTART / "Stop-TradingOSRuntime.ps1")

    snapshot_at = source.index("$PreflightSnapshot = Get-TradingOSProcessSnapshot")
    receipt_inventory_at = source.index(
        "foreach ($ReceiptFile in @(Get-ChildItem -LiteralPath $ReceiptDirectory -Filter '*.json' -File -ErrorAction Stop))"
    )
    component_preflight_at = source.index("foreach ($Component in $StopComponents)")
    preflight_decision_at = source.index("$PreflightSucceeded = $Failures.Count -eq 0")
    termination_gate_at = source.index("if (-not $WhatIf -and $PreflightSucceeded)")
    first_receipt_stop_at = source.index(
        "Stop-TradingOSRuntimeJobReceipt", termination_gate_at
    )

    assert (
        snapshot_at
        < receipt_inventory_at
        < component_preflight_at
        < preflight_decision_at
        < termination_gate_at
        < first_receipt_stop_at
    )
    assert "unknown_runtime_job_receipt:" in source
    assert "unrequested_control_panel_receipt_blocks_full_stop:" in source
    assert "$KnownComponentIds.Contains($ReceiptComponentId)" in source
    assert "-ProcessSnapshot $PreflightSnapshot" in source
    assert "$ExactPids = @($State.matching_script_pids)" in source
    assert (
        "$ExactIdentity = $ExactPids.Count -eq 1 -and [int]$ExactPids[0] -eq [int]$State.pid"
        in source
    )
    assert "blocked_uncontained_duplicate_or_unowned_runtime:" in source
    assert "if ($ExactIdentity -and $Contained -and $LockOwned)" in source
    assert "$ReceiptOwnsExactJob" in source
    assert "Quarantine-UnverifiableCapturedLock" in source


def test_windows_job_membership_is_atomic_at_process_creation() -> None:
    source = read(LIFECYCLE)
    create = source.split(
        "public static SuspendedJobProcess CreateSuspendedInJob", 1
    )[1].split("public static bool IsCurrentProcessInAnyJob", 1)[0]

    handle_list_at = create.index("UpdateProcThreadAttribute(HANDLE_LIST)")
    job_list_at = create.index("UpdateProcThreadAttribute(JOB_LIST)")
    create_process_at = create.index("CreateProcessW(applicationPath")
    verify_membership_at = create.index("IsProcessInJob(post-create)")

    assert "PROC_THREAD_ATTRIBUTE_JOB_LIST = new IntPtr(0x0002000D)" in source
    assert "InitializeProcThreadAttributeList(IntPtr.Zero, 2" in create
    assert "jobAttributes.bInheritHandle = 1" in create
    assert "Marshal.WriteIntPtr(handleList, IntPtr.Size * 3, job)" in create
    assert "new IntPtr(IntPtr.Size * 4)" in create
    assert (
        "CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT"
        in create
    )
    assert handle_list_at < job_list_at < create_process_at < verify_membership_at
    assert "AssignProcessToJobObject" not in create
    assert "Process was not atomically assigned to the runtime job" in create


def test_full_job_launch_commit_and_verified_stop_isolated(tmp_path: Path) -> None:
    temporary_root = tmp_path / "isolated managed runtime"
    (temporary_root / "configs").mkdir(parents=True)
    (temporary_root / "ops" / "autostart").mkdir(parents=True)
    dummy_script = temporary_root / "ops" / "autostart" / "Run-DummyLoop.ps1"
    dummy_script.write_text(
        "$ErrorActionPreference = 'Stop'\nwhile ($true) { Start-Sleep -Seconds 60 }\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "runtime_root_policy": "local_outside_google_drive",
        "live_trading_locked": True,
        "components": [
            {
                "id": "dummy_loop",
                "script": "ops/autostart/Run-DummyLoop.ps1",
                "lock_path": "logs/dummy_loop.lock.json",
                "status_path": "logs/dummy_loop_status.json",
                "start_owner": "runtime",
                "required": True,
                "trim_working_set": False,
                "default_sleep_seconds": 60,
            }
        ],
        "shutdown_only_components": [],
    }
    (temporary_root / "configs" / "TRADING_OS_RUNTIME_COMPONENTS.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    actual = run_isolated_powershell(
        r"""
$ErrorActionPreference = 'Stop'
. $env:TRADINGOS_LIFECYCLE_UNDER_TEST
$root = [System.IO.Path]::GetFullPath($env:TRADINGOS_TEMP_ROOT)
$manifest = Get-TradingOSRuntimeManifest -Root $root
$component = $manifest.components[0]
$script = Resolve-TradingOSRuntimePath -Root $root -Path ([string]$component.script)
$attemptId = [guid]::NewGuid().ToString()
$reservation = Enter-TradingOSRuntimeLaunchAttempt -Root $root -AttemptId $attemptId -NewInvocation
$disposition = Get-TradingOSRuntimeLaunchDisposition -Root $root -Manifest $manifest -ComponentId 'dummy_loop' -AttemptId $attemptId
if (-not $disposition.should_start) { throw "isolated launch disposition blocked: $($disposition.decision)" }
$started = Start-TradingOSRuntimeJobProcess -Root $root -ComponentId 'dummy_loop' -AttemptId $attemptId -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $script) -WorkingDirectory $root -ExpectedScriptPath $script
try { $startedPid = [int]$started.Id } finally { $started.Dispose() }
$before = Get-TradingOSRuntimeJobReceiptState -Root $root -ComponentId 'dummy_loop' -ExpectedScriptPath $script
try {
    $beforeDecision = [string]$before.decision
    $schemaVersion = [int]$before.receipt.schema_version
    $launchState = [string]$before.receipt.launch_state
    $receiptAttempt = [string]$before.receipt.attempt_id
} finally {
    if ($before.process) { $before.process.Dispose() }
}
$commit = Complete-TradingOSRuntimeLaunchAttempt -Root $root -AttemptId $attemptId
$stop = Stop-TradingOSRuntimeJobReceipt -Root $root -ComponentId 'dummy_loop' -ExpectedAttemptId $attemptId -ExpectedProcessId $startedPid -ExpectedScriptPath $script
Start-Sleep -Milliseconds 150
$remaining = @(Get-TradingOSProcessSnapshot).Values | Where-Object { Test-TradingOSManagedScriptProcess -CimProcess $_ -ExpectedScriptPath $script }
[ordered]@{
    disposition = [string]$disposition.decision
    before = $beforeDecision
    schema_version = $schemaVersion
    launch_state = $launchState
    receipt_attempt_matches = ([guid]$receiptAttempt -eq [guid]$attemptId)
    commit = [string]$commit.decision
    stop_success = [bool]$stop.success
    remaining_exact_processes = @($remaining).Count
    receipt_remaining = Test-Path -LiteralPath (Get-TradingOSRuntimeJobReceiptPath -Root $root -ComponentId 'dummy_loop')
    live_trading_locked = $true
    can_trade = $false
} | ConvertTo-Json -Compress
""",
        temporary_root=temporary_root,
    )

    assert actual == {
        "disposition": "start_missing_lock_no_matching_process",
        "before": "running_verified_job_contained",
        "schema_version": 2,
        "launch_state": "running",
        "receipt_attempt_matches": True,
        "commit": "committed",
        "stop_success": True,
        "remaining_exact_processes": 0,
        "receipt_remaining": False,
        "live_trading_locked": True,
        "can_trade": False,
    }


def test_loop_wrappers_do_not_exit_callers_and_commit_standalone_attempts() -> None:
    wrappers = sorted(AUTOSTART.glob("Start-*Loop.ps1"))

    assert wrappers
    for wrapper in wrappers:
        source = read(wrapper)
        assert not re.search(r"(?im)^\s*exit(?:\s|$)", source), wrapper.name
        assert "$OwnLaunchAttempt = -not [bool]$LaunchAttemptId" in source, wrapper.name
        assert "if (-not $LaunchAttemptId)" in source, wrapper.name
        assert source.count("Complete-TradingOSRuntimeLaunchAttempt") >= 2, wrapper.name
        assert (
            "$OwnLaunchAttempt -and $LaunchDisposition.decision -eq \"already_running_verified\""
            in source
        ), wrapper.name
        assert "if ($Confirmed -and $OwnLaunchAttempt)" in source, wrapper.name
        assert source.count("attempt_commit = $AttemptCommit") >= 2, wrapper.name

    panel = read(AUTOSTART / "Start-TradingOSControlPanel.ps1")
    assert not re.search(r"(?im)^\s*exit(?:\s|$)", panel)
    assert "$OwnLaunchAttempt = -not [bool]$LaunchAttemptId" in panel
    assert panel.count("Complete-TradingOSRuntimeLaunchAttempt") >= 2
    assert "-AllowedAttemptId $LaunchAttemptId" in panel
