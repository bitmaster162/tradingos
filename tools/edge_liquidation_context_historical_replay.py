#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.edge_liquidation_context_shadow_observer import classify_context, continuous_score, thresholds_from_lock  # noqa: E402
from tools.liquidity_sweep_hardening import max_drawdown, max_losing_streak  # noqa: E402
from tools.max_backtest import candle_value  # noqa: E402
from tools.max_v11_candidate_validator import atr14_at  # noqa: E402
from tools.range_edge_nested_holdout import find_split_index, now_iso, window_signals  # noqa: E402
from tools.range_family_validator import RangeConfig, load_interval_payload, replay_signals  # noqa: E402
from tools.range_watchlist_refiner import apply_filter_mode  # noqa: E402


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def context_feature_at(
    rows: list[dict[str, str]],
    derivatives_by_time: dict[str, dict[str, str]],
    index: int,
) -> dict[str, float] | None:
    if index < 100:
        return None
    current = derivatives_by_time.get(str(rows[index].get("time")))
    previous = derivatives_by_time.get(str(rows[index - 3].get("time")))
    if not current or not previous:
        return None
    try:
        oi = float(current["open_interest"])
        previous_oi = float(previous["open_interest"])
    except (KeyError, TypeError, ValueError):
        return None
    atr = atr14_at(rows, index)
    if not math.isfinite(atr) or atr <= 0 or previous_oi == 0:
        return None
    close = candle_value(rows[index], "close")
    previous_close = candle_value(rows[index - 3], "close")
    high = candle_value(rows[index], "high")
    low = candle_value(rows[index], "low")
    volume = candle_value(rows[index], "volume")
    volumes = [candle_value(row, "volume") for row in rows[index - 100 : index]]
    sigma = statistics.pstdev(volumes)
    return {
        "displacement_atr": (close - previous_close) / atr,
        "oi_delta_pct": (oi - previous_oi) / previous_oi * 100.0,
        "volume_z": (volume - statistics.mean(volumes)) / sigma if sigma > 0 else 0.0,
        "close_location": (close - low) / max(high - low, 1e-12),
    }


def build_context_events(
    rows: list[dict[str, str]],
    derivatives: list[dict[str, str]],
    thresholds: dict[str, float],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    derivatives_by_time = {str(row.get("time")): row for row in derivatives}
    events: list[dict[str, Any]] = []
    unavailable = 0
    for index, row in enumerate(rows):
        feature = context_feature_at(rows, derivatives_by_time, index)
        if feature is None:
            unavailable += 1
            continue
        context = classify_context(
            feature,
            displacement_threshold=thresholds["displacement_atr"],
            oi_drop_threshold=thresholds["oi_drop_pct"],
            volume_z_threshold=thresholds["volume_z"],
        )
        score = continuous_score(feature, thresholds)
        events.append(
            {
                "bar_ts": row.get("time"),
                "context": context,
                "context_score": round(score, 6),
                **{name: round(float(value), 6) for name, value in feature.items()},
            }
        )
    return events, {
        "price_rows": len(rows),
        "derivative_rows": len(derivatives),
        "context_rows": len(events),
        "unavailable_rows": unavailable,
        "coverage_pct": round(len(events) / len(rows) * 100.0, 3) if rows else 0.0,
    }


def aggregate_context(signal_bar_ts: str, contexts: list[dict[str, Any]]) -> dict[str, Any]:
    from tools.liquidation_impulse_reversal_nested_holdout import parse_ts

    start = parse_ts(signal_bar_ts)
    end = start + timedelta(hours=4)
    eligible = [row for row in contexts if start <= parse_ts(str(row["bar_ts"])) < end]
    if not eligible:
        return {"latest": "unknown", "strongest": "unknown", "strongest_bar_ts": None, "hours": 0}
    latest = max(eligible, key=lambda row: parse_ts(str(row["bar_ts"])))
    non_none = [row for row in eligible if row.get("context") != "none"]
    strongest = max(
        non_none or eligible,
        key=lambda row: (float(row.get("context_score") or 0.0), parse_ts(str(row["bar_ts"]))),
    )
    return {
        "latest": latest.get("context"),
        "latest_bar_ts": latest.get("bar_ts"),
        "strongest": strongest.get("context"),
        "strongest_bar_ts": strongest.get("bar_ts"),
        "strongest_score": strongest.get("context_score"),
        "hours": len(eligible),
    }


def summarize(rows: list[dict[str, Any]], field: str = "r") -> dict[str, Any]:
    values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(values) * 100.0, 3) if values else None,
        "expectancy_r": round(sum(values) / len(values), 6) if values else None,
        "net_r_total": round(sum(values), 6) if values else 0.0,
        "max_drawdown_r": max_drawdown(values),
        "max_losing_streak": max_losing_streak(values),
    }


def grouped(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(label) or "unknown")].append(row)
    return {
        name: {"base": summarize(items), "cost10": summarize(items, "r_cost10")}
        for name, items in sorted(groups.items())
    }


