from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from tools.microstructure_imbalance_continuation_research import aligned_minutes, configs_from_protocol


def test_configs_from_protocol_matches_preregistered_budget() -> None:
    configs = configs_from_protocol()

    assert len(configs) == 216
    assert len({cfg.strategy_id for cfg in configs}) == 216


def test_aligned_minutes_requires_binance_aggressor_side_but_not_coinbase() -> None:
    rows = [
        {
            "minute_ms": "1",
            "venue": "binance",
            "price_first": "100",
            "price_last": "101",
            "notional": "10000",
            "delta_notional": "250",
            "return_bps": "100",
            "aggressor_side_usable": "true",
        },
        {
            "minute_ms": "1",
            "venue": "coinbase",
            "price_first": "100",
            "price_last": "101",
            "notional": "10000",
            "delta_notional": "",
            "return_bps": "5",
            "aggressor_side_usable": "false",
        },
        {
            "minute_ms": "2",
            "venue": "binance",
            "price_first": "101",
            "price_last": "102",
            "notional": "10000",
            "delta_notional": "250",
            "return_bps": "99",
            "aggressor_side_usable": "false",
        },
        {
            "minute_ms": "2",
            "venue": "coinbase",
            "price_first": "101",
            "price_last": "102",
            "notional": "10000",
            "delta_notional": "",
            "return_bps": "5",
            "aggressor_side_usable": "false",
        },
    ]

    aligned = aligned_minutes(rows)

    assert len(aligned) == 1
    assert aligned[0].minute_ms == 1
    assert aligned[0].coinbase_return_bps == 5.0


def test_research_cli_writes_report_and_lock_on_synthetic_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    feature_path = cache_dir / "minute_features.csv"
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
        for index in range(520):
            minute_ms = index * 60_000
            next_price = price * (1.0 + (0.00003 if index % 11 else -0.00002))
            return_bps = (next_price / price - 1.0) * 10_000
            delta = 2_000.0 if index % 17 else -2_000.0
            for venue in ("binance", "coinbase"):
                writer.writerow(
                    {
                        "minute": f"1970-01-01T00:{index % 60:02d}:00Z",
                        "minute_ms": minute_ms,
                        "venue": venue,
                        "product": "BTCUSDT" if venue == "binance" else "BTC-USD",
                        "trades": 12,
                        "notional": 100_000.0,
                        "price_first": round(price, 6),
                        "price_last": round(next_price, 6),
                        "return_bps": round(return_bps if venue == "binance" else return_bps / 2.0, 6),
                        "buy_notional": 51_000.0,
                        "sell_notional": 49_000.0,
                        "delta_notional": delta if venue == "binance" else "",
                        "aggressor_side_usable": "true" if venue == "binance" else "false",
                        "book_snapshots": 3,
                        "avg_spread_bps": 1.0,
                        "avg_top_imbalance": 0.05,
                    }
                )
            price = next_price

    out_prefix = tmp_path / "research" / "IMBALANCE"
    lock_path = tmp_path / "locks" / "imbalance.lock.json"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/microstructure_imbalance_continuation_research.py",
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
    assert report["hypothesis_id"] == "HYP-MICROSTRUCTURE-IMBALANCE-001"
    assert report["search"]["tested"] == 216
    assert report["runtime_boundary"]["research_only"] is True
    assert report["can_trade"] is False
    assert lock["grid_configurations"] == 216
    assert lock["can_trade"] is False
    assert out_prefix.with_suffix(".md").is_file()
