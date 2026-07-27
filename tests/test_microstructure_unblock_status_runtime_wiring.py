from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_runtime_start_uses_pid_safe_unblock_launcher() -> None:
    script = read("ops/autostart/Start-TradingOSRuntime.ps1")

    assert "MicrostructureUnblockStatusSleepSeconds" in script
    assert "Start-MicrostructureUnblockStatusLoop.ps1" in script
    assert "microstructure_unblock_status_alive" in script
    assert "$MicrostructureUnblockStatusStartResult.pid" in script
    assert "microstructure_unblock_status_observability_only = $true" in script


def test_optimizer_repairs_missing_unblock_loop() -> None:
    script = read("ops/autostart/Optimize-TradingOSRuntime.ps1")

    assert "microstructure_unblock_status_loop.lock.json" in script
    assert "-not $MicrostructureUnblockStatusAliveBefore" in script
    assert "-MicrostructureUnblockStatusSleepSeconds $MicrostructureUnblockStatusSleepSeconds" in script
    assert "microstructure_unblock_status_loop_alive" in script


def test_status_and_stop_cover_unblock_loop() -> None:
    status = read("ops/autostart/Get-TradingOSAutostartStatus.ps1")
    stop = read("ops/autostart/Stop-TradingOSRuntime.ps1")

    assert "cross_venue_microstructure_unblock_loop" in status
    assert "cross_venue_microstructure_unblock_report" in status
    assert "microstructure_unblock_status_loop.lock.json" in stop
