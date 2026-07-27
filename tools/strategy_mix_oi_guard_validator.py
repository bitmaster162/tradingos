#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def row_r(row: dict[str, Any], cost_r: float = 0.0) -> float | None:
    value = safe_float(row.get("r_net"))
    if value is None:
        return None
    return value - cost_r


def stats(rows: list[dict[str, Any]], cost_r: float = 0.0) -> dict[str, Any]:
    values = [value for row in rows if (value := row_r(row, cost_r)) is not None]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    if not values:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "winrate_pct": None,
            "expectancy_r": None,
            "net_r_total": 0.0,
            "avg_win_r": None,
            "avg_loss_r": None,
        }
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(values) * 100, 3),
        "expectancy_r": round(sum(values) / len(values), 6),
        "net_r_total": round(sum(values), 6),
        "avg_win_r": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 6) if losses else None,
    }


def by_year(rows: list[dict[str, Any]], cost_r: float = 0.0) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        year = str(row.get("entry_ts", ""))[:4] or "unknown"
        groups.setdefault(year, []).append(row)
    output: list[dict[str, Any]] = []
    for year, items in sorted(groups.items()):
        output.append({"year": year, **stats(items, cost_r)})
    return output


def temporal_folds(rows: list[dict[str, Any]], folds: int, cost_r: float = 0.0) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: str(row.get("entry_ts", "")))
    if not ordered:
        return []
    folds = max(1, min(folds, len(ordered)))
    output: list[dict[str, Any]] = []
    for index in range(folds):
        start = round(index * len(ordered) / folds)
        end = round((index + 1) * len(ordered) / folds)
        items = ordered[start:end]
        output.append(
            {
                "fold": index + 1,
                "start": items[0].get("entry_ts") if items else None,
                "end": items[-1].get("entry_ts") if items else None,
                **stats(items, cost_r),
            }
        )
    return output


def bootstrap_prob_positive(rows: list[dict[str, Any]], iterations: int, seed: int, cost_r: float = 0.0) -> float | None:
    values = [value for row in rows if (value := row_r(row, cost_r)) is not None]
    if not values:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(iterations):
        sample_sum = 0.0
        for _ in values:
            sample_sum += rng.choice(values)
        if sample_sum / len(values) > 0:
            positive += 1
    return round(positive / iterations, 4)


def candidate_filters() -> dict[str, Callable[[dict[str, Any]], bool]]:
    return {
        "keep_oi_expansion_strong": lambda row: row.get("oi_state") == "expansion_strong",
        "keep_context_trend_confirmation_long": lambda row: row.get("context_bias") == "trend_confirmation_long",
        "avoid_oi_contraction_strong": lambda row: row.get("oi_state") != "contraction_strong",
        "full_context_only": lambda row: row.get("data_quality") == "full_context_available",
    }


