from __future__ import annotations

from tools.cross_venue_microstructure_validation_protocol_builder import build_protocol


def test_validation_protocol_waits_for_sealed_snapshot() -> None:
    protocol = build_protocol(
        {"decision": "blocked_waiting_for_sealed_snapshot", "can_trade": False},
        {"decision": "blocked_waiting_for_sealed_snapshot", "can_trade": False},
    )

    assert protocol["decision"] == "blocked_waiting_for_sealed_snapshot"
    assert protocol["candidate_count"] == 0
    assert protocol["runtime_boundary"]["opens_validation"] is False
    assert protocol["can_trade"] is False


def test_validation_protocol_blocks_without_candidate() -> None:
    protocol = build_protocol(
        {"decision": "blocked_no_candidate_to_review", "candidate_count": 0, "can_trade": False},
        {"decision": "reject_no_microstructure_candidate", "can_trade": False},
    )

    assert protocol["decision"] == "blocked_no_candidate_to_validate"
    assert protocol["global_validation_rules"]["validation_data_opened_by_this_builder"] is False


def test_validation_protocol_draft_locks_candidate_without_opening_validation() -> None:
    protocol = build_protocol(
        {
            "decision": "microstructure_candidate_review_pack_ready",
            "snapshot_id": "train-snapshot",
            "run_id": "run-id",
            "candidates": [
                {
                    "rank": 1,
                    "experiment": "exp",
                    "hypothesis_id": "HYP",
                    "family": "fam",
                    "strategy_id": "strategy",
                    "report_path": "REPORT.json",
                    "train": {"trades": 100, "stress_mean_net_bps": 2.5},
                }
            ],
            "can_trade": False,
        },
        {"decision": "microstructure_candidate_review_required_no_promotion", "can_trade": False},
    )

    assert protocol["decision"] == "microstructure_validation_protocol_draft_ready"
    assert protocol["candidate_count"] == 1
    candidate = protocol["protocols"][0]
    assert candidate["source_train_snapshot_id"] == "train-snapshot"
    assert candidate["validation_contract"]["validation_opened"] is False
    assert candidate["validation_contract"]["parameter_search_allowed"] is False
    assert candidate["validation_contract"]["reoptimization_allowed"] is False
    assert candidate["promotion_after_validation"]["automatic_live_execution_allowed"] is False
    assert candidate["validation_contract"]["can_trade"] is False
