from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from tools.microstructure_cross_venue_dislocation_reversion_research import (
    aligned_minutes,
    configs_from_protocol,
)


def test_configs_from_protocol_matches_preregistered_budget() -> None:
    configs = configs_from_protocol()

    assert len(configs) == 162
    assert len({cfg.strategy_id for cfg in configs}) == 162


def test_aligned_minutes_uses_return_only_cross_venue_fields() -> None:
    rows = [
        {
            "minute_ms": "1",
            "venue": "binance",
            "price_first": "100",
            "price_last": "101",
            "return_bps": "100",
            "avg_spread_bps": "2.0",
        },
        {
            "minute_ms": "1",
            "venue": "coinbase",
            "price_first": "200",
            "price_last": "201",
            "return_bps": "50",
            "avg_spread_bps": "3.0",
        },
    ]

    aligned = aligned_minutes(rows)

    assert len(aligned) == 1
    assert aligned[0].binance_return_bps == 100.0
    assert aligned[0].coinbase_return_bps == 50.0
    assert aligned[0].binance_spread_bps == 2.0


def test_dislocation_reversion_cli_writes_report_and_lock_on_synthetic_cache(tmp_path: Path) -> None:
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
        binance_price = 100_000.0
        coinbase_price = 100_000.0
        for index in range(520):
            base_move = 0.00002 if index % 13 else -0.00001
            dislocation = 0.00004 if index % 29 == 0 else 0.0
            binance_next = binance_price * (1.0 + base_move + dislocation)
            coinbase_next = coinbase_price * (1.0 + base_move)
            for venue, price, next_price in (
                ("binance", binance_price, binance_next),
                ("coinbase", coinbase_price, coinbase_next),
            ):
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
                        "buy_notional": 51_000.0,
                        "sell_notional": 49_000.0,
                        "delta_notional": 2_000.0 if venue == "binance" else "",
                        "aggressor_side_usable": "true" if venue == "binance" else "false",
                        "book_snapshots": 3,
                        "avg_spread_bps": 1.0,
                        "avg_top_imbalance": 0.05,
                    }
                )
            binance_price = binance_next
            coinbase_price = coinbase_next

    out_prefix = tmp_path / "research" / "DISLOCATION"
    lock_path = tmp_path / "locks" / "dislocation.lock.json"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/microstructure_cross_venue_dislocation_reversion_research.py",
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
    assert report["hypothesis_id"] == "HYP-MICROSTRUCTURE-DISLOCATION-002"
    assert report["search"]["tested"] == 162
    assert report["runtime_boundary"]["research_only"] is True
    assert report["can_trade"] is False
    assert lock["grid_configurations"] == 162
    assert lock["can_trade"] is False
    assert out_prefix.with_suffix(".md").is_file()
