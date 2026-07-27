from __future__ import annotations

from tools import bitunix_trade_bar_finality_audit as module


def trade(ts: int, price: str, size: str, *, recv_offset: int = 10) -> dict:
    return {
        "symbol": "BTCUSDT",
        "venue_ts": ts,
        "recv_ns": (ts + recv_offset) * 1_000_000,
        "p": price,
        "v": size,
    }


def test_build_trade_bars_uses_only_fully_covered_intervals() -> None:
    rows = [
        trade(300_001, "10", "2"),
        trade(400_000, "12", "1"),
        trade(599_999, "11", "3"),
        trade(600_001, "99", "1"),
    ]

    bars, failures = module.build_trade_bars(
        rows,
        capture_start_ms=250_000,
        capture_end_ms=650_000,
        symbol="BTCUSDT",
    )

    assert failures == []
    assert len(bars) == 1
    assert bars[0]["bucket_start_ms"] == 300_000
    assert bars[0]["open"] == "10"
    assert bars[0]["high"] == "12"
    assert bars[0]["low"] == "10"
    assert bars[0]["close"] == "11"
    assert bars[0]["coin_volume"] == "6"
    assert bars[0]["quote_volume"] == "65"


def test_compare_bars_requires_price_and_both_volume_fields() -> None:
    bars = [
        {
            "bucket_start_ms": 300_000,
            "open": "10",
            "high": "12",
            "low": "9",
            "close": "11",
            "coin_volume": "6",
            "quote_volume": "65",
            "trade_count": 3,
        }
    ]
    rest_items = [
        {
            "time": 300_000,
            "open": "10.0",
            "high": "12",
            "low": "9.00",
            "close": "11",
            "quoteVol": "6.0",
            "baseVol": "65.00",
        }
    ]

    report = module.compare_bars(bars, rest_items)

    assert report["blockers"] == []
    assert report["matching_bars"] == 1
    assert report["all_full_bars_match"] is True


def test_compare_bars_fails_when_trade_volume_is_incomplete() -> None:
    bars = [
        {
            "bucket_start_ms": 300_000,
            "open": "10",
            "high": "12",
            "low": "9",
            "close": "11",
            "coin_volume": "5.9",
            "quote_volume": "65",
            "trade_count": 3,
        }
    ]
    rest_items = [
        {
            "time": 300_000,
            "open": "10",
            "high": "12",
            "low": "9",
            "close": "11",
            "quoteVol": "6",
            "baseVol": "65",
        }
    ]

    report = module.compare_bars(bars, rest_items)

    assert report["blockers"] == ["trade_bar_mismatch:300000"]
    assert report["all_full_bars_match"] is False
