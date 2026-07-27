from __future__ import annotations

from tools.cross_venue_microstructure_candidate_review_pack import build_pack


def test_review_pack_waits_for_sealed_snapshot() -> None:
    pack = build_pack(
        {"decision": "blocked_waiting_for_sealed_snapshot", "candidate_count": 0, "can_trade": False},
        {"decision": "blocked_waiting_for_sealed_snapshot", "can_trade": False},
    )

    assert pack["decision"] == "blocked_waiting_for_sealed_snapshot"
    assert pack["candidate_count"] == 0
    assert pack["can_trade"] is False


def test_review_pack_blocks_when_no_candidate_exists() -> None:
    pack = build_pack(
        {"decision": "reject_no_microstructure_candidate", "candidate_count": 0, "can_trade": False},
        {"decision": "microstructure_research_batch_completed_no_candidate", "can_trade": False},
    )

    assert pack["decision"] == "blocked_no_candidate_to_review"
    assert pack["review_rules"]["automatic_validation_opening_allowed"] is False


def test_review_pack_builds_manual_checklist_for_candidate() -> None:
    governance = {
        "decision": "microstructure_candidate_review_required_no_promotion",
        "snapshot_id": "snap",
        "run_id": "run",
        "candidates": [
            {
                "experiment": "exp",
                "hypothesis_id": "HYP",
                "family": "fam",
                "strategy_id": "strategy",
                "report_path": "REPORT.json",
                "train": {
                    "stress_mean_net_bps": 2.1,
                    "positive_folds": 4,
                    "max_drawdown_bps": -100,
                },
            }
        ],
        "can_trade": False,
    }

    pack = build_pack(governance, {"decision": "microstructure_candidates_require_validation_review"})

    assert pack["decision"] == "microstructure_candidate_review_pack_ready"
    assert pack["candidate_count"] == 1
    candidate = pack["candidates"][0]
    assert candidate["strategy_id"] == "strategy"
    assert candidate["promotion_boundary"]["paper_execution_allowed"] is False
    checks = {item["check"] for item in candidate["review_checklist"]}
    assert "validation_protocol_required" in checks
    assert "no_feature_leakage" in checks
