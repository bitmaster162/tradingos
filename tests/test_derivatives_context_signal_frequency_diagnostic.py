from __future__ import annotations

from dataclasses import replace

from tools.derivatives_context_composite_miner import CompositeConfig
from tools.derivatives_context_signal_frequency_diagnostic import analyze_configs, dedupe_entry_configs


def config(**overrides) -> CompositeConfig:
    base = CompositeConfig(
        strategy_id="shape_a",
        family="funding_extreme_fade",
        side="LONG",
        interval="1h",
        lookback=6,
        price_atr=0.4,
        oi_pct=0.15,
        funding_abs=0.0001,
        volume_z=0.0,
        close_location=0.55,
        regime_filter="ema200_slope",
        context_mode="composite2",
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
        "price_move_atr": -0.5,
        "oi_delta_pct": 0.0,
        "funding": -0.0002,
        "volume_z": 0.0,
        "close_location": 0.7,
        "close": 105.0,
        "ema50": 104.0,
        "ema200": 100.0,
        "ema50_slope_20": 1.0,
        "ema200_slope_20": 1.0,
        "spot_perp_divergence_pct": 0.03,
        "spot_volume_ratio": 0.6,
        "bullish_sweep_12": 1.0,
        "bearish_sweep_12": 0.0,
    }
    base.update(overrides)
    return base


def test_dedupe_entry_configs_ignores_exit_parameters() -> None:
    first = config(strategy_id="a", take_atr=1.5, max_hold_bars=8)
    second = config(strategy_id="b", take_atr=2.0, max_hold_bars=16)

    assert dedupe_entry_configs([first, second]) == [first]


def test_analyze_configs_counts_full_intersection() -> None:
    cfg = config()
    features = {
        10: {6: feature()},
        11: {6: feature(spot_perp_divergence_pct=0.0, bullish_sweep_12=0.0)},
    }

    report = analyze_configs([cfg], {"1h": features}, {"1h": (0, 20)})
    row = report["top_full_intersection"][0]

    assert row["counts"]["derivative_event"] == 2
    assert row["counts"]["context"] == 1
    assert row["counts"]["event_and_context"] == 1
    assert row["counts"]["full_intersection"] == 1
    assert row["counts"]["bottleneck"] == "passes_frequency_smoke"


def test_analyze_configs_marks_context_kills_event() -> None:
    cfg = config()
    features = {
        10: {6: feature(spot_perp_divergence_pct=0.0, bullish_sweep_12=0.0)},
        11: {6: feature(spot_perp_divergence_pct=-0.02, bullish_sweep_12=0.0)},
    }

    report = analyze_configs([cfg], {"1h": features}, {"1h": (0, 20)})
    row = report["top_full_intersection"][0]

    assert row["counts"]["derivative_event"] == 2
    assert row["counts"]["context"] == 0
    assert row["counts"]["full_intersection"] == 0
    assert row["counts"]["bottleneck"] == "context_zero"
