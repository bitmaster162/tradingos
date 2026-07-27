from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "ops" / "autostart" / "Start-DeribitOptionsV3DataLayer.ps1").read_text(encoding="utf-8")


def test_launcher_is_fail_closed_and_never_stops_or_trades() -> None:
    assert 'ValidateSet("collector", "readiness")' in SOURCE
    assert "blocked_upstream_collector_not_alive" in SOURCE
    assert "blocked_duplicate_processes" in SOURCE
    assert "blocked_preflight_failed" in SOURCE
    assert "automatic_restart_allowed = $false" in SOURCE
    assert "process_stop_allowed = $false" in SOURCE
    assert "signals_allowed = $false" in SOURCE
    assert "orders_allowed = $false" in SOURCE
    assert "can_trade = $false" in SOURCE
    assert "Stop-Process" not in SOURCE
    assert "taskkill" not in SOURCE.lower()


def test_launcher_uses_only_v3_collector_and_v2_readiness() -> None:
    assert "deribit_options_surface_collector_v3.py" in SOURCE
    assert "DERIBIT_OPTIONS_SURFACE_COLLECTOR_V3_LOCK.json" in SOURCE
    assert "deribit_options_readiness_guard_v2.py" in SOURCE
    assert "DERIBIT_OPTIONS_READINESS_GUARD_V2_LOCK.json" in SOURCE
    assert "deribit_options_skew_forward" not in SOURCE
