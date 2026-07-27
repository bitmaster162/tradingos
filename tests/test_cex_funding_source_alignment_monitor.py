from __future__ import annotations

import json
from pathlib import Path

from tools.cex_funding_source_alignment_monitor import (
    build_report,
    iso_from_ms,
    parameter_contract_sha256,
    parse_iso_ms,
    run,
    validate_lock,
)


BASE_MS = 1_783_904_000_000
ROOT = Path(__file__).resolve().parents[1]


def lock(minimum_buckets: int = 2) -> dict:
    return {
        "lock_id": "alignment_test",
        "status": "fixed_source_alignment_contract",
        "inputs": {"aggregate_journal": "aggregate.jsonl", "direct_journal": "direct.jsonl"},
        "mapping": [
            {"aggregate_venue": "BinPerp", "direct_venue": "BinanceDirect", "label": "binance"},
            {"aggregate_venue": "BybitPerp", "direct_venue": "BybitDirect", "label": "bybit"},
        ],
        "symbols": ["BTC", "ETH", "SOL"],
        "metrics": {
            "field": "funding_rate_per_hour",
            "same_minute_only": True,
            "lagged_comparisons_allowed": False,
            "price_outcomes_allowed": False,
        },
        "readiness_gate": {
            "minimum_matching_minute_buckets": minimum_buckets,
            "minimum_independent_utc_days": 1,
            "minimum_comparison_coverage": 1.0,
            "minimum_matching_time_coverage": 0.95,
            "maximum_consecutive_gap_minutes": 5,
            "maximum_bad_jsonl_lines": 0,
            "automatic_source_equivalence_claim_allowed": False,
        },
        "runtime_boundary": {
            "data_quality_only": True,
            "edge_evaluator": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def row(bucket: int, aggregate: bool, offset: float = 0.0) -> dict:
    venues = ("BinPerp", "BybitPerp") if aggregate else ("BinanceDirect", "BybitDirect")
    return {
        "minute_bucket_ms": bucket,
        "symbols": {
            symbol: {
                venues[0]: {"funding_rate_per_hour": 0.00001 + offset},
                venues[1]: {"funding_rate_per_hour": 0.00002 + offset},
            }
            for symbol in ("BTC", "ETH", "SOL")
        },
        "can_trade": False,
    }


def test_same_minute_alignment_reaches_manual_review_without_edge_claim() -> None:
    aggregate = [row(BASE_MS, True), row(BASE_MS + 60_000, True, 0.000001)]
    direct = [row(BASE_MS, False), row(BASE_MS + 60_000, False)]

    report = build_report(lock(), aggregate, direct, 0, 0)

    assert report["decision"] == "cex_funding_source_alignment_ready_for_manual_semantic_review"
    assert report["sample"]["matching_minute_buckets"] == 2
    assert report["sample"]["comparison_coverage"] == 1.0
    assert report["sample"]["matching_time_coverage"] == 1.0
    assert report["sample"]["maximum_consecutive_gap_minutes"] == 1.0
    assert report["same_minute_metrics"]["BTC"]["binance"]["points"] == 2
    assert report["same_minute_metrics"]["BTC"]["binance"]["maximum_absolute_delta_bps_per_hour"] == 0.01
    assert report["automatic_equivalence_claim"] is False
    assert report["edge_evaluated"] is False
    assert report["can_trade"] is False


def test_unmatched_minutes_are_not_compared() -> None:
    aggregate = [row(BASE_MS, True), row(BASE_MS + 60_000, True)]
    direct = [row(BASE_MS, False), row(BASE_MS + 120_000, False)]

    report = build_report(lock(), aggregate, direct, 0, 0)

    assert report["decision"] == "cex_funding_source_alignment_collecting"
    assert report["sample"]["matching_minute_buckets"] == 1
    assert report["sample"]["valid_comparisons"] == 6


def test_bad_jsonl_line_blocks_alignment(tmp_path: Path) -> None:
    lock_path = tmp_path / "lock.json"
    aggregate_path = tmp_path / "aggregate.jsonl"
    direct_path = tmp_path / "direct.jsonl"
    out_prefix = tmp_path / "report"
    value = lock(minimum_buckets=1)
    value["inputs"] = {"aggregate_journal": str(aggregate_path), "direct_journal": str(direct_path)}
    lock_path.write_text(json.dumps(value), encoding="utf-8")
    aggregate_path.write_text(json.dumps(row(BASE_MS, True)) + "\n{bad\n", encoding="utf-8")
    direct_path.write_text(json.dumps(row(BASE_MS, False)) + "\n", encoding="utf-8")

    code, report = run(lock_path, out_prefix)

    assert code == 1
    assert report["decision"] == "cex_funding_source_alignment_blocked_data_quality"
    assert report["sample"]["aggregate_bad_lines"] == 1
    assert report["can_trade"] is False


def test_time_continuity_gate_detects_sparse_matching_buckets() -> None:
    aggregate = [row(BASE_MS, True), row(BASE_MS + 10 * 60_000, True)]
    direct = [row(BASE_MS, False), row(BASE_MS + 10 * 60_000, False)]

    report = build_report(lock(), aggregate, direct, 0, 0)

    assert report["decision"] == "cex_funding_source_alignment_terminal_data_quality_failure"
    assert report["sample"]["expected_minute_buckets"] == 11
    assert report["sample"]["matching_time_coverage"] == 0.18181818
    assert report["sample"]["maximum_consecutive_gap_minutes"] == 10.0
    assert "minimum_matching_time_coverage" in report["blockers"]
    assert "maximum_consecutive_gap_minutes" in report["blockers"]
    assert report["terminal"]["reached"] is True
    assert report["terminal"]["reasons"] == ["maximum_consecutive_gap_minutes_exceeded"]
    assert report["can_trade"] is False


def test_rows_before_lock_floor_are_excluded_from_alignment() -> None:
    value = lock(minimum_buckets=1)
    value["forward_start_at"] = iso_from_ms(BASE_MS + 60_000)
    aggregate = [row(BASE_MS, True), row(BASE_MS + 60_000, True)]
    direct = [row(BASE_MS, False), row(BASE_MS + 60_000, False)]

    report = build_report(value, aggregate, direct, 0, 0)

    assert report["sample"]["matching_minute_buckets"] == 1
    assert report["sample"]["aggregate_rows_before_forward_floor_excluded"] == 1
    assert report["sample"]["direct_rows_before_forward_floor_excluded"] == 1
    assert report["sample"]["first_matching_bucket"] == iso_from_ms(BASE_MS + 60_000)
    assert report["can_trade"] is False


def test_persisted_v3_successor_is_parameter_identical_and_fail_closed() -> None:
    v2 = json.loads(
        (ROOT / "configs" / "CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V2_2026-07-13.json").read_text(
            encoding="utf-8"
        )
    )
    v3 = json.loads(
        (ROOT / "configs" / "CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V3_2026-07-14.json").read_text(
            encoding="utf-8"
        )
    )
    parameter_keys = ("inputs", "mapping", "symbols", "metrics", "readiness_gate", "runtime_boundary", "can_trade")

    assert {key: v3[key] for key in parameter_keys} == {key: v2[key] for key in parameter_keys}
    assert v3["parameter_contract_sha256"] == parameter_contract_sha256(v3)
    assert v3["predecessor"]["parameters_changed"] is False
    assert v3["predecessor"]["history_rewritten"] is False
    assert v3["lifecycle"]["predecessor_rows_admitted"] is False
    assert v3["lifecycle"]["historical_backfill_allowed"] is False
    assert validate_lock(v3) == []


def test_v3_waits_for_floor_without_admitting_predecessor_rows() -> None:
    value = json.loads(
        (ROOT / "configs" / "CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V3_2026-07-14.json").read_text(
            encoding="utf-8"
        )
    )
    floor_ms = parse_iso_ms(value["forward_start_at"])
    assert floor_ms is not None
    aggregate = [row(floor_ms - 60_000, True)]
    direct = [row(floor_ms - 60_000, False)]

    report = build_report(value, aggregate, direct, 0, 0, current_ms=floor_ms - 1)

    assert report["decision"] == "cex_funding_source_alignment_waiting_forward_floor"
    assert report["sample"]["matching_minute_buckets"] == 0
    assert report["sample"]["aggregate_rows_before_forward_floor_excluded"] == 1
    assert report["terminal"]["reached"] is False
    assert report["edge_evaluated"] is False
    assert report["can_trade"] is False


def test_leading_gap_is_measured_from_the_frozen_floor() -> None:
    value = lock(minimum_buckets=1)
    value["forward_start_at"] = iso_from_ms(BASE_MS)
    aggregate = [row(BASE_MS + 10 * 60_000, True)]
    direct = [row(BASE_MS + 10 * 60_000, False)]

    report = build_report(value, aggregate, direct, 0, 0, current_ms=BASE_MS + 11 * 60_000)

    assert report["decision"] == "cex_funding_source_alignment_terminal_data_quality_failure"
    assert report["sample"]["leading_gap_from_floor_minutes"] == 10.0
    assert report["sample"]["maximum_consecutive_gap_minutes"] == 10.0
    assert report["terminal"]["reached"] is True


def test_v3_parameter_hash_detects_threshold_mutation() -> None:
    value = json.loads(
        (ROOT / "configs" / "CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V3_2026-07-14.json").read_text(
            encoding="utf-8"
        )
    )
    value["readiness_gate"]["maximum_consecutive_gap_minutes"] = 6

    assert "parameter_contract_sha256" in validate_lock(value)


def test_v2_terminal_tombstone_prevents_resume_or_sample_reuse() -> None:
    tombstone = json.loads(
        (ROOT / "docs" / "CEX_FUNDING_SOURCE_ALIGNMENT_V2_TERMINAL_TOMBSTONE_2026-07-14.json").read_text(
            encoding="utf-8"
        )
    )

    assert tombstone["status"] == "TOMBSTONED_TERMINAL_DATA_QUALITY_FAILURE"
    assert tombstone["sample"]["maximum_consecutive_gap_minutes"] == 27.0
    assert tombstone["disposition"]["resume_allowed"] is False
    assert tombstone["disposition"]["predecessor_rows_admitted_to_successor"] is False
    assert tombstone["disposition"]["price_outcomes_read"] is False
    assert tombstone["superseded_by"]["parameters_changed"] is False
    assert tombstone["can_trade"] is False
