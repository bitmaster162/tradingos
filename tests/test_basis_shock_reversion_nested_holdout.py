from __future__ import annotations

from tools.basis_shock_reversion_nested_holdout import (
    ReversionConfig,
    generate_signals,
    rolling_basis_z,
    simulate_window,
    trade_pnl,
)


def row(index: int, basis_bps: float, spot: float = 100.0) -> dict:
    futures = spot * (1.0 + basis_bps / 10_000.0)
    return {
        "time": f"2024-01-{1 + index // 24:02d}T{index % 24:02d}:00:00+00:00",
        "time_ms": index * 3_600_000,
        "spot_open": spot,
        "spot_close": spot,
        "futures_open": futures,
        "futures_close": futures,
        "basis_close_bps": basis_bps,
    }


def test_rolling_z_excludes_current_bar_from_baseline() -> None:
    rows = [row(index, value) for index, value in enumerate([0.0, 1.0, -1.0, 10.0])]
    z_values = rolling_basis_z(rows, 3)
    assert z_values[:3] == [None, None, None]
    assert z_values[3] > 10.0


def test_signal_requires_threshold_cross_and_minimum_basis() -> None:
    rows = [row(index, value) for index, value in enumerate([0.0, 1.0, -1.0, 0.0, 8.0, 9.0, 2.0, 1.0])]
    z_values = rolling_basis_z(rows, 3)
    config = ReversionConfig("test", 3, 1.5, 0.0, 5.0, 12)
    assert generate_signals(config, rows, z_values) == [4]


def test_market_neutral_convergence_is_profitable_before_costs() -> None:
    entry = row(1, 100.0)
    exit_row = row(2, 0.0)
    pnl = trade_pnl(entry, exit_row, [], {0: 100.0}, fee_bps=0.0, slippage_bps=0.0)
    assert pnl["price_pnl_quote"] > 0
    assert pnl["net_return_bps"] > 0


def test_simulation_enters_next_bar_and_exits_after_observed_convergence() -> None:
    rows = [row(index, basis) for index, basis in enumerate([0, 0, 0, 20, 20, -1, -1, -1])]
    z_values = [None, None, 0.0, 2.0, 1.5, -0.5, -0.5, -0.5]
    config = ReversionConfig("test", 3, 1.5, 0.0, 5.0, 12)
    trades = simulate_window(
        config, rows, z_values, [3], [],
        start_index=0, end_index=len(rows), fee_bps=0.0, slippage_bps=0.0,
    )
    assert len(trades) == 1
    assert trades[0]["entry_time"] == rows[4]["time"]
    assert trades[0]["exit_time"] == rows[6]["time"]
    assert trades[0]["exit_reason"] == "basis_z_converged"
