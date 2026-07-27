#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def implemented_count(contract: dict[str, Any]) -> int:
    experiments = contract.get("experiments") if isinstance(contract.get("experiments"), dict) else {}
    return sum(
        1
        for item in experiments.values()
        if isinstance(item, dict) and item.get("implementation_status") == "implemented_locked"
    )


def experiment_reports(runner_report: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in runner_report.get("experiment_results", []):
        if not isinstance(item, dict):
            continue
        report_path = Path(str(item.get("report_path") or ""))
        report = read_json(report_path)
        output.append({"runner_result": item, "report": report})
    return output


def safety_checks(runner_report: dict[str, Any], reports: list[dict[str, Any]], queue: dict[str, Any], contract: dict[str, Any]) -> dict[str, bool]:
    runtime = runner_report.get("runtime_boundary") if isinstance(runner_report.get("runtime_boundary"), dict) else {}
    queue_budget = queue.get("portfolio_budget") if isinstance(queue.get("portfolio_budget"), dict) else {}
    checks = {
        "runner_report_present": bool(runner_report),
        "runner_can_trade_false": runner_report.get("can_trade") is False,
        "runner_orders_forbidden": runtime.get("orders_allowed") is False,
        "runner_signals_forbidden": runtime.get("signals_allowed") is False,
        "queue_locked": queue.get("status") == "locked_preregistration_queue",
        "contract_locked": contract.get("status") == "locked_skeleton",
        "all_contract_scripts_implemented": implemented_count(contract) == len(contract.get("experiments", {}) if isinstance(contract.get("experiments"), dict) else {}),
        "prereg_budget_not_mutated": queue_budget.get("used_configurations") == 0 and queue_budget.get("used_oos_openings") == 0,
    }
    if reports:
        checks.update(
            {
                "all_experiment_reports_present": all(bool(item["report"]) for item in reports),
                "all_experiment_can_trade_false": all(item["report"].get("can_trade") is False for item in reports),
                "all_validation_closed": all(
                    item["report"].get("splits", {}).get("validation_opened") is False
                    for item in reports
                    if isinstance(item["report"].get("splits"), dict)
                ),
                "all_oos_closed": all(
                    item["report"].get("splits", {}).get("oos_opened") is False
                    for item in reports
                    if isinstance(item["report"].get("splits"), dict)
                ),
            }
        )
    return checks


def candidate_rows(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in reports:
        report = item["report"]
        selected = report.get("selected_on_train")
        if isinstance(selected, dict) and str(report.get("decision") or "").startswith("candidate_requires_validation_review"):
            candidates.append(
                {
                    "experiment": report.get("experiment"),
                    "hypothesis_id": report.get("hypothesis_id"),
                    "family": report.get("family"),
                    "strategy_id": selected.get("strategy_id"),
                    "train": selected.get("train"),
                    "report_path": item["runner_result"].get("report_path"),
                }
            )
    return candidates


def audit_governance(runner_report: dict[str, Any], queue: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    reports = experiment_reports(runner_report)
    checks = safety_checks(runner_report, reports, queue, contract)
    candidates = candidate_rows(reports)
    runner_decision = str(runner_report.get("decision") or "")

    if not runner_report:
        decision = "blocked_missing_microstructure_runner_report"
        next_action = "wait_for_runner_report"
    elif not all(checks.values()):
        decision = "blocked_microstructure_governance_violation"
        next_action = "inspect_failed_governance_checks"
    elif runner_decision in {"blocked_waiting_for_sealed_snapshot", "blocked_missing_exact_snapshot_id"}:
        decision = "blocked_waiting_for_sealed_snapshot"
        next_action = "continue_collecting_until_snapshot_gate_seals"
    elif runner_decision == "microstructure_snapshot_verification_failed":
        decision = "blocked_snapshot_verification_failed"
        next_action = "do_not_run_research_until_snapshot_integrity_is_fixed"
    elif runner_decision == "microstructure_research_batch_failed":
        decision = "blocked_microstructure_research_batch_failed"
        next_action = "inspect_failed_experiment_run_results"
    elif candidates:
        decision = "microstructure_candidate_review_required_no_promotion"
        next_action = "manual_review_then_register_validation_protocol; no_observer_no_paper_no_live"
    elif runner_decision == "microstructure_research_batch_completed_no_candidate":
        decision = "reject_no_microstructure_candidate"
        next_action = "keep_collecting_or_preregister_new_hypotheses; do_not_promote"
    else:
        decision = "blocked_unknown_microstructure_runner_decision"
        next_action = "inspect_runner_decision_before_any_next_stage"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "runner_decision": runner_report.get("decision"),
        "snapshot_id": runner_report.get("snapshot_id"),
        "run_id": runner_report.get("run_id"),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "candidate_count": len(candidates),
        "candidates": candidates,
        "next_action": next_action,
        "promotion_boundary": {
            "opens_validation": False,
            "opens_oos": False,
            "observer_registration_allowed": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Microstructure Candidate Governance Gate",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Runner decision: `{report.get('runner_decision')}`.",
        f"- Snapshot: `{report.get('snapshot_id')}`.",
        f"- Candidates: `{report.get('candidate_count')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        "- This gate never opens validation/OOS/observer/paper/live by itself.",
        "- `can_trade=false`.",
        "",
    ]
    for candidate in report.get("candidates", []):
        lines.append(f"- `{candidate.get('experiment')}` -> `{candidate.get('strategy_id')}`.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Governance gate for microstructure research batch outputs")
    parser.add_argument("--runner-report", default="docs/CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_2026-06-25.json")
    parser.add_argument("--queue", default="configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    parser.add_argument("--contract", default="configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_CANDIDATE_GOVERNANCE_2026-06-25")
    args = parser.parse_args()

    report = audit_governance(
        read_json(resolve_path(args.runner_report)),
        read_json(resolve_path(args.queue)),
        read_json(resolve_path(args.contract)),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "candidate_count": report["candidate_count"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if report["decision"] not in {"blocked_microstructure_governance_violation"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
