from __future__ import annotations

import copy
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE.parent / "tools" / "tradingos_market_decision_bridge.py"
SPEC = importlib.util.spec_from_file_location("r77_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


CAPTURED = "2026-08-24T00:00:00Z"
CAPTURED_MS = ms(CAPTURED)


def kline(close_time: int, *, close="118400", volume="1000", buy="600"):
    open_time = close_time - 14_400_000 + 1
    return [
        open_time,
        "118000",
        "119000",
        "117000",
        close,
        volume,
        close_time,
        "0",
        0,
        buy,
        "0",
        "0",
    ]


def capture():
    close_time = CAPTURED_MS - 1_000
    return {
        "schema": m.CAPTURE_SCHEMA,
        "captured_at": CAPTURED,
        "credentials_used": False,
        "private_api_used": False,
        "symbols": ["BTCUSDT"],
        "assets": {
            "BTCUSDT": {
                "futures_klines": {
                    "4h": [kline(close_time)],
                },
                "spot_klines_4h": [kline(close_time - 2_000, buy="700")],
                "open_interest": {
                    "symbol": "BTCUSDT",
                    "openInterest": "1000",
                    "time": CAPTURED_MS - 3_000,
                },
                "mark_price": {
                    "symbol": "BTCUSDT",
                    "markPrice": "118410",
                    "indexPrice": "118400",
                    "lastFundingRate": "0.00008",
                    "time": CAPTURED_MS - 4_000,
                },
            }
        },
    }


def watchtower(c):
    four = {
        "state": "LONG",
        "score": 3.25,
        "trend": "up",
        "last": 118400.0,
        "ema9": 117900.0,
        "ema21": 116700.0,
        "change_pct": 1.8,
        "atr_pct": 1.9,
        "support": 116800.0,
        "resistance": 119600.0,
        "range_position": 0.57,
        "relative_volume": 1.35,
        "perp_taker_flow": "up",
        "reasons": [],
    }
    neutral = dict(four, state="NEUTRAL", score=0.0)
    row = {
        "symbol": "BTCUSDT",
        "bias": "WATCH_LONG",
        "clarity": "CLEAR",
        "weighted_confluence": 5,
        "confluence_normalized": 0.8333,
        "conflict": None,
        "timeframes": {
            "1h": neutral,
            "4h": four,
            "1d": dict(four, state="LONG", score=2.0),
        },
        "derivatives": {
            "open_interest_change_pct": 2.1,
            "funding_rate": 0.00008,
            "funding_z": 0.7,
            "basis_pct": 0.04,
            "basis_z": 0.6,
        },
        "spot_flow_4h": "up",
        "spot_relative_volume_4h": 1.2,
        "distance_4h": {"to_support_pct": 1.0, "to_resistance_pct": 1.0},
        "attention_score": 77.0,
        "can_trade": False,
    }
    return {
        "schema": m.WATCHTOWER_SCHEMA,
        "version": m.WATCHTOWER_VERSION,
        "captured_at": CAPTURED,
        "symbols": ["BTCUSDT"],
        "matrix": [row],
        "cross_asset": {
            "state": "ALIGNED",
            "watch_long": ["BTCUSDT"],
            "watch_short": [],
            "top_attention": "BTCUSDT",
        },
        "provenance": {
            "producer": m.WATCHTOWER_PRODUCER,
            "producer_sha256": m.EXPECTED_WATCHTOWER_PRODUCER_SHA256,
            "capture_sha256": m.stable_sha256(c),
            "contract": "fixture",
        },
        "safety": dict(m.WATCHTOWER_SAFETY),
    }


def radar(w):
    watch = w["matrix"][0]
    row = {
        "symbol": "BTCUSDT",
        "bias": watch["bias"],
        "decision_quality": "CLEAR",
        "priority_score": 81.5,
        "timeframes": {
            tf: watch["timeframes"][tf]["state"] for tf in m.EXPECTED_TFS
        },
        "confluence": watch["weighted_confluence"],
        "watchtower_conflict": None,
        "liquidity": {
            "quality": "PASS",
            "state": "BALANCED",
            "spread_bps": 1.2,
            "nearest_bid_wall": None,
            "nearest_ask_wall": None,
        },
        "vetoes": [],
        "notes": [],
        "can_trade": False,
    }
    return {
        "schema": m.RADAR_SCHEMA,
        "version": m.RADAR_VERSION,
        "watchtower_captured_at": CAPTURED,
        "liquidity_captured_at": CAPTURED,
        "capture_skew_seconds": 0.0,
        "symbols": ["BTCUSDT"],
        "matrix": [row],
        "top_priority": "BTCUSDT",
        "provenance": {
            "watchtower_schema": m.WATCHTOWER_SCHEMA,
            "watchtower_version": m.WATCHTOWER_VERSION,
            "watchtower_report_sha256": m.stable_sha256(w),
            "watchtower_capture_sha256": w["provenance"]["capture_sha256"],
            "watchtower_producer_sha256": m.EXPECTED_WATCHTOWER_PRODUCER_SHA256,
            "liquidity_schema": "tradingos.liquidity_lens.v1",
            "liquidity_version": "1.1.0",
            "liquidity_report_sha256": "a" * 64,
            "liquidity_producer_sha256": m.EXPECTED_LIQUIDITY_PRODUCER_SHA256,
            "liquidity_capture_sha256": "b" * 64,
            "symbol_sets_exactly_bound": True,
            "max_capture_skew_seconds": 120,
        },
        "contract": {
            "watchtower_bias_is_authoritative": True,
            "liquidity_can_create_directional_bias": False,
            "liquidity_role": "fixture",
        },
        "safety": dict(m.RADAR_SAFETY),
    }


def bundle():
    c = capture()
    w = watchtower(c)
    r = radar(w)
    return c, w, r


def test_builds_decision_brief_v2_compatible_nested_snapshot():
    c, w, r = bundle()
    out = m.build_bridge(c, w, r)
    snap = out["snapshot"]

    assert snap["schema_version"] == 1
    assert snap["symbol"] == "BTCUSDT"
    assert snap["timeframe"] == "4h"
    assert snap["can_trade"] is False
    assert snap["price"]["last"] == 118400.0
    assert snap["price"]["ema_fast"] == 117900.0
    assert snap["price"]["ema_slow"] == 116700.0
    assert snap["market_structure"]["trend"] == "up"
    assert snap["derivatives"]["funding_z"] == 0.7
    assert snap["flow"]["spot_cvd_direction"] == "up"
    assert snap["flow"]["perp_cvd_direction"] == "up"
    assert snap["data_quality"]["present_sources"] == [
        "ohlcv", "open_interest", "funding", "spot_flow"
    ]
    assert {row["kind"] for row in snap["provenance"]["sources"]} == {
        "ohlcv", "open_interest", "funding", "spot_flow"
    }


def test_source_freshness_uses_raw_observation_times_not_capture_time():
    c, w, r = bundle()
    out = m.build_bridge(c, w, r)
    sources = {row["kind"]: row for row in out["snapshot"]["provenance"]["sources"]}

    assert sources["ohlcv"]["observed_at"].endswith("59:59.000Z")
    assert sources["spot_flow"]["observed_at"].endswith("59:57.000Z")
    assert sources["open_interest"]["observed_at"].endswith("59:57.000Z")
    assert sources["funding"]["observed_at"].endswith("59:56.000Z")
    assert len({row["source_id"] for row in sources.values()}) == 4


def test_attention_bias_is_separate_advisory_context_not_snapshot_stance():
    c, w, r = bundle()
    out = m.build_bridge(c, w, r)
    assert out["attention_context"]["bias"] == "WATCH_LONG"
    assert out["attention_context"]["confers_authority"] is False
    assert "bias" not in out["snapshot"]
    assert "stance" not in out["snapshot"]


def test_bridge_preserves_absolute_deny_only_effect_ceiling():
    c, w, r = bundle()
    out = m.build_bridge(c, w, r)
    assert out["safety"] == m.BRIDGE_SAFETY
    assert out["safety"]["execution_authority"] == "NONE"
    assert out["safety"]["signals_allowed"] is False
    assert out["safety"]["orders_allowed"] is False
    assert out["safety"]["can_trade"] is False
    assert out["safety"]["capital_permission"] == "DENY"
    assert out["snapshot"]["can_trade"] is False


def test_tampered_capture_fails_watchtower_hash_binding():
    c, w, r = bundle()
    c["assets"]["BTCUSDT"]["open_interest"]["openInterest"] = "9999"
    with pytest.raises(ValueError, match="watchtower capture sha256 mismatch"):
        m.build_bridge(c, w, r)


def test_tampered_watchtower_fails_radar_report_binding():
    c, w, r = bundle()
    w["matrix"][0]["attention_score"] = 99.0
    with pytest.raises(ValueError, match="radar watchtower report binding mismatch"):
        m.build_bridge(c, w, r)


def test_wrong_watchtower_producer_is_refused():
    c, w, r = bundle()
    w["provenance"]["producer_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="watchtower producer sha256 mismatch"):
        m.build_bridge(c, w, r)


def test_wrong_liquidity_producer_binding_is_refused():
    c, w, r = bundle()
    r["provenance"]["liquidity_producer_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="radar liquidity producer binding mismatch"):
        m.build_bridge(c, w, r)


def test_future_raw_source_timestamp_is_refused():
    c, w, r = bundle()
    c2 = copy.deepcopy(c)
    c2["assets"]["BTCUSDT"]["open_interest"]["time"] = CAPTURED_MS + 1
    w2 = watchtower(c2)
    r2 = radar(w2)
    with pytest.raises(ValueError, match="source timestamp after capture"):
        m.build_bridge(c2, w2, r2)


def test_radar_cannot_change_directional_bias_created_by_watchtower():
    c, w, r = bundle()
    r["matrix"][0]["bias"] = "WATCH_SHORT"
    with pytest.raises(ValueError, match="radar/watchtower bias mismatch"):
        m.build_bridge(c, w, r)


def test_radar_timeframe_state_drift_is_refused():
    c, w, r = bundle()
    r["matrix"][0]["timeframes"]["4h"] = "SHORT"
    with pytest.raises(ValueError, match="radar/watchtower timeframe mismatch"):
        m.build_bridge(c, w, r)


def test_only_current_policy_symbol_and_four_hour_bridge_are_supported():
    c, w, r = bundle()
    with pytest.raises(ValueError, match="BTCUSDT"):
        m.build_bridge(c, w, r, symbol="ETHUSDT")
    with pytest.raises(ValueError, match="4h"):
        m.build_bridge(c, w, r, timeframe="1h")


def test_snapshot_digest_is_replayable_and_tamper_evident():
    c, w, r = bundle()
    out = m.build_bridge(c, w, r)
    assert out["snapshot_sha256"] == m.stable_sha256(out["snapshot"])
    out["snapshot"]["price"]["last"] += 1.0
    with pytest.raises(ValueError, match="snapshot digest mismatch"):
        m.validate_bridge(out)
