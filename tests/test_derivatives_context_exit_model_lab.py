from __future__ import annotations

import argparse

from tools.derivatives_context_composite_miner import CompositeConfig
from tools.derivatives_context_exit_model_lab import ExitConfig, build_exit_configs, simulate_exit_model


def config(**overrides) -> CompositeConfig:
    base = {
        "strategy_id": "shape_a",
        "family": "funding_extreme_fade",
        "side": "SHORT",
        "interval": "1h",
        "lookback": 6,
        "price_atr": 0.4,
        "oi_pct": 0.15,
        "funding_abs": 0.0001,
        "volume_z": 0.0,
        "close_location": 0.55,
        "regime_filter": "none",
        "context_mode": "spot_confirm",
        "spot_divergence_pct": 0.02,
        "spot_volume_ratio": 0.5,
        "sweep_lookback": 12,
        "stop_atr": 1.0,
        "take_atr": 1.5,
        "max_hold_bars": 8,
    }
    base.update(overrides)
    return CompositeConfig(**base)


def row(index: int, *, open: float, high: float, low: float, close: float) -> dict[str, float | str]:
    return {
        "time": f"2024-01-01T{index:02d}:00:00+00:00",
        "open": open,
        "high": high,
        "low": low,
        "close": close,
    }


def test_fixed_rr_short_exits_at_take() -> None:
    rows = [
        row(0, open=100.0, high=100.0, low=100.0, close=100.0),
        row(1, open=100.0, high=100.5, low=98.4, close=98.7),
        row(2, open=98.7, high=99.0, low=98.0, close=98.2),
    ]

    outcome = simulate_exit_model(
        config(),
        ExitConfig(exit_model="fixed_rr", stop_atr=1.0, take_atr=1.5, max_hold_bars=4),
        rows,
        0,
        1.0,
        cost_bps_per_side=0.0,
    )

    assert outcome is not None
    assert outcome["exit_reason"] == "take"
    assert outcome["exit"] == 98.5
    assert outcome["net_r"] == 1.5


def test_breakeven_updates_stop_only_after_current_bar() -> None:
    rows = [
        row(0, open=100.0, high=100.0, low=100.0, close=100.0),
        row(1, open=100.0, high=100.8, low=99.0, close=99.2),
        row(2, open=99.2, high=100.2, low=99.5, close=99.8),
    ]

    outcome = simulate_exit_model(
        config(),
        ExitConfig(exit_model="breakeven_after_r", stop_atr=1.0, take_atr=3.0, max_hold_bars=4, breakeven_after_r=1.0),
        rows,
        0,
        1.0,
        cost_bps_per_side=0.0,
    )

    assert outcome is not None
    assert outcome["exit_index"] == 2
    assert outcome["exit_reason"] == "stop"
    assert outcome["exit"] == 100.0
    assert outcome["net_r"] == 0.0


def test_build_exit_configs_dedupes_irrelevant_parameters() -> None:
    args = argparse.Namespace(
        exit_models="fixed_rr,stop_only_time,breakeven_after_r,atr_trailing",
        stop_atr="1.0",
        take_atr="1.5,2.0",
        max_hold_bars="8",
        breakeven_after_r="0.75,1.0",
        trail_atr="0.75,1.0",
    )

    configs = build_exit_configs(args)
    keys = {
        (item.exit_model, item.stop_atr, item.take_atr, item.max_hold_bars, item.breakeven_after_r, item.trail_atr)
        for item in configs
    }

    assert len(configs) == len(keys)
    assert len([item for item in configs if item.exit_model == "fixed_rr"]) == 2
    assert len([item for item in configs if item.exit_model == "stop_only_time"]) == 1
    assert len([item for item in configs if item.exit_model == "breakeven_after_r"]) == 4
    assert len([item for item in configs if item.exit_model == "atr_trailing"]) == 2
