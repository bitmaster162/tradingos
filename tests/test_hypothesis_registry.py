from __future__ import annotations

import json
from pathlib import Path

from tools.hypothesis_registry import (
    assess_report,
    audit_registry,
    authorize_run,
    corrected_threshold,
)


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_bonferroni_threshold_uses_all_configurations() -> None:
    threshold = corrected_threshold(324, 0.05)
    assert threshold["per_trial_alpha"] == 0.05 / 324
    assert threshold["required_bootstrap_probability_min"] > 0.9998


def test_production_registry_audit_passes() -> None:
    result = audit_registry(
        load("configs/HYPOTHESIS_REGISTRY.json"),
        load("configs/RESEARCH_RUNNER_CONTRACT.json"),
    )
    assert result["decision"] == "hypothesis_registry_valid"
    assert result["summary"]["configurations_used"] == 918
    assert result["summary"]["rejected"] == 5


def test_rejected_hypothesis_allows_proof_but_not_discovery() -> None:
    registry = load("configs/HYPOTHESIS_REGISTRY.json")
    proof = authorize_run(
        registry,
        hypothesis_id="HYP-BASIS-SHOCK-001",
        experiment="basis_shock_reversion",
        purpose="proof",
        snapshot_id="sealed-id",
    )
    discovery = authorize_run(
        registry,
        hypothesis_id="HYP-BASIS-SHOCK-001",
        experiment="basis_shock_reversion",
        purpose="discovery",
        snapshot_id="sealed-id",
    )
    assert proof["authorized"] is True
    assert discovery["authorized"] is False
    assert "discovery_requires_prospective_preregistration" in discovery["reasons"]


def test_no_candidate_never_passes_multiplicity_gate() -> None:
    registry = load("configs/HYPOTHESIS_REGISTRY.json")
    result = assess_report(
        registry,
        "HYP-BASIS-SHOCK-001",
        {
            "search": {"tested": 324, "train_qualified": 0},
            "selected_on_train": None,
            "decision": "reject_no_train_candidate",
            "can_trade": False,
        },
    )
    assert result["multiplicity_status"] == "not_reached_no_train_candidate"
    assert result["eligible_for_next_stage"] is False


def test_candidate_must_pass_adjusted_probability() -> None:
    registry = load("configs/HYPOTHESIS_REGISTRY.json")
    report = {
        "search": {"tested": 324, "train_qualified": 1},
        "selected_on_train": {"train": {"bootstrap_probability_mean_gt_0": 0.99}},
        "decision": "candidate_requires_review",
        "can_trade": False,
    }
    result = assess_report(registry, "HYP-BASIS-SHOCK-001", report)
    assert result["bonferroni_adjusted_p_value"] == 1.0
    assert result["multiplicity_pass"] is False
    assert result["eligible_for_next_stage"] is False
