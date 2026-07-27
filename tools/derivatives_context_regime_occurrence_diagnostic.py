#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.derivatives_context_composite_miner import (  # noqa: E402
    CompositeConfig,
    build_configs,
    diversified_limit,
    load_interval,
    parse_int_grid,
    resolve_path,
)
from tools.derivatives_context_signal_frequency_diagnostic import count_shape, dedupe_entry_configs  # noqa: E402
from tools.derivatives_event_edge_miner import split_index  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def full_count(row: dict[str, Any], window: str) -> int:
    return int(row["windows"].get(window, {}).get("full_intersection") or 0)


def classify_row(row: dict[str, Any]) -> str:
    train = full_count(row, "train")
    validation = full_count(row, "validation")
    oos = full_count(row, "oos")
    if train <= 0 and validation <= 0 and oos <= 0:
        return "never_occurs"
    if train > 0 and validation <= 0 and oos <= 0:
        return "train_only_decay"
    if train > 0 and validation <= 0 and oos > 0:
        return "validation_gap_oos_reappears"
    if validation > 0:
        return "validation_frequency_present"
    if train <= 0 and validation <= 0 and oos > 0:
        return "oos_only_future_occurrence"
    return "other"


def analyze_window_decay(
    configs: list[CompositeConfig],
    features_by_interval: dict[str, dict[int, dict[int, dict[str, float]]]],
    windows_by_interval: dict[str, dict[str, tuple[int, int]]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for config in dedupe_entry_configs(configs):
        features = features_by_interval.get(config.interval, {})
        windows = windows_by_interval.get(config.interval, {})
        counts_by_window = {
            name: count_shape(config, features, start_index=start, end_index=end)
            for name, (start, end) in windows.items()
        }
        row = {"strategy_id": config.strategy_id, "config": asdict(config), "windows": counts_by_window}
        row["classification"] = classify_row(row)
        rows.append(row)

    class_counts: dict[str, int] = {}
    for row in rows:
        classification = row["classification"]
        class_counts[classification] = class_counts.get(classification, 0) + 1

    train_positive = [row for row in rows if full_count(row, "train") > 0]
    validation_positive = [row for row in rows if full_count(row, "validation") > 0]
    oos_positive = [row for row in rows if full_count(row, "oos") > 0]
    train_only_decay = [row for row in rows if row["classification"] == "train_only_decay"]

    def sort_decay(row: dict[str, Any]) -> tuple[int, int, int]:
        return (
            full_count(row, "train"),
            int(row["windows"].get("train", {}).get("event_and_context") or 0),
            int(row["windows"].get("train", {}).get("derivative_event") or 0),
        )

    def sort_validation(row: dict[str, Any]) -> tuple[int, int, int]:
        return (
            full_count(row, "validation"),
            full_count(row, "train"),
            int(row["windows"].get("validation", {}).get("event_and_context") or 0),
        )

    validation_survival_rate = (len(validation_positive) / len(train_positive) * 100.0) if train_positive else 0.0
    return {
        "unique_entry_shapes": len(rows),
        "classification_counts": class_counts,
        "train_positive_shapes": len(train_positive),
        "validation_positive_shapes": len(validation_positive),
        "oos_positive_shapes": len(oos_positive),
        "train_only_decay_shapes": len(train_only_decay),
        "validation_survival_rate_pct": round(validation_survival_rate, 3),
        "top_train_only_decay": sorted(train_only_decay, key=sort_decay, reverse=True)[:30],
        "top_validation_frequency": sorted(validation_positive, key=sort_validation, reverse=True)[:30],
        "top_train_frequency": sorted(rows, key=sort_decay, reverse=True)[:30],
    }


def render_counts(counts: dict[str, Any]) -> str:
    return (
        f"full={counts.get('full_intersection')} "
        f"event={counts.get('derivative_event')} "
        f"regime={counts.get('regime')} "
        f"context={counts.get('context')} "
        f"event+context={counts.get('event_and_context')}"
    )


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Derivatives Context Regime Occurrence Diagnostic",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Can trade: `{report.get('can_trade')}`",
        "",
        "## Summary",
        "",
        f"- Unique entry shapes: `{summary.get('unique_entry_shapes')}`.",
        f"- Train-positive shapes: `{summary.get('train_positive_shapes')}`.",
        f"- Validation-positive shapes: `{summary.get('validation_positive_shapes')}`.",
        f"- OOS-positive shapes: `{summary.get('oos_positive_shapes')}`.",
        f"- Train-only decay shapes: `{summary.get('train_only_decay_shapes')}`.",
        f"- Validation survival rate: `{summary.get('validation_survival_rate_pct')}`%.",
        f"- Classifications: `{summary.get('classification_counts')}`.",
        "",
        "## Top Train-Only Decay",
        "",
        "| strategy | train | validation | oos | classification |",
        "|---|---|---|---|---|",
    ]
    for row in report.get("top_train_only_decay", [])[:15]:
        windows = row.get("windows", {})
        lines.append(
            "| "
            f"`{row.get('strategy_id')}` | "
            f"{render_counts(windows.get('train', {}))} | "
            f"{render_counts(windows.get('validation', {}))} | "
            f"{render_counts(windows.get('oos', {}))} | "
            f"{row.get('classification')} |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Occurrence/frequency diagnostic only.",
            "- No PnL simulation.",
            "- No candidate promotion.",
            "- No paper/live execution.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose train/validation/OOS occurrence decay for derivatives/context entry shapes.")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="1h,4h")
    parser.add_argument("--families", default="funding_extreme_fade")
    parser.add_argument("--sides", default="SHORT")
    parser.add_argument("--regime-filters", default="none")
    parser.add_argument("--context-modes", default="spot_confirm,sweep_confirm")
    parser.add_argument("--train-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2026-01-01T00:00:00+00:00")
    parser.add_argument("--lookbacks", default="6,12")
    parser.add_argument("--price-atr", default="0.4,0.6")
    parser.add_argument("--oi-pct", default="0.15,0.25")
    parser.add_argument("--funding-abs", default="0.0001,0.0002")
    parser.add_argument("--close-location", default="0.55,0.65")
    parser.add_argument("--spot-divergence-pct", default="0.02,0.05")
    parser.add_argument("--spot-volume-ratio", default="0.5")
    parser.add_argument("--sweep-lookback", default="12,24")
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", default="1.5,2.0,3.0")
    parser.add_argument("--max-hold-bars", default="8,16")
    parser.add_argument("--max-configs-per-interval", type=int, default=240)
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_CONTEXT_REGIME_OCCURRENCE_DIAGNOSTIC_2026-06-29")
    args = parser.parse_args()

    cache_dir = resolve_path(args.cache_dir)
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    lookbacks = tuple(parse_int_grid(args.lookbacks))
    sweep_lookbacks = tuple(parse_int_grid(args.sweep_lookback))
    configs: list[CompositeConfig] = []
    features_by_interval: dict[str, dict[int, dict[int, dict[str, float]]]] = {}
    windows_by_interval: dict[str, dict[str, tuple[int, int]]] = {}
    data: list[dict[str, Any]] = []

    for interval in intervals:
        rows, features, meta = load_interval(cache_dir, interval, lookbacks, sweep_lookbacks)
        data.append(meta)
        if not rows or not features:
            continue
        train_end = split_index(rows, args.train_end)
        validation_end = split_index(rows, args.validation_end)
        features_by_interval[interval] = features
        windows_by_interval[interval] = {
            "train": (0, train_end),
            "validation": (train_end, validation_end),
            "oos": (validation_end, len(rows)),
        }
        configs.extend(diversified_limit(build_configs(args, interval), max(1, args.max_configs_per_interval)))

    analysis = analyze_window_decay(configs, features_by_interval, windows_by_interval)
    decision = "validation_occurrence_decay_detected"
    if analysis["validation_positive_shapes"] > 0:
        decision = "validation_occurrence_present_requires_pnl_gate"
    if analysis["train_positive_shapes"] == 0:
        decision = "no_train_occurrence"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "settings": {
            "intervals": intervals,
            "families": args.families,
            "sides": args.sides,
            "regime_filters": args.regime_filters,
            "context_modes": args.context_modes,
            "train_end": args.train_end,
            "validation_end": args.validation_end,
            "max_configs_per_interval": args.max_configs_per_interval,
        },
        "data": data,
        "summary": {
            "unique_entry_shapes": analysis["unique_entry_shapes"],
            "classification_counts": analysis["classification_counts"],
            "train_positive_shapes": analysis["train_positive_shapes"],
            "validation_positive_shapes": analysis["validation_positive_shapes"],
            "oos_positive_shapes": analysis["oos_positive_shapes"],
            "train_only_decay_shapes": analysis["train_only_decay_shapes"],
            "validation_survival_rate_pct": analysis["validation_survival_rate_pct"],
        },
        "top_train_only_decay": analysis["top_train_only_decay"],
        "top_validation_frequency": analysis["top_validation_frequency"],
        "top_train_frequency": analysis["top_train_frequency"],
        "runtime_boundary": {
            "occurrence_diagnostic_only": True,
            "pnl_simulation": False,
            "oos_selection": False,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "summary": report["summary"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
