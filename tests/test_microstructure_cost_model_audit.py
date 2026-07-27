from __future__ import annotations

import json
import math
from pathlib import Path

from tools.microstructure_cost_model_audit import audit_script, build_report


ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_all_locked_scripts_share_cost_math_and_queue_gates() -> None:
    policy = load("configs/CROSS_VENUE_MICROSTRUCTURE_COST_AUDIT_POLICY.json")
    queue = load("configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    runner = load("configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    hypotheses = {item["experiment"]: item for item in queue["hypotheses"]}
    costs = policy["canonical_research_cost_model"]

    rows = []
    for experiment in policy["scope"]["experiments"]:
        script = ROOT / runner["experiments"][experiment]["script"]
        rows.append(audit_script(experiment, script, hypotheses[experiment], costs))

    assert len(rows) == 4
    assert all(row["pass"] for row in rows)
    assert all(row["control_trade"]["actual_net_bps"] == 20.0 for row in rows)
    assert all(row["control_trade"]["actual_stress_net_bps"] == 10.0 for row in rows)
    assert all(math.isclose(row["control_trade"]["next_minute_long_bps"], 100.0) for row in rows)
    assert all(row["train_gate_probe"]["pass"] for row in rows)


def test_policy_keeps_execution_promotion_blocked() -> None:
    policy = load("configs/CROSS_VENUE_MICROSTRUCTURE_COST_AUDIT_POLICY.json")

    assert policy["governance"]["changes_preregistration"] is False
    assert policy["governance"]["candidate_specific_execution_overlay_required_before_paper_review"] is True
    assert policy["runtime_boundary"]["orders_allowed"] is False
    assert policy["runtime_boundary"]["can_trade"] is False


def test_portable_fixture_proves_cost_contract_without_runtime_drill_path() -> None:
    report = build_report(
        ROOT / "configs/CROSS_VENUE_MICROSTRUCTURE_COST_AUDIT_POLICY.json",
        ROOT / "configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json",
        ROOT / "configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json",
        None,
        ROOT / "tests/fixtures/microstructure_cost_model_synthetic_reports.json",
    )

    assert report["decision"] == "cost_model_consistent_research_only_execution_overlay_required"
    assert report["summary"]["synthetic_evidence_mode"] == "portable_synthetic_fixture"
    assert report["summary"]["synthetic_reports_passed"] == 4
    assert report["failed_checks"] == []
    assert report["can_trade"] is False
