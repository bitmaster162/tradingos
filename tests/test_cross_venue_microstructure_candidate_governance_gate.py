from __future__ import annotations

import json
from pathlib import Path

from tools.cross_venue_microstructure_candidate_governance_gate import audit_governance


def queue() -> dict:
    return {
        "status": "locked_preregistration_queue",
        "portfolio_budget": {"used_configurations": 0, "used_oos_openings": 0},
    }


def contract() -> dict:
    return {
        "status": "locked_skeleton",
        "experiments": {
            "a": {"implementation_status": "implemented_locked"},
            "b": {"implementation_status": "implemented_locked"},
            "c": {"implementation_status": "implemented_locked"},
        },
    }


def test_governance_blocks_until_snapshot_seals() -> None:
    report = {
        "decision": "blocked_waiting_for_sealed_snapshot",
        "runtime_boundary": {"signals_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }

    audited = audit_governance(report, queue(), contract())

    assert audited["decision"] == "blocked_waiting_for_sealed_snapshot"
    assert audited["promotion_boundary"]["live_execution_allowed"] is False
    assert audited["can_trade"] is False


def test_governance_rejects_no_candidate_batch() -> None:
    report = {
        "decision": "microstructure_research_batch_completed_no_candidate",
        "snapshot_id": "s",
        "run_id": "r",
        "experiment_results": [],
        "runtime_boundary": {"signals_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }

    audited = audit_governance(report, queue(), contract())

    assert audited["decision"] == "reject_no_microstructure_candidate"
    assert audited["next_action"] == "keep_collecting_or_preregister_new_hypotheses; do_not_promote"


def test_governance_routes_candidate_to_review_without_promotion(tmp_path: Path) -> None:
    experiment_report = tmp_path / "REPORT.json"
    experiment_report.write_text(
        json.dumps(
            {
                "decision": "candidate_requires_validation_review",
                "hypothesis_id": "HYP",
                "experiment": "exp",
                "family": "fam",
                "selected_on_train": {"strategy_id": "s1", "train": {"trades": 100}},
                "splits": {"validation_opened": False, "oos_opened": False},
                "runtime_boundary": {"signals_allowed": False, "orders_allowed": False, "can_trade": False},
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    runner_report = {
        "decision": "microstructure_candidates_require_validation_review",
        "snapshot_id": "s",
        "run_id": "r",
        "experiment_results": [{"report_path": str(experiment_report)}],
        "runtime_boundary": {"signals_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }

    audited = audit_governance(runner_report, queue(), contract())

    assert audited["decision"] == "microstructure_candidate_review_required_no_promotion"
    assert audited["candidate_count"] == 1
    assert audited["promotion_boundary"]["observer_registration_allowed"] is False
    assert audited["promotion_boundary"]["can_trade"] is False


def test_governance_fails_closed_on_trade_permission() -> None:
    runner_report = {
        "decision": "microstructure_research_batch_completed_no_candidate",
        "runtime_boundary": {"signals_allowed": False, "orders_allowed": True, "can_trade": False},
        "can_trade": False,
    }

    audited = audit_governance(runner_report, queue(), contract())

    assert audited["decision"] == "blocked_microstructure_governance_violation"
    assert "runner_orders_forbidden" in audited["failed_checks"]
