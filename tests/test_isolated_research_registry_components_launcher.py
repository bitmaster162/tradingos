from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "ops" / "autostart" / "Start-IsolatedResearchRegistryComponents.ps1"
REGISTRY = (
    ROOT
    / "HANDOFF"
    / "INCOMING"
    / "codex"
    / "20260712_research_runtime_supervisor"
    / "REGISTRY.json"
)
STATUS_COMMAND = ROOT / "ops" / "autostart" / "Get-TradingOSAutostartStatus.ps1"


def test_launcher_is_explicit_fail_closed_and_orderless() -> None:
    source = LAUNCHER.read_text(encoding="utf-8-sig")

    assert "TradingOSIsolatedResearchRegistryComponentsLauncher" in source
    assert "ISOLATED_RESEARCH_RUNTIME_REGISTRY_V3" in source
    assert "blocked_duplicate_component_processes" in source
    assert "blocked_preflight_failed" in source
    assert "full_registry_observability_only" in source
    assert "automatic_restart_allowed = $false" in source
    assert "process_stop_allowed = $false" in source
    assert "credentials_allowed = $false" in source
    assert "signals_allowed = $false" in source
    assert "paper_entries_allowed = $false" in source
    assert "orders_allowed = $false" in source
    assert "can_trade = $false" in source
    assert "Start-Process" in source
    assert "-WindowStyle Hidden" in source
    assert "Stop-Process" not in source
    assert "taskkill" not in source.lower()
    assert 'scope = "full_registry_observability_only"' in source
    assert "affects_managed_launcher_status = $false" in source
    assert '$Status = if ($Failed.Count -eq 0)' in source


def test_launcher_only_contains_current_non_deribit_active_components() -> None:
    source = LAUNCHER.read_text(encoding="utf-8-sig")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    active = {item["component_id"] for item in registry["active_components"]}
    expected = {
        "trade_arrival_burst",
        "portfolio_overlap",
        "stablecoin_supply_v3",
        "stablecoin_readiness",
        "macro_liquidity",
        "macro_readiness",
    }

    assert expected <= active
    assert "large_trade_tail" not in source
    assert "book_replenishment" not in source
    for component_id in expected:
        assert f'"{component_id}"' in source


def test_registry_tracks_deribit_v3_and_defers_v2_bound_skew() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    active = {item["component_id"] for item in registry["active_components"]}
    retired = {item["component_id"] for item in registry["retired_components"]}
    deferred = {item["component_id"]: item for item in registry["deferred_components"]}

    assert "deribit_options_surface_v3" in active
    assert "deribit_options_readiness_v2" in active
    assert "deribit_options_v2" in retired
    assert "options_readiness" in retired
    assert "deribit_options_skew_forward" not in active
    assert deferred["deribit_options_skew_forward"]["automatic_start_allowed"] is False
    assert deferred["deribit_options_skew_forward"]["retune_allowed"] is False
    assert deferred["deribit_options_skew_forward"]["can_trade"] is False


def test_autostart_status_exposes_explicit_recovery_launcher() -> None:
    source = STATUS_COMMAND.read_text(encoding="utf-8-sig")

    assert "isolated_research_registry_components_launcher_status.json" in source
    assert "isolated_research_registry_components_launcher" in source


def test_login_optimizer_launches_managed_research_components_once_without_restart() -> None:
    source = (ROOT / "ops" / "autostart" / "Optimize-TradingOSRuntime.ps1").read_text(encoding="utf-8-sig")

    assert "Start-IsolatedResearchRegistryComponents.ps1" in source
    assert "$IsolatedResearchRegistryLauncher -Component all" in source
    assert 'start_isolated_research_registry_components" -Status' in source
    assert "isolated_research_registry_components_startup_launch_only = $true" in source
    assert "isolated_research_registry_components_automatic_restart_allowed = $false" in source
    assert "isolated_research_registry_components_status" in source
