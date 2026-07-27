#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "configs" / "HYPOTHESIS_REGISTRY.json"
DEFAULT_RUNNER_CONTRACT = ROOT / "configs" / "RESEARCH_RUNNER_CONTRACT.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected_json_object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def hypothesis_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = registry.get("hypotheses")
    if not isinstance(rows, list):
        raise ValueError("hypotheses_list_missing")
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("hypothesis_id"):
            raise ValueError("invalid_hypothesis_record")
        key = str(row["hypothesis_id"])
        if key in mapped:
            raise ValueError(f"duplicate_hypothesis_id: {key}")
        mapped[key] = row
    return mapped


def corrected_threshold(configurations: int, alpha: float) -> dict[str, float]:
    if configurations <= 0:
        raise ValueError("configurations_must_be_positive")
    per_trial_alpha = alpha / configurations
    return {
        "familywise_alpha": alpha,
        "configurations": configurations,
        "per_trial_alpha": per_trial_alpha,
        "required_bootstrap_probability_min": 1.0 - per_trial_alpha,
    }


def audit_registry(registry: dict[str, Any], runner_contract: dict[str, Any]) -> dict[str, Any]:
    mapped = hypothesis_map(registry)
    experiments = runner_contract.get("experiments")
    if not isinstance(experiments, dict):
        raise ValueError("runner_experiments_missing")
    portfolio = registry.get("portfolio_budget") if isinstance(registry.get("portfolio_budget"), dict) else {}
    alpha = float(registry.get("multiple_testing_policy", {}).get("familywise_alpha", 0.05))
    checks: dict[str, bool] = {
        "registry_locked": registry.get("status") == "locked_governance",
        "runtime_can_trade_false": registry.get("runtime_boundary", {}).get("can_trade") is False,
        "hypothesis_count_within_budget": len(mapped) <= int(portfolio.get("max_hypotheses", 0)),
        "registered_count_matches": len(mapped) == int(portfolio.get("registered_hypotheses", -1)),
    }
    experiment_names: set[str] = set()
    used_configs = 0
    used_oos = 0
    rows: list[dict[str, Any]] = []
    for hypothesis_id, row in mapped.items():
        experiment = str(row.get("experiment"))
        budget = row.get("trial_budget") if isinstance(row.get("trial_budget"), dict) else {}
        splits = row.get("splits") if isinstance(row.get("splits"), dict) else {}
        maximum = int(budget.get("max_configurations", 0))
        used = int(budget.get("used_configurations", -1))
        used_configs += max(0, used)
        used_oos += int(splits.get("oos_opened") is True)
        experiment_unique = experiment not in experiment_names
        experiment_names.add(experiment)
        runner_spec = experiments.get(experiment) if isinstance(experiments.get(experiment), dict) else {}
        runner_required = row.get("status") != "registered_pending"
        entry_checks = {
            "experiment_unique": experiment_unique,
            "runner_experiment_present_or_pending": bool(runner_spec) or not runner_required,
            "runner_hypothesis_matches_or_pending": (
                runner_spec.get("hypothesis_id") == hypothesis_id if runner_spec else not runner_required
            ),
            "budget_positive": maximum > 0,
            "budget_not_exceeded": 0 <= used <= maximum,
            "oos_limit_not_exceeded": int(splits.get("oos_opened") is True) <= int(splits.get("max_oos_openings", 0)),
            "registration_type_explicit": row.get("registration_type") in {"prospective_preregistration", "retroactive_locked_record"},
        }
        rows.append({
            "hypothesis_id": hypothesis_id,
            "experiment": experiment,
            "status": row.get("status"),
            "budget": {"used": used, "max": maximum},
            "multiple_testing": corrected_threshold(maximum, alpha),
            "checks": entry_checks,
            "pass": all(entry_checks.values()),
        })
    checks.update({
        "portfolio_config_budget_matches": used_configs == int(portfolio.get("used_configurations", -1)),
        "portfolio_config_budget_not_exceeded": used_configs <= int(portfolio.get("max_total_configurations", 0)),
        "portfolio_oos_usage_matches": used_oos == int(portfolio.get("used_oos_openings", -1)),
        "portfolio_oos_budget_not_exceeded": used_oos <= int(portfolio.get("max_oos_openings", 0)),
        "runner_experiments_are_registered": set(experiments).issubset(experiment_names),
    })
    return {
        "generated_at": now_iso(),
        "registry_id": registry.get("registry_id"),
        "decision": "hypothesis_registry_valid" if all(checks.values()) and all(row["pass"] for row in rows) else "hypothesis_registry_invalid",
        "checks": checks,
        "hypotheses": rows,
        "summary": {
            "registered": len(rows),
            "rejected": sum(str(row["status"]).startswith("rejected") for row in rows),
            "pending": sum(row["status"] == "registered_pending" for row in rows),
            "configurations_used": used_configs,
            "configurations_max": portfolio.get("max_total_configurations"),
            "oos_openings_used": used_oos,
            "oos_openings_max": portfolio.get("max_oos_openings"),
        },
        "can_trade": False,
    }


