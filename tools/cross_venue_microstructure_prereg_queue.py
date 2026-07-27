#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str, root: Path = ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def corrected_threshold(configurations: int, alpha: float) -> dict[str, Any]:
    if configurations <= 0:
        raise ValueError("configurations_must_be_positive")
    per_trial_alpha = alpha / configurations
    return {
        "familywise_alpha": alpha,
        "configurations": configurations,
        "per_trial_alpha": per_trial_alpha,
        "required_bootstrap_probability_min": 1.0 - per_trial_alpha,
    }


def list_product(values: Any) -> int:
    if not isinstance(values, dict):
        return 0
    total = 1
    saw_dimension = False
    for key, value in values.items():
        if key == "total_configurations":
            continue
        if isinstance(value, list):
            saw_dimension = True
            total *= len(value)
    return total if saw_dimension else 0


def audit_queue(queue: dict[str, Any], snapshot_gate: dict[str, Any] | None = None) -> dict[str, Any]:
    hypotheses = queue.get("hypotheses") if isinstance(queue.get("hypotheses"), list) else []
    budget = queue.get("portfolio_budget") if isinstance(queue.get("portfolio_budget"), dict) else {}
    boundary = queue.get("runtime_boundary") if isinstance(queue.get("runtime_boundary"), dict) else {}
    alpha = float(queue.get("multiple_testing_policy", {}).get("familywise_alpha", 0.05))
    snapshot_gate = snapshot_gate if isinstance(snapshot_gate, dict) else {}
    latest_snapshot_id = snapshot_gate.get("snapshot_id")
    seal_decision = snapshot_gate.get("decision")

    seen_ids: set[str] = set()
    seen_experiments: set[str] = set()
    rows: list[dict[str, Any]] = []
    used_configs = 0
    max_configs = 0
    registered_pending = 0
    rejected = 0
    row_failures = 0
    for item in hypotheses:
        if not isinstance(item, dict):
            row_failures += 1
            continue
        hypothesis_id = str(item.get("hypothesis_id") or "")
        experiment = str(item.get("experiment") or "")
        grid = item.get("grid") if isinstance(item.get("grid"), dict) else {}
        trial_budget = item.get("trial_budget") if isinstance(item.get("trial_budget"), dict) else {}
        declared_total = int(grid.get("total_configurations") or 0)
        computed_total = list_product(grid)
        budget_max = int(trial_budget.get("max_configurations") or 0)
        budget_used = int(trial_budget.get("used_configurations") or 0)
        used_configs += budget_used
        max_configs += budget_max
        registered_pending += int(item.get("status") == "registered_pending_first_seal")
        rejected += int(str(item.get("status") or "").startswith("rejected"))

        checks = {
            "id_present": bool(hypothesis_id),
            "id_unique": hypothesis_id not in seen_ids,
            "experiment_present": bool(experiment),
            "experiment_unique": experiment not in seen_experiments,
            "prospective_registration": item.get("registration_type") == "prospective_preregistration",
            "pending_first_seal": item.get("status") == "registered_pending_first_seal",
            "grid_total_matches_product": declared_total == computed_total,
            "budget_matches_grid": budget_max == declared_total,
            "budget_unused": budget_used == 0,
            "feature_contract_forbids_lookahead": bool(item.get("feature_contract", {}).get("uses_completed_minutes_only") is True)
            and any("future" in str(value).lower() for value in item.get("feature_contract", {}).get("forbidden_features", [])),
            "train_gate_present": isinstance(item.get("train_gate"), dict) and bool(item.get("train_gate")),
        }
        seen_ids.add(hypothesis_id)
        seen_experiments.add(experiment)
        row_failures += int(not all(checks.values()))
        rows.append(
            {
                "hypothesis_id": hypothesis_id,
                "experiment": experiment,
                "family": item.get("family"),
                "status": item.get("status"),
                "declared_configurations": declared_total,
                "computed_configurations": computed_total,
                "budget": {"used": budget_used, "max": budget_max},
                "multiple_testing": corrected_threshold(max(1, declared_total), alpha),
                "checks": checks,
                "pass": all(checks.values()),
            }
        )

    checks = {
        "queue_locked": queue.get("status") == "locked_preregistration_queue",
        "snapshot_exact_id_required": queue.get("scope", {}).get("exact_snapshot_id_required") is True,
        "sealed_snapshot_required": queue.get("scope", {}).get("sealed_snapshot_required") is True,
        "snapshot_not_yet_opened_or_already_sealed": seal_decision in {None, "waiting_for_microstructure_readiness", "snapshot_already_sealed_for_readiness_epoch", "microstructure_snapshot_sealed"},
        "runtime_can_trade_false": boundary.get("can_trade") is False and boundary.get("orders_allowed") is False and boundary.get("signals_allowed") is False,
        "hypothesis_count_matches_budget": len(hypotheses) == int(budget.get("registered_hypotheses", -1)),
        "hypothesis_count_within_budget": len(hypotheses) <= int(budget.get("max_hypotheses", 0)),
        "portfolio_used_configurations_zero": int(budget.get("used_configurations", -1)) == 0,
        "portfolio_used_oos_zero": int(budget.get("used_oos_openings", -1)) == 0,
        "portfolio_max_configurations_matches_rows": max_configs == int(budget.get("max_total_configurations", -1)),
        "all_hypotheses_pass": row_failures == 0,
    }
    decision = "microstructure_prereg_queue_valid" if all(checks.values()) else "microstructure_prereg_queue_invalid"
    execution_state = "waiting_for_first_sealed_snapshot" if not latest_snapshot_id else "sealed_snapshot_available_requires_explicit_runner_contract"
    return {
        "generated_at": now_iso(),
        "queue_id": queue.get("queue_id"),
        "decision": decision,
        "execution_state": execution_state,
        "seal_decision": seal_decision,
        "latest_snapshot_id": latest_snapshot_id,
        "checks": checks,
        "hypotheses": rows,
        "summary": {
            "registered": len(rows),
            "pending_first_seal": registered_pending,
            "rejected": rejected,
            "configurations_used": used_configs,
            "configurations_max": max_configs,
            "portfolio_configurations_max": budget.get("max_total_configurations"),
            "oos_openings_used": budget.get("used_oos_openings"),
            "oos_openings_max": budget.get("max_oos_openings"),
        },
        "runtime_boundary": {
            "research_only": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Cross-Venue Microstructure Preregistration Queue",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"- Decision: `{report['decision']}`.",
        f"- Execution state: `{report['execution_state']}`.",
        f"- Seal decision: `{report.get('seal_decision')}`.",
        f"- Latest snapshot ID: `{report.get('latest_snapshot_id')}`.",
        f"- Registered / pending / rejected: `{summary['registered']}` / `{summary['pending_first_seal']}` / `{summary['rejected']}`.",
        f"- Configuration budget used/max: `{summary['configurations_used']}` / `{summary['configurations_max']}`.",
        f"- OOS openings used/max: `{summary['oos_openings_used']}` / `{summary['oos_openings_max']}`.",
        "- This queue is preregistration only. It registers hypotheses for the first sealed SQLite microstructure dataset; it does not run tests, emit signals or permit orders.",
        "- `can_trade=false`.",
        "",
        "## Hypotheses",
        "",
    ]
    for row in report["hypotheses"]:
        threshold = row["multiple_testing"]["required_bootstrap_probability_min"]
        lines.append(
            f"- `{row['hypothesis_id']}`: `{row['family']}`, status `{row['status']}`, configs `{row['declared_configurations']}`, required bootstrap probability `{threshold:.9f}`."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the preregistered queue for the first sealed cross-venue microstructure dataset")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--queue", default="configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE_AUDIT_2026-06-25")
    args = parser.parse_args()
    active_root = Path(args.root).resolve()
    queue = read_json(resolve_path(args.queue, active_root))
    snapshot_gate = read_json(resolve_path(args.snapshot_gate, active_root))
    report = audit_queue(queue, snapshot_gate)
    prefix = resolve_path(args.out_prefix, active_root)
    write_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "registered": report["summary"]["registered"], "pending": report["summary"]["pending_first_seal"], "configurations_max": report["summary"]["configurations_max"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if report["decision"] == "microstructure_prereg_queue_valid" else 2


if __name__ == "__main__":
    raise SystemExit(main())
