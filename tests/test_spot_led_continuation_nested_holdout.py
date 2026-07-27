from __future__ import annotations

from dataclasses import replace

from tools.spot_led_continuation_nested_holdout import (
    SpotLeadConfig,
    build_configs,
    causal_rolling_z,
    gate,
    signal_matches,
)


def config() -> SpotLeadConfig:
    return build_configs()[0]


def test_grid_is_preregistered_96_configurations() -> None:
    configs = build_configs()
    assert len(configs) == 96
    assert len({item.strategy_id for item in configs}) == 96


def test_causal_z_excludes_current_value() -> None:
    z = causal_rolling_z([1.0, 2.0, 3.0, 100.0], 3)
    assert z[:3] == [None, None, None]
    assert z[3] is not None
    assert z[3] > 100.0


def test_direction_requires_spot_return_and_divergence_agreement() -> None:
    long = replace(config(), side="LONG_SPOT_LEADS", entry_abs_z=2.0, volume_filter="none")
    short = replace(config(), side="SHORT_SPOT_LEADS", entry_abs_z=2.0, volume_filter="none")
    assert signal_matches(long, spot_return_pct=0.3, divergence_z=2.1, spot_volume_z=None, futures_volume_z=None)
    assert not signal_matches(long, spot_return_pct=-0.3, divergence_z=2.1, spot_volume_z=None, futures_volume_z=None)
    assert signal_matches(short, spot_return_pct=-0.3, divergence_z=-2.1, spot_volume_z=None, futures_volume_z=None)
    assert not signal_matches(short, spot_return_pct=0.3, divergence_z=-2.1, spot_volume_z=None, futures_volume_z=None)


def test_volume_filter_requires_spot_relative_volume_lead() -> None:
    filtered = replace(config(), entry_abs_z=1.5, volume_filter="spot_relative_volume_leads")
    assert signal_matches(filtered, spot_return_pct=0.2, divergence_z=2.0, spot_volume_z=1.2, futures_volume_z=0.8)
    assert not signal_matches(filtered, spot_return_pct=0.2, divergence_z=2.0, spot_volume_z=0.4, futures_volume_z=0.8)


def test_train_gate_requires_stress_and_bootstrap() -> None:
    result = {
        "summary": {"trades": 100, "expectancy_r": 0.2, "max_drawdown_r": -5.0},
        "stable_folds": 4,
        "bootstrap_probability_expectancy_gt_0": 0.96,
        "cost_stress": {"summary": {"expectancy_r": -0.01}},
    }
    assert gate(result, "train")["pass"] is False
    result["cost_stress"]["summary"]["expectancy_r"] = 0.01
    assert gate(result, "train")["pass"] is True
