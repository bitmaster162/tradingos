from __future__ import annotations

from tools.cross_venue_microstructure_validation_runner_skeleton import build_runner_status


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


def approval() -> dict:
    return {
        "approval": {
            "manual_approval_granted": True,
            "validation_opening_allowed": True,
            "can_trade": False,
        }
    }


def sealed_gate(snapshot_id: str) -> dict:
    return {
        "decision": "microstructure_snapshot_sealed",
        "snapshot_id": snapshot_id,
        "can_trade": False,
    }


def test_validation_runner_skeleton_waits_for_training_snapshot() -> None:
    report = build_runner_status(
        {"decision": "blocked_waiting_for_sealed_snapshot", "can_trade": False},
        {"decision": "waiting_for_microstructure_readiness", "snapshot_id": None, "can_trade": False},
        {},
    )

    assert report["decision"] == "blocked_waiting_for_training_candidate_snapshot"
    assert report["runtime_boundary"]["opens_validation"] is False
    assert report["can_trade"] is False


def test_validation_runner_skeleton_blocks_without_manual_approval() -> None:
    report = build_runner_status(ready_protocol(), sealed_gate("validation-snapshot"), {})

    assert report["decision"] == "blocked_manual_approval_missing"
    assert report["checks"]["manual_approval_file_present"] is False
    assert report["runtime_boundary"]["orders_allowed"] is False


def test_validation_runner_skeleton_forbids_train_snapshot_reuse() -> None:
    report = build_runner_status(ready_protocol(), sealed_gate("train-snapshot"), approval())

    assert report["decision"] == "blocked_validation_snapshot_same_as_train"
    assert report["checks"]["validation_snapshot_differs_from_train"] is False
    assert report["runtime_boundary"]["executes_strategy_code"] is False


def test_validation_runner_skeleton_does_not_execute_even_when_gates_pass() -> None:
    report = build_runner_status(ready_protocol(), sealed_gate("new-validation-snapshot"), approval())

    assert report["decision"] == "blocked_validation_runner_skeleton_no_execution"
    assert report["checks"]["validation_execution_implemented"] is False
    assert report["runtime_boundary"]["opens_validation"] is False
    assert report["runtime_boundary"]["signals_allowed"] is False
    assert report["can_trade"] is False
