#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hyperliquid_cross_venue_funding_collector import (  # noqa: E402
    finite,
    iso_from_ms,
    now_ms,
    portable_path,
    read_journal,
    read_json,
    resolve_path,
    write_json,
)


DEFAULT_LOCK = ROOT / "configs" / "CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V3_2026-07-14.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CEX_FUNDING_SOURCE_ALIGNMENT_V3_2026-07-14"
PARAMETER_CONTRACT_KEYS = (
    "inputs",
    "mapping",
    "symbols",
    "metrics",
    "readiness_gate",
    "runtime_boundary",
    "can_trade",
)


def parameter_contract_sha256(lock: dict[str, Any]) -> str:
    payload = {key: lock.get(key) for key in PARAMETER_CONTRACT_KEYS}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_ss = sum((value - left_mean) ** 2 for value in left)
    right_ss = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_ss * right_ss)
    return numerator / denominator if denominator > 0 else None


def validate_lock(lock: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    metrics = lock.get("metrics") if isinstance(lock.get("metrics"), dict) else {}
    boundary = lock.get("runtime_boundary") if isinstance(lock.get("runtime_boundary"), dict) else {}
    readiness = lock.get("readiness_gate") if isinstance(lock.get("readiness_gate"), dict) else {}
    if lock.get("status") != "fixed_source_alignment_contract":
        failures.append("status")
    if lock.get("can_trade") is not False:
        failures.append("can_trade")
    if metrics.get("same_minute_only") is not True or metrics.get("lagged_comparisons_allowed") is not False:
        failures.append("same_minute_only")
    if metrics.get("price_outcomes_allowed") is not False:
        failures.append("price_outcomes")
    if readiness.get("automatic_source_equivalence_claim_allowed") is not False:
        failures.append("automatic_equivalence")
    if boundary.get("data_quality_only") is not True or boundary.get("edge_evaluator") is not False:
        failures.append("data_quality_boundary")
    if boundary.get("signals_allowed") is not False or boundary.get("orders_allowed") is not False:
        failures.append("execution_boundary")
    if not lock.get("mapping") or not lock.get("symbols"):
        failures.append("scope")
    if int(lock.get("schema_version") or 0) >= 3:
        lifecycle = lock.get("lifecycle") if isinstance(lock.get("lifecycle"), dict) else {}
        predecessor = lock.get("predecessor") if isinstance(lock.get("predecessor"), dict) else {}
        successor_review = lock.get("successor_review") if isinstance(lock.get("successor_review"), dict) else {}
        baseline = (
            lifecycle.get("pre_floor_bad_line_baseline")
            if isinstance(lifecycle.get("pre_floor_bad_line_baseline"), dict)
            else {}
        )
        if not parse_iso_ms(lock.get("forward_start_at")):
            failures.append("forward_start_at")
        if lock.get("parameter_contract_sha256") != parameter_contract_sha256(lock):
            failures.append("parameter_contract_sha256")
        if predecessor.get("parameters_changed") is not False or predecessor.get("history_rewritten") is not False:
            failures.append("predecessor_boundary")
        if successor_review.get("manual_review_completed") is not True:
            failures.append("manual_successor_review")
        if (
            lifecycle.get("pre_floor_rows_excluded") is not True
            or lifecycle.get("predecessor_rows_admitted") is not False
            or lifecycle.get("automatic_successor_allowed") is not False
            or lifecycle.get("retune_allowed") is not False
            or lifecycle.get("historical_backfill_allowed") is not False
        ):
            failures.append("successor_lifecycle")
        if any(not isinstance(baseline.get(name), int) or baseline.get(name) < 0 for name in ("aggregate", "direct")):
            failures.append("pre_floor_bad_line_baseline")
    return failures


def parse_iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def point(row: dict[str, Any], symbol: str, venue: str, field: str) -> float | None:
    symbols = row.get("symbols") if isinstance(row.get("symbols"), dict) else {}
    symbol_row = symbols.get(symbol) if isinstance(symbols.get(symbol), dict) else {}
    venue_row = symbol_row.get(venue) if isinstance(symbol_row.get(venue), dict) else {}
    return finite(venue_row.get(field))


def build_report(
    lock: dict[str, Any],
    aggregate_rows: list[dict[str, Any]],
    direct_rows: list[dict[str, Any]],
    aggregate_bad_lines: int,
    direct_bad_lines: int,
    current_ms: int | None = None,
) -> dict[str, Any]:
    field = str(lock["metrics"]["field"])
    forward_floor_utc = lock.get("forward_start_at") or lock.get("sealed_at")
    forward_floor_ms = parse_iso_ms(forward_floor_utc) or 0
    evaluated_at_ms = now_ms() if current_ms is None else current_ms
    waiting_forward_floor = bool(lock.get("forward_start_at") and evaluated_at_ms < forward_floor_ms)
    aggregate_before_floor = sum(
        int(row.get("minute_bucket_ms") or 0) < forward_floor_ms
        for row in aggregate_rows
        if int(row.get("minute_bucket_ms") or 0) > 0
    )
    direct_before_floor = sum(
        int(row.get("minute_bucket_ms") or 0) < forward_floor_ms
        for row in direct_rows
        if int(row.get("minute_bucket_ms") or 0) > 0
    )
    aggregate_by_bucket = {
        int(row.get("minute_bucket_ms") or 0): row
        for row in aggregate_rows
        if int(row.get("minute_bucket_ms") or 0) >= forward_floor_ms
    }
    direct_by_bucket = {
        int(row.get("minute_bucket_ms") or 0): row
        for row in direct_rows
        if int(row.get("minute_bucket_ms") or 0) >= forward_floor_ms
    }
    matching_buckets = sorted(set(aggregate_by_bucket).intersection(direct_by_bucket))
    symbols = [str(item).upper() for item in lock["symbols"]]
    mappings = [item for item in lock["mapping"] if isinstance(item, dict)]
    metric_rows: dict[str, dict[str, Any]] = {}
    valid_comparisons = 0

    for symbol in symbols:
        symbol_metrics: dict[str, Any] = {}
        for mapping in mappings:
            aggregate_venue = str(mapping["aggregate_venue"])
            direct_venue = str(mapping["direct_venue"])
            label = str(mapping["label"])
            aggregate_values: list[float] = []
            direct_values: list[float] = []
            deltas: list[float] = []
            for bucket in matching_buckets:
                aggregate_value = point(aggregate_by_bucket[bucket], symbol, aggregate_venue, field)
                direct_value = point(direct_by_bucket[bucket], symbol, direct_venue, field)
                if aggregate_value is None or direct_value is None:
                    continue
                aggregate_values.append(aggregate_value)
                direct_values.append(direct_value)
                deltas.append((aggregate_value - direct_value) * 10_000.0)
            valid_comparisons += len(deltas)
            absolute = [abs(value) for value in deltas]
            correlation = pearson(aggregate_values, direct_values)
            symbol_metrics[label] = {
                "aggregate_venue": aggregate_venue,
                "direct_venue": direct_venue,
                "points": len(deltas),
                "mean_signed_delta_bps_per_hour": round(statistics.fmean(deltas), 12) if deltas else None,
                "median_absolute_delta_bps_per_hour": round(statistics.median(absolute), 12) if absolute else None,
                "p95_absolute_delta_bps_per_hour": round(nearest_rank(absolute, 0.95), 12) if absolute else None,
                "maximum_absolute_delta_bps_per_hour": round(max(absolute), 12) if absolute else None,
                "same_minute_pearson_correlation": round(correlation, 12) if correlation is not None else None,
            }
        metric_rows[symbol] = symbol_metrics

    expected_comparisons = len(matching_buckets) * len(symbols) * len(mappings)
    coverage = valid_comparisons / expected_comparisons if expected_comparisons else 0.0
    independent_days = {iso_from_ms(bucket)[:10] for bucket in matching_buckets}
    continuity_start_ms = forward_floor_ms if lock.get("forward_start_at") else (matching_buckets[0] if matching_buckets else 0)
    expected_minute_buckets = (
        int((matching_buckets[-1] - continuity_start_ms) / 60_000) + 1
        if matching_buckets
        else 0
    )
    matching_time_coverage = len(matching_buckets) / expected_minute_buckets if expected_minute_buckets else 0.0
    internal_gap_minutes = [
        (right - left) / 60_000.0
        for left, right in zip(matching_buckets, matching_buckets[1:])
    ]
    leading_gap_minutes = (
        max(0.0, (matching_buckets[0] - forward_floor_ms) / 60_000.0)
        if matching_buckets and lock.get("forward_start_at")
        else 0.0
    )
    gap_minutes = ([leading_gap_minutes] if leading_gap_minutes > 0 else []) + internal_gap_minutes
    maximum_gap_minutes = max(gap_minutes) if gap_minutes else 0.0
    readiness = lock["readiness_gate"]
    lifecycle = lock.get("lifecycle") if isinstance(lock.get("lifecycle"), dict) else {}
    bad_line_baseline = (
        lifecycle.get("pre_floor_bad_line_baseline")
        if isinstance(lifecycle.get("pre_floor_bad_line_baseline"), dict)
        else {}
    )
    aggregate_forward_bad_lines = max(0, aggregate_bad_lines - int(bad_line_baseline.get("aggregate") or 0))
    direct_forward_bad_lines = max(0, direct_bad_lines - int(bad_line_baseline.get("direct") or 0))
    total_bad_lines = aggregate_forward_bad_lines + direct_forward_bad_lines
    gates = {
        "minimum_matching_minute_buckets": len(matching_buckets) >= int(readiness["minimum_matching_minute_buckets"]),
        "minimum_independent_utc_days": len(independent_days) >= int(readiness["minimum_independent_utc_days"]),
        "minimum_comparison_coverage": coverage >= float(readiness["minimum_comparison_coverage"]),
        "minimum_matching_time_coverage": matching_time_coverage >= float(readiness["minimum_matching_time_coverage"]),
        "maximum_consecutive_gap_minutes": maximum_gap_minutes <= float(readiness["maximum_consecutive_gap_minutes"]),
        "maximum_bad_jsonl_lines": total_bad_lines <= int(readiness["maximum_bad_jsonl_lines"]),
    }
    terminal_failures: list[str] = []
    if maximum_gap_minutes > float(readiness["maximum_consecutive_gap_minutes"]):
        terminal_failures.append("maximum_consecutive_gap_minutes_exceeded")
    if total_bad_lines > int(readiness["maximum_bad_jsonl_lines"]):
        terminal_failures.append("maximum_bad_jsonl_lines_exceeded")
    if waiting_forward_floor:
        decision = "cex_funding_source_alignment_waiting_forward_floor"
        terminal_failures = []
    elif total_bad_lines > int(readiness["maximum_bad_jsonl_lines"]):
        decision = "cex_funding_source_alignment_blocked_data_quality"
    elif terminal_failures:
        decision = "cex_funding_source_alignment_terminal_data_quality_failure"
    elif all(gates.values()):
        decision = "cex_funding_source_alignment_ready_for_manual_semantic_review"
    else:
        decision = "cex_funding_source_alignment_collecting"
    return {
        "schema_version": 1,
        "generated_at": iso_from_ms(evaluated_at_ms),
        "tool": "tools/cex_funding_source_alignment_monitor.py",
        "decision": decision,
        "lock_id": lock.get("lock_id"),
        "forward_floor_utc": forward_floor_utc,
        "sample": {
            "aggregate_rows": len(aggregate_rows),
            "direct_rows": len(direct_rows),
            "aggregate_rows_before_forward_floor_excluded": aggregate_before_floor,
            "direct_rows_before_forward_floor_excluded": direct_before_floor,
            "aggregate_bad_lines": aggregate_bad_lines,
            "direct_bad_lines": direct_bad_lines,
            "aggregate_pre_floor_bad_line_baseline": int(bad_line_baseline.get("aggregate") or 0),
            "direct_pre_floor_bad_line_baseline": int(bad_line_baseline.get("direct") or 0),
            "aggregate_forward_bad_lines": aggregate_forward_bad_lines,
            "direct_forward_bad_lines": direct_forward_bad_lines,
            "matching_minute_buckets": len(matching_buckets),
            "expected_minute_buckets": expected_minute_buckets,
            "matching_time_coverage": round(matching_time_coverage, 8),
            "maximum_consecutive_gap_minutes": round(maximum_gap_minutes, 3),
            "leading_gap_from_floor_minutes": round(leading_gap_minutes, 3),
            "gaps_over_one_minute": sum(value > 1.0 for value in gap_minutes),
            "first_matching_bucket": iso_from_ms(matching_buckets[0]) if matching_buckets else None,
            "last_matching_bucket": iso_from_ms(matching_buckets[-1]) if matching_buckets else None,
            "independent_utc_days": len(independent_days),
            "expected_comparisons": expected_comparisons,
            "valid_comparisons": valid_comparisons,
            "comparison_coverage": round(coverage, 8),
        },
        "same_minute_metrics": metric_rows,
        "readiness_gates": gates,
        "blockers": [name for name, passed in gates.items() if not passed],
        "terminal": {
            "reached": bool(terminal_failures),
            "reasons": terminal_failures,
            "history_rewrite_allowed": False,
            "retune_allowed": False,
        },
        "next_action": (
            "Wait for the frozen forward floor; keep collection running and admit no predecessor rows."
            if waiting_forward_floor
            else
            "Preserve this lock as failed; repair runtime continuity and open only a parameter-identical future-floor successor after manual review."
            if terminal_failures
            else "Continue collecting until every fixed readiness gate passes."
        ),
        "automatic_equivalence_claim": False,
        "edge_evaluated": False,
        "runtime_boundary": lock.get("runtime_boundary"),
        "can_trade": False,
    }


def write_report(report: dict[str, Any], out_prefix: Path) -> None:
    write_json(out_prefix.with_suffix(".json"), report)
    sample = report.get("sample") if isinstance(report.get("sample"), dict) else {}
    lines = [
        "# CEX Funding Source Alignment Monitor",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Forward floor: `{report.get('forward_floor_utc')}`",
        f"- Matching minute buckets: `{sample.get('matching_minute_buckets')}`",
        f"- Independent UTC days: `{sample.get('independent_utc_days')}`",
        f"- Comparison coverage: `{sample.get('comparison_coverage')}`",
        f"- Matching time coverage: `{sample.get('matching_time_coverage')}`",
        f"- Maximum consecutive gap: `{sample.get('maximum_consecutive_gap_minutes')}` minutes",
        f"- Blockers: `{', '.join(report.get('blockers') or report.get('lock_failures') or [])}`",
        f"- Terminal: `{((report.get('terminal') or {}).get('reached'))}`; reasons: `{', '.join((report.get('terminal') or {}).get('reasons') or []) or 'none'}`",
        f"- Next action: {report.get('next_action')}",
        "",
        "## Same-minute deltas",
        "",
    ]
    for symbol, venue_rows in (report.get("same_minute_metrics") or {}).items():
        for label, metrics in venue_rows.items():
            lines.append(
                f"- {symbol} {label}: n=`{metrics['points']}`, median abs=`{metrics['median_absolute_delta_bps_per_hour']}` bps/hour, p95 abs=`{metrics['p95_absolute_delta_bps_per_hour']}` bps/hour, corr=`{metrics['same_minute_pearson_correlation']}`"
            )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Same-minute source comparison only; no lagged tests or price outcomes.",
            "- Readiness permits manual semantic review, never automatic equivalence.",
            "- No signal or order. `can_trade=false`.",
            "",
        ]
    )
    out_prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


