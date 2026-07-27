import math

from tools.cross_asset_cointegration_residual_nested_holdout import (
    Panel,
    ResidualConfig,
    build_prefix,
    load_panel,
    rolling_ols_point,
    simulate_stage,
)


def test_rolling_regression_excludes_current_signal_bar():
    x = [float(index) for index in range(1, 31)]
    y = [1.0 + 2.0 * value + (0.02 if index % 2 else -0.02) for index, value in enumerate(x)]
    y[-1] += 20.0
    point = rolling_ols_point(x, y, build_prefix(x, y), index=29, window=20)
    assert point is not None
    assert math.isclose(point.beta, 2.0, rel_tol=0.01)
    assert point.signal_z > 100.0


def test_signal_enters_and_exits_on_later_bar_opens():
    times = [f"2026-01-{1 + index // 24:02d}T{index % 24:02d}:00:00+00:00" for index in range(80)]
    x_close = [100.0 + index * 0.1 for index in range(80)]
    y_close = [200.0 + index * 0.2 for index in range(80)]
    cache = [None] * 80
    cache[50] = type("Point", (), {
        "alpha": math.log(2.0),
        "beta": 1.0,
        "residual_std": 0.01,
        "signal_z": 2.0,
        "half_life_hours": 4.0,
    })()
    panel = Panel(
        times=times,
        opens={"BTCUSDT": x_close[:], "ETHUSDT": y_close[:]},
        closes={"BTCUSDT": x_close[:], "ETHUSDT": y_close[:]},
    )
    config = ResidualConfig(20, 1.5, 0.5, 6, 4.0, 0.1, 3.0)
    trades = simulate_stage(config, panel, "BTCUSDT", "ETHUSDT", cache, start_index=0, end_index=80)
    assert trades
    first = trades[0]
    assert first["signal_time"] == times[50]
    assert first["entry_time"] == times[51]
    assert first["exit_time"] == times[52]


def test_stage_boundary_does_not_leak_exit_into_next_split():
    times = [f"t{index:03d}" for index in range(40)]
    prices = [100.0 + index for index in range(40)]
    panel = Panel(
        times=times,
        opens={"BTCUSDT": prices[:], "ETHUSDT": [value * 2 for value in prices]},
        closes={"BTCUSDT": prices[:], "ETHUSDT": [value * 2 for value in prices]},
    )
    cache = [None] * 40
    cache[37] = type("Point", (), {
        "alpha": math.log(2.0),
        "beta": 1.0,
        "residual_std": 0.01,
        "signal_z": 2.0,
        "half_life_hours": 4.0,
    })()
    config = ResidualConfig(10, 1.5, 0.5, 6, 4.0, 0.1, 3.0)
    trades = simulate_stage(config, panel, "BTCUSDT", "ETHUSDT", cache, start_index=0, end_index=39)
    assert trades == []


def test_panel_cutoff_freezes_mutating_cache(tmp_path):
    symbols = ["BTCUSDT", "ETHUSDT"]
    for symbol in symbols:
        target = tmp_path / "futures" / symbol / "1h_klines.csv"
        target.parent.mkdir(parents=True)
        target.write_text(
            "time,open,close\n"
            "2026-07-12T03:00:00+00:00,100,101\n"
            "2026-07-12T04:00:00+00:00,101,102\n"
            "2026-07-12T05:00:00+00:00,102,103\n",
            encoding="utf-8",
        )

    panel = load_panel(tmp_path, symbols, "1h", "2026-07-12T04:00:00+00:00", minimum_rows=1)

    assert panel.times == ["2026-07-12T03:00:00+00:00", "2026-07-12T04:00:00+00:00"]
