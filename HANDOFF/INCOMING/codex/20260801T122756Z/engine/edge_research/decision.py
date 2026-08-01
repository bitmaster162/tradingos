"""Exact fail-closed research terminal decisions."""

from __future__ import annotations

from typing import Any

from .common import ContractError, require_fields


TERMINALS = (
    "KEEP_FOR_FORWARD_PAPER",
    "KILL",
    "INSUFFICIENT_DATA",
    "INVALID_RESEARCH_RETURN",
)


def decide(evidence: dict[str, Any]) -> dict[str, Any]:
    require_fields(
        evidence,
        [
            "preregistration_valid",
            "source_provenance_valid",
            "source_hashes_match",
            "final_test_evaluated",
            "independent_sample_sufficient",
            "post_cost_expectancy",
            "bootstrap_lower_bound",
            "placebo_materially_weaker",
            "tail_risk_acceptable",
            "source_ablation_robust",
            "regime_ablation_robust",
            "leakage_detected",
        ],
        "evidence",
    )
    invalid_reasons = []
    if not evidence["preregistration_valid"]:
        invalid_reasons.append("invalid preregistration")
    if not evidence["source_provenance_valid"]:
        invalid_reasons.append("invalid source provenance")
    if not evidence["source_hashes_match"]:
        invalid_reasons.append("source mutation")
    if evidence["leakage_detected"]:
        invalid_reasons.append("leakage detected")
    if invalid_reasons:
        terminal = "INVALID_RESEARCH_RETURN"
        reason = "; ".join(invalid_reasons)
    elif not evidence["final_test_evaluated"] or not evidence["independent_sample_sufficient"]:
        terminal = "INSUFFICIENT_DATA"
        reason = "frozen final-test evidence or independent sample is insufficient"
    elif float(evidence["post_cost_expectancy"]) <= 0 or float(evidence["bootstrap_lower_bound"]) <= 0:
        terminal = "KILL"
        reason = "post-cost final-test expectancy or lower confidence bound is non-positive"
    elif not all(
        evidence[field]
        for field in (
            "placebo_materially_weaker",
            "tail_risk_acceptable",
            "source_ablation_robust",
            "regime_ablation_robust",
        )
    ):
        terminal = "KILL"
        reason = "one or more frozen robustness controls invalidate the claim"
    else:
        terminal = "KEEP_FOR_FORWARD_PAPER"
        reason = "all frozen final-test and robustness gates pass"
    return {
        "terminal": terminal,
        "reason": reason,
        "measurement_authorization_only": terminal == "KEEP_FOR_FORWARD_PAPER",
        "strategy_accepted": False,
        "can_trade": False,
        "capital_permission": "DENY",
        "deploy_permission": "DENY",
    }


def validate_terminal(value: str) -> str:
    if value not in TERMINALS:
        raise ContractError("INVALID_RESEARCH_TERMINAL", "terminal is not controller-approved")
    return value
