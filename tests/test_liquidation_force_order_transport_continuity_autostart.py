from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def test_transport_continuity_sidecar_is_required_and_fail_closed() -> None:
    manifest = json.loads(read("configs/TRADING_OS_RUNTIME_COMPONENTS.json"))
    component = next(
        item for item in manifest["components"] if item["id"] == "liquidation_force_order_transport_continuity"
    )
    loop = read("ops/autostart/Run-LiquidationForceOrderTransportContinuityLoop.ps1")
    runtime = read("ops/autostart/Start-TradingOSRuntime.ps1")

    assert component["required"] is True
    assert component["default_sleep_seconds"] == 30
    assert component["trim_working_set"] is False
    assert "liquidation_force_order_transport_liveness_recorder.py" in loop
    assert "Enter-TradingOSLoopOwnership" in loop
    assert 'signals_allowed = $false' in loop
    assert 'paper_entries_allowed = $false' in loop
    assert 'orders_allowed = $false' in loop
    assert 'can_trade = $false' in loop
    assert 'ComponentId "liquidation_force_order_transport_continuity"' in runtime
    assert "LiquidationForceOrderTransportContinuitySleepSeconds" in runtime
