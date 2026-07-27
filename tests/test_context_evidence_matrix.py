from __future__ import annotations

import json
from pathlib import Path

from tools.context_evidence_matrix import build_report


def test_context_evidence_matrix_blocks_rejected_spot_perp(tmp_path: Path) -> None:
    report_path = tmp_path / "SPOT_PERP_DIVERGENCE_HARDENING_TEST.json"
    report_path.write_text(
        json.dumps(
            {
                "passed_count": 0,
                "top_results": [
                    {
                        "strategy_id": "demo",
                        "summary": {"trades": 100, "winrate_pct": 40.0, "expectancy_r": -0.1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_report([str(report_path)])

    assert report["decision"] == "no_context_factor_ready_for_derivatives_event_integration"
    assert report["summary"]["ready_for_integration"] == 0
    assert report["rows"][0]["report_type"] == "spot_perp_divergence"
    assert report["can_trade"] is False


def test_context_evidence_matrix_detects_ready_context(tmp_path: Path) -> None:
    report_path = tmp_path / "LIQUIDATION_IMPULSE_REVERSAL_NESTED_HOLDOUT_TEST.json"
    report_path.write_text(
        json.dumps(
            {
                "decision": "oos_pass_observer_candidate_not_trade_permission",
                "oos": {"summary": {"trades": 12, "expectancy_r": 0.2}},
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )

    report = build_report([str(report_path)])

    assert report["decision"] == "context_factor_candidate_ready_for_precommitted_integration"
    assert report["summary"]["ready_for_integration"] == 1
    assert report["runtime_boundary"]["orders_allowed"] is False


def test_context_evidence_matrix_classifies_composite_reject(tmp_path: Path) -> None:
    report_path = tmp_path / "DERIVATIVES_CONTEXT_COMPOSITE_MINER_TEST.json"
    report_path.write_text(
        json.dumps(
            {
                "decision": "reject_validation_gate_failed",
                "summary": {"train_qualified": 5, "validation_qualified": 0},
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )

    report = build_report([str(report_path)])

    assert report["summary"]["ready_for_integration"] == 0
    assert report["rows"][0]["report_type"] == "derivatives_context_composite"
    assert report["rows"][0]["evidence_level"] == "nested_holdout_reject"