def evidence_verdict(train: list[dict[str, Any]], oos: list[dict[str, Any]], min_oos_group: int) -> dict[str, Any]:
    train_groups = grouped(train, "strongest_context")
    oos_groups = grouped(oos, "strongest_context")
    informative = [
        name
        for name, row in oos_groups.items()
        if name not in {"none", "unknown"} and int(row["base"]["trades"]) >= min_oos_group
    ]
    repeated: list[str] = []
    for name in informative:
        train_exp = train_groups.get(name, {}).get("base", {}).get("expectancy_r")
        oos_exp = oos_groups.get(name, {}).get("base", {}).get("expectancy_r")
        oos_stress = oos_groups.get(name, {}).get("cost10", {}).get("expectancy_r")
        if all(isinstance(value, (int, float)) and value > 0 for value in (train_exp, oos_exp, oos_stress)):
            repeated.append(name)
    if not informative:
        verdict = "insufficient_oos_context_subgroup_sample"
    elif not repeated:
        verdict = "no_repeatable_positive_context_separation"
    else:
        verdict = "repeatable_context_signal_requires_new_precommitted_holdout"
    return {
        "classification": verdict,
        "min_oos_group": min_oos_group,
        "informative_oos_contexts": informative,
        "repeatable_positive_contexts": repeated,
        "recommended_filter_change": False,
        "requires_new_precommitted_holdout": bool(repeated),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Edge Liquidation Context Historical Replay",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Frozen Edge parameters and fixed liquidation thresholds only.",
        "- OOS is used for evaluation, never for reselection or runtime mutation.",
        "- No filter, veto, Telegram message, paper intent, credentials, or orders.",
        "",
        "## Reproduction",
        "",
        f"- Frozen candidate: `{report['candidate']['strategy_id']}`.",
        f"- Expected/replayed train trades: `{report['reproduction']['expected_train_trades']}` / `{report['reproduction']['actual_train_trades']}`.",
        f"- Expected/replayed OOS trades: `{report['reproduction']['expected_oos_trades']}` / `{report['reproduction']['actual_oos_trades']}`.",
        f"- Exact count match: `{report['reproduction']['exact_trade_count_match']}`.",
        "",
        "## Evidence",
        "",
        f"- Train baseline: `{report['windows']['train']['baseline']['trades']}` trades, `{report['windows']['train']['baseline']['expectancy_r']}`R expectancy.",
        f"- OOS baseline: `{report['windows']['oos']['baseline']['trades']}` trades, `{report['windows']['oos']['baseline']['expectancy_r']}`R expectancy.",
        f"- Classification: `{report['evidence_gate']['classification']}`.",
        "",
    ]
    for window in ("train", "oos"):
        lines.extend([f"### {window.upper()} strongest event in signal bar", "", "| Context | Trades | Winrate | Exp R | Cost10 Exp R |", "|---|---:|---:|---:|---:|"])
        for name, row in report["windows"][window]["by_strongest_context"].items():
            lines.append(f"| `{name}` | `{row['base']['trades']}` | `{row['base']['winrate_pct']}` | `{row['base']['expectancy_r']}` | `{row['cost10']['expectancy_r']}` |")
        lines.append("")
    lines.extend(
        [
            "## Decision",
            "",
            f"- `{report['decision']}`.",
            "- `recommended_filter_change=false`.",
            "- `can_trade=false`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay frozen Edge outcomes against fixed liquidation/OI context labels")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--edge-lock", default="configs/EDGE_FORWARD_LOCK.json")
    parser.add_argument("--edge-source-report", default="docs/RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json")
    parser.add_argument("--liquidation-lock", default="configs/LIQUIDATION_IMPULSE_CONTINUATION_RESEARCH_LOCK.json")
    parser.add_argument("--split-ts", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--cost-bps-per-side", type=float, default=7.0)
    parser.add_argument("--cost-stress-extra-bps", type=float, default=10.0)
    parser.add_argument("--min-oos-context-group", type=int, default=8)
    parser.add_argument("--out-prefix", default="docs/EDGE_LIQUIDATION_CONTEXT_HISTORICAL_REPLAY_2026-06-23")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    edge_lock = read_json(resolve_path(args.edge_lock))
    source_report = read_json(resolve_path(args.edge_source_report))
    candidate_lock = edge_lock["candidate"]
    source_family = next(row for row in source_report["families"] if row.get("family") == "EDGE_FORWARD_4H")
    config = RangeConfig(**source_family["selected_on_train"]["config"])
    if config.strategy_id != candidate_lock.get("strategy_id"):
        raise ValueError("edge lock and source report candidate differ")

    bars, features, rsi14 = load_interval_payload(cache, "4h", 12, 12)
    split_index = find_split_index(bars, args.split_ts)
    train_base = window_signals(config, bars, features, rsi14, start_index=0, end_index=split_index - config.max_hold_bars - 1)
    oos_base = window_signals(config, bars, features, rsi14, start_index=split_index, end_index=len(bars))
    filters = tuple(candidate_lock.get("filters") or [])
    train_signals = apply_filter_mode(config, train_base, filters)
    oos_signals = apply_filter_mode(config, oos_base, filters)
    train_trades = replay_signals(config, bars, train_signals, args.cost_bps_per_side, True)
    oos_trades = replay_signals(config, bars, oos_signals, args.cost_bps_per_side, True)
    train_stress = {trade.entry_ts: trade for trade in replay_signals(config, bars, train_signals, args.cost_bps_per_side + args.cost_stress_extra_bps, True)}
    oos_stress = {trade.entry_ts: trade for trade in replay_signals(config, bars, oos_signals, args.cost_bps_per_side + args.cost_stress_extra_bps, True)}
    signal_ts_by_entry = {
        bars[int(signal["bar_index"]) + 1].ts: bars[int(signal["bar_index"])].ts
        for signal in train_signals + oos_signals
        if int(signal["bar_index"]) + 1 < len(bars)
    }

    one_hour_rows = read_csv(cache / "futures" / "BTCUSDT" / "1h_klines.csv")
    one_hour_derivatives = read_csv(cache / "futures" / "BTCUSDT" / "1h_oi_aligned.csv")
    thresholds = thresholds_from_lock(resolve_path(args.liquidation_lock))
    contexts, context_coverage = build_context_events(one_hour_rows, one_hour_derivatives, thresholds)

    def label_trades(trades: list[Any], stress: dict[str, Any], window: str) -> list[dict[str, Any]]:
        labelled: list[dict[str, Any]] = []
        for trade in trades:
            signal_ts = signal_ts_by_entry.get(trade.entry_ts)
            aggregate = aggregate_context(signal_ts, contexts) if signal_ts else {"latest": "unknown", "strongest": "unknown", "hours": 0}
            labelled.append(
                {
                    "window": window,
                    "signal_bar_ts": signal_ts,
                    "entry_ts": trade.entry_ts,
                    "exit_ts": trade.exit_ts,
                    "r": trade.r_net,
                    "r_cost10": stress[trade.entry_ts].r_net if trade.entry_ts in stress else None,
                    "latest_context": aggregate.get("latest"),
                    "strongest_context": aggregate.get("strongest"),
                    "strongest_context_bar_ts": aggregate.get("strongest_bar_ts"),
                    "strongest_context_score": aggregate.get("strongest_score"),
                    "context_hours": aggregate.get("hours"),
                }
            )
        return labelled

    train_rows = label_trades(train_trades, train_stress, "train")
    oos_rows = label_trades(oos_trades, oos_stress, "oos")
    expected_train = int(edge_lock["selection"]["train_trades"])
    expected_oos = int(edge_lock["selection"]["oos_trades"])
    reproduction = {
        "expected_train_trades": expected_train,
        "actual_train_trades": len(train_rows),
        "expected_oos_trades": expected_oos,
        "actual_oos_trades": len(oos_rows),
        "exact_trade_count_match": len(train_rows) == expected_train and len(oos_rows) == expected_oos,
    }
    gate = evidence_verdict(train_rows, oos_rows, args.min_oos_context_group)
    decision = gate["classification"] if reproduction["exact_trade_count_match"] else "blocked_edge_reproduction_mismatch"
    report = {
        "generated_at": now_iso(),
        "method": "frozen_edge_replay_with_fixed_causal_1h_liquidation_context",
        "candidate": asdict(config),
        "thresholds": thresholds,
        "data": {
            "cache_dir": rel(cache),
            "four_hour_bars": len(bars),
            "one_hour_context_coverage": context_coverage,
            "split_ts": args.split_ts,
        },
        "reproduction": reproduction,
        "windows": {
            "train": {
                "baseline": summarize(train_rows),
                "baseline_cost10": summarize(train_rows, "r_cost10"),
                "by_latest_context": grouped(train_rows, "latest_context"),
                "by_strongest_context": grouped(train_rows, "strongest_context"),
            },
            "oos": {
                "baseline": summarize(oos_rows),
                "baseline_cost10": summarize(oos_rows, "r_cost10"),
                "by_latest_context": grouped(oos_rows, "latest_context"),
                "by_strongest_context": grouped(oos_rows, "strongest_context"),
            },
        },
        "evidence_gate": gate,
        "labelled_trades": train_rows + oos_rows,
        "runtime_boundary": {
            "research_only": True,
            "changes_edge_signal": False,
            "applies_filter": False,
            "applies_veto": False,
            "recommended_filter_change": False,
            "sends_orders": False,
            "can_trade": False,
        },
        "decision": decision,
        "next_action": "collect independent forward context outcomes; do not alter frozen Edge",
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    with out.with_name(out.name + "_trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list((train_rows + oos_rows)[0]) if train_rows or oos_rows else ["window"])
        writer.writeheader()
        writer.writerows(train_rows + oos_rows)
    print(json.dumps({"decision": decision, "reproduction": reproduction, "evidence_gate": gate, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if reproduction["exact_trade_count_match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