def authorize_run(
    registry: dict[str, Any],
    *,
    hypothesis_id: str,
    experiment: str,
    purpose: str,
    snapshot_id: str,
) -> dict[str, Any]:
    row = hypothesis_map(registry).get(hypothesis_id)
    reasons: list[str] = []
    if row is None:
        reasons.append("hypothesis_not_registered")
    else:
        if row.get("experiment") != experiment:
            reasons.append("experiment_hypothesis_mismatch")
        budget = row.get("trial_budget") if isinstance(row.get("trial_budget"), dict) else {}
        if purpose == "discovery":
            if row.get("registration_type") != "prospective_preregistration":
                reasons.append("discovery_requires_prospective_preregistration")
            if row.get("status") != "registered_pending":
                reasons.append("hypothesis_not_pending")
            if int(budget.get("used_configurations", 0)) >= int(budget.get("max_configurations", 0)):
                reasons.append("configuration_budget_exhausted")
            if int(budget.get("used_verified_discovery_runs", 0)) >= int(budget.get("max_verified_discovery_runs", 0)):
                reasons.append("verified_discovery_run_budget_exhausted")
        elif purpose != "proof":
            reasons.append("purpose_not_allowed")
    if not snapshot_id or snapshot_id.lower() == "latest":
        reasons.append("exact_snapshot_id_required")
    return {
        "hypothesis_id": hypothesis_id,
        "experiment": experiment,
        "purpose": purpose,
        "snapshot_id": snapshot_id,
        "status": row.get("status") if row else None,
        "authorized": not reasons,
        "reasons": reasons,
        "can_trade": False,
    }


