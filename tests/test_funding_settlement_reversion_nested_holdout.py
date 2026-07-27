from __future__ import annotations

from tools.funding_settlement_reversion_nested_holdout import (
    FundingEventConfig,
    build_configs,
    gate,
    rolling_event_z,
    signal_matches,
)


def config(side: str = "SHORT_AFTER_POSITIVE", spot_filter: str = "none") -> FundingEventConfig:
    return FundingEventConfig("test", 90, 2.0, side, spot_filter, 1.0, 1.5)


def test_grid_is_exactly_preregistered_96_configs() -> None:
    assert len(build_configs()) == 96


def test_event_z_excludes_current_settlement() -> None:
    events = [{"rate": value} for value in [0.0, 0.001, -0.001, 0.01]]
    values = rolling_event_z(events, 3)
    assert values[:3] == [None, None, None]
    assert values[3] > 10


def test_oi_confirmation_is_mandatory() -> None:
    assert signal_matches(config(), 0.001, 3.0, {"oi_delta_pct": 0.49, "spot_perp_divergence_pct": -0.1}) is False
    assert signal_matches(config(), 0.001, 3.0, {"oi_delta_pct": 0.5, "spot_perp_divergence_pct": -0.1}) is True


def test_spot_nonconfirmation_is_directional() -> None:
    short = config("SHORT_AFTER_POSITIVE", "perp_excess_move")
    long = config("LONG_AFTER_NEGATIVE", "perp_excess_move")
    assert signal_matches(short, 0.001, 3.0, {"oi_delta_pct": 1.0, "spot_perp_divergence_pct": -0.1}) is True
    assert signal_matches(short, 0.001, 3.0, {"oi_delta_pct": 1.0, "spot_perp_divergence_pct": 0.1}) is False
    assert signal_matches(long, -0.001, -3.0, {"oi_delta_pct": -1.0, "spot_perp_divergence_pct": 0.1}) is True


def test_train_gate_requires_screening_bootstrap_and_stress() -> None:
    result = {
        "summary": {"trades": 100, "expectancy_r": 0.2, "max_drawdown_r": -5.0},
        "stable_folds": 4,
        "bootstrap_probability_expectancy_gt_0": 0.99,
        "cost_stress": {"summary": {"expectancy_r": 0.02}},
    }
    assert gate(result, "train")["pass"] is True
    result["cost_stress"]["summary"]["expectancy_r"] = -0.01
    assert gate(result, "train")["pass"] is False
