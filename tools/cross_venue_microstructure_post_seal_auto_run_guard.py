#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEALED_DECISIONS = {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"}


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


def latest_path_from_contract(active_root: Path, contract: dict[str, Any]) -> Path:
    rel = str(contract.get("run_root_relative_to_active") or "_dl/research_runs_cross_venue_microstructure")
    return resolve_path(rel, root=active_root) / "LATEST.json"


def payload_safe(payload: dict[str, Any]) -> bool:
    runtime = payload.get("runtime_boundary") if isinstance(payload.get("runtime_boundary"), dict) else {}
    execution = payload.get("execution_contract") if isinstance(payload.get("execution_contract"), dict) else {}
    if payload.get("can_trade") is False:
        return True
    if runtime.get("can_trade") is False and runtime.get("orders_allowed") is False:
        return True
    if execution.get("orders_allowed") is False and execution.get("signals_allowed") is False:
        return True
    return False


def runner_command(active_root: Path, contract_path: Path, snapshot_gate_path: Path, runner_out_prefix: Path, timeout: int, force: bool) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "cross_venue_microstructure_research_runner.py"),
        "--active-root",
        str(active_root),
        "--contract",
        str(contract_path),
        "--snapshot-gate",
        str(snapshot_gate_path),
        "--out-prefix",
        str(runner_out_prefix),
        "--timeout-seconds",
        str(timeout),
    ]
    if force:
        command.append("--force")
    command.append("run-if-ready")
    return command


def build_report(
    *,
    active_root: Path,
    preseal_plan: dict[str, Any],
    snapshot_gate: dict[str, Any],
    contract: dict[str, Any],
    latest_runner: dict[str, Any],
    execute: bool,
    force: bool,
    contract_path: Path,
    snapshot_gate_path: Path,
    runner_out_prefix: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    gate_decision = snapshot_gate.get("decision")
    snapshot_id = snapshot_gate.get("snapshot_id")
    latest_snapshot_id = latest_runner.get("snapshot_id")
    latest_status = latest_runner.get("status")
    preseal_ready = preseal_plan.get("decision") == "preseal_launch_plan_ready_waiting_for_snapshot"
    safe_inputs = all(payload_safe(payload) for payload in (preseal_plan, snapshot_gate, contract) if payload)
    checks = {
        "preseal_plan_ready": preseal_ready,
        "contract_present": bool(contract),
        "snapshot_gate_present": bool(snapshot_gate),
        "safe_inputs": safe_inputs,
        "execute_uses_shell_false": True,
        "force_disabled_or_explicit": force is False or execute is True,
    }
    runner_result: dict[str, Any] | None = None
    command = runner_command(active_root, contract_path, snapshot_gate_path, runner_out_prefix, timeout_seconds, force)

    if not all(checks.values()):
        decision = "post_seal_auto_run_guard_blocked_inputs_failed"
        next_action = "fix_guard_inputs_before_snapshot_seal"
    elif gate_decision not in SEALED_DECISIONS:
        decision = "post_seal_auto_run_guard_armed_waiting_for_snapshot"
        next_action = "keep_collector_running_until_snapshot_gate_is_sealed"
    elif not isinstance(snapshot_id, str) or not snapshot_id:
        decision = "post_seal_auto_run_guard_blocked_missing_snapshot_id"
        next_action = "rerun_snapshot_gate_before_research_runner"
    elif latest_snapshot_id == snapshot_id and latest_status == "completed" and not force:
        decision = "post_seal_auto_run_guard_duplicate_blocked_already_completed"
        next_action = "review_candidate_governance_outputs_do_not_rerun"
    elif not execute:
        decision = "post_seal_auto_run_guard_would_execute_once"
        next_action = "rerun_guard_with_execute_or_wait_for_watchdog"
    else:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, timeout_seconds + 30),
            check=False,
        )
        runner_result = {
            "return_code": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        }
        refreshed_latest = read_json(latest_path_from_contract(active_root, contract))
        if completed.returncode == 0 and refreshed_latest.get("snapshot_id") == snapshot_id:
            decision = "post_seal_auto_run_guard_executed_locked_runner_once"
            next_action = "run_governance_and_manual_review_chain"
        else:
            decision = "post_seal_auto_run_guard_runner_failed"
            next_action = "inspect_runner_logs_keep_validation_closed"
        latest_runner = refreshed_latest or latest_runner

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "next_action": next_action,
        "execute_requested": execute,
        "force_requested": force,
        "snapshot": {
            "gate_decision": gate_decision,
            "snapshot_id": snapshot_id,
        },
        "latest_runner": {
            "snapshot_id": latest_runner.get("snapshot_id"),
            "status": latest_runner.get("status"),
            "run_id": latest_runner.get("run_id"),
            "decision": latest_runner.get("decision"),
        },
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "command": command,
        "runner_result": runner_result,
        "runtime_boundary": {
            "guard_only": not execute,
            "runs_research_only_when_execute_and_sealed": bool(execute),
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
        "# Cross-Venue Microstructure Post-Seal Auto-Run Guard",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Next action: `{report['next_action']}`.",
        f"- Snapshot gate: `{report['snapshot'].get('gate_decision')}`.",
        f"- Snapshot ID: `{report['snapshot'].get('snapshot_id')}`.",
        f"- Latest runner snapshot/status: `{report['latest_runner'].get('snapshot_id')}` / `{report['latest_runner'].get('status')}`.",
        f"- Execute requested: `{report.get('execute_requested')}`.",
        f"- Failed checks: `{', '.join(report.get('failed_checks') or []) or 'none'}`.",
        "- Guard keeps validation/OOS/signals/orders closed.",
        "- `can_trade=false`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard one-shot post-seal microstructure research runner execution")
    parser.add_argument("--active-root", default=str(ROOT))
    parser.add_argument("--preseal-plan", default="docs/CROSS_VENUE_MICROSTRUCTURE_PRESEAL_LAUNCH_PLAN_2026-06-29.json")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--contract", default="configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    parser.add_argument("--runner-out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_2026-06-25")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_POST_SEAL_AUTO_RUN_GUARD_2026-06-29")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    active_root = Path(args.active_root).resolve()
    preseal_plan_path = resolve_path(args.preseal_plan, root=active_root)
    snapshot_gate_path = resolve_path(args.snapshot_gate, root=active_root)
    contract_path = resolve_path(args.contract, root=active_root)
    runner_out_prefix = resolve_path(args.runner_out_prefix, root=active_root)
    out_prefix = resolve_path(args.out_prefix, root=active_root)
    contract = read_json(contract_path)
    report = build_report(
        active_root=active_root,
        preseal_plan=read_json(preseal_plan_path),
        snapshot_gate=read_json(snapshot_gate_path),
        contract=contract,
        latest_runner=read_json(latest_path_from_contract(active_root, contract)),
        execute=args.execute,
        force=args.force,
        contract_path=contract_path,
        snapshot_gate_path=snapshot_gate_path,
        runner_out_prefix=runner_out_prefix,
        timeout_seconds=args.timeout_seconds,
    )
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "snapshot_id": report["snapshot"]["snapshot_id"],
                "execute_requested": report["execute_requested"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"] != "post_seal_auto_run_guard_runner_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
