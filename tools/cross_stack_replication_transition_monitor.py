#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_root_not_object"}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_resolved(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"1": 0, "2": 0, "4": 0}
    return {str(key): as_int(item) for key, item in value.items()}


def boundary_false(report: dict[str, Any]) -> bool:
    boundary = report.get("boundary") if isinstance(report.get("boundary"), dict) else {}
    return (
        boundary.get("can_trade") is False
        and boundary.get("paper_entries_allowed") is False
        and boundary.get("orders_allowed") is False
        and boundary.get("alerts_allowed") is False
    )


def current_state(report: dict[str, Any]) -> dict[str, Any]:
    resolved = normalize_resolved(report.get("resolved_per_horizon"))
    return {
        "report_generated_at": report.get("generated_at"),
        "decision": report.get("decision"),
        "verdict_rule_result": report.get("verdict_rule_result"),
        "forward_floor_utc": report.get("forward_floor_utc"),
        "post_floor_squeeze_event_bars": as_int(report.get("post_floor_squeeze_event_bars")),
        "resolved_per_horizon": resolved,
        "required_per_horizon": as_int(report.get("required_per_horizon")) or 15,
        "boundary_false": boundary_false(report),
    }


def build_report(replication_report_path: Path, state_path: Path, update_state: bool) -> dict[str, Any]:
    source = read_json(replication_report_path)
    previous = read_json(state_path) if state_path.exists() else {}
    current = current_state(source) if not source.get("_read_error") else {}
    previous_state = previous.get("last_state") if isinstance(previous.get("last_state"), dict) else {}

    current_resolved = normalize_resolved(current.get("resolved_per_horizon"))
    previous_resolved = normalize_resolved(previous_state.get("resolved_per_horizon"))
    resolved_delta = {
        horizon: current_resolved.get(horizon, 0) - previous_resolved.get(horizon, 0)
        for horizon in sorted(set(current_resolved) | set(previous_resolved))
    }
    required = as_int(current.get("required_per_horizon")) or 15
    threshold_ready = bool(current_resolved) and all(value >= required for value in current_resolved.values())
    nonzero_detected = any(value > 0 for value in current_resolved.values())
    resolved_increased = any(value > 0 for value in resolved_delta.values())
    event_count_delta = as_int(current.get("post_floor_squeeze_event_bars")) - as_int(
        previous_state.get("post_floor_squeeze_event_bars")
    )
    first_run = not bool(previous_state)

    if source.get("_read_error"):
        decision = "cross_stack_replication_transition_source_unreadable"
        next_action = "fix or sync Claude replication report before monitoring transitions"
    elif not current.get("boundary_false"):
        decision = "cross_stack_replication_transition_boundary_failed"
        next_action = "reject report until can_trade/paper/orders/alerts boundary is false"
    elif threshold_ready:
        decision = "cross_stack_replication_threshold_sample_ready_manual_review_required"
        next_action = "manual review raw and after-cost metrics; keep Codex forward sample separate"
    elif resolved_increased or (nonzero_detected and first_run):
        decision = "cross_stack_replication_nonzero_forward_events_detected_review_required"
        next_action = "review new external resolved events; do not promote or merge"
    else:
        decision = "cross_stack_replication_no_transition_waiting"
        next_action = "keep waiting for non-zero post-floor resolved events"

    report = {
        "generated_at": now_iso(),
        "tool": "cross_stack_replication_transition_monitor",
        "decision": decision,
        "replication_report_path": portable(replication_report_path),
        "state_path": portable(state_path),
        "first_run": first_run,
        "previous_state": previous_state,
        "current_state": current,
        "transition": {
            "event_count_delta": event_count_delta,
            "resolved_delta": resolved_delta,
            "nonzero_detected": nonzero_detected,
            "resolved_increased": resolved_increased,
            "threshold_ready": threshold_ready,
        },
        "policy": {
            "external_validity_check_only": True,
            "not_codex_forward_sample": True,
            "manual_review_required": True,
            "no_parameter_changes": True,
        },
        "next_action": next_action,
        "runtime_boundary": {
            "monitor_only": True,
            "alerts_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
        },
        "can_trade": False,
    }
    if update_state and not source.get("_read_error") and current.get("boundary_false"):
        write_json(state_path, {"updated_at": report["generated_at"], "last_state": current})
    return report


def render_markdown(report: dict[str, Any]) -> str:
    transition = report.get("transition") or {}
    current = report.get("current_state") or {}
    lines = [
        "# Cross-Stack Replication Transition Monitor",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        f"Can trade: `{report.get('can_trade')}`",
        "",
        "## Current",
        "",
        f"- Report: `{report.get('replication_report_path')}`.",
        f"- Forward floor: `{current.get('forward_floor_utc')}`.",
        f"- Post-floor squeeze event bars: `{current.get('post_floor_squeeze_event_bars')}`.",
        f"- Resolved per horizon: `{current.get('resolved_per_horizon')}`.",
        f"- Required per horizon: `{current.get('required_per_horizon')}`.",
        "",
        "## Transition",
        "",
        f"- First run: `{report.get('first_run')}`.",
        f"- Event count delta: `{transition.get('event_count_delta')}`.",
        f"- Resolved delta: `{transition.get('resolved_delta')}`.",
        f"- Non-zero detected: `{transition.get('nonzero_detected')}`.",
        f"- Threshold ready: `{transition.get('threshold_ready')}`.",
        "",
        "## Boundary",
        "",
        "- Monitor-only.",
        "- External validity check only.",
        "- No alerts, signals, paper entries or orders.",
        "",
        f"Next action: `{report.get('next_action')}`.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], out_prefix: str) -> None:
    prefix = resolve_path(out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect transitions in Claude external replication report")
    parser.add_argument("--replication-report", default="HANDOFF/CLAUDE_REPLICATION_REPORT_BYBIT_SQUEEZE.json")
    parser.add_argument("--state", default="_dl/state/cross_stack_replication_transition_state.json")
    parser.add_argument("--no-state-update", action="store_true")
    parser.add_argument("--out-prefix", default="docs/CROSS_STACK_REPLICATION_TRANSITION_MONITOR_2026-07-03")
    args = parser.parse_args()
    report = build_report(resolve_path(args.replication_report), resolve_path(args.state), not args.no_state_update)
    write_outputs(report, args.out_prefix)
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "event_count_delta": report["transition"]["event_count_delta"],
                "resolved_delta": report["transition"]["resolved_delta"],
                "nonzero_detected": report["transition"]["nonzero_detected"],
                "threshold_ready": report["transition"]["threshold_ready"],
                "can_trade": report["can_trade"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"] != "cross_stack_replication_transition_boundary_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
