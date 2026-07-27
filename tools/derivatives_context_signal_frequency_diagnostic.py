#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.derivatives_context_composite_miner import (  # noqa: E402
    CompositeConfig,
    base_config,
    build_configs,
    context_matches,
    diversified_limit,
    load_interval,
    parse_int_grid,
    resolve_path,
)
from tools.derivatives_event_edge_miner import regime_matches, signal_matches, split_index  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def entry_key(config: CompositeConfig) -> tuple[Any, ...]:
    return (
        config.family,
        config.side,
        config.interval,
        config.lookback,
        config.price_atr,
        config.oi_pct,
        config.funding_abs,
        config.volume_z,
        config.close_location,
        config.regime_filter,
        config.context_mode,
        config.spot_divergence_pct,
        config.spot_volume_ratio,
        config.sweep_lookback,
    )


def dedupe_entry_configs(configs: list[CompositeConfig]) -> list[CompositeConfig]:
    seen: set[tuple[Any, ...]] = set()
    output: list[CompositeConfig] = []
    for config in configs:
        key = entry_key(config)
        if key in seen:
            continue
        seen.add(key)
        output.append(config)
    return output


def count_shape(
    config: CompositeConfig,
    features: dict[int, dict[int, dict[str, float]]],
    *,
    start_index: int,
    end_index: int,
) -> dict[str, Any]:
    no_regime = replace(config, regime_filter="none")
    event_config = base_config(config)
    no_regime_event_config = base_config(no_regime)
    counts = {
        "feature_bars": 0,
        "derivative_event": 0,
        "regime": 0,
        "context": 0,
        "event_and_regime": 0,
        "event_and_context": 0,
        "full_intersection": 0,
    }
    for index in range(start_index, end_index):
        by_lookback = features.get(index)
        if not by_lookback or config.lookback not in by_lookback:
            continue
        feature = by_lookback[config.lookback]
        counts["feature_bars"] += 1
        event_ok = signal_matches(no_regime_event_config, feature)
        regime_ok = regime_matches(event_config, feature)
        context_ok = context_matches(config, feature)
        if event_ok:
            counts["derivative_event"] += 1
        if regime_ok:
            counts["regime"] += 1
        if context_ok:
            counts["context"] += 1
        if event_ok and regime_ok:
            counts["event_and_regime"] += 1
        if event_ok and context_ok:
            counts["event_and_context"] += 1
        if event_ok and regime_ok and context_ok:
            counts["full_intersection"] += 1
    bottleneck = "passes_frequency_smoke"
    if counts["derivative_event"] == 0:
        bottleneck = "derivative_event_zero"
    elif counts["context"] == 0:
        bottleneck = "context_zero"
    elif counts["regime"] == 0:
        bottleneck = "regime_zero"
    elif counts["event_and_context"] == 0:
        bottleneck = "context_kills_event"
    elif counts["event_and_regime"] == 0:
        bottleneck = "regime_kills_event"
    elif counts["full_intersection"] == 0:
        bottleneck = "three_way_intersection_zero"
    return {**counts, "bottleneck": bottleneck}


