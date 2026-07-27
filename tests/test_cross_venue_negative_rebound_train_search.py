from __future__ import annotations

from tools.cross_venue_catchup_nested_holdout import CatchupConfig
from tools.cross_venue_negative_rebound_train_search import build_configs, generate_signals


def test_grid_is_preregistered_108_configurations() -> None:
    configs = build_configs()
    assert len(configs) == 108
    assert len({item.strategy_id for item in configs}) == 108


def test_signal_requires_negative_coinbase_return_and_negative_dislocation() -> None:
    config = CatchupConfig("x", 1, 360, 2.0, 5.0, 1)
    features = [
        {"time_ms": 0, "coinbase_return_bps": -10.0, "divergence_bps": -8.0},
        {"time_ms": 60_000, "coinbase_return_bps": 10.0, "divergence_bps": -8.0},
        {"time_ms": 120_000, "coinbase_return_bps": -10.0, "divergence_bps": 8.0},
    ]
    z_values = {0: -3.0, 60_000: -3.0, 120_000: 3.0}
    signals = generate_signals(config, features, z_values)
    assert [item["time_ms"] for item in signals] == [0]


def test_signal_thresholds_are_inclusive() -> None:
    config = CatchupConfig("x", 1, 360, 2.0, 5.0, 1)
    features = [{"time_ms": 0, "coinbase_return_bps": -5.0, "divergence_bps": -4.0}]
    signals = generate_signals(config, features, {0: -2.0})
    assert len(signals) == 1
