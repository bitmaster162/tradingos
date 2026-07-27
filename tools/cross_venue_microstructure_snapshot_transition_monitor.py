#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEALED_DECISIONS = {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"}
RUNNER_DONE_DECISIONS = {
    "microstructure_research_batch_completed_no_candidate",
    "microstructure_candidates_require_validation_review",
    "microstructure_research_batch_already_completed_for_snapshot",
}


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


def diagnostics(gate: dict[str, Any]) -> dict[str, Any]:
    payload = gate.get("readiness_diagnostics")
    return payload if isinstance(payload, dict) else {}


def failed_checks(gate: dict[str, Any]) -> list[str]:
    summary = gate.get("summary") if isinstance(gate.get("summary"), dict) else {}
    failed = summary.get("failed")
    if isinstance(failed, list):
        return [str(item) for item in failed]
    diag_failed = diagnostics(gate).get("failed_checks")
    if isinstance(diag_failed, list):
        return [str(item) for item in diag_failed]
    return []


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def nested(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def runner_done_for_snapshot(runner: dict[str, Any], snapshot_id: str | None) -> bool:
    if not snapshot_id:
        return False
    return runner.get("snapshot_id") == snapshot_id and runner.get("decision") in RUNNER_DONE_DECISIONS


def build_transition_report(
    gate: dict[str, Any],
    runner: dict[str, Any],
    previous_state: dict[str, Any] | None = None,
    book_coverage_diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous_state = previous_state if isinstance(previous_state, dict) else {}
    book_coverage_diagnostic = book_coverage_diagnostic if isinstance(book_coverage_diagnostic, dict) else {}
    gate_decision = str(gate.get("decision") or "")
    snapshot_id = gate.get("snapshot_id") if isinstance(gate.get("snapshot_id"), str) else None
    diag = diagnostics(gate)
    remaining_hours = safe_float(diag.get("remaining_hours"))
    primary_blocker = str(diag.get("primary_blocker") or "")
    failed = failed_checks(gate)
    coverage_recent = nested(book_coverage_diagnostic, "recent_windows")
    recent_6h_book_coverage = safe_float(nested(coverage_recent, "6h").get("dual_book_coverage_pct"))
    recent_24h_book_coverage = safe_float(nested(coverage_recent, "24h").get("dual_book_coverage_pct"))
    coverage_eta = nested(book_coverage_diagnostic, "eta").get("eta_utc")
    healthy_recent_coverage = (
        recent_6h_book_coverage is not None
        and recent_24h_book_coverage is not None
        and recent_6h_book_coverage >= 95.0
        and recent_24h_book_coverage >= 95.0
    )
    coverage_rollout_decisions = {
        "microstructure_book_coverage_wait_for_old_gaps_to_roll_out",
        "microstructure_book_coverage_recovered_waiting_recent_gap_rollout",
    }
    book_coverage_decision = str(book_coverage_diagnostic.get("decision") or "")
    waiting_for_book_coverage_rollout = bool(
        primary_blocker == "coverage_threshold"
        and "dual_book_coverage" in failed
        and book_coverage_decision in coverage_rollout_decisions
        and healthy_recent_coverage
        and isinstance(coverage_eta, str)
        and coverage_eta
    )

    if not gate:
        transition_state = "blocked_missing_snapshot_gate"
        next_action = "run_snapshot_gate"
        research_runner_can_attempt_now = False
    elif gate_decision in SEALED_DECISIONS and not snapshot_id:
        transition_state = "blocked_sealed_gate_missing_snapshot_id"
        next_action = "inspect_snapshot_gate_integrity"
        research_runner_can_attempt_now = False
    elif gate_decision in SEALED_DECISIONS and runner_done_for_snapshot(runner, snapshot_id):
        transition_state = "sealed_snapshot_research_batch_already_completed"
        next_action = "continue_candidate_governance_review_flow"
        research_runner_can_attempt_now = False
    elif gate_decision in SEALED_DECISIONS:
        transition_state = "sealed_snapshot_ready_for_train_research_batch"
        next_action = "run_microstructure_research_runner_run_if_ready"
        research_runner_can_attempt_now = True
    elif gate_decision != "waiting_for_microstructure_readiness":
        transition_state = "blocked_unknown_snapshot_gate_decision"
        next_action = "inspect_snapshot_gate_decision"
        research_runner_can_attempt_now = False
    elif (remaining_hours is not None and remaining_hours > 0) or primary_blocker == "minimum_time_window" or "minimum_hours" in failed:
        transition_state = "waiting_for_minimum_time_window"
        next_action = "continue_collecting_until_time_gate"
        research_runner_can_attempt_now = False
    elif waiting_for_book_coverage_rollout:
        transition_state = "waiting_for_book_coverage_rollout"
        next_action = "keep_collector_running_until_rolling_window_eta"
        research_runner_can_attempt_now = False
    elif primary_blocker and primary_blocker not in {"none", "minimum_time_window"}:
        transition_state = "blocked_after_time_window"
        next_action = "fix_failed_snapshot_gates_before_research_batch"
        research_runner_can_attempt_now = False
    else:
        transition_state = "blocked_waiting_gate_inconsistent"
        next_action = "inspect_snapshot_gate_diagnostics"
        research_runner_can_attempt_now = False

    previous_transition_state = previous_state.get("transition_state")
    transition_changed = bool(previous_transition_state and previous_transition_state != transition_state)
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "transition_state": transition_state,
        "previous_transition_state": previous_transition_state,
        "transition_changed": transition_changed,
        "gate_decision": gate.get("decision"),
        "runner_decision": runner.get("decision"),
        "snapshot_id": snapshot_id,
        "runner_snapshot_id": runner.get("snapshot_id"),
        "primary_blocker": primary_blocker or None,
        "remaining_hours": remaining_hours,
        "earliest_time_gate_at_utc": coverage_eta
        if waiting_for_book_coverage_rollout
        else diag.get("estimated_earliest_time_gate_at_utc"),
        "book_coverage_eta_utc": coverage_eta,
        "failed_checks": failed,
        "checks_passed": gate.get("summary", {}).get("passed") if isinstance(gate.get("summary"), dict) else None,
        "checks_total": gate.get("summary", {}).get("total") if isinstance(gate.get("summary"), dict) else None,
        "trade_coverage_pct": diag.get("trade_coverage_pct"),
        "book_coverage_pct": diag.get("book_coverage_pct"),
        "recent_6h_book_coverage_pct": recent_6h_book_coverage,
        "recent_24h_book_coverage_pct": recent_24h_book_coverage,
        "book_coverage_rollout_verified": waiting_for_book_coverage_rollout,
        "legacy_gap_rollout_verified": bool(
            waiting_for_book_coverage_rollout
            and book_coverage_decision == "microstructure_book_coverage_wait_for_old_gaps_to_roll_out"
        ),
        "current_gap_recovery_verified": bool(
            waiting_for_book_coverage_rollout
            and book_coverage_decision == "microstructure_book_coverage_recovered_waiting_recent_gap_rollout"
        ),
        "binance_missing_ids": diag.get("binance_missing_ids"),
        "coinbase_missing_ids": diag.get("coinbase_missing_ids"),
        "research_runner_can_attempt_now": research_runner_can_attempt_now,
        "next_action": next_action,
        "runtime_boundary": {
            "monitor_only": True,
            "runs_research_batch": False,
            "registers_hypothesis": False,
            "opens_validation": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Microstructure Snapshot Transition Monitor",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Transition state: `{report['transition_state']}`.",
        f"- Previous state: `{report.get('previous_transition_state')}`.",
        f"- Transition changed: `{report.get('transition_changed')}`.",
        f"- Gate decision: `{report.get('gate_decision')}`.",
        f"- Runner decision: `{report.get('runner_decision')}`.",
        f"- Snapshot: `{report.get('snapshot_id')}`.",
        f"- Primary blocker: `{report.get('primary_blocker')}`.",
        f"- Remaining hours: `{report.get('remaining_hours')}`.",
        f"- Earliest time gate: `{report.get('earliest_time_gate_at_utc')}`.",
        f"- Recent book coverage 6h/24h: `{report.get('recent_6h_book_coverage_pct')}` / `{report.get('recent_24h_book_coverage_pct')}`.",
        f"- Failed checks: `{', '.join(report.get('failed_checks') or []) or 'none'}`.",
        f"- Research runner can attempt now: `{report.get('research_runner_can_attempt_now')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        "- Monitor-only. It does not run research, open validation, send signals, or place orders.",
        "- `can_trade=false`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor the sealed snapshot transition into the train research batch stage")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--research-runner", default="docs/CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_2026-06-25.json")
    parser.add_argument(
        "--book-coverage-diagnostic",
        default="docs/CROSS_VENUE_MICROSTRUCTURE_BOOK_COVERAGE_DIAGNOSTIC_2026-07-03.json",
    )
    parser.add_argument("--state", default="logs/cross_venue_microstructure/snapshot_transition_monitor_state.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_MONITOR_2026-06-25")
    args = parser.parse_args()

    state_path = resolve_path(args.state)
    report = build_transition_report(
        read_json(resolve_path(args.snapshot_gate)),
        read_json(resolve_path(args.research_runner)),
        read_json(state_path),
        read_json(resolve_path(args.book_coverage_diagnostic)),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_json(
        state_path,
        {
            "updated_at": report["generated_at"],
            "transition_state": report["transition_state"],
            "snapshot_id": report.get("snapshot_id"),
            "gate_decision": report.get("gate_decision"),
            "runner_decision": report.get("runner_decision"),
            "can_trade": False,
        },
    )
    print(json.dumps({"transition_state": report["transition_state"], "snapshot_id": report.get("snapshot_id"), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
