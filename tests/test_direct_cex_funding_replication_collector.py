from __future__ import annotations

import json
from pathlib import Path

from tools.direct_cex_funding_replication_collector import derive_snapshot, run_once


OBSERVED_AT_MS = 1_783_904_000_000


def contract() -> dict:
    return {
        "lock_id": "direct_test",
        "status": "fixed_forward_data_collection_contract",
        "sources": {},
        "collection": {
            "symbols": ["BTC", "ETH", "SOL"],
            "quote_asset": "USDT",
            "venue_ids": ["BinanceDirect", "BybitDirect"],
            "bucket_seconds": 60,
            "request_timeout_seconds": 20,
            "default_binance_funding_interval_hours": 8,
            "credentials_allowed": False,
        },
        "quality_gate": {
            "minimum_interval_hours_exclusive": 0,
            "maximum_interval_hours": 24,
            "maximum_source_age_seconds": 30,
            "maximum_source_clock_lead_seconds": 5,
            "maximum_next_funding_past_seconds": 300,
            "maximum_next_funding_future_hours": 24,
        },
        "replication_gate": {"minimum_unique_minute_snapshots": 10000},
        "runtime_boundary": {
            "collector_only": True,
            "directional_signal": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def payloads() -> dict:
    markets = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    return {
        "binance_premium_index": [
            {
                "symbol": market,
                "lastFundingRate": "0.00008",
                "nextFundingTime": OBSERVED_AT_MS + 8 * 3_600_000,
                "time": OBSERVED_AT_MS - 1000,
            }
            for market in markets
        ],
        "binance_funding_info": [{"symbol": "SOLUSDT", "fundingIntervalHours": 4}],
        "bybit_tickers": {
            "retCode": 0,
            "time": OBSERVED_AT_MS - 500,
            "result": {
                "list": [
                    {
                        "symbol": market,
                        "fundingRate": "0.00004",
                        "fundingIntervalHour": "8",
                        "nextFundingTime": str(OBSERVED_AT_MS + 8 * 3_600_000),
                    }
                    for market in markets
                ]
            },
        },
    }


def test_direct_snapshot_preserves_semantics_and_interval_override() -> None:
    snapshot = derive_snapshot(payloads(), OBSERVED_AT_MS, contract())

    assert snapshot["quality"]["quality_pass"] is True
    assert snapshot["quality"]["valid_points"] == 6
    assert snapshot["symbols"]["BTC"]["BinanceDirect"]["funding_rate_per_hour"] == 0.00001
    assert snapshot["symbols"]["SOL"]["BinanceDirect"]["funding_rate_per_hour"] == 0.00002
    assert snapshot["symbols"]["SOL"]["BinanceDirect"]["funding_interval_source"] == "fundingInfo_override"
    assert snapshot["directional_signal"] is None
    assert snapshot["can_trade"] is False


def test_missing_direct_symbol_fails_quality() -> None:
    value = payloads()
    value["bybit_tickers"]["result"]["list"].pop()

    snapshot = derive_snapshot(value, OBSERVED_AT_MS, contract())

    assert snapshot["quality"]["quality_pass"] is False
    assert snapshot["quality"]["missing_points"] == ["SOL:BybitDirect"]


def test_fixture_run_writes_separate_replication_journal(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.json"
    journal = tmp_path / "direct.jsonl"
    out_prefix = tmp_path / "report"
    contract_path.write_text(json.dumps(contract()), encoding="utf-8")

    code, report = run_once(
        contract_path,
        journal,
        out_prefix,
        payloads=payloads(),
        observed_at_ms=OBSERVED_AT_MS,
    )

    assert code == 0
    assert report["decision"] == "direct_cex_funding_replication_snapshot_healthy_appended"
    assert report["sample"]["unique_minute_buckets"] == 1
    assert report["sample"]["required_point_coverage"] == 1.0
    assert report["can_trade"] is False
