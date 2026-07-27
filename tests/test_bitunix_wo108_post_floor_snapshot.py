from __future__ import annotations

import json
from pathlib import Path

from tools.bitunix_wo108_post_floor_snapshot import build_snapshot


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def build_fixture(tmp_path: Path) -> Path:
    inbound = tmp_path / "inbound.md"
    inbound.write_text(
        "Inspect the already-running frozen V2 loop. Current Bangkok time is after "
        "2026-07-14 19:00 Asia/Bangkok.",
        encoding="utf-8",
    )
    write_json(
        tmp_path / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3_2026-07-14.json",
        {
            "schema": "bitunix-wo105-causal-shadow-prereg-v3",
            "cohort_id": "V3_TEST",
            "forward_start_at": "2026-07-14T14:00:00Z",
            "parameter_cohort_sha256": "params",
            "bindings": {"evaluator": "missing.py", "evaluator_sha256": "missing"},
        },
    )
    write_json(
        tmp_path / "docs" / "BITUNIX_WO105_V3_STATUS_2026-07-14.json",
        {
            "decision": "bitunix_wo105_v3_collecting_causal_forward_sample",
            "forward_events": 0,
            "terminal_forward_events": 0,
            "minimum_forward_events": 30,
            "minimum_terminal_forward_events": 30,
            "terminal_forward_progress": "0/30",
        },
    )
    write_json(
        tmp_path / "docs" / "BITUNIX_WO105_V3_BLIND_REVIEW_GATE_2026-07-14.json",
        {
            "decision": "collecting",
            "interim_outcome_values_accessed": False,
            "interim_outcome_metrics_disclosed": False,
        },
    )
    write_json(
        tmp_path / "docs" / "BITUNIX_WO105_V3_FIRST_CYCLE_GATE_2026-07-14.json",
        {
            "decision": "bitunix_wo105_v3_first_cycle_operational_blocked",
            "checks": {"loop_transitioned_after_floor": False},
            "overdue": ["loop_transitioned_after_floor", "post_floor_rest_snapshot"],
        },
    )
    write_json(
        tmp_path / "_dl" / "bitunix_wo105_shadow_v3" / "PACKET_ASSEMBLY_STATUS.json",
        {
            "decision": "bitunix_wo105_v3_packet_no_current_causal_setup",
            "packet_written": False,
            "evaluation_run": False,
            "setup_status": "NO_SETUP",
        },
    )
    write_json(tmp_path / "configs" / "TRADING_OS_RUNTIME_COMPONENTS.json", {"components": []})
    return inbound


def test_stale_v2_request_routes_zero_event_v3_to_operational_rollover(tmp_path: Path) -> None:
    inbound = build_fixture(tmp_path)

    report = build_snapshot(tmp_path, inbound_path=inbound, generated_at="2026-07-14T15:00:00Z")

    assert report["decision"] == "bitunix_wo108_v3_zero_event_operational_rollover_required"
    assert report["inbound"]["request_matches_current_runtime"] is False
    assert report["forward"]["progress"] == "0/30"
    assert report["packet"]["present"] is False
    assert report["ledger"]["rows"] == 0
    assert report["rollover_eligible"] is True
    assert "inbound_request_targets_tombstoned_v2" in report["blockers"]
    assert "loop_transitioned_after_floor" in report["blockers"]
    assert report["mutation_audit"]["evaluator_run"] is False
    assert report["can_trade"] is False


def test_any_ledger_row_prevents_zero_event_rollover(tmp_path: Path) -> None:
    inbound = build_fixture(tmp_path)
    ledger = tmp_path / "_dl" / "bitunix_wo105_shadow_v3" / "EVENT_LEDGER.jsonl"
    ledger.write_text(json.dumps({"event_id": "e1", "state": "NO_FILL"}) + "\n", encoding="utf-8")

    report = build_snapshot(tmp_path, inbound_path=inbound, generated_at="2026-07-14T15:00:00Z")

    assert report["ledger"]["rows"] == 1
    assert report["rollover_eligible"] is False
    assert report["decision"] == "WAITING_POST_FLOOR_DATA"
    assert report["can_trade"] is False
