#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def resolve_path(value: str, root: Path = ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def queue_rows(queue: dict[str, Any]) -> list[dict[str, Any]]:
    rows = queue.get("hypotheses")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def experiment_specs(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    specs = contract.get("experiments")
    return {str(k): v for k, v in specs.items() if isinstance(v, dict)} if isinstance(specs, dict) else {}


def payload_keeps_trading_disabled(payload: dict[str, Any]) -> bool:
    runtime = payload.get("runtime_boundary") if isinstance(payload.get("runtime_boundary"), dict) else {}
    execution = payload.get("execution_contract") if isinstance(payload.get("execution_contract"), dict) else {}
    if payload.get("can_trade") is False:
        return True
    if runtime.get("can_trade") is False and runtime.get("orders_allowed") is False:
        return True
    if execution.get("orders_allowed") is False and execution.get("signals_allowed") is False:
        return True
    return False


def build_hypothesis_plan(queue: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    specs = experiment_specs(contract)
    plan: list[dict[str, Any]] = []
    for idx, row in enumerate(queue_rows(queue), start=1):
        experiment = str(row.get("experiment") or "")
        grid = row.get("grid") if isinstance(row.get("grid"), dict) else {}
        train_gate = row.get("train_gate") if isinstance(row.get("train_gate"), dict) else {}
        spec = specs.get(experiment, {})
        configurations = safe_int(grid.get("total_configurations"))
        min_trades = safe_int(train_gate.get("min_trades"))
        script = str(spec.get("script") or "")
        status = str(row.get("status") or "")
        implementation_status = str(spec.get("implementation_status") or "")
        ready_for_runner = (
            status == "registered_pending_first_seal"
            and implementation_status == "implemented_locked"
            and bool(script)
            and resolve_path(script).is_file()
        )
        plan.append(
            {
                "launch_order": idx,
                "hypothesis_id": row.get("hypothesis_id"),
                "experiment": experiment,
                "family": row.get("family"),
                "claim": row.get("claim"),
                "script": script,
                "implementation_status": implementation_status,
                "status": status,
                "ready_for_locked_runner_after_seal": ready_for_runner,
                "configuration_budget": configurations,
                "min_train_trades": min_trades,
                "train_gate": train_gate,
                "multiple_testing_note": {
                    "method": "bonferroni_over_declared_configurations",
                    "declared_configurations": configurations,
                    "must_pass_adjusted_threshold": True,
                },
                "forbidden_after_seal": [
                    "expanding_grid",
                    "changing_feature_contract",
                    "reoptimizing_after_results",
                    "opening_validation_without_manual_approval",
                    "observer_registration",
                    "paper_execution",
                    "live_execution",
                ],
            }
        )
    return plan


def build_report(
    *,
    queue: dict[str, Any],
    contract: dict[str, Any],
    prereg_audit: dict[str, Any],
    runner_contract_audit: dict[str, Any],
    snapshot_gate: dict[str, Any],
    readiness: dict[str, Any],
    autopilot: dict[str, Any],
    post_snapshot: dict[str, Any],
) -> dict[str, Any]:
    hypotheses = build_hypothesis_plan(queue, contract)
    prereg_valid = prereg_audit.get("decision") == "microstructure_prereg_queue_valid"
    contract_valid = runner_contract_audit.get("decision") in {
        "microstructure_runner_contract_valid_locked",
        "microstructure_runner_contract_valid_skeleton",
    }
    autopilot_clean = bool(autopilot) and not autopilot.get("failed_checks")
    post_snapshot_ready = post_snapshot.get("decision") == "post_snapshot_launch_ready_waiting_for_snapshot"
    gate_decision = snapshot_gate.get("decision")
    readiness_decision = readiness.get("decision")
    remaining_hours = safe_float(
        readiness.get("remaining_hours")
        or snapshot_gate.get("readiness_diagnostics", {}).get("remaining_hours")
        if isinstance(snapshot_gate.get("readiness_diagnostics"), dict)
        else None
    )
    all_hypotheses_ready = bool(hypotheses) and all(item["ready_for_locked_runner_after_seal"] for item in hypotheses)
    checks = {
        "prereg_queue_valid": prereg_valid,
        "runner_contract_valid": contract_valid,
        "all_hypotheses_implemented_locked": all_hypotheses_ready,
        "autopilot_clean": autopilot_clean,
        "post_snapshot_launch_ready": post_snapshot_ready,
        "snapshot_not_yet_opened": gate_decision in {None, "waiting_for_microstructure_readiness"},
        "can_trade_false_everywhere": all(
            payload_keeps_trading_disabled(payload)
            for payload in (queue, contract, prereg_audit, runner_contract_audit, snapshot_gate, readiness, autopilot, post_snapshot)
            if payload
        ),
    }
    if not checks["snapshot_not_yet_opened"]:
        decision = "preseal_plan_stale_snapshot_state_changed"
        next_action = "rerun_snapshot_gate_and_runner_status_before_any_research"
    elif all(checks.values()):
        decision = "preseal_launch_plan_ready_waiting_for_snapshot"
        next_action = "wait_for_snapshot_gate_then_run_locked_microstructure_runner_once"
    else:
        decision = "preseal_launch_plan_blocked"
        next_action = "fix_failed_preseal_checks_before_snapshot_gate"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "next_action": next_action,
        "snapshot": {
            "gate_decision": gate_decision,
            "readiness_decision": readiness_decision,
            "remaining_hours": remaining_hours,
            "snapshot_id": snapshot_gate.get("snapshot_id"),
        },
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "launch_command_after_seal": {
            "command": "python tools/cross_venue_microstructure_research_runner.py run-if-ready --active-root C:\\Users\\coins\\TradingOS\\Active --contract configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json --snapshot-gate docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json --out-prefix docs/CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_2026-06-25",
            "allowed_now": False,
            "requires_snapshot_gate_decision": [
                "microstructure_snapshot_sealed",
                "snapshot_already_sealed_for_readiness_epoch",
            ],
        },
        "hypotheses": hypotheses,
        "global_rules": {
            "run_research_before_seal": False,
            "mutate_queue_before_first_run": False,
            "open_validation_automatically": False,
            "open_oos_automatically": False,
            "register_observer_automatically": False,
            "promote_to_paper_or_live": False,
            "max_oos_openings": queue.get("portfolio_budget", {}).get("max_oos_openings")
            if isinstance(queue.get("portfolio_budget"), dict)
            else None,
        },
        "runtime_boundary": {
            "plan_only": True,
            "runs_research": False,
            "opens_validation": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Microstructure Pre-Seal Launch Plan",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Next action: `{report['next_action']}`.",
        f"- Snapshot gate: `{report['snapshot'].get('gate_decision')}`.",
        f"- Remaining hours: `{report['snapshot'].get('remaining_hours')}`.",
        f"- Failed checks: `{', '.join(report.get('failed_checks') or []) or 'none'}`.",
        "- This is a plan only. It does not run research, open validation/OOS, emit signals, or place orders.",
        "- `can_trade=false`.",
        "",
        "## Launch Order After Seal",
        "",
    ]
    for item in report.get("hypotheses", []):
        lines.extend(
            [
                f"### {item.get('launch_order')}. {item.get('hypothesis_id')}",
                "",
                f"- Experiment: `{item.get('experiment')}`.",
                f"- Family: `{item.get('family')}`.",
                f"- Script: `{item.get('script')}`.",
                f"- Configurations: `{item.get('configuration_budget')}`.",
                f"- Min train trades: `{item.get('min_train_trades')}`.",
                f"- Ready for runner after seal: `{item.get('ready_for_locked_runner_after_seal')}`.",
                "- Forbidden after seal: expand grid, change feature contract, reoptimize, open validation without manual approval.",
                "",
            ]
        )
    lines.extend(
        [
            "## Locked Command",
            "",
            "```powershell",
            report["launch_command_after_seal"]["command"],
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a pre-seal launch plan for locked microstructure research")
    parser.add_argument("--queue", default="configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    parser.add_argument("--contract", default="configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    parser.add_argument("--prereg-audit", default="docs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE_AUDIT_2026-06-25.json")
    parser.add_argument("--runner-contract-audit", default="docs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT_AUDIT_2026-06-25.json")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--readiness", default="docs/CROSS_VENUE_MICROSTRUCTURE_READINESS_PROGRESS_2026-06-29.json")
    parser.add_argument("--autopilot", default="docs/CROSS_VENUE_MICROSTRUCTURE_AUTOPILOT_AUDIT_2026-06-29.json")
    parser.add_argument("--post-snapshot", default="docs/CROSS_VENUE_MICROSTRUCTURE_POST_SNAPSHOT_LAUNCH_AUDIT_2026-06-29.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_PRESEAL_LAUNCH_PLAN_2026-06-29")
    args = parser.parse_args()

    report = build_report(
        queue=read_json(resolve_path(args.queue)),
        contract=read_json(resolve_path(args.contract)),
        prereg_audit=read_json(resolve_path(args.prereg_audit)),
        runner_contract_audit=read_json(resolve_path(args.runner_contract_audit)),
        snapshot_gate=read_json(resolve_path(args.snapshot_gate)),
        readiness=read_json(resolve_path(args.readiness)),
        autopilot=read_json(resolve_path(args.autopilot)),
        post_snapshot=read_json(resolve_path(args.post_snapshot)),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "hypotheses": len(report["hypotheses"]),
                "failed_checks": report["failed_checks"],
                "remaining_hours": report["snapshot"]["remaining_hours"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"] == "preseal_launch_plan_ready_waiting_for_snapshot" else 2


if __name__ == "__main__":
    raise SystemExit(main())
