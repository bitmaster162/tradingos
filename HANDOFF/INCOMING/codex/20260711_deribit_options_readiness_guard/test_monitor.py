from __future__ import annotations

import monitor


def record(timestamp: int) -> dict:
    return {
        "collected_at_ms": timestamp,
        "quality_pass": True,
        "quality": {
            "join_rate": 1.0,
            "mark_iv_coverage": 1.0,
            "open_interest_coverage": 1.0,
            "distinct_expiries": 12,
        },
    }


def test_readiness_uses_unique_slots_not_raw_row_count() -> None:
    interval_ms = 300_000
    start = 1_800_000_000_000
    records = [record(start + index * interval_ms) for index in range(2017)]
    records.append(record(start + 10 * interval_ms + 1_000))
    contract = {
        "schedule": {
            "interval_seconds": 300,
            "maximum_freshness_seconds": 900,
            "maximum_admitted_gap_seconds": 900,
        },
        "research_gate": {
            "minimum_span_days": 7.0,
            "minimum_healthy_slots": 1800,
            "minimum_scheduled_coverage": 0.95,
            "minimum_join_rate": 0.98,
            "minimum_mark_iv_coverage": 0.95,
            "minimum_open_interest_coverage": 0.95,
            "minimum_distinct_expiries": 3,
            "raw_provenance_required": True,
            "collector_lock_required": True,
        },
    }
    current = start + 2016 * interval_ms + 60_000
    report = monitor.evaluate(records, len(records), [], current, contract, True)
    assert report["decision"] == "deribit_options_ready_for_preregistration_review"
    assert report["metrics"]["records"] == 2018
    assert report["metrics"]["unique_slots"] == 2017
    assert report["metrics"]["oversampled_records"] == 1
    assert report["metrics"]["scheduled_coverage"] == 1.0
    assert report["research_gate_ready"] is True
    assert report["can_trade"] is False
