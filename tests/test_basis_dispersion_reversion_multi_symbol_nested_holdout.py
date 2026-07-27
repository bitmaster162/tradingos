from tools.basis_dispersion_reversion_multi_symbol_nested_holdout import (
    DispersionConfig,
    rolling_relative_z,
    simulate_stage,
)


def make_rows():
    rows = []
    symbols = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    for index in range(80):
        basis = {"AAAUSDT": 10.0, "BBBUSDT": 10.0, "CCCUSDT": 10.0}
        if index >= 60:
            basis["AAAUSDT"] = 35.0
        row = {"time": f"2026-01-01T{index % 24:02d}:00:00+00:00", "symbols": {}}
        for symbol in symbols:
            row["symbols"][symbol] = {
                "spot_open": 100.0 + index,
                "spot_close": 100.5 + index,
                "perp_open": 100.0 + index + basis[symbol] / 100.0,
                "perp_close": 100.5 + index + basis[symbol] / 100.0,
                "basis_close_bps": basis[symbol],
                "funding_event_bps": 0.0,
                "relative_basis_bps": 0.0,
            }
        median_basis = sorted(basis.values())[1]
        for symbol in symbols:
            row["symbols"][symbol]["relative_basis_bps"] = basis[symbol] - median_basis
        rows.append(row)
    return rows, symbols


def test_signal_enters_next_bar_not_same_bar():
    rows, symbols = make_rows()
    z = rolling_relative_z(rows, symbols, 48)
    config = DispersionConfig(lookback_hours=48, entry_z=2.0, exit_z=0.5, min_abs_basis_bps=20.0, max_hold_hours=6)
    trades = simulate_stage(
        config,
        rows,
        symbols,
        z,
        start_index=0,
        end_index=len(rows),
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    assert trades
    first = trades[0]
    assert first["symbol"] == "AAAUSDT"
    assert first["signal_time"] != first["entry_time"]


def test_no_trade_before_enough_rolling_history():
    rows, symbols = make_rows()
    z = rolling_relative_z(rows, symbols, 48)
    assert all(value is None for value in z["AAAUSDT"][:48])