def analyze_configs(
    configs: list[CompositeConfig],
    features_by_interval: dict[str, dict[int, dict[int, dict[str, float]]]],
    windows_by_interval: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    unique_configs = dedupe_entry_configs(configs)
    rows: list[dict[str, Any]] = []
    for config in unique_configs:
        features = features_by_interval.get(config.interval, {})
        start_index, end_index = windows_by_interval.get(config.interval, (0, 0))
        counts = count_shape(config, features, start_index=start_index, end_index=end_index)
        rows.append({"strategy_id": config.strategy_id, "config": asdict(config), "counts": counts})

    bottlenecks: dict[str, int] = {}
    for row in rows:
        key = row["counts"]["bottleneck"]
        bottlenecks[key] = bottlenecks.get(key, 0) + 1

    def sort_key(field: str) -> Any:
        return lambda row: (
            row["counts"].get(field) or 0,
            row["counts"].get("event_and_context") or 0,
            row["counts"].get("derivative_event") or 0,
        )

    return {
        "unique_entry_shapes": len(rows),
        "bottleneck_counts": bottlenecks,
        "top_full_intersection": sorted(rows, key=sort_key("full_intersection"), reverse=True)[:25],
        "top_event_and_context": sorted(rows, key=sort_key("event_and_context"), reverse=True)[:25],
        "top_derivative_event": sorted(rows, key=sort_key("derivative_event"), reverse=True)[:25],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Derivatives Context Signal Frequency Diagnostic",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Can trade: `{report.get('can_trade')}`",
        "",
        "## Summary",
        "",
        f"- Unique entry shapes: `{report.get('summary', {}).get('unique_entry_shapes')}`.",
        f"- Bottlenecks: `{report.get('summary', {}).get('bottleneck_counts')}`.",
        "",
        "## Top Full Intersections",
        "",
        "| strategy | full | event | regime | context | event+regime | event+context | bottleneck |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report.get("top_full_intersection", [])[:15]:
        counts = row.get("counts", {})
        lines.append(
            "| "
            f"`{row.get('strategy_id')}` | "
            f"{counts.get('full_intersection')} | "
            f"{counts.get('derivative_event')} | "
            f"{counts.get('regime')} | "
            f"{counts.get('context')} | "
            f"{counts.get('event_and_regime')} | "
            f"{counts.get('event_and_context')} | "
            f"{counts.get('bottleneck')} |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Frequency diagnostic only.",
            "- No PnL simulation.",
            "- No OOS opening.",
            "- No observer, paper, or live permission.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose why derivatives/context composite entry shapes are too sparse.")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="1h,4h")
    parser.add_argument("--families", default="funding_extreme_fade")
    parser.add_argument("--sides", default="LONG,SHORT")
    parser.add_argument("--regime-filters", default="ema200_slope,ema50_stack")
    parser.add_argument("--context-modes", default="composite2")
    parser.add_argument("--train-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--lookbacks", default="6,12")
    parser.add_argument("--price-atr", default="0.4,0.6")
    parser.add_argument("--oi-pct", default="0.15,0.25")
    parser.add_argument("--funding-abs", default="0.0001,0.0002")
    parser.add_argument("--close-location", default="0.55,0.65")
    parser.add_argument("--spot-divergence-pct", default="0.02,0.05")
    parser.add_argument("--spot-volume-ratio", default="0.5,0.8")
    parser.add_argument("--sweep-lookback", default="12,24")
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", default="1.5,2.0")
    parser.add_argument("--max-hold-bars", default="8,16")
    parser.add_argument("--max-configs-per-interval", type=int, default=320)
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_CONTEXT_SIGNAL_FREQUENCY_DIAGNOSTIC_2026-06-29")
    args = parser.parse_args()

    cache_dir = resolve_path(args.cache_dir)
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    lookbacks = tuple(parse_int_grid(args.lookbacks))
    sweep_lookbacks = tuple(parse_int_grid(args.sweep_lookback))
    configs: list[CompositeConfig] = []
    features_by_interval: dict[str, dict[int, dict[int, dict[str, float]]]] = {}
    windows_by_interval: dict[str, tuple[int, int]] = {}
    data: list[dict[str, Any]] = []

    for interval in intervals:
        rows, features, meta = load_interval(cache_dir, interval, lookbacks, sweep_lookbacks)
        data.append(meta)
        if not rows or not features:
            continue
        train_end = split_index(rows, args.train_end)
        features_by_interval[interval] = features
        windows_by_interval[interval] = (0, train_end)
        configs.extend(diversified_limit(build_configs(args, interval), max(1, args.max_configs_per_interval)))

    analysis = analyze_configs(configs, features_by_interval, windows_by_interval)
    full_positive = [row for row in analysis["top_full_intersection"] if row["counts"].get("full_intersection", 0) > 0]
    decision = "frequency_smoke_has_full_intersections" if full_positive else "frequency_smoke_zero_full_intersections"
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
            "max_configs_per_interval": args.max_configs_per_interval,
        },
        "data": data,
        "summary": {
            "unique_entry_shapes": analysis["unique_entry_shapes"],
            "bottleneck_counts": analysis["bottleneck_counts"],
            "full_positive_shapes": len(full_positive),
        },
        "top_full_intersection": analysis["top_full_intersection"],
        "top_event_and_context": analysis["top_event_and_context"],
        "top_derivative_event": analysis["top_derivative_event"],
        "runtime_boundary": {
            "frequency_diagnostic_only": True,
            "pnl_simulation": False,
            "oos_opened": False,
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
                "unique_entry_shapes": analysis["unique_entry_shapes"],
                "full_positive_shapes": len(full_positive),
                "bottlenecks": analysis["bottleneck_counts"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
