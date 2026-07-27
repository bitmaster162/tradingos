from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Condition:
    id: str
    label: str
    family: str
    predicate: Callable[[dict[str, str]], bool]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def read_events(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def numeric(row: dict[str, str], name: str) -> float:
    return parse_float(row.get(name))


def text_eq(name: str, value: str) -> Callable[[dict[str, str]], bool]:
    return lambda row: str(row.get(name, "")) == value


def flag_eq(name: str, value: int = 1) -> Callable[[dict[str, str]], bool]:
    return lambda row: parse_int(row.get(name)) == value


def ge(name: str, threshold: float) -> Callable[[dict[str, str]], bool]:
    return lambda row: not math.isnan(numeric(row, name)) and numeric(row, name) >= threshold


def le(name: str, threshold: float) -> Callable[[dict[str, str]], bool]:
    return lambda row: not math.isnan(numeric(row, name)) and numeric(row, name) <= threshold


def between(name: str, low: float, high: float) -> Callable[[dict[str, str]], bool]:
    return lambda row: not math.isnan(numeric(row, name)) and low <= numeric(row, name) <= high


def abs_ge(name: str, threshold: float) -> Callable[[dict[str, str]], bool]:
    return lambda row: not math.isnan(numeric(row, name)) and abs(numeric(row, name)) >= threshold


def build_conditions() -> list[Condition]:
    conditions: list[Condition] = []

    for name in (
        "bullish_sweep",
        "bearish_sweep",
        "near_low",
        "near_high",
        "breakout_up",
        "breakout_down",
        "range_ok",
    ):
        conditions.append(Condition(f"{name}=1", name, name, flag_eq(name, 1)))

    conditions.append(Condition("data_degraded=0", "data not degraded", "data_degraded", flag_eq("data_degraded", 0)))

    for value in ("LONG", "SHORT", "NEUTRAL"):
        conditions.append(Condition(f"htf_bias={value}", f"HTF bias {value}", "htf_bias", text_eq("htf_bias", value)))

    for name in ("v02_side", "v04_trend_side", "v04_sweep_side", "v04_range_side", "v05_spot_trend_side", "v05_spot_sweep_side", "v05_spot_range_side"):
        for value in ("LONG", "SHORT"):
            conditions.append(Condition(f"{name}={value}", f"{name} {value}", name, text_eq(name, value)))

    numeric_specs: list[tuple[str, list[tuple[str, str, Callable[[str, float], Callable[[dict[str, str]], bool]], float]]]] = [
        ("rsi14", [("<=", "oversold_30", le, 30), ("<=", "weak_40", le, 40), ("<=", "below_50", le, 50), (">=", "above_50", ge, 50), (">=", "strong_60", ge, 60), (">=", "overbought_70", ge, 70)]),
        ("trend_strength", [("<=", "down_1_5", le, -1.5), ("<=", "down_0_8", le, -0.8), (">=", "up_0_8", ge, 0.8), (">=", "up_1_5", ge, 1.5)]),
        ("relative_volume", [("<=", "quiet_0_8", le, 0.8), ("<=", "normal_1_2", le, 1.2), (">=", "active_1_5", ge, 1.5), (">=", "climax_2_0", ge, 2.0)]),
        ("oi_delta_pct", [("<=", "oi_down_0", le, 0.0), ("<=", "oi_down_0_05", le, -0.05), (">=", "oi_up_0", ge, 0.0), (">=", "oi_up_0_05", ge, 0.05)]),
        ("oi_zscore", [("<=", "oi_z_neg_1_5", le, -1.5), (">=", "oi_z_pos_1_5", ge, 1.5)]),
        ("funding", [("<=", "funding_neg", le, 0.0), ("<=", "funding_neg_hot", le, -0.0008), (">=", "funding_pos", ge, 0.0), (">=", "funding_pos_hot", ge, 0.0008)]),
        ("spot_perp_divergence_3", [("<=", "spot_lag_0", le, 0.0), ("<=", "spot_lag_0_05", le, -0.05), (">=", "spot_lead_0", ge, 0.0), (">=", "spot_lead_0_05", ge, 0.05)]),
        ("spot_perp_divergence_12", [("<=", "spot_lag_0", le, 0.0), ("<=", "spot_lag_0_25", le, -0.25), (">=", "spot_lead_0", ge, 0.0), (">=", "spot_lead_0_25", ge, 0.25)]),
        ("spot_volume_ratio", [("<=", "spot_quiet_0_8", le, 0.8), ("<=", "spot_normal_1_2", le, 1.2), (">=", "spot_active_1_5", ge, 1.5), (">=", "spot_climax_2_0", ge, 2.0)]),
        ("donchian_width_atr", [("<=", "tight_4", le, 4.0), (">=", "wide_6", ge, 6.0), (">=", "very_wide_10", ge, 10.0)]),
        ("delta", [("<=", "short_score_2", le, -2.0), ("<=", "short_score_5", le, -5.0), (">=", "long_score_2", ge, 2.0), (">=", "long_score_5", ge, 5.0)]),
    ]
    for name, specs in numeric_specs:
        for operator, label, builder, threshold in specs:
            conditions.append(
                Condition(
                    f"{name}{operator}{threshold}",
                    f"{name} {operator} {threshold:g}",
                    name,
                    builder(name, threshold),
                )
            )

    conditions.append(Condition("abs(oi_zscore)>=2", "abs oi_zscore >= 2", "oi_zscore", abs_ge("oi_zscore", 2.0)))
    conditions.append(Condition("rsi14_between_40_60", "rsi14 between 40 and 60", "rsi14", between("rsi14", 40.0, 60.0)))
    conditions.append(Condition("donchian_width_atr_between_2_8", "donchian width ATR between 2 and 8", "donchian_width_atr", between("donchian_width_atr", 2.0, 8.0)))

    return conditions


def compatible(combo: tuple[Condition, ...]) -> bool:
    seen_ids: set[str] = set()
    seen_families: set[str] = set()
    for condition in combo:
        if condition.id in seen_ids:
            return False
        seen_ids.add(condition.id)
        if condition.family in seen_families:
            return False
        seen_families.add(condition.family)
    return True


def fold_ranges(total: int, folds: int) -> list[tuple[int, int]]:
    folds = max(1, min(folds, total if total else 1))
    span = max(1, math.ceil(total / folds))
    ranges: list[tuple[int, int]] = []
    for idx in range(folds):
        start = idx * span
        end = min(total, start + span)
        if start < end:
            ranges.append((start, end))
    return ranges


def hit_pct(rows: list[dict[str, str]], side: str) -> float | None:
    if not rows:
        return None
    field = "long_1r_outcome" if side == "LONG" else "short_1r_outcome"
    hits = sum(1 for row in rows if parse_int(row.get(field)) == 1)
    return hits / len(rows) * 100


def avg_forward_pct(rows: list[dict[str, str]], side: str) -> float | None:
    values = [parse_float(row.get("future_ret_pct")) for row in rows]
    clean = [value for value in values if not math.isnan(value)]
    if not clean:
        return None
    avg = sum(clean) / len(clean)
    return avg if side == "LONG" else -avg


def evaluate_combo(
    *,
    rows: list[dict[str, str]],
    combo: tuple[Condition, ...],
    side: str,
    baseline_hit_pct: float,
    folds: int,
    min_fold_events: int,
    min_edge_pct: float,
) -> dict[str, Any] | None:
    selected = [row for row in rows if all(condition.predicate(row) for condition in combo)]
    if not selected:
        return None

    hit = hit_pct(selected, side)
    if hit is None:
        return None
    edge = hit - baseline_hit_pct
    avg_forward = avg_forward_pct(selected, side)

    fold_metrics: list[dict[str, Any]] = []
    stable = 0
    ranges = fold_ranges(len(rows), folds)
    for fold_idx, (start, end) in enumerate(ranges, start=1):
        fold_rows = rows[start:end]
        fold_selected = [row for row in fold_rows if all(condition.predicate(row) for condition in combo)]
        fold_hit = hit_pct(fold_selected, side)
        fold_edge = None if fold_hit is None else fold_hit - baseline_hit_pct
        if len(fold_selected) >= min_fold_events and fold_edge is not None and fold_edge >= min_edge_pct:
            stable += 1
        fold_metrics.append(
            {
                "fold": fold_idx,
                "rows": len(fold_selected),
                "hit_pct": None if fold_hit is None else round(fold_hit, 3),
                "edge_pct": None if fold_edge is None else round(fold_edge, 3),
            }
        )

    fold_count = len(fold_metrics)
    stability_ratio = stable / fold_count if fold_count else 0.0
    score = edge * math.log10(len(selected) + 1) * (0.35 + 0.65 * stability_ratio)

    return {
        "side": side,
        "conditions": [condition.id for condition in combo],
        "labels": [condition.label for condition in combo],
        "condition_count": len(combo),
        "rows": len(selected),
        "hit_pct": round(hit, 3),
        "baseline_hit_pct": round(baseline_hit_pct, 3),
        "edge_pct": round(edge, 3),
        "avg_forward_pct_for_side": None if avg_forward is None else round(avg_forward, 6),
        "stable_folds": stable,
        "fold_count": fold_count,
        "stability_ratio": round(stability_ratio, 3),
        "score": round(score, 6),
        "folds": fold_metrics,
    }


def mine_events(
    *,
    rows: list[dict[str, str]],
    max_conditions: int,
    folds: int,
    min_events: int,
    min_fold_events: int,
    min_hit_pct: float,
    min_edge_pct: float,
    top: int,
) -> dict[str, Any]:
    conditions = build_conditions()
    baseline = {
        "LONG": hit_pct(rows, "LONG") or 0.0,
        "SHORT": hit_pct(rows, "SHORT") or 0.0,
    }
    candidates: list[dict[str, Any]] = []
    total = len(rows)
    condition_masks: list[int] = []
    for condition in conditions:
        mask = 0
        for idx, row in enumerate(rows):
            if condition.predicate(row):
                mask |= 1 << idx
        condition_masks.append(mask)

    hit_masks = {
        "LONG": 0,
        "SHORT": 0,
    }
    for idx, row in enumerate(rows):
        if parse_int(row.get("long_1r_outcome")) == 1:
            hit_masks["LONG"] |= 1 << idx
        if parse_int(row.get("short_1r_outcome")) == 1:
            hit_masks["SHORT"] |= 1 << idx

    future_returns = [parse_float(row.get("future_ret_pct")) for row in rows]
    fold_masks: list[int] = []
    for start, end in fold_ranges(total, folds):
        mask = 0
        for idx in range(start, end):
            mask |= 1 << idx
        fold_masks.append(mask)

    def average_from_mask(mask: int, side: str) -> float | None:
        values: list[float] = []
        idx = 0
        current = mask
        while current:
            if current & 1:
                value = future_returns[idx]
                if not math.isnan(value):
                    values.append(value)
            current >>= 1
            idx += 1
        if not values:
            return None
        avg = sum(values) / len(values)
        return avg if side == "LONG" else -avg

    for size in range(1, max(1, max_conditions) + 1):
        for combo_indexes in itertools.combinations(range(len(conditions)), size):
            combo = tuple(conditions[idx] for idx in combo_indexes)
            if not compatible(combo):
                continue
            combo_mask = (1 << total) - 1 if total else 0
            for idx in combo_indexes:
                combo_mask &= condition_masks[idx]
                if combo_mask == 0:
                    break
            selected_count = combo_mask.bit_count()
            if selected_count < min_events:
                continue
            for side in ("LONG", "SHORT"):
                hit = (combo_mask & hit_masks[side]).bit_count() / selected_count * 100
                edge = hit - baseline[side]
                if hit < min_hit_pct:
                    continue
                if edge < min_edge_pct:
                    continue
                fold_metrics: list[dict[str, Any]] = []
                stable = 0
                for fold_idx, fold_mask in enumerate(fold_masks, start=1):
                    fold_selected_mask = combo_mask & fold_mask
                    fold_rows = fold_selected_mask.bit_count()
                    fold_hit = None
                    fold_edge = None
                    if fold_rows:
                        fold_hit = (fold_selected_mask & hit_masks[side]).bit_count() / fold_rows * 100
                        fold_edge = fold_hit - baseline[side]
                    if fold_rows >= min_fold_events and fold_edge is not None and fold_edge >= min_edge_pct:
                        stable += 1
                    fold_metrics.append(
                        {
                            "fold": fold_idx,
                            "rows": fold_rows,
                            "hit_pct": None if fold_hit is None else round(fold_hit, 3),
                            "edge_pct": None if fold_edge is None else round(fold_edge, 3),
                        }
                    )
                fold_count = len(fold_metrics)
                stability_ratio = stable / fold_count if fold_count else 0.0
                score = edge * math.log10(selected_count + 1) * (0.35 + 0.65 * stability_ratio)
                avg_forward = average_from_mask(combo_mask, side)
                result = {
                    "side": side,
                    "conditions": [condition.id for condition in combo],
                    "labels": [condition.label for condition in combo],
                    "condition_count": len(combo),
                    "rows": selected_count,
                    "hit_pct": round(hit, 3),
                    "baseline_hit_pct": round(baseline[side], 3),
                    "edge_pct": round(edge, 3),
                    "avg_forward_pct_for_side": None if avg_forward is None else round(avg_forward, 6),
                    "stable_folds": stable,
                    "fold_count": fold_count,
                    "stability_ratio": round(stability_ratio, 3),
                    "score": round(score, 6),
                    "folds": fold_metrics,
                }
                candidates.append(result)

    candidates.sort(
        key=lambda item: (
            item["stable_folds"],
            item["score"],
            item["rows"],
            item["edge_pct"],
        ),
        reverse=True,
    )
    top_candidates = candidates[:top]
    promoted = [
        item
        for item in top_candidates
        if item["stable_folds"] == item["fold_count"] and item["fold_count"] > 0
    ]
    return {
        "baseline": {
            "rows": len(rows),
            "long_1r_hit_pct": round(baseline["LONG"], 3),
            "short_1r_hit_pct": round(baseline["SHORT"], 3),
        },
        "condition_count": len(conditions),
        "tested_candidates": len(candidates),
        "top_candidates": top_candidates,
        "promoted_for_rule_design": promoted,
    }


def render_markdown(report: dict[str, Any]) -> str:
    cfg = report["config"]
    result = report["result"]
    lines = [
        "# MAX Core Lite v0.7 Feature-Slice Miner",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Events file: `{report['events_file']}`",
        f"- Rows: `{result['baseline']['rows']}`",
        f"- Baseline long 1R hit: `{result['baseline']['long_1r_hit_pct']}`",
        f"- Baseline short 1R hit: `{result['baseline']['short_1r_hit_pct']}`",
        f"- Config: `{json.dumps(cfg, ensure_ascii=False)}`",
        "",
        "## Top Candidates",
        "",
        "| Rank | Side | Rows | Hit % | Edge % | Stable folds | Score | Conditions |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for idx, item in enumerate(result["top_candidates"], start=1):
        labels = " + ".join(f"`{label}`" for label in item["labels"])
        lines.append(
            f"| {idx} | `{item['side']}` | {item['rows']} | {item['hit_pct']} | {item['edge_pct']} | "
            f"{item['stable_folds']}/{item['fold_count']} | {item['score']} | {labels} |"
        )
    lines.extend(
        [
            "",
            "## Promotion Boundary",
            "",
            f"- Promoted for rule design: `{len(result['promoted_for_rule_design'])}`",
            "- Promotion here means only: worth turning into a strict strategy hypothesis and re-testing through v0.5 leaderboard.",
            "- It does not mean paper trading approval.",
            "",
            "## Runtime Boundary",
            "",
            report["runtime_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite v0.7 feature-slice miner")
    parser.add_argument("--events", default="_dl/event_exports/BTCUSDT_1h_v06_events.csv")
    parser.add_argument("--out-prefix", default="_dl/event_exports/BTCUSDT_1h_v07_miner")
    parser.add_argument("--max-conditions", type=int, default=3)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-events", type=int, default=20)
    parser.add_argument("--min-fold-events", type=int, default=4)
    parser.add_argument("--min-hit-pct", type=float, default=55.0)
    parser.add_argument("--min-edge-pct", type=float, default=7.0)
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    events_path = Path(args.events)
    if not events_path.is_absolute():
        events_path = ROOT / events_path
    if not events_path.exists():
        raise SystemExit(f"events_file_not_found:{events_path}")

    rows = read_events(events_path)
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    config = {
        "max_conditions": args.max_conditions,
        "folds": args.folds,
        "min_events": args.min_events,
        "min_fold_events": args.min_fold_events,
        "min_hit_pct": args.min_hit_pct,
        "min_edge_pct": args.min_edge_pct,
        "top": args.top,
    }
    result = mine_events(
        rows=rows,
        max_conditions=args.max_conditions,
        folds=args.folds,
        min_events=args.min_events,
        min_fold_events=args.min_fold_events,
        min_hit_pct=args.min_hit_pct,
        min_edge_pct=args.min_edge_pct,
        top=args.top,
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_FEATURE_SLICE_MINER",
        "engine_version": "0.7.0",
        "events_file": str(events_path),
        "config": config,
        "result": result,
        "runtime_boundary": (
            "Research-only feature-slice miner. It searches labelled historical event rows for candidate patterns; "
            "it does not place orders, does not generate live signals, and does not prove future profitability."
        ),
    }
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(json_path),
                "md": str(md_path),
                "top_candidates": len(result["top_candidates"]),
                "promoted_for_rule_design": len(result["promoted_for_rule_design"]),
                "baseline": result["baseline"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
