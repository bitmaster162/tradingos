from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tools.derivatives_event_edge_miner import (
    EventConfig,
    build_features,
    diversified_limit,
    gate,
    join_rows,
    signal_matches,
    simulate_window,
    stats,
)


def synthetic_rows(count: int = 260, event_index: int = 230) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    klines: list[dict[str, str]] = []
    derivatives: list[dict[str, str]] = []
    price = 100.0
    oi = 1000.0
    for index in range(count):
        ts = (start + timedelta(hours=index)).isoformat(timespec="seconds")
        drift = 0.2
        if index == event_index:
            drift = 5.0
        open_ = price
        close = price + drift
        high = max(open_, close) + 1.0
        low = min(open_, close) - 1.0
        volume = 100.0 if index != event_index else 400.0
        price = close
        oi += 1.0
        if index == event_index:
            oi += 40.0
        klines.append(
            {
                "time": ts,
                "open": str(open_),
                "high": str(high),
                "low": str(low),
                "close": str(close),
                "volume": str(volume),
            }
        )
        derivatives.append(
            {
                "time": ts,
                "price": str(close),
                "open_interest": str(oi),
                "volume": str(volume),
                "funding": "0.001" if index == event_index else "0.0001",
            }
        )
    return klines, derivatives


def config() -> EventConfig:
    return EventConfig(
        strategy_id="test",
        family="oi_build_fade",
        side="SHORT",
        interval="1h",
        lookback=6,
        price_atr=1.0,
        oi_pct=1.0,
        funding_abs=0.0005,
        volume_z=0.0,
        close_location=0.55,
        regime_filter="none",
        stop_atr=1.0,
        take_atr=1.5,
        max_hold_bars=6,
    )


def test_build_features_and_signal_match_for_oi_build_fade() -> None:
    klines, derivatives = synthetic_rows()
    rows = join_rows(klines, derivatives)
    features = build_features(rows, lookbacks=(6,), volume_window=20)
    feature = features[230][6]

    assert feature["price_move_atr"] > 1.0
    assert feature["oi_delta_pct"] > 1.0
    assert feature["funding"] >= 0.001
    assert signal_matches(config(), feature) is True


def test_simulate_window_uses_next_open_and_writes_trade() -> None:
    klines, derivatives = synthetic_rows()
    rows = join_rows(klines, derivatives)
    features = build_features(rows, lookbacks=(6,), volume_window=20)

    trades = simulate_window(config(), rows, features, start_index=220, end_index=len(rows), cost_bps_per_side=0.0)

    assert len(trades) == 1
    assert trades[0]["side"] == "SHORT"
    assert trades[0]["entry_time"] == rows[231]["time"]
    assert trades[0]["strategy_id"] == "test"


def test_stats_and_gate_stay_research_only_shape() -> None:
    summary = stats([{"net_r": 1.0}, {"net_r": -1.0}, {"net_r": 2.0}])
    folds = [{"trades": 3, "expectancy_r": 0.2}, {"trades": 3, "expectancy_r": 0.1}]
    result = gate(summary, folds, min_trades=3, min_expectancy=0.1, min_stable_folds=2, max_drawdown=3.0)

    assert summary["trades"] == 3
    assert summary["winrate_pct"] == 66.667
    assert result["pass"] is True


def test_diversified_limit_keeps_family_and_side_coverage() -> None:
    configs = [
        EventConfig(f"a{i}", "family_a", "LONG", "1h", 6, 1, 1, 0.1, 0, 0.55, "none", 1, 2, 8)
        for i in range(10)
    ] + [
        EventConfig(f"b{i}", "family_b", "SHORT", "1h", 6, 1, 1, 0.1, 0, 0.55, "none", 1, 2, 8)
        for i in range(10)
    ]

    limited = diversified_limit(configs, 4)
    keys = {(item.family, item.side) for item in limited}

    assert len(limited) == 4
    assert keys == {("family_a", "LONG"), ("family_b", "SHORT")}


def test_regime_filter_blocks_wrong_direction() -> None:
    feature = {
        "price_move_atr": 1.2,
        "oi_delta_pct": 1.0,
        "funding": 0.0001,
        "volume_z": 0.0,
        "close_location": 0.8,
        "close": 120.0,
        "ema50": 110.0,
        "ema200": 100.0,
        "ema50_slope_20": 2.0,
        "ema200_slope_20": 1.0,
    }
    long_config = EventConfig("long", "oi_build_continuation", "LONG", "4h", 6, 1.0, 0.5, 0.0002, 0, 0.55, "ema50_stack", 1, 2, 8)
    short_config = EventConfig("short", "oi_build_continuation", "SHORT", "4h", 6, 1.0, 0.5, 0.0002, 0, 0.55, "ema50_stack", 1, 2, 8)

    assert signal_matches(long_config, feature) is True
    assert signal_matches(short_config, feature) is False