def nested_get(payload: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = payload
        for key in path:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if value is not None:
            return value
    return None


def assess_report(registry: dict[str, Any], hypothesis_id: str, report: dict[str, Any]) -> dict[str, Any]:
    row = hypothesis_map(registry).get(hypothesis_id)
    if row is None:
        raise ValueError(f"hypothesis_not_registered: {hypothesis_id}")
    budget = row["trial_budget"]
    maximum = int(budget["max_configurations"])
    tested_raw = nested_get(report, ("search", "tested"), ("search", "configs_tested"))
    tested = int(tested_raw) if isinstance(tested_raw, (int, float)) else None
    qualified_raw = nested_get(report, ("search", "train_qualified"))
    qualified = int(qualified_raw) if isinstance(qualified_raw, (int, float)) else None
    selected = report.get("selected_on_train") if isinstance(report.get("selected_on_train"), dict) else None
    probability = None
    if selected:
        probability = nested_get(
            selected,
            ("train", "bootstrap_probability_mean_gt_0"),
            ("train", "bootstrap_probability_expectancy_gt_0"),
            ("train", "bootstrap_prob_expectancy_gt_zero"),
            ("train", "bootstrap", "expectancy_r", "prob_gt_0"),
        )
    alpha = float(registry["multiple_testing_policy"]["familywise_alpha"])
    correction_trials = tested if tested and tested > 0 else maximum
    threshold = corrected_threshold(correction_trials, alpha)
    raw_p = 1.0 - float(probability) if isinstance(probability, (int, float)) else None
    adjusted_p = min(1.0, raw_p * correction_trials) if raw_p is not None else None
    budget_pass = tested is not None and tested <= maximum
    no_candidate = qualified == 0 or selected is None
    if no_candidate:
        multiplicity_status = "not_reached_no_train_candidate"
        multiplicity_pass = False
    elif raw_p is None:
        multiplicity_status = "failed_missing_candidate_probability"
        multiplicity_pass = False
    else:
        multiplicity_pass = adjusted_p is not None and adjusted_p <= alpha
        multiplicity_status = "passed_adjusted_significance" if multiplicity_pass else "failed_adjusted_significance"
    eligible = bool(
        budget_pass
        and multiplicity_pass
        and report.get("can_trade") is False
        and isinstance(report.get("decision"), str)
        and not str(report.get("decision")).startswith("reject")
    )
    return {
        "hypothesis_id": hypothesis_id,
        "experiment": row.get("experiment"),
        "tested_configurations": tested,
        "max_configurations": maximum,
        "configuration_budget_pass": budget_pass,
        "train_qualified": qualified,
        "candidate_bootstrap_probability": probability,
        "raw_p_value": raw_p,
        "bonferroni_adjusted_p_value": adjusted_p,
        "threshold": threshold,
        "multiplicity_status": multiplicity_status,
        "multiplicity_pass": multiplicity_pass,
        "eligible_for_next_stage": eligible,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Hypothesis Registry Audit",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"- Decision: `{report['decision']}`.",
        f"- Registered / rejected / pending: `{summary['registered']}` / `{summary['rejected']}` / `{summary['pending']}`.",
        f"- Configuration budget: `{summary['configurations_used']}` / `{summary['configurations_max']}`.",
        f"- OOS openings: `{summary['oos_openings_used']}` / `{summary['oos_openings_max']}`.",
        "- Existing studies are retroactive locked records, not claimed prospective preregistrations.",
        "- Bonferroni thresholds are applied over tested configurations before any next-stage eligibility.",
        "- `can_trade=false`.",
        "",
    ]
    for row in report["hypotheses"]:
        threshold = row["multiple_testing"]["required_bootstrap_probability_min"]
        lines.append(
            f"- `{row['hypothesis_id']}`: `{row['status']}`, budget `{row['budget']['used']}/{row['budget']['max']}`, required bootstrap probability `{threshold:.9f}`."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Audit and enforce the bounded research hypothesis registry")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--runner-contract", default=str(DEFAULT_RUNNER_CONTRACT))
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--out-prefix")
    auth_parser = subparsers.add_parser("authorize")
    auth_parser.add_argument("--hypothesis-id", required=True)
    auth_parser.add_argument("--experiment", required=True)
    auth_parser.add_argument("--purpose", choices=["proof", "discovery"], required=True)
    auth_parser.add_argument("--snapshot-id", required=True)
    assess_parser = subparsers.add_parser("assess")
    assess_parser.add_argument("--hypothesis-id", required=True)
    assess_parser.add_argument("--report", required=True)
    args = parser.parse_args()

    registry = read_json(Path(args.registry))
    if args.command == "audit":
        result = audit_registry(registry, read_json(Path(args.runner_contract)))
        if args.out_prefix:
            out = Path(args.out_prefix)
            if not out.is_absolute():
                out = ROOT / out
            write_json(out.with_suffix(".json"), result)
            out.with_suffix(".md").write_text(render_markdown(result), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["decision"] == "hypothesis_registry_valid" else 1
    if args.command == "authorize":
        result = authorize_run(
            registry,
            hypothesis_id=args.hypothesis_id,
            experiment=args.experiment,
            purpose=args.purpose,
            snapshot_id=args.snapshot_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["authorized"] else 2
    result = assess_report(registry, args.hypothesis_id, read_json(Path(args.report)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc), "can_trade": False}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2)
