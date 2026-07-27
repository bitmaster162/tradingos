from __future__ import annotations

from dataclasses import replace

from tools.derivatives_context_composite_miner import CompositeConfig
from tools.derivatives_context_regime_occurrence_diagnostic import analyze_window_decay, classify_row


def config(**overrides) -> CompositeConfig:
    base = CompositeConfig(
        strategy_id="shape_a",
        family="funding_extreme_fade",
        side="SHORT",
        interval="4h",
        lookback=6,
        price_atr=0.4,
        oi_pct=0.15,
        funding_abs=0.0002,
        volume_z=0.0,
        close_location=0.55,
        regime_filter="none",
        context_mode="sweep_confirm",
        spot_divergence_pct=0.02,
        spot_volume_ratio=0.5,
        sweep_lookback=12,
        stop_atr=1.0,
        take_atr=1.5,
        max_hold_bars=8,
    )
    return replace(base, **overrides)


def feature(**overrides) -> dict[str, float]:
    base = {
        "price_move_atr": 0.6,
        "oi_delta_pct": 0.0,
        "funding": 0.0003,
        "volume_z": 0.0,
        "close_location": 0.2,
        "close": 100.0,
        "ema50": 99.0,
        "ema200": 98.0,
        "ema50_slope_20": 1.0,
        "ema200_slope_20": 1.0,
        "spot_perp_divergence_pct": -0.03,
        "spot_volume_ratio": 0.6,
        "bullish_sweep_12": 0.0,
        "bearish_sweep_12": 1.0,
    }
    base.update(overrides)
    return base


def test_classify_row_train_only_decay() -> None:
    row = {
        "windows": {
            "train": {"full_intersection": 3},
            "validation": {"full_intersection": 0},
            "oos": {"full_intersection": 0},
        }
    }

    assert classify_row(row) == "train_only_decay"


def test_analyze_window_decay_counts_survival() -> None:
    cfg_train_only = config(strategy_id="train_only", context_mode="sweep_confirm")
    cfg_survives = config(strategy_id="survives", context_mode="spot_confirm")
    features = {
        10: {6: feature()},
        11: {6: feature()},
        20: {6: feature(bearish_sweep_12=0.0, spot_perp_divergence_pct=-0.03)},
        21: {6: feature(bearish_sweep_12=0.0, spot_perp_divergence_pct=-0.03)},
    }

    report = analyze_window_decay(
        [cfg_train_only, cfg_survives],
        {"4h": features},
        {"4h": {"train": (0, 15), "validation": (15, 25), "oos": (25, 30)}},
    )

    assert report["unique_entry_shapes"] == 2
    assert report["train_positive_shapes"] == 2
    assert report["validation_positive_shapes"] == 1
    assert report["train_only_decay_shapes"] == 1
    assert report["validation_survival_rate_pct"] == 50.0
    assert report["classification_counts"]["train_only_decay"] == 1
    assert report["classification_counts"]["validation_frequency_present"] == 1
