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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_command(command: list[str], timeout_s: int) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def refresh_matrix(args: argparse.Namespace) -> dict[str, Any]:
    if not args.refresh_matrix:
        return {"skipped": True}
    command = [
        sys.executable,
        str(ROOT / "tools" / "real_edge_readiness_matrix.py"),
        "--out-prefix",
        args.matrix_out_prefix,
    ]
    return run_command(command, timeout_s=args.timeout_seconds)


def microstructure_action(matrix: dict[str, Any], state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    micro = matrix.get("microstructure") if isinstance(matrix.get("microstructure"), dict) else {}
    decision = str(micro.get("decision") or "")
    snapshot_id = str(micro.get("snapshot_id") or "")
    if decision != "microstructure_ready_for_locked_runner":
        return {
            "status": "waiting",
            "reason": decision or "microstructure_status_missing",
            "executed": False,
        }
    if not snapshot_id:
        return {"status": "blocked", "reason": "snapshot_id_missing", "executed": False}
    completed = state.get("completed_microstructure_snapshots")
    completed_list = completed if isinstance(completed, list) else []
    if snapshot_id in completed_list and not args.force:
        return {
            "status": "already_completed",
            "snapshot_id": snapshot_id,
            "executed": False,
        }
    command = [
        sys.executable,
        str(ROOT / "tools" / "cross_venue_microstructure_post_seal_auto_run_guard.py"),
        "--snapshot-gate",
        args.snapshot_gate,
        "--out-prefix",
        args.microstructure_guard_out_prefix,
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if args.execute_ready:
        command.append("--execute")
    if args.force:
        command.append("--force")
    result = run_command(command, timeout_s=args.timeout_seconds + 60)
    guard_report = read_json(resolve_path(args.microstructure_guard_out_prefix).with_suffix(".json"))
    executed = guard_report.get("decision") == "post_seal_auto_run_guard_executed_locked_runner_once"
    if executed:
        completed_list.append(snapshot_id)
        state["completed_microstructure_snapshots"] = sorted(set(completed_list))
    return {
        "status": "executed" if executed else "guard_ran",
        "snapshot_id": snapshot_id,
        "execute_ready": args.execute_ready,
        "executed": executed,
        "result": result,
        "guard_decision": guard_report.get("decision"),
    }


def liquidation_action(matrix: dict[str, Any], state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    liquidation = matrix.get("liquidation") if isinstance(matrix.get("liquidation"), dict) else {}
    decision = str(liquidation.get("decision") or "")
    total_events = int(liquidation.get("total_events") or 0)
    if decision != "liquidation_events_available_for_preregistered_study":
        return {
            "status": "waiting",
            "reason": decision or "liquidation_status_missing",
            "total_events": total_events,
            "executed": False,
        }
    if total_events < args.min_liquidation_events:
        return {
            "status": "waiting_min_events",
            "total_events": total_events,
            "min_liquidation_events": args.min_liquidation_events,
            "executed": False,
        }
    completed_counts = state.get("completed_liquidation_event_counts")
    completed_list = completed_counts if isinstance(completed_counts, list) else []
    if total_events in completed_list and not args.force:
        return {
            "status": "already_completed",
            "total_events": total_events,
            "executed": False,
        }
    command = [
        sys.executable,
        str(ROOT / "tools" / "force_order_liquidation_research_pipeline.py"),
        "--symbols",
        args.liquidation_symbols,
        "--out-prefix",
        args.liquidation_pipeline_out_prefix,
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if not args.execute_ready:
        return {
            "status": "would_execute",
            "total_events": total_events,
            "command": command,
            "executed": False,
        }
    result = run_command(command, timeout_s=args.timeout_seconds + 60)
    pipeline_report = read_json(resolve_path(args.liquidation_pipeline_out_prefix).with_suffix(".json"))
    ran_ok = result.get("exit_code") == 0 and bool(pipeline_report)
    if ran_ok:
        completed_list.append(total_events)
        state["completed_liquidation_event_counts"] = sorted(set(int(item) for item in completed_list))
    return {
        "status": "executed" if ran_ok else "failed",
        "total_events": total_events,
        "executed": ran_ok,
        "result": result,
        "pipeline_decision": pipeline_report.get("decision"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Real Edge Autopilot Guard",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Execute ready: `{report['execute_ready']}`",
        f"- Can trade: `false`",
        "",
        "## Matrix",
        "",
        f"- Matrix decision: `{report['matrix'].get('decision')}`",
        f"- Matrix path: `{report['matrix_path']}`",
        "",
        "## Actions",
        "",
        f"- Microstructure: `{report['actions']['microstructure'].get('status')}` / `{report['actions']['microstructure'].get('reason') or report['actions']['microstructure'].get('guard_decision')}`",
        f"- Liquidation: `{report['actions']['liquidation'].get('status')}` / `{report['actions']['liquidation'].get('reason') or report['actions']['liquidation'].get('pipeline_decision')}`",
        "",
        "## Boundary",
        "",
        "- Guard is fail-closed.",
        "- It can only run research-only scripts when `--execute-ready` is explicitly supplied.",
        "- It does not emit trading signals, open paper entries, or place orders.",
        "- `can_trade=false`.",
        "",
        "## Next Action",
        "",
        f"- {report['next_action']}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed autopilot guard for real-edge research paths")
    parser.add_argument("--matrix-path", default="docs/REAL_EDGE_READINESS_MATRIX_2026-07-01.json")
    parser.add_argument("--matrix-out-prefix", default="docs/REAL_EDGE_READINESS_MATRIX_2026-07-01")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_REAL_EDGE_2026-07-01.json")
    parser.add_argument("--microstructure-guard-out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_POST_SEAL_AUTO_RUN_GUARD_REAL_EDGE_2026-07-01")
    parser.add_argument("--liquidation-pipeline-out-prefix", default="docs/FORCE_ORDER_LIQUIDATION_RESEARCH_PIPELINE_REAL_EDGE_2026-07-01")
    parser.add_argument("--liquidation-symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--min-liquidation-events", type=int, default=1)
    parser.add_argument("--state-path", default="logs/real_edge/real_edge_autopilot_guard_state.json")
    parser.add_argument("--out-prefix", default="docs/REAL_EDGE_AUTOPILOT_GUARD_2026-07-01")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--refresh-matrix", action="store_true")
    parser.add_argument("--execute-ready", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    refresh = refresh_matrix(args)
    matrix_path = resolve_path(args.matrix_path)
    matrix = read_json(matrix_path)
    state_path = resolve_path(args.state_path)
    state = read_json(state_path)
    state.setdefault("created_at", now_iso())
    state["updated_at"] = now_iso()

    actions = {
        "microstructure": microstructure_action(matrix, state, args),
        "liquidation": liquidation_action(matrix, state, args),
    }
    if any(action.get("status") in {"failed", "blocked"} for action in actions.values()):
        decision = "real_edge_autopilot_guard_blocked"
        next_action = "inspect failed action; keep trading disabled"
        exit_code = 1
    elif any(action.get("executed") for action in actions.values()):
        decision = "real_edge_autopilot_guard_executed_research"
        next_action = "review generated research outputs; do not promote automatically"
        exit_code = 0
    elif any(action.get("status") == "would_execute" for action in actions.values()):
        decision = "real_edge_autopilot_guard_ready_requires_execute_flag"
        next_action = "rerun with --execute-ready only if research execution is intended"
        exit_code = 0
    else:
        decision = "real_edge_autopilot_guard_waiting"
        next_action = "keep collectors running and rerun guard after data changes"
        exit_code = 0

    write_json(state_path, state)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/real_edge_autopilot_guard.py",
        "decision": decision,
        "can_trade": False,
        "execute_ready": args.execute_ready,
        "force": args.force,
        "refresh_matrix": refresh,
        "matrix_path": portable(matrix_path),
        "matrix": {
            "decision": matrix.get("decision"),
            "microstructure": matrix.get("microstructure", {}).get("decision") if isinstance(matrix.get("microstructure"), dict) else None,
            "liquidation": matrix.get("liquidation", {}).get("decision") if isinstance(matrix.get("liquidation"), dict) else None,
        },
        "actions": actions,
        "state_path": portable(state_path),
        "next_action": next_action,
        "boundary": {
            "guard_only": not args.execute_ready,
            "research_only_when_execute_ready": True,
            "emits_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "microstructure": actions["microstructure"].get("status"),
                "liquidation": actions["liquidation"].get("status"),
                "execute_ready": args.execute_ready,
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
