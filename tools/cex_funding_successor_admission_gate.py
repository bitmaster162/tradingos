#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cex_funding_source_alignment_monitor import (  # noqa: E402
    build_report as build_alignment_report,
    parse_iso_ms,
    validate_lock,
)
from tools.hyperliquid_cross_venue_funding_collector import (  # noqa: E402
    read_journal,
    read_json,
    resolve_path,
    write_json,
)


TOOL_PATH = "tools/cex_funding_successor_admission_gate.py"
DEFAULT_LOCK = "configs/CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V3_2026-07-14.json"
DEFAULT_PREDECESSOR_REPORT = "docs/CEX_FUNDING_SOURCE_ALIGNMENT_V3_2026-07-14.json"


def now_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def common_minute_buckets(
    aggregate_rows: list[dict[str, Any]],
    direct_rows: list[dict[str, Any]],
    minimum_bucket_ms: int,
) -> list[int]:
    aggregate = {
        int(row.get("minute_bucket_ms") or 0)
        for row in aggregate_rows
        if int(row.get("minute_bucket_ms") or 0) >= minimum_bucket_ms
    }
    direct = {
        int(row.get("minute_bucket_ms") or 0)
        for row in direct_rows
        if int(row.get("minute_bucket_ms") or 0) >= minimum_bucket_ms
    }
    return sorted(aggregate.intersection(direct))


def latest_clean_segment_start(buckets: list[int], maximum_gap_minutes: float) -> int | None:
    if not buckets:
        return None
    segment_start = buckets[0]
    for left, right in zip(buckets, buckets[1:]):
        if (right - left) / 60_000.0 > maximum_gap_minutes:
            segment_start = right
    return segment_start


