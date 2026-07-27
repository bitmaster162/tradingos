from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from tools.microstructure_book_pressure_alignment_research import aligned_minutes, configs_from_protocol


def test_configs_from_protocol_matches_preregistered_budget() -> None:
    configs = configs_from_protocol()

    assert len(configs) == 324
    assert len({cfg.strategy_id for cfg in configs}) == 324


def test_aligned_minutes_requires_both_venue_top_of_book_pressure() -> None:
    rows = [
        {
            "minute_ms": "1",
            "venue": "binance",
            "price_first": "100",
            "price_last": "101",
            "avg_top_imbalance": "0.2",
            "avg_spread_bps": "1.0",
        },
        {
            "minute_ms": "1",
            "venue": "coinbase",
            "price_first": "100",
            "price_last": "101",
            "avg_top_imbalance": "-0.1",
            "avg_spread_bps": "1.2",
        },
        {
            "minute_ms": "2",
            "venue": "binance",
            "price_first": "101",
            "price_last": "102",
            "avg_top_imbalance": "",
            "avg_spread_bps": "1.0",
        },
        {
            "minute_ms": "2",
            "venue": "coinbase",
            "price_first": "101",
            "price_last": "102",
            "avg_top_imbalance": "0.2",
            "avg_spread_bps": "1.0",
        },
    ]

    aligned = aligned_minutes(rows)

    assert len(aligned) == 1
    assert aligned[0].minute_ms == 1
    assert aligned[0].binance_pressure == 0.2
    assert aligned[0].coinbase_pressure == -0.1


def test_book_pressure_cli_writes_report_and_lock_on_synthetic_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    feature_path = cache_dir / "minute_features_v2.csv"
    fieldnames = [
        "minute",
        "minute_ms",
        "venue",
        "product",
        "trades",
        "notional",
        "price_first",
        "price_last",
        "return_bps",
        "buy_notional",
        "sell_notional",
        "delta_notional",
        "aggressor_side_usable",
        "book_snapshots",
        "avg_spread_bps",
        "avg_top_imbalance",
    ]
    with feature_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        price = 100_000.0
        for index in range(620):
            pressure_event = index % 41 == 0
            pressure = 0.45 if pressure_event else 0.03
            coinbase_pressure = 0.25 if pressure_event else 0.02
            next_price = price * (1.0 + (0.00005 if pressure_event else 0.000005))
            for venue, top_imbalance in (("binance", pressure), ("coinbase", coinbase_pressure)):
                writer.writerow(
                    {
                        "minute": f"1970-01-01T00:{index % 60:02d}:00Z",
                        "minute_ms": index * 60_000,
                        "venue": venue,
                        "product": "BTCUSDT" if venue == "binance" else "BTC-USD",
                        "trades": 12,
                        "notional": 100_000.0,
                        "price_first": round(price, 6),
                        "price_last": round(next_price, 6),
                        "return_bps": round((next_price / price - 1.0) * 10_000, 6),
                        "buy_notional": 52_000.0,
                        "sell_notional": 48_000.0,
                        "delta_notional": 4_000.0 if venue == "binance" else "",
                        "aggressor_side_usable": "true" if venue == "binance" else "false",
                        "book_snapshots": 3,
                        "avg_spread_bps": 1.0,
                        "avg_top_imbalance": top_imbalance,
                    }
                )
            price = next_price

    out_prefix = tmp_path / "research" / "BOOK_PRESSURE"
    lock_path = tmp_path / "locks" / "book_pressure.lock.json"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/microstructure_book_pressure_alignment_research.py",
            "--cache-dir",
            str(cache_dir),
            "--out-prefix",
            str(out_prefix),
            "--lock-path",
            str(lock_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    assert '"can_trade": false' in completed.stdout.lower()
    report = json.loads(out_prefix.with_suffix(".json").read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert report["hypothesis_id"] == "HYP-MICROSTRUCTURE-BOOK-PRESSURE-004"
    assert report["search"]["tested"] == 324
    assert report["runtime_boundary"]["research_only"] is True
    assert report["can_trade"] is False
    assert lock["grid_configurations"] == 324
    assert lock["can_trade"] is False
    assert out_prefix.with_suffix(".md").is_file()