def evaluate_candidate(
    name: str,
    rows: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    *,
    baseline_expectancy: float | None,
    min_trades: int,
    min_positive_folds: int,
    folds: int,
    bootstrap_iterations: int,
    seed: int,
    cost_stress_r: float,
) -> dict[str, Any]:
    base = stats(selected)
    stressed = stats(selected, cost_stress_r)
    yearly = by_year(selected)
    fold_stats = temporal_folds(selected, folds)
    positive_folds = sum(1 for item in fold_stats if isinstance(item.get("expectancy_r"), (int, float)) and item["expectancy_r"] > 0)
    positive_years = sum(1 for item in yearly if isinstance(item.get("expectancy_r"), (int, float)) and item["expectancy_r"] > 0)
    worst_fold = min((item.get("expectancy_r") for item in fold_stats if isinstance(item.get("expectancy_r"), (int, float))), default=None)
    worst_year = min((item.get("expectancy_r") for item in yearly if isinstance(item.get("expectancy_r"), (int, float))), default=None)
    expectancy = base.get("expectancy_r")
    lift = round(expectancy - baseline_expectancy, 6) if isinstance(expectancy, (int, float)) and isinstance(baseline_expectancy, (int, float)) else None
    boot = bootstrap_prob_positive(selected, bootstrap_iterations, seed)
    boot_stressed = bootstrap_prob_positive(selected, bootstrap_iterations, seed, cost_stress_r)
    rejected = len(rows) - len(selected)
    rejection_pct = round(rejected / len(rows) * 100, 3) if rows else 0.0

    gates = {
        "min_trades": base["trades"] >= min_trades,
        "positive_expectancy": isinstance(expectancy, (int, float)) and expectancy > 0,
        "positive_lift": isinstance(lift, (int, float)) and lift > 0,
        "positive_folds": positive_folds >= min_positive_folds,
        "bootstrap_positive": isinstance(boot, (int, float)) and boot >= 0.9,
        "cost_stress_positive": isinstance(stressed.get("expectancy_r"), (int, float)) and stressed["expectancy_r"] > 0,
    }
    if all(gates.values()):
        verdict = "candidate_for_forward_guard_observation"
    elif base["trades"] < min_trades:
        verdict = "reject_too_few_trades"
    elif not gates["positive_lift"]:
        verdict = "reject_no_lift"
    else:
        verdict = "watchlist_not_promoted"

    return {
        "candidate": name,
        "verdict": verdict,
        "selected_stats": base,
        "cost_stress_stats": stressed,
        "rejected_trades": rejected,
        "rejection_pct": rejection_pct,
        "expectancy_lift_r": lift,
        "positive_folds": positive_folds,
        "folds": folds,
        "positive_years": positive_years,
        "years": len(yearly),
        "worst_fold_expectancy_r": worst_fold,
        "worst_year_expectancy_r": worst_year,
        "bootstrap_prob_positive": boot,
        "bootstrap_prob_positive_cost_stress": boot_stressed,
        "gates": gates,
        "yearly": yearly,
        "temporal_folds": fold_stats,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Mix OI Guard Validation",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Offline validation only.",
        "- Uses enriched paper replay trades.",
        "- No private credentials, no network, no orders, no live guard promotion.",
        "",
        "## Baseline",
        "",
    ]
    for key, value in report.get("baseline", {}).items():
        lines.append(f"- {key}: `{value}`.")
    lines.extend(["", "## Candidates", ""])
    lines.append("| candidate | verdict | trades | winrate | expectancy | lift | folds+ | boot+ | stress_exp | rejected_% |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for item in report.get("candidates", []):
        selected = item.get("selected_stats", {})
        stress = item.get("cost_stress_stats", {})
        lines.append(
            f"| {item.get('candidate')} | {item.get('verdict')} | {selected.get('trades')} | "
            f"{selected.get('winrate_pct')} | {selected.get('expectancy_r')} | {item.get('expectancy_lift_r')} | "
            f"{item.get('positive_folds')}/{item.get('folds')} | {item.get('bootstrap_prob_positive')} | "
            f"{stress.get('expectancy_r')} | {item.get('rejection_pct')} |"
        )
    lines.extend(["", "## Decision", "", f"- `{report.get('decision')}`.", ""])
    lines.extend(["## Next Action", "", f"- `{report.get('next_action')}`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate OI/funding guard candidates from enriched replay trades")
    parser.add_argument("--enriched-trades-csv", default="docs/STRATEGY_MIX_OI_FUNDING_REPLAY_AUDIT_2026-06-15_enriched_trades.csv")
    parser.add_argument("--min-trades", type=int, default=60)
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--min-positive-folds", type=int, default=4)
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=1701)
    parser.add_argument("--cost-stress-r", type=float, default=0.05)
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_OI_GUARD_VALIDATION_2026-06-15")
    args = parser.parse_args()

    input_path = resolve_path(args.enriched_trades_csv)
    rows = read_csv_rows(input_path)
    baseline = stats(rows)
    baseline_expectancy = baseline.get("expectancy_r")
    candidates = []
    for name, predicate in candidate_filters().items():
        selected = [row for row in rows if predicate(row)]
        candidates.append(
            evaluate_candidate(
                name,
                rows,
                selected,
                baseline_expectancy=baseline_expectancy if isinstance(baseline_expectancy, (int, float)) else None,
                min_trades=args.min_trades,
                min_positive_folds=args.min_positive_folds,
                folds=args.folds,
                bootstrap_iterations=args.bootstrap_iterations,
                seed=args.seed,
                cost_stress_r=args.cost_stress_r,
            )
        )
    candidates.sort(
        key=lambda item: (
            item["verdict"] != "candidate_for_forward_guard_observation",
            -(item.get("expectancy_lift_r") if isinstance(item.get("expectancy_lift_r"), (int, float)) else -999),
            -item["selected_stats"]["trades"],
        )
    )
    forward_candidates = [item for item in candidates if item["verdict"] == "candidate_for_forward_guard_observation"]
    decision = "forward_guard_observation_candidate_found" if forward_candidates else "no_guard_promoted"
    report = {
        "generated_at": now_iso(),
        "boundary": {
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "inputs": {
            "enriched_trades_csv": rel(input_path),
            "min_trades": args.min_trades,
            "folds": args.folds,
            "min_positive_folds": args.min_positive_folds,
            "bootstrap_iterations": args.bootstrap_iterations,
            "cost_stress_r": args.cost_stress_r,
        },
        "baseline": baseline,
        "candidates": candidates,
        "decision": decision,
        "next_action": "add best OI guard candidate to forward observer only; do not change live execution" if forward_candidates else "keep OI as research context and mine stricter/nonlinear combinations",
    }

    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "md": str(md_path), "decision": decision, "baseline": baseline, "top_candidate": candidates[0] if candidates else None, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
