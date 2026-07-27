#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.force_order_liquidation_event_study import independent_4h_block_id  # noqa: E402
from tools.force_order_liquidation_research_pipeline import locked_study, sha256_file  # noqa: E402


REQUIRED_RECORD_FIELDS = {
    "symbol",
    "bar_ts",
    "independent_4h_block",
    "signal_time",
    "entry_time",
    "entry_model",
    "horizon_bars",
    "dominant_context",
    "reversal_return_bps",
}
DIRECTIONAL_CONTEXTS = {"long_liquidation_flush", "short_liquidation_squeeze"}


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
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_records(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.is_file():
        return [], [f"records_csv_missing:{portable(path)}"]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[tuple[str, str, int]] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(REQUIRED_RECORD_FIELDS - set(reader.fieldnames or []))
        if missing:
            return [], [f"records_csv_missing_columns:{','.join(missing)}"]
        for row_number, source in enumerate(reader, start=2):
            try:
                horizon = int(source.get("horizon_bars") or 0)
                reversal = (
                    float(source["reversal_return_bps"])
                    if source.get("reversal_return_bps") not in (None, "")
                    else None
                )
            except (TypeError, ValueError):
                errors.append(f"row_{row_number}:bad_numeric_value")
                continue
            symbol = str(source.get("symbol") or "").upper()
            bar_ts = str(source.get("bar_ts") or "")
            key = (symbol, bar_ts, horizon)
            row_errors: list[str] = []
            if not symbol:
                row_errors.append("missing_symbol")
            if parse_ts(bar_ts) is None:
                row_errors.append("bad_bar_ts")
            if horizon <= 0:
                row_errors.append("bad_horizon")
            if source.get("signal_time") != "event_bar_close":
                row_errors.append("bad_signal_model")
            if source.get("entry_model") != "next_bar_open":
                row_errors.append("bad_entry_model")
            if source.get("dominant_context") in DIRECTIONAL_CONTEXTS and reversal is None:
                row_errors.append("missing_directional_reversal")
            if source.get("independent_4h_block") != independent_4h_block_id(bar_ts):
                row_errors.append("bad_independent_block")
            if key in seen:
                row_errors.append("duplicate_event_horizon")
            if row_errors:
                errors.append(f"row_{row_number}:{';'.join(row_errors)}")
                continue
            seen.add(key)
            row = dict(source)
            row["symbol"] = symbol
            row["horizon_bars"] = horizon
            row["reversal_return_bps"] = reversal
            rows.append(row)
    return rows, errors


def percentile(sorted_values: list[float], probability: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def bootstrap_cluster_mean(
    values: list[float],
    iterations: int,
    seed: int,
    confidence_level: float,
) -> dict[str, Any]:
    if not values:
        return {
            "iterations": iterations,
            "seed": seed,
            "confidence_level": confidence_level,
            "probability_mean_gt_zero": None,
            "mean_ci_bps": [None, None],
        }
    rng = random.Random(seed)
    means: list[float] = []
    count = len(values)
    for _ in range(iterations):
        means.append(statistics.fmean(values[rng.randrange(count)] for _ in range(count)))
    means.sort()
    tail = (1.0 - confidence_level) / 2.0
    lower = percentile(means, tail)
    upper = percentile(means, 1.0 - tail)
    return {
        "iterations": iterations,
        "seed": seed,
        "confidence_level": confidence_level,
        "probability_mean_gt_zero": round(sum(value > 0.0 for value in means) / iterations, 6),
        "mean_ci_bps": [round(float(lower), 6), round(float(upper), 6)],
    }


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean_bps": None, "median_bps": None, "winrate_positive_pct": None}
    return {
        "n": len(values),
        "mean_bps": round(statistics.fmean(values), 6),
        "median_bps": round(statistics.median(values), 6),
        "winrate_positive_pct": round(100.0 * sum(value > 0.0 for value in values) / len(values), 3),
    }


def clustered_net_values(records: list[dict[str, Any]], cost_bps: float) -> list[float]:
    clusters: dict[str, list[float]] = defaultdict(list)
    for row in records:
        clusters[str(row["independent_4h_block"])].append(float(row["reversal_return_bps"]) - cost_bps)
    return [statistics.fmean(clusters[key]) for key in sorted(clusters)]


def evaluate_horizon(records: list[dict[str, Any]], horizon: int, params: dict[str, Any]) -> dict[str, Any]:
    selected = [
        row
        for row in records
        if int(row["horizon_bars"]) == horizon
        and row.get("dominant_context") in DIRECTIONAL_CONTEXTS
        and row.get("reversal_return_bps") is not None
    ]
    cost = float(params["cost_buffer_bps"])
    raw_net = [float(row["reversal_return_bps"]) - cost for row in selected]
    cluster_values = clustered_net_values(selected, cost)
    symbols = sorted({str(row["symbol"]) for row in selected})
    by_symbol = {
        symbol: summarize(
            [float(row["reversal_return_bps"]) - cost for row in selected if row["symbol"] == symbol]
        )
        for symbol in symbols
    }
    leave_one_symbol_out = {
        symbol: summarize(clustered_net_values([row for row in selected if row["symbol"] != symbol], cost))
        for symbol in symbols
    }
    symbol_counts = {symbol: sum(row["symbol"] == symbol for row in selected) for symbol in symbols}
    largest_symbol_share = max(symbol_counts.values(), default=0) / len(selected) * 100.0 if selected else None
    contexts = {
        context: sum(1 for row in selected if row.get("dominant_context") == context)
        for context in sorted(DIRECTIONAL_CONTEXTS)
    }
    return {
        "horizon_bars": horizon,
        "records": len(selected),
        "symbols": symbols,
        "symbol_record_counts": symbol_counts,
        "largest_symbol_record_share_pct": round(largest_symbol_share, 3) if largest_symbol_share is not None else None,
        "by_symbol_after_cost": by_symbol,
        "leave_one_symbol_out_cluster_after_cost": leave_one_symbol_out,
        "contexts": contexts,
        "independent_4h_blocks": len(cluster_values),
        "cost_buffer_bps": cost,
        "raw_event_after_cost": summarize(raw_net),
        "cluster_after_cost": summarize(cluster_values),
        "cluster_bootstrap": bootstrap_cluster_mean(
            cluster_values,
            int(params["bootstrap_iterations"]),
            int(params["bootstrap_seed"]) + horizon,
            float(params["confidence_level"]),
        ),
    }


def evaluate_records(records: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    horizons = [evaluate_horizon(records, horizon, params) for horizon in params["horizons"]]
    minimum_blocks = int(params["min_independent_4h_blocks"])
    minimum_symbols = int(params["minimum_symbols_with_events"])
    minimum_context = int(params["min_context_bars"])
    sample_checks = {
        "every_horizon_has_minimum_independent_4h_blocks": all(
            row["independent_4h_blocks"] >= minimum_blocks for row in horizons
        ),
        "every_horizon_has_minimum_symbols": all(len(row["symbols"]) >= minimum_symbols for row in horizons),
        "every_horizon_has_minimum_long_liquidation_flush_records": all(
            row["contexts"].get("long_liquidation_flush", 0) >= minimum_context for row in horizons
        ),
        "every_horizon_has_minimum_short_liquidation_squeeze_records": all(
            row["contexts"].get("short_liquidation_squeeze", 0) >= minimum_context for row in horizons
        ),
    }
    sample_ready = all(sample_checks.values())
    primary = next((row for row in horizons if row["horizon_bars"] == int(params["primary_horizon_bars"])), None)
    positive_horizons = sum(
        row["cluster_after_cost"]["mean_bps"] is not None and row["cluster_after_cost"]["mean_bps"] > 0.0
        for row in horizons
    )
    lower_ci = primary["cluster_bootstrap"]["mean_ci_bps"][0] if primary else None
    primary_mean = primary["cluster_after_cost"]["mean_bps"] if primary else None
    primary_winrate = primary["cluster_after_cost"]["winrate_positive_pct"] if primary else None
    primary_mean_without_symbols = {
        symbol: summary.get("mean_bps")
        for symbol, summary in ((primary or {}).get("leave_one_symbol_out_cluster_after_cost") or {}).items()
    }
    sign_flip_symbols = sorted(
        symbol
        for symbol, mean in primary_mean_without_symbols.items()
        if primary_mean is not None and mean is not None and (primary_mean > 0.0) != (mean > 0.0)
    )
    economic_checks = {
        "primary_cluster_mean_after_cost_positive": primary_mean is not None and primary_mean > 0.0,
        "primary_cluster_winrate_exceeds_locked_threshold": (
            primary_winrate is not None and primary_winrate > float(params["primary_winrate_must_exceed_pct"])
        ),
        "primary_cluster_bootstrap_ci_lower_exceeds_locked_threshold": (
            lower_ci is not None and lower_ci > float(params["primary_cluster_ci_lower_must_exceed_bps"])
        ),
        "minimum_positive_horizons_after_cost": (
            positive_horizons >= int(params["minimum_positive_horizons_after_cost"])
        ),
    }
    if not sample_ready:
        decision = "force_order_cluster_evaluator_waiting_independent_sample"
    elif all(economic_checks.values()):
        decision = str(params["terminal_pass_decision"])
    else:
        decision = str(params["terminal_fail_decision"])
    return {
        "decision": decision,
        "sample_ready": sample_ready,
        "sample_checks": sample_checks,
        "economic_checks_evaluated": sample_ready,
        "economic_checks": economic_checks if sample_ready else {},
        "positive_horizons_after_cost": positive_horizons if sample_ready else None,
        "primary": primary,
        "symbol_concentration_diagnostics": {
            "informational_only_not_a_v3_gate": True,
            "primary_largest_symbol_record_share_pct": (
                primary.get("largest_symbol_record_share_pct") if primary else None
            ),
            "primary_leave_one_symbol_out_mean_bps": primary_mean_without_symbols,
            "primary_sign_flip_symbols": sign_flip_symbols,
        },
        "horizons": horizons,
    }


def contract_errors(
    lock: dict[str, Any],
    lock_path: Path,
    event_report: dict[str, Any],
    records_path: Path,
    records: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    params, errors = locked_study(lock)
    artifacts = event_report.get("artifacts") if isinstance(event_report.get("artifacts"), dict) else {}
    inputs = event_report.get("inputs") if isinstance(event_report.get("inputs"), dict) else {}
    boundary = event_report.get("boundary") if isinstance(event_report.get("boundary"), dict) else {}
    if not lock_path.is_file():
        errors.append("preregistration_lock_missing")
    if event_report.get("decision") != "force_order_event_study_ready_for_review":
        errors.append("event_study_not_ready")
    if event_report.get("can_trade") is not False:
        errors.append("event_study_trade_boundary_invalid")
    if boundary.get("entry_at_next_bar_open") is not True or boundary.get("event_bar_close_fill_forbidden") is not True:
        errors.append("event_study_execution_timing_invalid")
    artifact_path = resolve_path(str(artifacts.get("records_csv") or ""))
    if artifact_path.resolve() != records_path.resolve():
        errors.append("records_artifact_path_mismatch")
    actual_hash = sha256_file(records_path)
    if not actual_hash or actual_hash.lower() != str(artifacts.get("records_csv_sha256") or "").lower():
        errors.append("records_artifact_hash_mismatch")
    if int(artifacts.get("records") or -1) != len(records):
        errors.append("records_artifact_count_mismatch")
    if inputs.get("symbols") != params.get("symbols"):
        errors.append("event_study_symbols_mismatch")
    if inputs.get("horizons_bars") != params.get("horizons"):
        errors.append("event_study_horizons_mismatch")
    if inputs.get("interval") != params.get("interval"):
        errors.append("event_study_interval_mismatch")
    start = parse_ts(params.get("event_start_at"))
    if start is None:
        errors.append("event_start_invalid")
    elif any((parse_ts(row.get("bar_ts")) or datetime.min.replace(tzinfo=timezone.utc)) < start for row in records):
        errors.append("pre_lock_record_present")
    if any(row.get("symbol") not in set(params.get("symbols") or []) for row in records):
        errors.append("record_symbol_outside_lock")
    if any(int(row.get("horizon_bars") or 0) not in set(params.get("horizons") or []) for row in records):
        errors.append("record_horizon_outside_lock")
    return params, sorted(set(errors))


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    lock_path = resolve_path(args.prereg_lock)
    event_report_path = resolve_path(args.event_study_report)
    records_path = resolve_path(args.records_csv)
    lock = read_json(lock_path)
    event_report = read_json(event_report_path)
    records, record_errors = read_records(records_path)
    params, integrity_errors = contract_errors(lock, lock_path, event_report, records_path, records)
    integrity_errors.extend(record_errors)
    integrity_errors = sorted(set(integrity_errors))
    evaluation = evaluate_records(records, params) if not integrity_errors else None
    decision = evaluation["decision"] if evaluation else "force_order_cluster_evaluator_integrity_blocked"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": "tools/liquidation_force_order_cluster_evaluator.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "preregistration": {
            "path": portable(lock_path),
            "lock_id": lock.get("lock_id"),
            "sha256": sha256_file(lock_path),
        },
        "source": {
            "event_study_report": portable(event_report_path),
            "records_csv": portable(records_path),
            "records_csv_sha256": sha256_file(records_path),
            "records": len(records),
        },
        "integrity_errors": integrity_errors,
        "locked_parameters": params,
        "evaluation": evaluation,
        "boundary": {
            "research_evaluator_only": True,
            "cluster_resampling_only": True,
            "parameter_search": False,
            "automatic_promotion": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "next_action": (
            "manual forward-review only; no automatic promotion"
            if decision == params.get("terminal_pass_decision")
            else "record the failed preregistered hypothesis as a tombstone; do not retune this sample"
            if decision == params.get("terminal_fail_decision")
            else "keep collecting untouched independent 4h blocks"
            if decision == "force_order_cluster_evaluator_waiting_independent_sample"
            else "repair provenance or lock integrity before evaluating outcomes"
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    evaluation = report.get("evaluation") or {}
    lines = [
        "# Binance ForceOrder Cluster Evaluator",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Sample ready: `{evaluation.get('sample_ready')}`",
        "- `can_trade=false`",
        "",
        "## Boundary",
        "",
        "- Fixed preregistered reversal hypothesis; no parameter search or automatic promotion.",
        "- Costs are deducted before equal-weight market-wide 4H cluster aggregation.",
        "- Confidence interval is a deterministic nonparametric bootstrap over independent 4H cluster means.",
        "",
        "## Horizons",
        "",
        "| Horizon | Records | Symbols | 4H blocks | Net cluster mean bps | Cluster winrate | CI |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in evaluation.get("horizons") or []:
        lines.append(
            f"| {row.get('horizon_bars')} | {row.get('records')} | {len(row.get('symbols') or [])} | "
            f"{row.get('independent_4h_blocks')} | {row.get('cluster_after_cost', {}).get('mean_bps')} | "
            f"{row.get('cluster_after_cost', {}).get('winrate_positive_pct')} | "
            f"{row.get('cluster_bootstrap', {}).get('mean_ci_bps')} |"
        )
    if report.get("integrity_errors"):
        lines.extend(["", "## Integrity Errors", ""])
        lines.extend(f"- `{item}`" for item in report["integrity_errors"])
    lines.extend(["", "## Next Action", "", f"- {report.get('next_action')}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cluster-aware cost-adjusted evaluator for locked Binance forceOrder records")
    parser.add_argument("--prereg-lock", default="configs/BINANCE_FORCE_ORDER_EVENT_STUDY_PREREG_2026-07-12.json")
    parser.add_argument("--event-study-report", required=True)
    parser.add_argument("--records-csv", required=True)
    parser.add_argument("--out-prefix", default="docs/FORCE_ORDER_LIQUIDATION_CLUSTER_EVALUATION_2026-07-12")
    args = parser.parse_args()
    report = build_report(args)
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "sample_ready": (report.get("evaluation") or {}).get("sample_ready"),
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if report["decision"] == "force_order_cluster_evaluator_integrity_blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
