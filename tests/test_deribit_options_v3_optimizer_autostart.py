from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPTIMIZER = (ROOT / "ops" / "autostart" / "Optimize-TradingOSRuntime.ps1").read_text(encoding="utf-8")
RUNTIME = (ROOT / "ops" / "autostart" / "Start-TradingOSRuntime.ps1").read_text(encoding="utf-8")


def test_optimizer_launches_v3_data_layer_in_dependency_order() -> None:
    assert "Start-DeribitOptionsV3DataLayer.ps1" in OPTIMIZER
    collector_call = "& $DeribitOptionsV3Launcher -Component collector"
    readiness_call = "& $DeribitOptionsV3Launcher -Component readiness"
    assert collector_call in OPTIMIZER
    assert readiness_call in OPTIMIZER
    assert OPTIMIZER.index(collector_call) < OPTIMIZER.index(readiness_call)
    assert "upstream_collector_alive" in OPTIMIZER
    assert "deribit_options_v3_collector_startup_launch_only = $true" in OPTIMIZER
    assert "deribit_options_v3_readiness_startup_launch_only = $true" in OPTIMIZER


def test_v3_startup_does_not_enter_core_runtime_or_gain_restart_rights() -> None:
    assert "Start-DeribitOptionsV3DataLayer.ps1" not in RUNTIME
    assert "automatic_restart_allowed = $false" in OPTIMIZER
    assert "Stop-Process" not in OPTIMIZER
    assert "taskkill" not in OPTIMIZER.lower()
