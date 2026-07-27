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


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_minutes(value: Any, now: datetime | None = None) -> float | None:
    parsed = parse_utc(value)
    if parsed is None:
        return None
    now = now or datetime.now(timezone.utc)
    return round(max(0.0, (now - parsed).total_seconds() / 60.0), 6)


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def status_ok(status: dict[str, Any], *, max_age_minutes: float, now: datetime | None = None) -> tuple[bool, float | None]:
    age = age_minutes(status.get("ts"), now=now)
    ok = (
        bool(status)
        and status.get("status") in {"sleeping", "running_once", "running_health_check", "startup_grace"}
        and age is not None
        and age <= max_age_minutes
        and status.get("can_trade") is False
        and status.get("sends_orders") is False
    )
    return ok, age


def required_exits_state(status: dict[str, Any], required: list[str]) -> tuple[str, bool, dict[str, Any]]:
    extra = status.get("extra") if isinstance(status.get("extra"), dict) else {}
    exits = {name: extra.get(name) for name in required}
    values = list(exits.values())
    all_zero = all(value == 0 for value in values)
    has_failure = any(value not in (0, None) for value in values)
    running = status.get("status") in {"running_once", "running_health_check"}
    in_progress = running and not has_failure and any(value is None for value in values)
    if all_zero:
        return "zero", True, exits
    if in_progress:
        return "in_progress", True, exits
    return "failed", False, exits


def runner_wiring(script_path: Path) -> dict[str, Any]:
    try:
        text = script_path.read_text(encoding="utf-8-sig")
    except OSError:
        return {"mode": "missing", "has_run_if_ready": False, "exactly_once_guard": False}
    direct = "cross_venue_microstructure_research_runner.py" in text and "run-if-ready" in text
    guard_referenced = "cross_venue_microstructure_post_seal_auto_run_guard.py" in text and "--execute" in text
    guard_path = ROOT / "tools" / "cross_venue_microstructure_post_seal_auto_run_guard.py"
    try:
        guard_text = guard_path.read_text(encoding="utf-8-sig")
    except OSError:
        guard_text = ""
    guard_contract = (
        "cross_venue_microstructure_research_runner.py" in guard_text
        and "run-if-ready" in guard_text
        and "post_seal_auto_run_guard_duplicate_blocked_already_completed" in guard_text
    )
    integrity_guard_referenced = "active_source_integrity_guard.py" in text
    integrity_fail_closed = (
        "$SourceIntegrityExit -eq 0" in text
        and "source_integrity_blocked_research_runner" in text
    )
    if guard_referenced and guard_contract:
        mode = "post_seal_exactly_once_guard"
    elif direct:
        mode = "direct_run_if_ready"
    elif guard_referenced:
        mode = "broken_post_seal_guard_contract"
    else:
        mode = "missing"
    return {
        "mode": mode,
        "has_run_if_ready": direct or (guard_referenced and guard_contract),
        "exactly_once_guard": guard_referenced and guard_contract,
        "guard_referenced": guard_referenced,
        "guard_contract_valid": guard_contract,
        "integrity_guard_referenced": integrity_guard_referenced,
        "integrity_fail_closed": integrity_fail_closed,
    }


def diagnostics(gate: dict[str, Any]) -> dict[str, Any]:
    payload = gate.get("readiness_diagnostics")
    return payload if isinstance(payload, dict) else {}


