from __future__ import annotations

from tools.cross_venue_microstructure_validation_approval_audit import build_audit


def ready_protocol() -> dict:
    return {
        "decision": "microstructure_validation_protocol_draft_ready",
        "source_train_snapshot_id": "train-snapshot",
        "protocols": [
            {
                "candidate_rank": 1,
                "strategy_id": "candidate",
                "validation_contract": {"validation_opened": False, "can_trade": False},
            }
        ],
        "can_trade": False,
    }


def valid_approval(*, strategy_id: str = "candidate", train_snapshot_id: str = "train-snapshot", validation_snapshot_id: str = "validation-snapshot") -> dict:
    return {
        "approval": {
            "manual_approval_granted": True,
            "validation_opening_allowed": True,
            "approval_scope": "microstructure_validation_only",
            "candidate_rank": 1,
            "strategy_id": strategy_id,
            "source_train_snapshot_id": train_snapshot_id,
            "validation_snapshot_id": validation_snapshot_id,
            "checked": {
                "candidate_report_reviewed": True,
                "train_result_reproducible": True,
                "cost_stress_survives_reviewed": True,
                "fold_stability_reviewed": True,
                "drawdown_tail_risk_reviewed": True,
                "no_feature_leakage_reviewed": True,
                "multiple_testing_policy_reviewed": True,
                "validation_budget_accepted": True,
                "no_live_execution_understood": True,
            },
            "prohibitions": {
                "parameter_search_allowed": False,
                "reoptimization_allowed": False,
                "observer_registration_allowed": False,
                "paper_execution_allowed": False,
                "live_execution_allowed": False,
                "signals_allowed": False,
                "orders_allowed": False,
            },
            "can_trade": False,
        }
    }


def sealed_gate(snapshot_id: str) -> dict:
    return {"decision": "microstructure_snapshot_sealed", "snapshot_id": snapshot_id, "can_trade": False}


def test_approval_audit_waits_for_training_candidate_snapshot() -> None:
    report = build_audit(
        {"decision": "blocked_waiting_for_sealed_snapshot", "can_trade": False},
        {},
        {"decision": "waiting_for_microstructure_readiness", "snapshot_id": None, "can_trade": False},
    )

    assert report["decision"] == "blocked_waiting_for_training_candidate_snapshot"
    assert report["runtime_boundary"]["opens_validation"] is False
    assert report["can_trade"] is False


def test_approval_audit_blocks_missing_approval_after_protocol_ready() -> None:
    report = build_audit(ready_protocol(), {}, sealed_gate("validation-snapshot"))

    assert report["decision"] == "blocked_validation_approval_missing"
    assert report["checks"]["approval_file_present"] is False


def test_approval_audit_blocks_candidate_mismatch() -> None:
    report = build_audit(ready_protocol(), valid_approval(strategy_id="wrong"), sealed_gate("validation-snapshot"))

    assert report["decision"] == "blocked_approval_candidate_mismatch"
    assert report["checks"]["candidate_matches_protocol"] is False


def test_approval_audit_blocks_train_snapshot_reuse() -> None:
    report = build_audit(
        ready_protocol(),
        valid_approval(validation_snapshot_id="train-snapshot"),
        sealed_gate("train-snapshot"),
    )

    assert report["decision"] == "blocked_validation_snapshot_same_as_train"
    assert report["checks"]["validation_snapshot_differs_from_train"] is False


def test_approval_audit_accepts_structure_but_keeps_runtime_closed() -> None:
    report = build_audit(ready_protocol(), valid_approval(), sealed_gate("validation-snapshot"))

    assert report["decision"] == "validation_approval_structurally_valid_runner_still_skeleton"
    assert report["checks"]["all_human_checks_true"] is True
    assert report["checks"]["all_execution_prohibitions_false"] is True
    assert report["runtime_boundary"]["opens_validation"] is False
    assert report["runtime_boundary"]["executes_strategy_code"] is False
    assert report["can_trade"] is False
