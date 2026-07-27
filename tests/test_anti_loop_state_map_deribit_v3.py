from __future__ import annotations

import json
from pathlib import Path

from tools.anti_loop_state_map import build_report


def write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_deribit_v3_audit_supersedes_unreachable_v2_as_current_lane(tmp_path: Path) -> None:
    write(
        tmp_path / "DERIBIT_OPTIONS_RESEARCH_RUNTIME_AUDIT_2026-07-13.json",
        {
            "decision": "deribit_options_stack_forward_collecting_readiness",
            "runtime": {"all_components_passed": True},
            "forward_progress": {"readiness_gate_ready": False, "span_days": 5.0, "healthy_slots": 959, "scheduled_coverage": 0.67, "events_total": 0},
            "can_trade": False,
        },
    )
    write(
        tmp_path / "DERIBIT_OPTIONS_V3_DATA_LAYER_AUDIT_2026-07-16.json",
        {
            "decision": "deribit_options_v3_data_layer_forward_collecting",
            "predecessor_v2": {"rows_admitted_to_v3": False, "price_outcomes_read": False},
            "v3": {
                "forward_floor_utc": "2026-07-16T13:00:00Z",
                "collector_logical_processes": 1,
                "readiness_logical_processes": 1,
                "readiness_metrics": {"span_days": 0.0, "healthy_slots": 0, "scheduled_coverage": 0.0},
                "readiness_gate_ready": False,
            },
            "can_trade": False,
        },
    )

    report = build_report(tmp_path)

    row = next(item for item in report["built_running"] if item["name"] == "deribit_options_v3_data_layer")
    assert row["status"] == "deribit_options_v3_data_layer_forward_collecting"
    assert "predecessor_rows=false" in row["why"]
    assert "deribit_options_v3_waiting_locked_readiness_gate" in report["current_blockers"]
    assert "deribit_options_waiting_locked_readiness_gate" not in report["current_blockers"]
    assert any("No observer successor exists" in item for item in report["not_done"])