def build_report(
    *,
    collector_status: dict[str, Any],
    watchdog_status: dict[str, Any],
    snapshot_gate: dict[str, Any],
    snapshot_transition: dict[str, Any],
    research_runner: dict[str, Any],
    watchdog_script_path: Path,
    max_collector_age_minutes: float = 3.0,
    max_watchdog_age_minutes: float = 3.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    collector_ok, collector_age = status_ok(collector_status, max_age_minutes=max_collector_age_minutes, now=now)
    watchdog_ok, watchdog_age = status_ok(watchdog_status, max_age_minutes=max_watchdog_age_minutes, now=now)
    required_exits = [
        "last_source_integrity_exit_code",
        "last_storage_exit_code",
        "last_collector_sla_exit_code",
        "last_health_exit_code",
        "last_seal_exit_code",
        "last_readiness_progress_exit_code",
        "last_snapshot_transition_exit_code",
        "last_prereg_exit_code",
        "last_runner_contract_exit_code",
        "last_research_runner_exit_code",
        "last_candidate_governance_exit_code",
        "last_candidate_review_exit_code",
        "last_validation_protocol_exit_code",
        "last_validation_approval_exit_code",
        "last_validation_runner_exit_code",
    ]
    exits_state, exits_ok, exits = required_exits_state(watchdog_status, required_exits)
    wiring = runner_wiring(watchdog_script_path)
    transition_state = snapshot_transition.get("transition_state")
    transition_next_action = snapshot_transition.get("next_action")
    gate_diag = diagnostics(snapshot_gate)
    remaining_hours = safe_float(snapshot_transition.get("remaining_hours"))
    if remaining_hours is None:
        remaining_hours = safe_float(gate_diag.get("remaining_hours"))
    runner_decision = research_runner.get("decision")
    runner_snapshot_id = research_runner.get("snapshot_id")

    checks = {
        "collector_loop_fresh_and_safe": collector_ok,
        "watchdog_loop_fresh_and_safe": watchdog_ok,
        "watchdog_has_run_if_ready": wiring.get("has_run_if_ready") is True,
        "watchdog_runner_wiring_valid": wiring.get("mode") in {
            "direct_run_if_ready",
            "post_seal_exactly_once_guard",
        },
        "watchdog_source_integrity_guard_wired": (
            wiring.get("integrity_guard_referenced") is True
            and wiring.get("integrity_fail_closed") is True
        ),
        "watchdog_required_exits_zero": exits_ok,
        "snapshot_transition_present": bool(snapshot_transition),
        "snapshot_gate_present": bool(snapshot_gate),
        "can_trade_false": (
            collector_status.get("can_trade") is False
            and watchdog_status.get("can_trade") is False
            and snapshot_gate.get("can_trade") is False
            and snapshot_transition.get("can_trade") is False
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]

    if failed:
        decision = "microstructure_autopilot_needs_repair"
        next_action = "fix_failed_autopilot_checks_before_waiting_for_snapshot"
    elif exits_state == "in_progress":
        decision = "microstructure_autopilot_cycle_in_progress"
        next_action = "wait_for_watchdog_cycle_to_finish_then_recheck"
    elif transition_state == "sealed_snapshot_ready_for_train_research_batch":
        decision = "microstructure_autopilot_ready_for_locked_runner"
        next_action = "watchdog_should_run_research_runner_run_if_ready_on_next_cycle"
    elif transition_state == "sealed_snapshot_research_batch_already_completed":
        decision = "microstructure_autopilot_research_batch_done"
        next_action = "review_candidate_governance_outputs"
    elif transition_state == "waiting_for_minimum_time_window":
        decision = "microstructure_autopilot_waiting_for_snapshot_window"
        next_action = "keep_collector_and_watchdog_running_until_snapshot_gate_passes"
    elif transition_state == "waiting_for_book_coverage_rollout":
        decision = "microstructure_autopilot_waiting_for_book_coverage_rollout"
        next_action = "keep_collector_and_watchdog_running_until_rolling_window_eta"
    else:
        decision = "microstructure_autopilot_blocked_by_snapshot_gate"
        next_action = transition_next_action or "inspect_snapshot_transition_report"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "checks": checks,
        "failed_checks": failed,
        "collector": {
            "status": collector_status.get("status"),
            "pid": collector_status.get("pid"),
            "age_minutes": collector_age,
            "sleep_seconds": collector_status.get("sleep_seconds"),
        },
        "watchdog": {
            "status": watchdog_status.get("status"),
            "pid": watchdog_status.get("pid"),
            "age_minutes": watchdog_age,
            "sleep_seconds": watchdog_status.get("sleep_seconds"),
            "required_exits_state": exits_state,
            "required_exits": exits,
            "runner_wiring": wiring,
        },
        "snapshot": {
            "gate_decision": snapshot_gate.get("decision"),
            "transition_state": transition_state,
            "transition_next_action": transition_next_action,
            "remaining_hours": remaining_hours,
            "earliest_time_gate_at_utc": snapshot_transition.get("earliest_time_gate_at_utc")
            or gate_diag.get("estimated_earliest_time_gate_at_utc"),
            "snapshot_id": snapshot_transition.get("snapshot_id") or snapshot_gate.get("snapshot_id"),
        },
        "research_runner": {
            "decision": runner_decision,
            "snapshot_id": runner_snapshot_id,
        },
        "next_action": next_action,
        "runtime_boundary": {
            "audit_only": True,
            "runs_research_batch": False,
            "opens_validation": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Microstructure Autopilot Audit",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Failed checks: `{', '.join(report.get('failed_checks') or []) or 'none'}`.",
        f"- Collector: `{report['collector'].get('status')}` pid `{report['collector'].get('pid')}` age `{report['collector'].get('age_minutes')}`m.",
        f"- Watchdog: `{report['watchdog'].get('status')}` pid `{report['watchdog'].get('pid')}` age `{report['watchdog'].get('age_minutes')}`m.",
        f"- Snapshot transition: `{report['snapshot'].get('transition_state')}`.",
        f"- Remaining hours: `{report['snapshot'].get('remaining_hours')}`.",
        f"- ETA UTC: `{report['snapshot'].get('earliest_time_gate_at_utc')}`.",
        f"- Research runner decision: `{report['research_runner'].get('decision')}`.",
        f"- Next action: `{report['next_action']}`.",
        "- Audit-only. It does not run research, open validation, emit signals, or place orders.",
        "- `can_trade=false`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the microstructure collector/watchdog autopilot handoff")
    parser.add_argument("--collector-status", default="logs/cross_venue_microstructure/microstructure_loop_status.json")
    parser.add_argument("--watchdog-status", default="logs/cross_venue_microstructure/microstructure_watchdog_loop_status.json")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--snapshot-transition", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_MONITOR_2026-06-25.json")
    parser.add_argument("--research-runner", default="docs/CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_2026-06-25.json")
    parser.add_argument("--watchdog-script", default="ops/autostart/Run-CrossVenueMicrostructureWatchdogLoop.ps1")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_AUTOPILOT_AUDIT_2026-06-29")
    args = parser.parse_args()

    report = build_report(
        collector_status=read_json(resolve_path(args.collector_status)),
        watchdog_status=read_json(resolve_path(args.watchdog_status)),
        snapshot_gate=read_json(resolve_path(args.snapshot_gate)),
        snapshot_transition=read_json(resolve_path(args.snapshot_transition)),
        research_runner=read_json(resolve_path(args.research_runner)),
        watchdog_script_path=resolve_path(args.watchdog_script),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "failed_checks": report["failed_checks"],
                "transition_state": report["snapshot"]["transition_state"],
                "remaining_hours": report["snapshot"]["remaining_hours"],
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
