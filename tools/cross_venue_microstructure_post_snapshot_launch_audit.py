#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_PIPELINE_SCRIPTS = [
    "tools/cross_venue_microstructure_post_seal_auto_run_guard.py",
    "tools/cross_venue_microstructure_research_runner.py",
    "tools/cross_venue_microstructure_research_runner_telegram_notify.py",
    "tools/cross_venue_microstructure_candidate_governance_gate.py",
    "tools/cross_venue_microstructure_candidate_review_pack.py",
    "tools/cross_venue_microstructure_validation_protocol_builder.py",
    "tools/cross_venue_microstructure_validation_approval_audit.py",
    "tools/cross_venue_microstructure_validation_runner_skeleton.py",
]


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


def resolve_path(value: str, *, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def file_exists(root: Path, rel: str) -> bool:
    return (root / rel).is_file()


def all_false(payload: dict[str, Any], keys: list[str]) -> bool:
    return all(payload.get(key) is False for key in keys)


def sum_hypothesis_configurations(queue: dict[str, Any]) -> int:
    total = 0
    for item in queue.get("hypotheses", []):
        if not isinstance(item, dict):
            continue
        grid = item.get("grid") if isinstance(item.get("grid"), dict) else {}
        try:
            total += int(grid.get("total_configurations") or 0)
        except (TypeError, ValueError):
            return -1
    return total


def watchdog_contains(root: Path, tokens: list[str]) -> dict[str, bool]:
    path = root / "ops" / "autostart" / "Run-CrossVenueMicrostructureWatchdogLoop.ps1"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        text = ""
    return {token: token in text for token in tokens}


def contract_checks(root: Path, contract: dict[str, Any]) -> dict[str, bool]:
    execution = contract.get("execution_contract") if isinstance(contract.get("execution_contract"), dict) else {}
    runtime = contract.get("runtime_boundary") if isinstance(contract.get("runtime_boundary"), dict) else {}
    experiments = contract.get("experiments") if isinstance(contract.get("experiments"), dict) else {}
    safe_false = [
        "credentials_allowed",
        "network_required",
        "orders_allowed",
        "signals_allowed",
        "observer_registration_allowed",
        "paper_or_live_promotion_allowed",
    ]
    return {
        "contract_present": bool(contract),
        "contract_locked": contract.get("status") == "locked_skeleton",
        "execution_contract_safe": all_false(execution, safe_false),
        "snapshot_required": execution.get("exact_snapshot_id_required") is True and execution.get("sealed_snapshot_required") is True,
        "runtime_can_trade_false": runtime.get("can_trade") is False,
        "all_experiments_implemented": bool(experiments)
        and all(isinstance(item, dict) and item.get("implementation_status") == "implemented_locked" for item in experiments.values()),
        "all_experiment_scripts_exist": bool(experiments)
        and all(file_exists(root, str(item.get("script") or "")) for item in experiments.values() if isinstance(item, dict)),
        "all_experiments_support_lock_path": bool(experiments)
        and all(isinstance(item, dict) and item.get("supports_lock_path") is True for item in experiments.values()),
    }


def queue_checks(queue: dict[str, Any]) -> dict[str, bool]:
    budget = queue.get("portfolio_budget") if isinstance(queue.get("portfolio_budget"), dict) else {}
    max_total = budget.get("max_total_configurations")
    calculated_total = sum_hypothesis_configurations(queue)
    return {
        "queue_present": bool(queue),
        "queue_locked": queue.get("status") == "locked_preregistration_queue",
        "all_hypotheses_registered_pending": bool(queue.get("hypotheses"))
        and all(isinstance(item, dict) and item.get("status") == "registered_pending_first_seal" for item in queue.get("hypotheses", [])),
        "registered_equals_max_hypotheses": budget.get("registered_hypotheses") == budget.get("max_hypotheses"),
        "max_total_matches_hypothesis_sum": calculated_total >= 0 and max_total == calculated_total,
        "no_configurations_spent": budget.get("used_configurations") == 0,
        "no_oos_openings_spent": budget.get("used_oos_openings") == 0,
    }


def approval_template_checks(template: dict[str, Any]) -> dict[str, bool]:
    approval = template.get("approval") if isinstance(template.get("approval"), dict) else {}
    prohibitions = approval.get("prohibitions") if isinstance(approval.get("prohibitions"), dict) else {}
    return {
        "approval_template_present": bool(template),
        "template_not_granted": template.get("status") == "template_not_granted",
        "manual_approval_false": approval.get("manual_approval_granted") is False,
        "validation_opening_allowed_false": approval.get("validation_opening_allowed") is False,
        "approval_can_trade_false": approval.get("can_trade") is False,
        "all_execution_prohibitions_false": all_false(
            prohibitions,
            [
                "parameter_search_allowed",
                "reoptimization_allowed",
                "observer_registration_allowed",
                "paper_execution_allowed",
                "live_execution_allowed",
                "signals_allowed",
                "orders_allowed",
            ],
        ),
    }


def build_report(
    *,
    root: Path,
    contract: dict[str, Any],
    queue: dict[str, Any],
    approval_template: dict[str, Any],
    autopilot: dict[str, Any],
    snapshot_transition: dict[str, Any],
) -> dict[str, Any]:
    required_files = {rel: file_exists(root, rel) for rel in REQUIRED_PIPELINE_SCRIPTS}
    watchdog_tokens = [
        "cross_venue_microstructure_post_seal_auto_run_guard.py",
        "--execute",
        "cross_venue_microstructure_candidate_governance_gate.py",
        "cross_venue_microstructure_candidate_review_pack.py",
        "cross_venue_microstructure_validation_protocol_builder.py",
        "cross_venue_microstructure_validation_approval_audit.py",
        "cross_venue_microstructure_validation_runner_skeleton.py",
        "cross_venue_microstructure_research_runner_telegram_notify.py",
    ]
    watchdog = watchdog_contains(root, watchdog_tokens)
    autopilot_failed = autopilot.get("failed_checks")
    if not isinstance(autopilot_failed, list):
        autopilot_failed = []
    transition_state = snapshot_transition.get("transition_state")
    checks = {
        **{f"file:{rel}": exists for rel, exists in required_files.items()},
        **{f"contract:{name}": passed for name, passed in contract_checks(root, contract).items()},
        **{f"queue:{name}": passed for name, passed in queue_checks(queue).items()},
        **{f"approval_template:{name}": passed for name, passed in approval_template_checks(approval_template).items()},
        **{f"watchdog:{token}": present for token, present in watchdog.items()},
        "autopilot_present": bool(autopilot),
        "autopilot_failed_checks_empty": not autopilot_failed,
        "autopilot_can_trade_false": autopilot.get("can_trade") is False if autopilot else True,
        "transition_report_present": bool(snapshot_transition),
        "transition_can_trade_false": snapshot_transition.get("can_trade") is False if snapshot_transition else True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        decision = "post_snapshot_launch_needs_repair"
        next_action = "fix_failed_post_snapshot_launch_checks_before_snapshot_gate"
    elif transition_state == "sealed_snapshot_ready_for_train_research_batch":
        decision = "post_snapshot_launch_ready_for_locked_runner"
        next_action = "let_watchdog_run_post_seal_guard_execute_once"
    elif transition_state == "sealed_snapshot_research_batch_already_completed":
        decision = "post_snapshot_launch_research_batch_already_completed"
        next_action = "review_candidate_governance_outputs"
    else:
        decision = "post_snapshot_launch_ready_waiting_for_snapshot"
        next_action = "continue_collecting_until_snapshot_transition_ready"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "checks": checks,
        "failed_checks": failed,
        "pipeline_scripts": required_files,
        "watchdog_tokens": watchdog,
        "contract": {
            "experiments": len(contract.get("experiments", {}) if isinstance(contract.get("experiments"), dict) else {}),
            "status": contract.get("status"),
        },
        "queue": {
            "registered_hypotheses": queue.get("portfolio_budget", {}).get("registered_hypotheses")
            if isinstance(queue.get("portfolio_budget"), dict)
            else None,
            "max_total_configurations": queue.get("portfolio_budget", {}).get("max_total_configurations")
            if isinstance(queue.get("portfolio_budget"), dict)
            else None,
        },
        "snapshot": {
            "transition_state": transition_state,
            "remaining_hours": snapshot_transition.get("remaining_hours"),
            "snapshot_id": snapshot_transition.get("snapshot_id"),
        },
        "next_action": next_action,
        "runtime_boundary": {
            "audit_only": True,
            "runs_research_batch": False,
            "opens_validation": False,
            "opens_oos": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Microstructure Post-Snapshot Launch Audit",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Failed checks: `{', '.join(report.get('failed_checks') or []) or 'none'}`.",
        f"- Experiments: `{report['contract'].get('experiments')}`.",
        f"- Max configurations: `{report['queue'].get('max_total_configurations')}`.",
        f"- Snapshot transition: `{report['snapshot'].get('transition_state')}`.",
        f"- Remaining hours: `{report['snapshot'].get('remaining_hours')}`.",
        f"- Next action: `{report['next_action']}`.",
        "- Audit-only. It does not run research, open validation/OOS, emit signals, or place orders.",
        "- `can_trade=false`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit readiness of the post-snapshot microstructure launch chain")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--contract", default="configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    parser.add_argument("--queue", default="configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    parser.add_argument("--approval-template", default="configs/CROSS_VENUE_MICROSTRUCTURE_VALIDATION_APPROVAL_TEMPLATE.json")
    parser.add_argument("--autopilot", default="docs/CROSS_VENUE_MICROSTRUCTURE_AUTOPILOT_AUDIT_2026-06-29.json")
    parser.add_argument("--snapshot-transition", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_MONITOR_2026-06-25.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_POST_SNAPSHOT_LAUNCH_AUDIT_2026-06-29")
    args = parser.parse_args()

    root = resolve_path(args.root, root=ROOT)
    report = build_report(
        root=root,
        contract=read_json(resolve_path(args.contract, root=root)),
        queue=read_json(resolve_path(args.queue, root=root)),
        approval_template=read_json(resolve_path(args.approval_template, root=root)),
        autopilot=read_json(resolve_path(args.autopilot, root=root)),
        snapshot_transition=read_json(resolve_path(args.snapshot_transition, root=root)),
    )
    out_prefix = resolve_path(args.out_prefix, root=root)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "failed_checks": report["failed_checks"],
                "transition_state": report["snapshot"]["transition_state"],
                "next_action": report["next_action"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
