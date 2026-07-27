from __future__ import annotations

from tools.derivatives_context_composite_miner import CompositeConfig, build_features, context_matches, join_rows


def _config(*, side: str = "LONG", mode: str = "spot_confirm") -> CompositeConfig:
    return CompositeConfig(
        strategy_id="demo",
        family="oi_build_continuation",
        side=side,
        interval="4h",
        lookback=6,
        price_atr=0.4,
        oi_pct=0.15,
        funding_abs=0.0001,
        volume_z=0.0,
        close_location=0.55,
        regime_filter="none",
        context_mode=mode,
        spot_divergence_pct=0.02,
        spot_volume_ratio=0.5,
        sweep_lookback=12,
        stop_atr=1.0,
        take_atr=1.5,
        max_hold_bars=8,
    )


def test_context_matches_spot_confirm_direction() -> None:
    assert context_matches(_config(side="LONG"), {"spot_perp_divergence_pct": 0.03, "spot_volume_ratio": 1.0})
    assert not context_matches(_config(side="LONG"), {"spot_perp_divergence_pct": -0.03, "spot_volume_ratio": 1.0})
    assert context_matches(_config(side="SHORT"), {"spot_perp_divergence_pct": -0.03, "spot_volume_ratio": 1.0})
    assert not context_matches(_config(side="SHORT"), {"spot_perp_divergence_pct": 0.03, "spot_volume_ratio": 1.0})


def test_context_matches_sweep_and_liq_proxy() -> None:
    assert context_matches(_config(side="LONG", mode="sweep_confirm"), {"bullish_sweep_12": 1.0})
    assert context_matches(_config(side="SHORT", mode="sweep_confirm"), {"bearish_sweep_12": 1.0})
    assert context_matches(
        _config(side="LONG", mode="liq_proxy"),
        {"price_move_atr": -0.5, "oi_delta_pct": -0.2, "close_location": 0.7},
    )
    assert context_matches(
        _config(side="SHORT", mode="liq_proxy"),
        {"price_move_atr": 0.5, "oi_delta_pct": -0.2, "close_location": 0.2},
    )


def test_join_rows_and_build_features_with_spot_context() -> None:
    futures = []
    spot = []
    oi_rows = []
    for index in range(240):
        time_value = f"2026-01-{(index // 24) + 1:02d}T{index % 24:02d}:00:00+00:00"
        close = 100.0 + index
        futures.append({"time": time_value, "open": close - 0.5, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": 1000.0})
        spot.append({"time": time_value, "open": close - 0.4, "high": close + 1.1, "low": close - 0.9, "close": close + 0.1 * index, "volume": 600.0})
        oi_rows.append({"time": time_value, "open_interest": 10000.0 + index, "funding": 0.00001})

    rows = join_rows(futures, oi_rows, spot)
    features = build_features(rows, lookbacks=(6,), sweep_lookbacks=(12,))
    latest = features[max(features)][6]

    assert latest["spot_perp_divergence_pct"] != 0.0
    assert latest["spot_volume_ratio"] == 0.6
    assert "bullish_sweep_12" in latest
