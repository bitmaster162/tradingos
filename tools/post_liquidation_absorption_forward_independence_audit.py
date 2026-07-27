from __future__ import annotations

import argparse
import hashlib
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

from tools.post_liquidation_absorption_spot_perp_confirmation import build_records


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
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def classify_source_messages(messages: list[str]) -> tuple[list[str], list[str]]:
    exclusions: list[str] = []
    errors: list[str] = []
    for message in messages:
        if message.startswith("row_") and message.endswith(":non_directional_context"):
            exclusions.append(message)
        else:
            errors.append(message)
    return exclusions, errors


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_ts(value: Any) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def nonoverlap_block_id(value: Any, hours: int) -> str:
    seconds = int(parse_ts(value).timestamp())
    width = int(hours) * 3600
    start = seconds - seconds % width
    return datetime.fromtimestamp(start, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean_bps": None, "median_bps": None, "winrate_pct": None, "min_bps": None, "max_bps": None}
    return {
        "n": len(values),
        "mean_bps": round(statistics.fmean(values), 6),
        "median_bps": round(statistics.median(values), 6),
        "winrate_pct": round(100.0 * sum(value > 0 for value in values) / len(values), 3),
        "min_bps": round(min(values), 6),
        "max_bps": round(max(values), 6),
    }


def cluster_values(records: list[dict[str, Any]], cost_bps: float, block_hours: int) -> tuple[list[float], dict[str, list[dict[str, Any]]]]:
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        clusters[nonoverlap_block_id(row["bar_ts"], block_hours)].append(row)
    values = [
        statistics.fmean(float(row["side_forward_bps"]) - cost_bps for row in rows)
        for _, rows in sorted(clusters.items())
    ]
    return values, dict(clusters)


def bootstrap_mean(values: list[float], iterations: int, seed: int) -> dict[str, Any]:
    if not values:
        return {"iterations": iterations, "probability_mean_gt_zero": None, "mean_ci95_bps": [None, None]}
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(statistics.fmean(sample))
    means.sort()
    lower = means[max(0, int(iterations * 0.025) - 1)]
    upper = means[min(iterations - 1, int(iterations * 0.975))]
    return {
        "iterations": iterations,
        "probability_mean_gt_zero": round(sum(value > 0 for value in means) / iterations, 6),
        "mean_ci95_bps": [round(lower, 6), round(upper, 6)],
    }


def horizon_audit(records: list[dict[str, Any]], config: dict[str, Any], horizon: int) -> dict[str, Any]:
    selected = [row for row in records if int(row["horizon_bars"]) == int(horizon)]
    costs = config["execution_realism"]
    block_hours = int(costs["nonoverlap_block_hours"])
    base_cost = float(costs["base_round_trip_cost_bps"])
    stress_cost = float(costs["stress_round_trip_cost_bps"])
    raw = [float(row["side_forward_bps"]) for row in selected]
    base = [value - base_cost for value in raw]
    stress = [value - stress_cost for value in raw]
    base_blocks, clusters = cluster_values(selected, base_cost, block_hours)
    stress_blocks, _ = cluster_values(selected, stress_cost, block_hours)
    symbols = sorted({str(row["symbol"]) for row in selected})
    leave_one_out: dict[str, Any] = {}
    for symbol in symbols:
        subset = [row for row in selected if row["symbol"] != symbol]
        subset_blocks, _ = cluster_values(subset, base_cost, block_hours)
        leave_one_out[symbol] = {
            "raw_events": len(subset),
            "independent_blocks": len(subset_blocks),
            "base_cost_cluster_summary": summarize(subset_blocks),
        }
    unique_bars = len({str(row["bar_ts"]) for row in selected})
    duplicate_event_keys = len(selected) - len({(str(row["symbol"]), str(row["bar_ts"])) for row in selected})
    bootstrap = config["bootstrap"]
    return {
        "horizon_bars": horizon,
        "raw_events": len(selected),
        "symbols": symbols,
        "unique_bars": unique_bars,
        "independent_4h_blocks": len(clusters),
        "duplicate_event_keys": duplicate_event_keys,
        "raw_summary": summarize(raw),
        "base_cost_bps": base_cost,
        "base_cost_raw_summary": summarize(base),
        "base_cost_cluster_summary": summarize(base_blocks),
        "stress_cost_bps": stress_cost,
        "stress_cost_raw_summary": summarize(stress),
        "stress_cost_cluster_summary": summarize(stress_blocks),
        "base_cost_cluster_bootstrap": bootstrap_mean(base_blocks, int(bootstrap["iterations"]), int(bootstrap["seed"]) + horizon),
        "leave_one_symbol_out": leave_one_out,
    }


def validate_contract(config_path: Path, config: dict[str, Any]) -> tuple[bool, list[str]]:
    source_lock_path = resolve_path(config.get("source_lock", ""))
    builder_path = resolve_path(config.get("source_builder", ""))
    source_lock = read_json(source_lock_path)
    selected = config.get("selected_bucket") if isinstance(config.get("selected_bucket"), dict) else {}
    locked_selected = source_lock.get("selected_bucket") if isinstance(source_lock.get("selected_bucket"), dict) else {}
    fixed = locked_selected.get("fixed_conditions") if isinstance(locked_selected.get("fixed_conditions"), dict) else {}
    checks = {
        "fixed_contract": config.get("status") == "fixed_audit_contract",
        "source_lock_hash": source_lock_path.is_file() and sha256_file(source_lock_path).lower() == str(config.get("source_lock_sha256", "")).lower(),
        "builder_hash": builder_path.is_file() and sha256_file(builder_path).lower() == str(config.get("source_builder_sha256", "")).lower(),
        "source_lock_status": source_lock.get("status") == "accepted_forward_observer_lock_not_runtime",
        "source_lock_can_trade_false": source_lock.get("can_trade") is False,
        "source_lock_orders_false": source_lock.get("orders_allowed") is False,
        "selected_setup": selected.get("setup") == locked_selected.get("setup"),
        "selected_side": selected.get("side") == locked_selected.get("side"),
        "selected_interval": selected.get("interval") == locked_selected.get("interval"),
        "horizons": selected.get("horizons_bars") == locked_selected.get("horizons_bars"),
        "after_lock_boundary": str(selected.get("after_bar_ts", "")).replace(".000Z", "Z") == str(source_lock.get("created_at", "")).replace(".000Z", "Z"),
        "absorption_threshold": float(selected.get("absorption_close_location", -1.0)) == float(fixed.get("futures_close_location_min", -2.0)),
        "spot_confirmation_threshold": float(selected.get("spot_confirm_min_bps", -1.0)) == float(fixed.get("spot_minus_perp_event_ret_bps_min", -2.0)),
        "real_feed_required": fixed.get("source_must_be_real_liquidation_feed") is True,
        "spot_confirmed_required": selected.get("spot_confirmed") is True,
        "parameter_search_false": config.get("runtime_boundary", {}).get("parameter_search") is False,
        "orders_false": config.get("runtime_boundary", {}).get("orders_allowed") is False,
        "can_trade_false": config.get("can_trade") is False,
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def source_args(config: dict[str, Any]) -> argparse.Namespace:
    selected = config["selected_bucket"]
    gate = config["review_gate"]
    return argparse.Namespace(
        context_csv=config["context_csv"],
        cache_dir=config["cache_dir"],
        symbols=",".join(selected["symbols"]),
        interval=selected["interval"],
        horizons=",".join(str(item) for item in selected["horizons_bars"]),
        after_bar_ts=selected["after_bar_ts"],
        absorption_close_location=float(selected["absorption_close_location"]),
        spot_confirm_min_bps=float(selected["spot_confirm_min_bps"]),
        min_events_per_bucket=int(gate["minimum_raw_events_per_horizon"]),
        min_mean_bps=0.0,
        min_winrate_pct=0.0,
    )


def build_report(config_path: Path) -> dict[str, Any]:
    config = read_json(config_path)
    contract_ok, failures = validate_contract(config_path, config)
    if not contract_ok:
        return {
            "generated_at": now_iso(),
            "decision": "post_liq_independence_audit_integrity_blocked",
            "contract_failures": failures,
            "can_trade": False,
        }
    records, source_messages, inputs = build_records(source_args(config))
    source_exclusions, source_errors = classify_source_messages(source_messages)
    selected = config["selected_bucket"]
    filtered = [
        row
        for row in records
        if row.get("setup") == selected["setup"]
        and bool(row.get("spot_confirmed")) is bool(selected["spot_confirmed"])
        and row.get("side") == selected["side"]
    ]
    audits = [horizon_audit(filtered, config, int(horizon)) for horizon in selected["horizons_bars"]]
    gate = config["review_gate"]
    sample_checks = {
        "minimum_raw_events": all(row["raw_events"] >= int(gate["minimum_raw_events_per_horizon"]) for row in audits),
        "minimum_symbols": all(len(row["symbols"]) >= int(gate["minimum_symbols"]) for row in audits),
        "minimum_independent_blocks": all(row["independent_4h_blocks"] >= int(gate["minimum_independent_blocks_per_horizon"]) for row in audits),
        "duplicates_zero": all(row["duplicate_event_keys"] == 0 for row in audits),
        "source_errors_zero": not source_errors,
    }
    observed_positive_base = sum((row["base_cost_cluster_summary"]["mean_bps"] or -math.inf) > 0 for row in audits)
    observed_positive_stress = sum((row["stress_cost_cluster_summary"]["mean_bps"] or -math.inf) > 0 for row in audits)
    sample_ready = all(sample_checks.values())
    cost_checks = {
        "minimum_positive_horizons_after_base_cost": observed_positive_base >= int(gate["minimum_positive_horizons_after_base_cost"]),
        "minimum_positive_horizons_after_stress_cost": observed_positive_stress >= int(gate["minimum_positive_horizons_after_stress_cost"]),
    }
    eligible_for_manual_review = sample_ready and all(cost_checks.values())
    if not sample_ready:
        decision = "post_liq_independence_audit_waiting_independent_sample"
    elif eligible_for_manual_review:
        decision = "post_liq_independence_audit_sample_ready_for_manual_review"
    else:
        decision = "post_liq_independence_audit_sample_ready_but_cost_gate_failed"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "audit_id": config.get("audit_id"),
        "decision": decision,
        "source_lock_verified": True,
        "selected_bucket": selected,
        "source_inputs": inputs,
        "source_exclusions": source_exclusions,
        "source_errors": source_errors,
        "records_reconstructed": len(records),
        "selected_records": len(filtered),
        "horizons": audits,
        "sample_checks": sample_checks,
        "sample_ready": sample_ready,
        "cost_checks": cost_checks,
        "observed_positive_horizons_after_base_cost": observed_positive_base,
        "observed_positive_horizons_after_stress_cost": observed_positive_stress,
        "positive_horizon_gate_evaluated": sample_ready,
        "eligible_for_manual_review": eligible_for_manual_review,
        "automatic_promotion_allowed": False,
        "runtime_boundary": config["runtime_boundary"],
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Post-Liquidation Forward Independence Audit",
        "",
        f"Decision: `{report.get('decision')}`",
        f"Generated: `{report.get('generated_at')}`",
        f"Can trade: `{report.get('can_trade')}`",
        "",
        f"Sample ready: `{report.get('sample_ready')}`",
        f"Eligible for manual review: `{report.get('eligible_for_manual_review')}`",
        f"Source exclusions/errors: `{len(report.get('source_exclusions') or [])}` / `{len(report.get('source_errors') or [])}`",
        "",
        "| Horizon | Raw | Symbols | Independent blocks | Base mean | Stress mean | Bootstrap P(mean>0) | 95% CI |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("horizons") or []:
        lines.append(
            f"| {row.get('horizon_bars')} | {row.get('raw_events')} | {len(row.get('symbols') or [])} | "
            f"{row.get('independent_4h_blocks')} | "
            f"{row.get('base_cost_cluster_summary', {}).get('mean_bps')} | "
            f"{row.get('stress_cost_cluster_summary', {}).get('mean_bps')} | "
            f"{row.get('base_cost_cluster_bootstrap', {}).get('probability_mean_gt_zero')} | "
            f"{row.get('base_cost_cluster_bootstrap', {}).get('mean_ci95_bps')} |"
        )
    lines.extend(
        [
            "",
            "The audit clusters correlated symbols and temporally overlapping events before evaluating costs. It does not modify the locked observer and cannot promote or trade.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cluster-aware audit for the locked post-liquidation forward observer")
    parser.add_argument("--config", default="configs/POST_LIQUIDATION_ABSORPTION_FORWARD_INDEPENDENCE_AUDIT_2026-07-12.json")
    parser.add_argument("--out-prefix", default="docs/POST_LIQUIDATION_ABSORPTION_FORWARD_INDEPENDENCE_AUDIT_2026-07-12")
    args = parser.parse_args()
    config_path = resolve_path(args.config)
    out_prefix = resolve_path(args.out_prefix)
    report = build_report(config_path)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report.get("decision"), "sample_ready": report.get("sample_ready"), "can_trade": False}, indent=2))
    return 0 if report.get("decision") != "post_liq_independence_audit_integrity_blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
