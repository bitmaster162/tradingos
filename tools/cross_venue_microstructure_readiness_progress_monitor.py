#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEALED_DECISIONS = {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"}
HEALTHY_CLASSIFICATIONS = {
    "cross_venue_microstructure_healthy_collecting",
    "cross_venue_microstructure_healthy_research_ready",
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


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def diagnostics(gate: dict[str, Any]) -> dict[str, Any]:
    payload = gate.get("readiness_diagnostics")
    return payload if isinstance(payload, dict) else {}


def coverage(data_quality: dict[str, Any]) -> dict[str, Any]:
    payload = data_quality.get("coverage")
    return payload if isinstance(payload, dict) else {}


def archive(data_quality: dict[str, Any]) -> dict[str, Any]:
    payload = data_quality.get("archive")
    return payload if isinstance(payload, dict) else {}


def build_progress_report(
    gate: dict[str, Any],
    data_quality: dict[str, Any],
    health: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    stall_threshold_minutes: float = 5.0,
) -> dict[str, Any]:
    previous = previous if isinstance(previous, dict) else {}
    gate_diag = diagnostics(gate)
    cov = coverage(data_quality)
    arc = archive(data_quality)
    gate_decision = str(gate.get("decision") or "")
    health_classification = str(health.get("classification") or "")

    data_generated_at = data_quality.get("generated_at")
    previous_data_generated_at = previous.get("data_generated_at")
    current_dt = parse_utc(data_generated_at)
    previous_dt = parse_utc(previous_data_generated_at)
    elapsed_minutes = None
    if current_dt and previous_dt:
        elapsed_minutes = max(0.0, (current_dt - previous_dt).total_seconds() / 60.0)

    span_hours = safe_float(gate_diag.get("span_hours"))
    if span_hours is None:
        span_hours = safe_float(cov.get("span_hours"))
    remaining_hours = safe_float(gate_diag.get("remaining_hours"))
    required_hours = safe_float(gate_diag.get("required_hours")) or safe_float(data_quality.get("research_readiness", {}).get("minimum_hours"))
    trade_coverage_pct = safe_float(gate_diag.get("trade_coverage_pct")) or safe_float(cov.get("both_trade_coverage_pct"))
    book_coverage_pct = safe_float(gate_diag.get("book_coverage_pct")) or safe_float(cov.get("both_book_coverage_pct"))
    required_trade_pct = safe_float(gate_diag.get("required_trade_coverage_pct")) or safe_float(data_quality.get("research_readiness", {}).get("minimum_dual_venue_coverage_pct"))
    required_book_pct = safe_float(gate_diag.get("required_book_coverage_pct")) or safe_float(data_quality.get("research_readiness", {}).get("minimum_dual_venue_coverage_pct"))
    binance_missing_ids = safe_int(gate_diag.get("binance_missing_ids"))
    coinbase_missing_ids = safe_int(gate_diag.get("coinbase_missing_ids"))
    if binance_missing_ids is None:
        binance_missing_ids = safe_int(data_quality.get("trade_id_integrity", {}).get("binance", {}).get("missing_ids"))
    if coinbase_missing_ids is None:
        coinbase_missing_ids = safe_int(data_quality.get("trade_id_integrity", {}).get("coinbase", {}).get("missing_ids"))

    previous_span = safe_float(previous.get("span_hours"))
    previous_remaining = safe_float(previous.get("remaining_hours"))
    previous_trade_coverage = safe_float(previous.get("trade_coverage_pct"))
    previous_book_coverage = safe_float(previous.get("book_coverage_pct"))
    span_delta_hours = None if span_hours is None or previous_span is None else round(span_hours - previous_span, 6)
    remaining_delta_hours = None if remaining_hours is None or previous_remaining is None else round(previous_remaining - remaining_hours, 6)
    trade_coverage_delta_pct = None if trade_coverage_pct is None or previous_trade_coverage is None else round(trade_coverage_pct - previous_trade_coverage, 6)
    book_coverage_delta_pct = None if book_coverage_pct is None or previous_book_coverage is None else round(book_coverage_pct - previous_book_coverage, 6)

    checks = {
        "inputs_present": bool(gate) and bool(data_quality) and bool(health),
        "data_report_fresh_relative_to_state": previous_data_generated_at != data_generated_at if previous_data_generated_at else True,
        "health_collecting_or_ready": health_classification in HEALTHY_CLASSIFICATIONS,
        "span_not_regressed": span_delta_hours is None or span_delta_hours >= -0.001,
        "remaining_not_regressed": remaining_delta_hours is None or remaining_delta_hours >= -0.05,
        "trade_coverage_above_threshold": trade_coverage_pct is not None and required_trade_pct is not None and trade_coverage_pct >= required_trade_pct,
        "book_coverage_above_threshold": book_coverage_pct is not None and required_book_pct is not None and book_coverage_pct >= required_book_pct,
        "coverage_not_sharply_regressed": (
            (trade_coverage_delta_pct is None or trade_coverage_delta_pct >= -1.0)
            and (book_coverage_delta_pct is None or book_coverage_delta_pct >= -1.0)
        ),
        "trade_id_gaps_zero": binance_missing_ids == 0 and coinbase_missing_ids == 0,
        "can_trade_false": gate.get("can_trade") is False and data_quality.get("can_trade") is False and health.get("can_trade") is False,
    }

    if not checks["inputs_present"]:
        decision = "readiness_progress_blocked_missing_inputs"
        next_action = "run_microstructure_collector_health_and_snapshot_gate"
    elif gate_decision in SEALED_DECISIONS:
        decision = "readiness_progress_snapshot_sealed"
        next_action = "handoff_to_research_runner"
    elif previous_span is None:
        decision = "readiness_progress_baseline_recorded"
        next_action = "continue_collecting_and_compare_next_cycle"
    elif not checks["health_collecting_or_ready"]:
        decision = "readiness_progress_health_degraded"
        next_action = "fix_microstructure_health_before_research_gate"
    elif not checks["span_not_regressed"]:
        decision = "readiness_progress_span_regressed"
        next_action = "inspect_collector_retention_or_clock"
    elif not checks["remaining_not_regressed"]:
        decision = "readiness_progress_eta_regressed"
        next_action = "inspect_time_gate_eta_and_report_generation"
    elif elapsed_minutes is not None and elapsed_minutes >= stall_threshold_minutes and (span_delta_hours is None or span_delta_hours < 0.001):
        decision = "readiness_progress_stalled_no_span_growth"
        next_action = "inspect_collector_loop_and_data_refresh"
    elif not checks["trade_coverage_above_threshold"] or not checks["book_coverage_above_threshold"]:
        decision = "readiness_progress_coverage_below_threshold"
        next_action = "continue_collection_or_fix_venue_coverage"
    elif not checks["coverage_not_sharply_regressed"]:
        decision = "readiness_progress_coverage_regressed"
        next_action = "inspect_book_or_trade_collection_degradation"
    elif not checks["trade_id_gaps_zero"]:
        decision = "readiness_progress_trade_id_gaps_present"
        next_action = "run_gap_backfill_until_gaps_zero"
    elif remaining_hours is not None and remaining_hours <= 0 and gate_decision != "microstructure_snapshot_sealed":
        decision = "readiness_progress_time_window_met_but_not_sealed"
        next_action = "inspect_failed_snapshot_gate_checks"
    else:
        decision = "readiness_progress_waiting_healthy"
        next_action = "continue_collecting_until_time_gate"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "gate_decision": gate.get("decision"),
        "health_classification": health_classification or None,
        "data_generated_at": data_generated_at,
        "previous_data_generated_at": previous_data_generated_at,
        "elapsed_minutes_since_previous_data_report": round(elapsed_minutes, 6) if elapsed_minutes is not None else None,
        "span_hours": span_hours,
        "previous_span_hours": previous_span,
        "span_delta_hours": span_delta_hours,
        "required_hours": required_hours,
        "remaining_hours": remaining_hours,
        "previous_remaining_hours": previous_remaining,
        "remaining_delta_hours": remaining_delta_hours,
        "earliest_time_gate_at_utc": gate_diag.get("estimated_earliest_time_gate_at_utc"),
        "trade_coverage_pct": trade_coverage_pct,
        "book_coverage_pct": book_coverage_pct,
        "trade_coverage_delta_pct": trade_coverage_delta_pct,
        "book_coverage_delta_pct": book_coverage_delta_pct,
        "required_trade_coverage_pct": required_trade_pct,
        "required_book_coverage_pct": required_book_pct,
        "binance_missing_ids": binance_missing_ids,
        "coinbase_missing_ids": coinbase_missing_ids,
        "archive_trades": arc.get("trades"),
        "archive_books": arc.get("book_snapshots"),
        "archive_feature_rows": arc.get("minute_feature_rows"),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "next_action": next_action,
        "runtime_boundary": {
            "progress_monitor_only": True,
            "runs_research_batch": False,
            "opens_validation": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Cross-Venue Microstructure Readiness Progress Monitor",
            "",
            f"- Generated: `{report['generated_at']}`.",
            f"- Decision: `{report['decision']}`.",
            f"- Gate decision: `{report.get('gate_decision')}`.",
            f"- Health: `{report.get('health_classification')}`.",
            f"- Span: `{report.get('span_hours')}` / `{report.get('required_hours')}` hours.",
            f"- Span delta: `{report.get('span_delta_hours')}` hours.",
            f"- Remaining: `{report.get('remaining_hours')}` hours.",
            f"- Coverage trade/book: `{report.get('trade_coverage_pct')}` / `{report.get('book_coverage_pct')}`.",
            f"- Missing IDs B/C: `{report.get('binance_missing_ids')}` / `{report.get('coinbase_missing_ids')}`.",
            f"- Failed checks: `{', '.join(report.get('failed_checks') or []) or 'none'}`.",
            f"- Next action: `{report.get('next_action')}`.",
            "- Progress monitor only; no research run, validation, signals, or orders.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Track microstructure readiness progress while waiting for sealed snapshot")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--data-quality", default="docs/CROSS_VENUE_MICROSTRUCTURE_DATA_QUALITY_2026-06-24.json")
    parser.add_argument("--health", default="docs/CROSS_VENUE_MICROSTRUCTURE_HEALTH_2026-06-24.json")
    parser.add_argument("--state", default="logs/cross_venue_microstructure/readiness_progress_state.json")
    parser.add_argument("--history", default="logs/cross_venue_microstructure/readiness_progress_history.jsonl")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_READINESS_PROGRESS_2026-06-25")
    parser.add_argument("--stall-threshold-minutes", type=float, default=5.0)
    args = parser.parse_args()

    state_path = resolve_path(args.state)
    report = build_progress_report(
        read_json(resolve_path(args.snapshot_gate)),
        read_json(resolve_path(args.data_quality)),
        read_json(resolve_path(args.health)),
        read_json(state_path),
        stall_threshold_minutes=max(0.0, args.stall_threshold_minutes),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_json(
        state_path,
        {
            "updated_at": report["generated_at"],
            "decision": report["decision"],
            "data_generated_at": report.get("data_generated_at"),
            "span_hours": report.get("span_hours"),
            "remaining_hours": report.get("remaining_hours"),
            "trade_coverage_pct": report.get("trade_coverage_pct"),
            "book_coverage_pct": report.get("book_coverage_pct"),
            "can_trade": False,
        },
    )
    append_jsonl(resolve_path(args.history), report)
    print(json.dumps({"decision": report["decision"], "span_hours": report.get("span_hours"), "remaining_hours": report.get("remaining_hours"), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
