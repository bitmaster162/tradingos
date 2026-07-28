from __future__ import annotations

import json
from pathlib import Path

from tools.deribit_options_v3_runtime_audit import logical_process_count, predecessor_summary


ROOT = Path(__file__).resolve().parents[1]


def test_predecessor_summary_marks_cumulative_gap_unrecoverable() -> None:
    rows = [
        {"collected_at_ms": 0, "quality_pass": True, "quality_checks": {"join_rate": True}},
        {"collected_at_ms": 300_000, "quality_pass": False, "quality_checks": {"join_rate": False}},
        {"collected_at_ms": 3_600_000, "quality_pass": True, "quality_checks": {"join_rate": True}},
    ]

    summary = predecessor_summary(rows)

    assert summary["maximum_gap_seconds"] == 3300.0
    assert summary["cumulative_max_gap_gate_recoverable_by_waiting"] is False
    assert summary["join_rate_failure_records"] == 1
    assert summary["rows_admitted_to_v3"] is False
    assert summary["price_outcomes_read"] is False


def test_logical_process_count_collapses_python_shim_child() -> None:
    rows = [
        {"ProcessId": 10, "ParentProcessId": 1, "CommandLine": "python deribit_options_surface_collector_v3.py loop"},
        {"ProcessId": 11, "ParentProcessId": 10, "CommandLine": "python deribit_options_surface_collector_v3.py loop"},
        {"ProcessId": 13, "ParentProcessId": 1, "CommandLine": "pytest test_deribit_options_surface_collector_v3.py"},
        {"ProcessId": 12, "ParentProcessId": 1, "CommandLine": "python unrelated.py loop"},
    ]

    assert logical_process_count(rows, "deribit_options_surface_collector_v3.py") == 1


def test_current_v3_audit_exposes_read_only_consumer_compatibility() -> None:
    path = ROOT / "docs" / "DERIBIT_OPTIONS_V3_DATA_LAYER_AUDIT_2026-07-16.json"
    report = json.loads(path.read_text(encoding="utf-8-sig"))

    assert report["forward_progress"]["span_days"] == report["v3"]["readiness_metrics"]["span_days"]
    assert report["forward_progress"]["minimum_span_days"] == 7.0
    assert report["forward_progress"]["minimum_healthy_slots"] == 1800
    assert report["decision"] == "deribit_options_v3_data_layer_integrity_blocked"
    assert report["runtime"]["all_components_passed"] is False
    assert report["failed_checks"] == [
        "collector_snapshot_healthy",
        "fresh_join_gate_preserved",
    ]
    assert report["checks"]["collector_lock_verified"] is True
    assert report["checks"]["readiness_lock_verified"] is True
    assert report["can_trade"] is False
