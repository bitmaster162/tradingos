from __future__ import annotations

import json
from pathlib import Path

from tools.hyperliquid_cross_venue_funding_collector import (
    append_snapshot,
    derive_snapshot,
    journal_metrics,
    run_once,
)


OBSERVED_AT_MS = 1_783_902_240_000


def contract() -> dict:
    return {
        "lock_id": "test_lock",
        "status": "fixed_forward_data_collection_contract",
        "source": {"url": "https://api.hyperliquid.xyz/info"},
        "collection": {
            "symbols": ["BTC", "ETH", "SOL"],
            "venue_ids": ["BinPerp", "BybitPerp", "HlPerp"],
            "bucket_seconds": 60,
            "direct_hyperliquid_context_required": True,
            "hyperliquid_funding_interval_hours": 1,
            "request_timeout_seconds": 20,
            "credentials_allowed": False,
        },
        "quality_gate": {
            "minimum_payload_rows": 3,
            "minimum_interval_hours_exclusive": 0,
            "maximum_interval_hours": 24,
            "maximum_next_funding_past_seconds": 300,
            "maximum_next_funding_future_hours": 24,
            "direct_hyperliquid_context_required": True,
        },
        "future_research_lock": {"minimum_unique_minute_snapshots": 10000},
        "runtime_boundary": {
            "collector_only": True,
            "directional_signal": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def venue_rows(rate: str = "0.00008") -> list:
    return [
        ["BinPerp", {"fundingRate": rate, "nextFundingTime": OBSERVED_AT_MS + 8 * 3_600_000, "fundingIntervalHours": 8}],
        ["BybitPerp", {"fundingRate": "0.00004", "nextFundingTime": OBSERVED_AT_MS + 4 * 3_600_000, "fundingIntervalHours": 4}],
        ["HlPerp", {"fundingRate": "0.00001", "nextFundingTime": OBSERVED_AT_MS + 3_600_000, "fundingIntervalHours": 1}],
    ]


def complete_payload() -> dict:
    symbols = ("BTC", "ETH", "SOL")
    return {
        "predictedFundings": [[symbol, venue_rows()] for symbol in symbols],
        "metaAndAssetCtxs": [
            {"universe": [{"name": symbol} for symbol in symbols]},
            [{"funding": "0.00001", "markPx": "100", "openInterest": "1000"} for _ in symbols],
        ],
    }


def write_contract(path: Path) -> None:
    path.write_text(json.dumps(contract()), encoding="utf-8")


def test_complete_snapshot_normalizes_intervals_and_stays_non_trading() -> None:
    snapshot = derive_snapshot(complete_payload(), OBSERVED_AT_MS, contract())

    assert snapshot["quality"]["quality_pass"] is True
    assert snapshot["quality"]["valid_points"] == 9
    assert snapshot["symbols"]["BTC"]["BinPerp"]["funding_rate_per_hour"] == 0.00001
    assert snapshot["symbols"]["BTC"]["HlPerp"]["funding_rate_per_hour"] == 0.00001
    assert snapshot["directional_signal"] is None
    assert snapshot["orders_allowed"] is False
    assert snapshot["can_trade"] is False


def test_missing_venue_is_recorded_as_degraded() -> None:
    payload = complete_payload()
    payload["metaAndAssetCtxs"][0]["universe"].pop(1)
    payload["metaAndAssetCtxs"][1].pop(1)

    snapshot = derive_snapshot(payload, OBSERVED_AT_MS, contract())

    assert snapshot["quality"]["quality_pass"] is False
    assert snapshot["quality"]["valid_points"] == 8
    assert snapshot["quality"]["missing_points"] == ["ETH:HlPerp"]


def test_append_only_journal_skips_duplicate_minute_without_rewrite(tmp_path: Path) -> None:
    journal = tmp_path / "snapshots.jsonl"
    first = derive_snapshot(complete_payload(), OBSERVED_AT_MS, contract())
    changed = derive_snapshot(complete_payload(), OBSERVED_AT_MS + 30_000, contract())

    assert append_snapshot(journal, first) == (True, "appended")
    assert append_snapshot(journal, changed) == (False, "duplicate_or_nonmonotonic_minute_bucket")
    rows = journal.read_text(encoding="utf-8").splitlines()

    assert len(rows) == 1
    assert json.loads(rows[0])["source_payload_sha256"] == first["source_payload_sha256"]


def test_run_once_with_fixture_writes_quality_report(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.json"
    journal_path = tmp_path / "snapshots.jsonl"
    out_prefix = tmp_path / "report"
    write_contract(contract_path)

    code, report = run_once(
        contract_path,
        journal_path,
        out_prefix,
        payload=complete_payload(),
        observed_at_ms=OBSERVED_AT_MS,
    )

    assert code == 0
    assert report["decision"] == "cex_dex_funding_snapshot_healthy_appended"
    assert report["snapshot_appended"] is True
    assert report["can_trade"] is False
    assert out_prefix.with_suffix(".json").is_file()
    assert out_prefix.with_suffix(".md").is_file()
    metrics = journal_metrics(journal_path, 9)
    assert metrics["unique_minute_buckets"] == 1
    assert metrics["required_point_coverage"] == 1.0
