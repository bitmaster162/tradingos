from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_derivatives_squeeze_observer_is_wired_after_data_refresh() -> None:
    script = (ROOT / "ops" / "autostart" / "Run-ForwardPaperOnce.ps1").read_text(encoding="utf-8-sig")

    collector_at = script.index("tools\\oi_funding_data_quality_collector.py")
    observer_at = script.index("tools\\derivatives_squeeze_disagreement_forward_observer.py")

    assert collector_at < observer_at
    assert "$null -eq $DataQualityExitCode -or $DataQualityExitCode -eq 0" in script
    assert "DERIVATIVES_SQUEEZE_DISAGREEMENT_FORWARD_OBSERVER_2026-07-03" in script


def test_scheduler_surfaces_observer_failure_without_trade_permission() -> None:
    script = (ROOT / "ops" / "autostart" / "Run-ForwardPaperOnce.ps1").read_text(encoding="utf-8-sig")

    assert "completed_derivatives_squeeze_warning" in script
    assert "derivatives_squeeze_exit_code" in script
    assert "live_trading_locked = $true" in script


def test_alt_breadth_observer_runs_only_after_both_public_market_refreshes() -> None:
    script = (ROOT / "ops" / "autostart" / "Run-ForwardPaperOnce.ps1").read_text(encoding="utf-8-sig")

    spot_at = script.index('"--market", "spot"')
    futures_at = script.index('"--market", "futures"')
    observer_at = script.index("tools\\alt_breadth_dislocation_forward_observer.py")

    assert spot_at < futures_at < observer_at
    assert "$AltSpotTailExitCode -eq 0 -and $AltFuturesTailExitCode -eq 0" in script
    assert "ETHUSDT,SOLUSDT,BCHUSDT" in script
    assert "SkipAltBreadthRefresh" in script


def test_scheduler_surfaces_alt_data_and_observer_failures() -> None:
    script = (ROOT / "ops" / "autostart" / "Run-ForwardPaperOnce.ps1").read_text(encoding="utf-8-sig")

    assert "completed_alt_breadth_data_warning" in script
    assert "completed_alt_breadth_observer_warning" in script
    assert "alt_spot_tail_exit_code" in script
    assert "alt_futures_tail_exit_code" in script
    assert "alt_breadth_exit_code" in script
    assert "live_trading_locked = $true" in script


def test_scheduler_refreshes_frontier_then_waiting_board() -> None:
    script = (ROOT / "ops" / "autostart" / "Run-ForwardPaperOnce.ps1").read_text(encoding="utf-8-sig")

    alt_observer_at = script.index("tools\\alt_breadth_dislocation_forward_observer.py")
    frontier_at = script.index("tools\\strategy_research_frontier_matrix.py")
    waiting_at = script.index("tools\\edge_waiting_board.py")

    assert alt_observer_at < frontier_at < waiting_at
    assert "$FrontierExitCode -eq 0" in script
    assert '"--derivatives-squeeze"' in script
    assert '"--alt-breadth"' in script
    assert "completed_strategy_frontier_warning" in script
    assert "completed_edge_waiting_board_warning" in script
    assert "strategy_frontier_exit_code" in script
    assert "edge_waiting_board_exit_code" in script
