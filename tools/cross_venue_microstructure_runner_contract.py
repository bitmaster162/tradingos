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


def queue_experiment_map(queue: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = queue.get("hypotheses") if isinstance(queue.get("hypotheses"), list) else []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("experiment"):
            mapped[str(row["experiment"])] = row
    return mapped


def audit_contract(contract: dict[str, Any], queue: dict[str, Any], snapshot_gate: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot_gate = snapshot_gate if isinstance(snapshot_gate, dict) else {}
    experiments = contract.get("experiments") if isinstance(contract.get("experiments"), dict) else {}
    queue_experiments = queue_experiment_map(queue)
    execution = contract.get("execution_contract") if isinstance(contract.get("execution_contract"), dict) else {}
    runtime = contract.get("runtime_boundary") if isinstance(contract.get("runtime_boundary"), dict) else {}
    required_cli = contract.get("required_cli") if isinstance(contract.get("required_cli"), dict) else {}
    report_contract = contract.get("required_report_contract") if isinstance(contract.get("required_report_contract"), dict) else {}
    latest_snapshot_id = snapshot_gate.get("snapshot_id")
    seal_decision = snapshot_gate.get("decision")

    rows: list[dict[str, Any]] = []
    implemented = 0
    planned = 0
    row_failures = 0
    for experiment, spec in sorted(experiments.items()):
        spec = spec if isinstance(spec, dict) else {}
        queue_row = queue_experiments.get(experiment)
        script = str(spec.get("script") or "")
        implementation_status = str(spec.get("implementation_status") or "")
        script_path = resolve_path(script)
        checks = {
            "experiment_registered_in_queue": queue_row is not None,
            "hypothesis_id_matches_queue": queue_row is not None and spec.get("hypothesis_id") == queue_row.get("hypothesis_id"),
            "family_matches_queue": queue_row is not None and spec.get("family") == queue_row.get("family"),
            "script_path_declared": script.startswith("tools/") and script.endswith(".py"),
            "implementation_status_explicit": implementation_status in {"planned_not_implemented", "implemented_locked"},
            "planned_scripts_do_not_need_to_exist": implementation_status != "planned_not_implemented" or not script_path.exists(),
            "implemented_scripts_must_exist": implementation_status != "implemented_locked" or script_path.is_file(),
            "lock_path_supported": spec.get("supports_lock_path") is True,
        }
        implemented += int(implementation_status == "implemented_locked")
        planned += int(implementation_status == "planned_not_implemented")
        row_failures += int(not all(checks.values()))
        rows.append(
            {
                "experiment": experiment,
                "hypothesis_id": spec.get("hypothesis_id"),
                "family": spec.get("family"),
                "script": script,
                "script_exists": script_path.is_file(),
                "implementation_status": implementation_status,
                "checks": checks,
                "pass": all(checks.values()),
            }
        )

    required_false = (
        "credentials_allowed",
        "network_required",
        "orders_allowed",
        "signals_allowed",
        "observer_registration_allowed",
        "paper_or_live_promotion_allowed",
    )
    cli_required = required_cli.get("required_args") if isinstance(required_cli.get("required_args"), list) else []
    report_fields = report_contract.get("top_level_required_fields") if isinstance(report_contract.get("top_level_required_fields"), list) else []
    checks = {
        "contract_locked_skeleton": contract.get("status") == "locked_skeleton",
        "queue_locked": queue.get("status") == "locked_preregistration_queue",
        "all_queue_experiments_covered": set(queue_experiments) == set(experiments),
        "all_experiments_pass": row_failures == 0,
        "implementation_statuses_safe": implemented + planned == len(experiments),
        "run_blocked_until_implementation": execution.get("run_blocked_while_implementation_status_planned") is True,
        "exact_snapshot_id_required": execution.get("exact_snapshot_id_required") is True,
        "sealed_snapshot_required": execution.get("sealed_snapshot_required") is True,
        "snapshot_verification_required": execution.get("snapshot_verification_required") is True,
        "hypothesis_authorization_required": execution.get("hypothesis_authorization_required") is True,
        "multiple_testing_required": execution.get("multiple_testing_assessment_required") is True,
        "unsafe_execution_flags_false": all(execution.get(name) is False for name in required_false),
        "shell_false": required_cli.get("shell") is False,
        "arbitrary_extra_args_false": required_cli.get("arbitrary_extra_args") is False,
        "cli_has_cache_out_lock": {"--cache-dir", "--out-prefix", "--lock-path"}.issubset(set(cli_required)),
        "report_requires_can_trade_false": report_contract.get("must_keep_can_trade_false") is True and "can_trade" in report_fields,
        "report_requires_decision_and_search": {"decision", "search", "selected_on_train", "splits", "costs"}.issubset(set(report_fields)),
        "runtime_can_trade_false": runtime.get("can_trade") is False and runtime.get("orders_allowed") is False and runtime.get("signals_allowed") is False,
    }
    if seal_decision == "waiting_for_microstructure_readiness":
        execution_state = "blocked_waiting_for_first_sealed_snapshot"
    elif latest_snapshot_id and planned:
        execution_state = "sealed_snapshot_available_but_partial_implementation_review_required"
    elif latest_snapshot_id:
        execution_state = "sealed_snapshot_available_contract_ready_for_explicit_runner_wiring"
    else:
        execution_state = "blocked_no_verified_snapshot_state"

    decision = "microstructure_runner_contract_valid_locked" if all(checks.values()) else "microstructure_runner_contract_invalid"
    return {
        "generated_at": now_iso(),
        "contract_id": contract.get("contract_id"),
        "decision": decision,
        "execution_state": execution_state,
        "seal_decision": seal_decision,
        "latest_snapshot_id": latest_snapshot_id,
        "checks": checks,
        "experiments": rows,
        "summary": {
            "experiments": len(rows),
            "planned_not_implemented": planned,
            "implemented_locked": implemented,
            "scripts_existing": sum(row["script_exists"] for row in rows),
            "row_failures": row_failures,
        },
        "runtime_boundary": {
            "contract_only": True,
            "runner_execution_allowed_now": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Cross-Venue Microstructure Runner Contract",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"- Decision: `{report['decision']}`.",
        f"- Execution state: `{report['execution_state']}`.",
        f"- Seal decision: `{report.get('seal_decision')}`.",
        f"- Latest snapshot ID: `{report.get('latest_snapshot_id')}`.",
        f"- Experiments: `{summary['experiments']}`.",
        f"- Planned / implemented: `{summary['planned_not_implemented']}` / `{summary['implemented_locked']}`.",
        f"- Existing scripts: `{summary['scripts_existing']}`.",
        "- This is a runner contract audit only. It does not run research and does not imply strategy readiness.",
        "- `can_trade=false`.",
        "",
        "## Experiments",
        "",
    ]
    for row in report["experiments"]:
        lines.append(
            f"- `{row['experiment']}` -> `{row['hypothesis_id']}`, script `{row['script']}`, status `{row['implementation_status']}`."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the skeleton runner contract for first sealed microstructure hypotheses")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--contract", default="configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    parser.add_argument("--queue", default="configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT_AUDIT_2026-06-25")
    args = parser.parse_args()
    active_root = Path(args.root).resolve()
    contract = read_json(resolve_path(args.contract, active_root))
    queue = read_json(resolve_path(args.queue, active_root))
    snapshot_gate = read_json(resolve_path(args.snapshot_gate, active_root))
    report = audit_contract(contract, queue, snapshot_gate)
    prefix = resolve_path(args.out_prefix, active_root)
    write_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "experiments": report["summary"]["experiments"], "planned": report["summary"]["planned_not_implemented"], "implemented": report["summary"]["implemented_locked"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if report["decision"] in {"microstructure_runner_contract_valid_skeleton", "microstructure_runner_contract_valid_locked"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