def run(lock_path: Path, out_prefix: Path) -> tuple[int, dict[str, Any]]:
    lock = read_json(lock_path)
    failures = validate_lock(lock)
    if failures:
        report = {
            "generated_at": iso_from_ms(now_ms()),
            "decision": "cex_funding_source_alignment_blocked_lock",
            "lock_failures": failures,
            "can_trade": False,
        }
        write_report(report, out_prefix)
        return 2, report
    inputs = lock["inputs"]
    aggregate_path = resolve_path(inputs["aggregate_journal"])
    direct_path = resolve_path(inputs["direct_journal"])
    aggregate_rows, aggregate_bad = read_journal(aggregate_path)
    direct_rows, direct_bad = read_journal(direct_path)
    report = build_report(lock, aggregate_rows, direct_rows, aggregate_bad, direct_bad)
    report["inputs"] = {
        "aggregate_journal": portable_path(aggregate_path),
        "direct_journal": portable_path(direct_path),
    }
    write_report(report, out_prefix)
    return (1 if report["decision"] == "cex_funding_source_alignment_blocked_data_quality" else 0), report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Same-minute semantic alignment monitor for aggregate and direct CEX funding sources")
    parser.add_argument("--lock", default=str(DEFAULT_LOCK))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()
    code, report = run(resolve_path(args.lock), resolve_path(args.out_prefix))
    print(json.dumps({"decision": report.get("decision"), "sample": report.get("sample"), "can_trade": False}, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
