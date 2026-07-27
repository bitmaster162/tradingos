from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOSTART = ROOT / "ops" / "autostart"
MANIFEST = ROOT / "configs" / "TRADING_OS_RUNTIME_COMPONENTS.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_every_managed_loop_uses_attempt_bound_shutdown_bypass() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    components = manifest["components"] + manifest["shutdown_only_components"]

    component_ids = [component["id"] for component in components]
    assert component_ids
    assert len(component_ids) == len(set(component_ids))
    for component in components:
        path = ROOT / component["script"]
        source = read(path)

        assert "[string]$LaunchAttemptId" in source, component["id"]
        assert "TradingOSRuntimeShutdownGate.ps1" in source, component["id"]
        assert "TradingOSRuntimeLifecycle.ps1" not in source, component["id"]
        assert "Get-Command Test-TradingOSRuntimeShutdownRequested" in source, component[
            "id"
        ]
        explicit_fail_closed = "$ShutdownRequested = $true" in source
        helper_fail_closed = all(
            marker in source
            for marker in (
                "function Test-LoopShutdownRequested",
                "if ($ShutdownGateResult -isnot [bool]) { return $true }",
                "catch {\n        return $true",
                "if (Test-LoopShutdownRequested) { exit 1 }",
            )
        )
        assert explicit_fail_closed or helper_fail_closed, component["id"]
        assert "([guid]$LaunchAttemptId).ToString()" in source, component["id"]
        assert (
            "Test-TradingOSRuntimeShutdownRequested -Root $Root "
            "-AllowedAttemptId $LaunchAttemptId"
        ) in source, component["id"]
        assert "Test-Path -LiteralPath (Join-Path $Root \"logs\\runtime_shutdown.request.json\")" not in source, component["id"]
        shutdown_gate_index = source.index("Test-TradingOSRuntimeShutdownRequested")
        lock_index = source.find(".lock.json")
        if lock_index >= 0:
            assert shutdown_gate_index < lock_index, component["id"]
        else:
            assert "Run-BitunixWO105V3ForwardLoop.ps1" in source, component["id"]
            assert shutdown_gate_index < source.index("$CoreScript"), component["id"]


def test_every_runtime_job_launcher_forwards_its_attempt_id() -> None:
    wrappers = sorted(AUTOSTART.glob("Start-*Loop.ps1"))

    assert len(wrappers) == 12
    for path in wrappers:
        source = read(path)
        matches = re.findall(
            r'["\']-LaunchAttemptId["\']\s*,\s*\$LaunchAttemptId', source
        )
        assert len(matches) == 1, path.name

    runtime = read(AUTOSTART / "Start-TradingOSRuntime.ps1")
    direct_launches = re.findall(
        r"^\s*Start-TradingOSRuntimeJobProcess\b[^\r\n]*-Arguments\s+\$(\w+)",
        runtime,
        flags=re.MULTILINE,
    )
    assert len(direct_launches) == 17
    assert len(set(direct_launches)) == 17
    for argument_variable in direct_launches:
        assignment = re.search(
            rf"^\s*\${re.escape(argument_variable)}\s*=\s*([^\r\n]+)",
            runtime,
            flags=re.MULTILINE,
        )
        assert assignment is not None, argument_variable
        assert '-LaunchAttemptId `"$AttemptId`"' in assignment.group(1), argument_variable

    assert "& $PanelScript -Port $ControlPanelPort -LaunchAttemptId $AttemptId" in runtime


def test_shutdown_gate_is_lightweight_and_binds_the_live_reservation_owner() -> None:
    gate = read(AUTOSTART / "TradingOSRuntimeShutdownGate.ps1")
    lifecycle = read(AUTOSTART / "TradingOSRuntimeLifecycle.ps1")

    assert "Add-Type" not in gate
    assert len(gate.encode("utf-8")) < 12_000
    assert "Get-TradingOSRuntimeShutdownAttemptReservationPath" in gate
    assert "[string]$Reservation.state -eq 'reserved'" in gate
    assert "Reservation.invocation_id" in gate
    assert "Reservation.owner_process_creation_utc" in gate
    assert "Get-Process -Id ([int]$Reservation.owner_pid) -ErrorAction Stop" in gate
    assert "$CurrentSessionId -eq [int]$Reservation.session_id" in gate
    assert "TradingOSRuntimeShutdownGate.ps1" in lifecycle
    assert "function Test-TradingOSRuntimeShutdownRequested" not in lifecycle
