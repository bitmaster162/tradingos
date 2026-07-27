from __future__ import annotations

import json
import sys
from urllib.parse import parse_qs, urlparse

import tools.cross_venue_spot_data_collector as collector
from tools.cross_venue_spot_data_collector import (
    align_rows,
    normalize_rows,
    merge_archive,
    parse_binance_candle,
    parse_coinbase_candle,
    quality_report,
)


def test_venue_shapes_normalize_to_same_contract() -> None:
    binance = parse_binance_candle([60_000, "100", "102", "99", "101", "5", 0, 0, 0, 0, 0, 0])
    coinbase = parse_coinbase_candle([60, 99, 102, 100, 101, 4])
    assert binance["time_ms"] == coinbase["time_ms"] == 60_000
    assert binance["close"] == coinbase["close"] == 101.0
    assert binance["venue"] == "binance"
    assert coinbase["venue"] == "coinbase"


def test_normalize_removes_open_out_of_window_and_duplicates() -> None:
    rows = [
        {"time_ms": 0, "close": 1},
        {"time_ms": 60_000, "close": 2},
        {"time_ms": 60_000, "close": 3},
        {"time_ms": 120_000, "close": 4},
    ]
    normalized, duplicates = normalize_rows(rows, start_ms=0, end_exclusive_ms=120_000, interval_ms=60_000)
    assert [row["time_ms"] for row in normalized] == [0, 60_000]
    assert normalized[-1]["close"] == 3
    assert duplicates == 1


def test_alignment_uses_only_shared_timestamps_and_causal_returns() -> None:
    left = [
        {"time_ms": 0, "close": 100.0, "volume": 2.0},
        {"time_ms": 60_000, "close": 101.0, "volume": 3.0},
    ]
    right = [
        {"time_ms": 0, "close": 100.0, "volume": 1.0},
        {"time_ms": 60_000, "close": 100.5, "volume": 1.5},
        {"time_ms": 120_000, "close": 102.0, "volume": 2.0},
    ]
    aligned = align_rows(left, right)
    assert len(aligned) == 2
    assert aligned[0]["return_diff_bps"] is None
    assert aligned[1]["return_diff_bps"] is not None
    assert aligned[1]["return_diff_bps"] > 0


def test_archive_merge_replaces_overlap_and_trims_retention() -> None:
    existing = [
        {"time_ms": 0, "close": 1.0},
        {"time_ms": 60_000, "close": 2.0},
    ]
    incoming = [
        {"time_ms": 60_000, "close": 3.0},
        {"time_ms": 120_000, "close": 4.0},
    ]
    merged = merge_archive(existing, incoming, cutoff_ms=60_000, end_exclusive_ms=180_000)
    assert [row["time_ms"] for row in merged] == [60_000, 120_000]
    assert merged[0]["close"] == 3.0


def test_quality_gate_requires_both_venues_and_correlation() -> None:
    left = []
    right = []
    for index, close in enumerate((100.0, 101.0, 100.5, 102.0)):
        timestamp = index * 60_000
        left.append({"time_ms": timestamp, "close": close, "volume": 2.0})
        right.append({"time_ms": timestamp, "close": close, "volume": 1.0})
    report = quality_report(
        binance=left,
        coinbase=right,
        aligned=align_rows(left, right),
        requested_bars=4,
        binance_duplicates=0,
        coinbase_duplicates=0,
        interval="1m",
        start_ms=0,
        end_exclusive_ms=240_000,
    )
    assert report["classification"] == "cross_venue_collection_ready"
    assert report["checks"]["same_base_asset"] is True
    assert report["checks"]["return_comparison_allowed"] is True
    assert report["alignment"]["quote_assets_same"] is False
    assert report["alignment"]["level_spread_comparison_allowed"] is False
    assert report["can_trade"] is False


def test_main_wires_coinbase_product_into_quality_report(tmp_path, monkeypatch) -> None:
    start = 1_700_000_000_000 - 1_700_000_000_000 % 60_000
    left = []
    right = []
    for index in range(60):
        timestamp = start + index * 60_000
        close = 100.0 + index * 0.1
        base = {
            "time": collector.iso_from_ms(timestamp),
            "time_ms": timestamp,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
        }
        left.append({**base, "venue": "binance", "product": "BTCUSDT"})
        right.append({**base, "venue": "coinbase", "product": "BTC-USD"})
    monkeypatch.setattr(collector, "fetch_binance_candles", lambda **kwargs: left)
    monkeypatch.setattr(collector, "fetch_coinbase_candles", lambda **kwargs: right)
    out_dir = tmp_path / "data"
    report_prefix = tmp_path / "report"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collector",
            "--hours",
            "1",
            "--end",
            collector.iso_from_ms(start + 60 * 60_000),
            "--out-dir",
            str(out_dir),
            "--report-prefix",
            str(report_prefix),
        ],
    )
    assert collector.main() == 0
    report = json.loads(report_prefix.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["venues"]["coinbase"]["product"] == "BTC-USD"
    assert report["alignment"]["level_spread_comparison_allowed"] is False
    assert report["archive"]["aligned_rows"] == 60
    manifest = json.loads((out_dir / "COLLECTION_MANIFEST.json").read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 3
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])
    assert manifest["can_trade"] is False


def test_coinbase_pagination_does_not_overlap_boundaries() -> None:
    requested_ranges: list[tuple[int, int]] = []

    def fake_fetch(url: str):
        query = parse_qs(urlparse(url).query)
        start = collector.parse_iso_ms(query["start"][0]) // 1000
        end = collector.parse_iso_ms(query["end"][0]) // 1000
        requested_ranges.append((start, end))
        return [[end, 99, 102, 100, 101, 1], [start, 99, 102, 100, 101, 1]]

    rows = collector.fetch_coinbase_candles(
        product="BTC-USD",
        interval="1m",
        start_ms=0,
        end_exclusive_ms=600 * 60_000,
        request_delay=0,
        fetcher=fake_fetch,
    )
    assert requested_ranges == [(0, 299 * 60), (300 * 60, 599 * 60)]
    timestamps = [row["time_ms"] for row in rows]
    assert len(timestamps) == len(set(timestamps))
