from __future__ import annotations

from tools.cross_venue_catchup_nested_holdout import (
    CatchupConfig,
    build_configs,
    causal_time_z,
    gate,
    replay,
    return_features,
)


def test_grid_is_preregistered_108_configurations() -> None:
    configs = build_configs()
    assert len(configs) == 108
    assert len({item.strategy_id for item in configs}) == 108


def test_return_features_require_exact_timestamp_lookback() -> None:
    aligned = [
        {"time_ms": 0, "binance_close": 100.0, "coinbase_close": 100.0},
        {"time_ms": 60_000, "binance_close": 100.0, "coinbase_close": 101.0},
        {"time_ms": 180_000, "binance_close": 100.0, "coinbase_close": 102.0},
    ]
    features = return_features(aligned, 1)
    assert [row["time_ms"] for row in features] == [60_000]


def test_causal_z_excludes_current_observation() -> None:
    features = [
        {"time_ms": index * 60_000, "divergence_bps": float(index + 1)}
        for index in range(4)
    ]
    z_values = causal_time_z(features, 3, min_coverage=1.0)
    assert set(z_values) == {180_000}
    assert z_values[180_000] > 2.0


def test_replay_enters_next_minute_and_charges_two_sided_costs() -> None:
    config = CatchupConfig("x", 1, 360, 2.0, 5.0, 1)
    signals = [{"time_ms": 0, "coinbase_return_bps": 10.0, "divergence_bps": 8.0, "divergence_z": 3.0}]
    binance = {
        60_000: {"open": 100.0, "close": 101.0},
    }
    trades = replay(config, signals, binance, start_ms=0, end_ms=120_000, cost_bps_per_side=10.0)
    assert len(trades) == 1
    assert trades[0].entry_ts.endswith("00:01:00+00:00")
    assert trades[0].gross_bps == 100.0
    assert trades[0].net_bps == 80.0


def test_train_gate_requires_bootstrap_and_positive_stress() -> None:
    result = {
        "summary": {"trades": 60, "mean_net_bps": 3.0, "max_drawdown_bps": -100.0},
        "stable_folds": 4,
        "bootstrap_probability_mean_gt_0": 0.96,
        "cost_stress": {"summary": {"mean_net_bps": -0.1}},
    }
    assert gate(result, "train")["pass"] is False
    result["cost_stress"]["summary"]["mean_net_bps"] = 0.1
    assert gate(result, "train")["pass"] is True
