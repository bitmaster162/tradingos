from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def test_aggressor_flow_collector_is_required_data_only_runtime_component() -> None:
    manifest = json.loads(read("configs/TRADING_OS_RUNTIME_COMPONENTS.json"))
    component = next(
        item for item in manifest["components"]
        if item["id"] == "binance_spot_perp_aggressor_flow"
    )
    loop = read("ops/autostart/Run-BinanceSpotPerpAggressorFlowLoop.ps1")
    runtime = read("ops/autostart/Start-TradingOSRuntime.ps1")

    assert component["required"] is True
    assert component["default_sleep_seconds"] == 10
    assert component["start_owner"] == "runtime"
    assert component["trim_working_set"] is False
    assert "binance_spot_perp_aggressor_flow_collector.py" in loop
    assert 'collector_only = $true' in loop
    assert 'credentials_allowed = $false' in loop
    assert 'strategy_search_allowed = $false' in loop
    assert 'signals_allowed = $false' in loop
    assert 'paper_entries_allowed = $false' in loop
    assert 'telegram_send_allowed = $false' in loop
    assert 'orders_allowed = $false' in loop
    assert 'can_trade = $false' in loop
    assert 'ComponentId "binance_spot_perp_aggressor_flow"' in runtime
    assert "BinanceSpotPerpAggressorFlowSleepSeconds" in runtime
    assert "binance_spot_perp_aggressor_flow_can_trade = $false" in runtime