def build_report(
    predecessor_lock: dict[str, Any],
    predecessor_report: dict[str, Any],
    aggregate_rows: list[dict[str, Any]],
    direct_rows: list[dict[str, Any]],
    aggregate_bad_lines: int,
    direct_bad_lines: int,
    *,
    observed_at: datetime | None = None,
    diagnostic_window_hours: float = 24.0,
    maximum_source_age_minutes: float = 5.0,
) -> dict[str, Any]:
    observed = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
    observed_ms = int(observed.timestamp() * 1000)
    diagnostic_floor = observed - timedelta(hours=diagnostic_window_hours)
    diagnostic_floor_ms = int(diagnostic_floor.timestamp() * 1000)

    lock_failures = validate_lock(predecessor_lock)
    predecessor_terminal = (
        predecessor_report.get("lock_id") == predecessor_lock.get("lock_id")
        and predecessor_report.get("decision") == "cex_funding_source_alignment_terminal_data_quality_failure"
        and isinstance(predecessor_report.get("terminal"), dict)
        and predecessor_report["terminal"].get("reached") is True
    )

    probe_lock = copy.deepcopy(predecessor_lock)
    probe_lock["forward_start_at"] = now_iso(diagnostic_floor)
    rolling = build_alignment_report(
        probe_lock,
        aggregate_rows,
        direct_rows,
        aggregate_bad_lines,
        direct_bad_lines,
        observed_ms,
    )

    predecessor_floor_ms = parse_iso_ms(predecessor_lock.get("forward_start_at")) or 0
    common = common_minute_buckets(aggregate_rows, direct_rows, predecessor_floor_ms)
    readiness = predecessor_lock.get("readiness_gate") if isinstance(predecessor_lock.get("readiness_gate"), dict) else {}
    maximum_gap_minutes = float(readiness.get("maximum_consecutive_gap_minutes") or 0.0)
    clean_segment_start_ms = latest_clean_segment_start(common, maximum_gap_minutes)
    minimum_bucket_minutes = max(0, int(readiness.get("minimum_matching_minute_buckets") or 0) - 1)
    required_clean_minutes = max(int(diagnostic_window_hours * 60), minimum_bucket_minutes)
    estimated_recheck = (
        datetime.fromtimestamp(clean_segment_start_ms / 1000.0, tz=timezone.utc)
        + timedelta(minutes=required_clean_minutes)
        if clean_segment_start_ms is not None
        else None
    )
    if estimated_recheck is not None and estimated_recheck <= observed:
        estimated_recheck = observed + timedelta(minutes=maximum_source_age_minutes)

    sample = rolling.get("sample") if isinstance(rolling.get("sample"), dict) else {}
    last_matching_ms = parse_iso_ms(sample.get("last_matching_bucket"))
    source_age_minutes = (
        max(0.0, (observed_ms - last_matching_ms) / 60_000.0)
        if last_matching_ms is not None
        else None
    )
    rolling_gates = rolling.get("readiness_gates") if isinstance(rolling.get("readiness_gates"), dict) else {}
    checks = {
        "predecessor_lock_valid": not lock_failures,
        "predecessor_terminal_proven": predecessor_terminal,
        "rolling_alignment_nonterminal": (rolling.get("terminal") or {}).get("reached") is False,
        "rolling_original_gates_passed": bool(rolling_gates) and all(rolling_gates.values()),
        "latest_common_bucket_fresh": (
            source_age_minutes is not None and source_age_minutes <= maximum_source_age_minutes
        ),
        "predecessor_rows_admitted": False,
        "price_outcomes_read": False,
    }
    contract_blocked = bool(lock_failures) or not predecessor_terminal
    eligible = (
        not contract_blocked
        and checks["rolling_alignment_nonterminal"]
        and checks["rolling_original_gates_passed"]
        and checks["latest_common_bucket_fresh"]
    )
    if contract_blocked:
        decision = "cex_funding_successor_admission_blocked_contract"
        next_action = "Preserve V3 and repair contract provenance before any successor discussion."
    elif eligible:
        decision = "cex_funding_successor_admission_ready_for_manual_lock_review"
        next_action = "Manual review may create one parameter-identical future-floor successor; do not inherit V3 rows."
    else:
        decision = "cex_funding_successor_admission_waiting_clean_window"
        next_action = "Keep both collectors running and recheck only after the bounded clean-window estimate."

    return {
        "schema_version": 1,
        "generated_at": now_iso(observed),
        "tool": TOOL_PATH,
        "decision": decision,
        "eligible_for_manual_successor_lock_review": eligible,
        "predecessor": {
            "lock_id": predecessor_lock.get("lock_id"),
            "terminal_report_decision": predecessor_report.get("decision"),
            "terminal_proven": predecessor_terminal,
            "lock_failures": lock_failures,
        },
        "diagnostic_window": {
            "hours": diagnostic_window_hours,
            "floor_utc": now_iso(diagnostic_floor),
            "maximum_source_age_minutes": maximum_source_age_minutes,
            "latest_common_bucket_age_minutes": round(source_age_minutes, 6) if source_age_minutes is not None else None,
            "clean_segment_start_utc": (
                now_iso(datetime.fromtimestamp(clean_segment_start_ms / 1000.0, tz=timezone.utc))
                if clean_segment_start_ms is not None
                else None
            ),
            "earliest_recheck_at_utc": now_iso(estimated_recheck) if estimated_recheck else None,
            "estimate_assumes_no_new_gap": True,
            "estimate_is_not_gate_pass": True,
        },
        "rolling_alignment": {
            "decision": rolling.get("decision"),
            "sample": sample,
            "readiness_gates": rolling_gates,
            "blockers": rolling.get("blockers") or [],
            "terminal": rolling.get("terminal") or {},
        },
        "checks": checks,
        "successor_policy": {
            "parameter_identical_required": True,
            "manual_review_required": True,
            "automatic_successor_creation_allowed": False,
            "predecessor_rows_admitted": False,
            "historical_backfill_allowed": False,
            "retune_allowed": False,
        },
        "next_action": next_action,
        "runtime_boundary": {
            "data_quality_only": True,
            "price_outcomes_read": False,
            "edge_evaluator": False,
            "successor_created": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    window = report["diagnostic_window"]
    rolling = report["rolling_alignment"]
    sample = rolling["sample"]
    return "\n".join(
        [
            "# CEX Funding Successor Admission Gate",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Eligible for manual successor review: `{report['eligible_for_manual_successor_lock_review']}`",
            f"- Rolling floor: `{window['floor_utc']}`",
            f"- Matching minutes: `{sample.get('matching_minute_buckets')}/{sample.get('expected_minute_buckets')}`",
            f"- Matching-time coverage: `{sample.get('matching_time_coverage')}`",
            f"- Maximum gap: `{sample.get('maximum_consecutive_gap_minutes')}` minutes",
            f"- Earliest bounded recheck: `{window['earliest_recheck_at_utc']}`",
            f"- Blockers: `{rolling['blockers']}`",
            f"- Next action: {report['next_action']}",
            "",
            "This gate reads source-alignment evidence only. It does not create a successor, evaluate an edge or permit trading.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Admission gate for a parameter-identical CEX funding alignment successor")
    parser.add_argument("--predecessor-lock", default=DEFAULT_LOCK)
    parser.add_argument("--predecessor-report", default=DEFAULT_PREDECESSOR_REPORT)
    parser.add_argument("--diagnostic-window-hours", type=float, default=24.0)
    parser.add_argument("--maximum-source-age-minutes", type=float, default=5.0)
    parser.add_argument("--out-prefix", default="docs/CEX_FUNDING_SUCCESSOR_ADMISSION_2026-07-16")
    args = parser.parse_args()

    lock_path = resolve_path(args.predecessor_lock)
    predecessor_report_path = resolve_path(args.predecessor_report)
    lock = read_json(lock_path)
    predecessor_report = read_json(predecessor_report_path)
    inputs = lock.get("inputs") if isinstance(lock.get("inputs"), dict) else {}
    aggregate_path = resolve_path(inputs.get("aggregate_journal") or "missing")
    direct_path = resolve_path(inputs.get("direct_journal") or "missing")
    aggregate_rows, aggregate_bad = read_journal(aggregate_path)
    direct_rows, direct_bad = read_journal(direct_path)
    report = build_report(
        lock,
        predecessor_report,
        aggregate_rows,
        direct_rows,
        aggregate_bad,
        direct_bad,
        diagnostic_window_hours=args.diagnostic_window_hours,
        maximum_source_age_minutes=args.maximum_source_age_minutes,
    )
    report["sources"] = {
        "predecessor_lock": portable(lock_path),
        "predecessor_report": portable(predecessor_report_path),
        "aggregate_journal": portable(aggregate_path),
        "direct_journal": portable(direct_path),
    }
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "eligible": report["eligible_for_manual_successor_lock_review"],
                "matching_minutes": report["rolling_alignment"]["sample"].get("matching_minute_buckets"),
                "earliest_recheck_at_utc": report["diagnostic_window"]["earliest_recheck_at_utc"],
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 2 if report["decision"] == "cex_funding_successor_admission_blocked_contract" else 0


if __name__ == "__main__":
    raise SystemExit(main())
