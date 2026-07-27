from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from tools.microstructure_liquidity_void_expansion_research import binance_minutes, configs_from_protocol


def test_configs_from_protocol_matches_preregistered_budget() -> None:
    configs = configs_from_protocol()

    assert len(configs) == 72
    assert len({cfg.strategy_id for cfg in configs}) == 72


def test_binance_minutes_requires_signed_delta_and_ignores_coinbase_rows() -> None:
    rows = [
        {
            "minute_ms": "1",
            "venue": "binance",
            "price_first": "100",
            "price_last": "101",
            "trades": "10",
            "notional": "10000",
            "delta_notional": "300",
            "aggressor_side_usable": "true",
            "avg_spread_bps": "2.0",
            "avg_top_imbalance": "0.1",
        },
        {
            "minute_ms": "1",
            "venue": "coinbase",
            "price_first": "100",
            "price_last": "101",
            "trades": "10",
            "notional": "10000",
            "delta_notional": "",
            "aggressor_side_usable": "false",
            "avg_spread_bps": "2.0",
            "avg_top_imbalance": "0.1",
        },
        {
            "minute_ms": "2",
            "venue": "binance",
            "price_first": "101",
            "price_last": "102",
            "trades": "10",
            "notional": "10000",
            "delta_notional": "300",
            "aggressor_side_usable": "false",
            "avg_spread_bps": "2.0",
            "avg_top_imbalance": "0.1",
        },
    ]

    parsed = binance_minutes(rows)

    assert len(parsed) == 1
    assert parsed[0].minute_ms == 1
    assert parsed[0].delta_ratio == 0.03


def test_liquidity_void_cli_writes_report_and_lock_on_synthetic_cache(tmp_path: Path) -> None:
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
        for index in range(520):
            burst = index % 37 == 0
            next_price = price * (1.0 + (0.00006 if burst else 0.00001))
            trades = 80 if burst else 12
            spread = 4.0 if burst else 1.0
            delta = 6_000.0 if burst else 1_000.0
            writer.writerow(
                {
                    "minute": f"1970-01-01T00:{index % 60:02d}:00Z",
                    "minute_ms": index * 60_000,
                    "venue": "binance",
                    "product": "BTCUSDT",
                    "trades": trades,
                    "notional": 100_000.0,
                    "price_first": round(price, 6),
                    "price_last": round(next_price, 6),
                    "return_bps": round((next_price / price - 1.0) * 10_000, 6),
                    "buy_notional": 53_000.0,
                    "sell_notional": 47_000.0,
                    "delta_notional": delta,
                    "aggressor_side_usable": "true",
                    "book_snapshots": 3,
                    "avg_spread_bps": spread,
                    "avg_top_imbalance": 0.12,
                }
            )
            writer.writerow(
                {
                    "minute": f"1970-01-01T00:{index % 60:02d}:00Z",
                    "minute_ms": index * 60_000,
                    "venue": "coinbase",
                    "product": "BTC-USD",
                    "trades": trades,
                    "notional": 100_000.0,
                    "price_first": round(price, 6),
                    "price_last": round(next_price, 6),
                    "return_bps": round((next_price / price - 1.0) * 10_000, 6),
                    "buy_notional": 53_000.0,
                    "sell_notional": 47_000.0,
                    "delta_notional": "",
                    "aggressor_side_usable": "false",
                    "book_snapshots": 3,
                    "avg_spread_bps": spread,
                    "avg_top_imbalance": 0.12,
                }
            )
            price = next_price

    out_prefix = tmp_path / "research" / "LIQUIDITY_VOID"
    lock_path = tmp_path / "locks" / "liquidity_void.lock.json"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/microstructure_liquidity_void_expansion_research.py",
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
    assert report["hypothesis_id"] == "HYP-MICROSTRUCTURE-LIQUIDITY-VOID-003"
    assert report["search"]["tested"] == 72
    assert report["runtime_boundary"]["research_only"] is True
    assert report["can_trade"] is False
    assert lock["grid_configurations"] == 72
    assert lock["can_trade"] is False
    assert out_prefix.with_suffix(".md").is_file()
