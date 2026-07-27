from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.liquidation_book_replenishment_independence_gate import build_report


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def policy() -> dict:
    return {
        "policy_id": "test",
        "status": "prospective_governance_gate_before_forward_outcomes",
        "created_at": "2026-01-01T00:00:00Z",
        "base_pass_decision": "liquidation_book_replenishment_passed_for_manual_review_only",
        "independence_requirements": {
            "independent_block_minutes": 30,
            "minimum_independent_blocks": 20,
            "minimum_unique_utc_days": 5,
            "maximum_single_day_event_share": 0.4,
            "generic_event_overlap_window_minutes": 2,
            "maximum_generic_event_overlap_rate": 0.5,
            "minimum_matched_outcomes_for_correlation": 10,
            "maximum_absolute_matched_outcome_correlation": 0.8,
        },
        "governance": {"retuning_allowed": False, "automatic_promotion_allowed": False},
        "runtime_boundary": {
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def candidate_rows(start: datetime) -> list[dict]:
    rows: list[dict] = []
    for index in range(20):
        event_time = start + timedelta(days=index // 4, minutes=(index % 4) * 60)
        event_id = f"candidate:{index}"
        for horizon in (5, 15):
            rows.append(
                {
                    "event_id": event_id,
                    "event_time": event_time.isoformat().replace("+00:00", "Z"),
                    "horizon_minutes": horizon,
                    "net_bps": float(index + horizon),
                    "can_trade": False,
                }
            )
    return rows


def run_gate(tmp_path: Path, base_decision: str, *, overlap: bool = False) -> dict:
    policy_path = tmp_path / "policy.json"
    base_path = tmp_path / "base.json"
    candidate_path = tmp_path / "candidate.jsonl"
    generic_events_path = tmp_path / "generic_events.jsonl"
    generic_outcomes_path = tmp_path / "generic_outcomes.jsonl"
    write_json(policy_path, policy())
    write_json(base_path, {"decision": base_decision, "can_trade": False})
    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    candidates = candidate_rows(start)
    write_jsonl(candidate_path, candidates)
    generic_events: list[dict] = []
    generic_outcomes: list[dict] = []
    if overlap:
        for index in range(20):
            event_time = start + timedelta(days=index // 4, minutes=(index % 4) * 60)
            event_id = f"generic:{index}"
            generic_events.append(
                {
                    "event_id": event_id,
                    "shock_minute_ms": int(event_time.timestamp() * 1000),
                    "can_trade": False,
                }
            )
            for horizon in (5, 15):
                generic_outcomes.append(
                    {
                        "event_id": event_id,
                        "horizon_minutes": horizon,
                        "net_base_bps": float(index + horizon),
                        "can_trade": False,
                    }
                )
    write_jsonl(generic_events_path, generic_events)
    write_jsonl(generic_outcomes_path, generic_outcomes)
    return build_report(
        policy_path=policy_path,
        base_report_path=base_path,
        candidate_ledger_path=candidate_path,
        generic_events_path=generic_events_path,
        generic_outcomes_path=generic_outcomes_path,
    )


def test_independence_gate_waits_for_base_statistical_pass(tmp_path: Path) -> None:
    report = run_gate(
        tmp_path,
        "liquidation_book_replenishment_collecting_resolved_outcomes",
    )
    assert report["decision"] == "liquidation_book_replenishment_independence_gate_collecting_base_sample"
    assert report["can_trade"] is False


def test_independence_gate_passes_structurally_distinct_sample_for_manual_review(tmp_path: Path) -> None:
    report = run_gate(
        tmp_path,
        "liquidation_book_replenishment_passed_for_manual_review_only",
    )
    assert report["evidence"]["independent_blocks"] == 20
    assert report["evidence"]["unique_utc_days"] == 5
    assert report["evidence"]["generic_event_overlap_rate"] == 0.0
    assert report["decision"] == "liquidation_book_replenishment_independence_gate_passed_manual_review_only"
    assert report["automatic_promotion_allowed"] is False


def test_independence_gate_marks_high_overlap_as_same_sleeve(tmp_path: Path) -> None:
    report = run_gate(
        tmp_path,
        "liquidation_book_replenishment_passed_for_manual_review_only",
        overlap=True,
    )
    assert report["evidence"]["generic_event_overlap_rate"] == 1.0
    assert report["decision"] == "liquidation_book_replenishment_independence_gate_same_sleeve_overlap"
    assert report["can_trade"] is False
