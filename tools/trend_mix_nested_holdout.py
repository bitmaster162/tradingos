#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_hardening import fold_summaries, simulate_trade, summarize_trades  # noqa: E402
from tools.strategy_mix_combo_tester import (  # noqa: E402
    ComboConfig,
    build_combos,
    generate_signals,
    load_interval_data,
    parse_rr_list,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def find_split_index(bars: list[Any], split_ts: str) -> int:
    boundary = parse_ts(split_ts)
    for index, bar in enumerate(bars):
        if parse_ts(str(bar.ts)) >= boundary:
            return index
    raise ValueError(f"split timestamp after data: {split_ts}")


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def stable_fold_count(folds: list[dict[str, Any]]) -> int:
    return sum(
        1
        for fold in folds
        if int(fold.get("trades") or 0) >= 5
        and finite(fold.get("expectancy_r")) is not None
        and float(fold["expectancy_r"]) > 0.0
    )


def bootstrap_probability(trades: list[Any], *, iterations: int, seed: int) -> float | None:
    values = [float(trade.r_net) for trade in trades]
    if not values or iterations <= 0:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(iterations):
        mean = sum(values[rng.randrange(len(values))] for _ in values) / len(values)
        positive += mean > 0.0
    return round(positive / iterations, 4)


def simulate_window(
    config: ComboConfig,
    *,
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    start_index: int,
    end_index: int,
    cost_bps_per_side: float,
) -> list[Any]:
    signals = generate_signals(config, bars, features, matrix)
    safe_end = min(end_index, len(bars)) - config.max_hold_bars - 1
    selected = [signal for signal in signals if start_index <= int(signal["bar_index"]) < safe_end]
    trades: list[Any] = []
    last_exit_bar = start_index - 1
    for signal in selected:
        signal_index = int(signal["bar_index"])
        if signal_index <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id="trend_mix_nested_BTCUSDT_4h",
            strategy_id=config.strategy_id,
            bars=bars[:end_index],
            signal=signal,
            stop_atr=config.stop_atr,
            take_atr=config.take_atr,
            max_hold_bars=config.max_hold_bars,
            cost_bps_per_side=cost_bps_per_side,
        )
        if trade is None:
            continue
        trades.append(trade)
        for index in range(signal_index + 1, min(end_index, signal_index + config.max_hold_bars + 2)):
            if bars[index].ts == trade.exit_ts:
                last_exit_bar = index
                break
    return trades


def evaluate_window(
    config: ComboConfig,
    *,
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    start_index: int,
    end_index: int,
    folds: int,
    base_cost_bps: float,
    stress_extra_bps: float,
    bootstrap_iterations: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    trades = simulate_window(
        config,
        bars=bars,
        features=features,
        matrix=matrix,
        start_index=start_index,
        end_index=end_index,
        cost_bps_per_side=base_cost_bps,
    )
    stressed = simulate_window(
        config,
        bars=bars,
        features=features,
        matrix=matrix,
        start_index=start_index,
        end_index=end_index,
        cost_bps_per_side=base_cost_bps + stress_extra_bps,
    )
    fold_rows = fold_summaries(trades, folds)
    return {
        "summary": summarize_trades(trades),
        "folds": fold_rows,
        "stable_folds": stable_fold_count(fold_rows),
        "bootstrap_prob_expectancy_gt_zero": bootstrap_probability(
            trades, iterations=bootstrap_iterations, seed=bootstrap_seed
        ),
        "cost_stress": {
            "extra_bps_per_side": stress_extra_bps,
            "summary": summarize_trades(stressed),
        },
        "sample_trades": [asdict(trade) for trade in trades[:3]],
    }


def window_gate(window: dict[str, Any], *, train: bool, args: argparse.Namespace) -> dict[str, Any]:
    prefix = "train" if train else "oos"
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    rr = args.take_floor_for_breakeven
    breakeven = 100.0 / (1.0 + rr)
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= getattr(args, f"min_{prefix}_trades"),
        "min_expectancy_r": finite(summary.get("expectancy_r")) is not None
        and float(summary["expectancy_r"]) >= getattr(args, f"min_{prefix}_expectancy_r"),
        "winrate_above_cost_free_breakeven": finite(summary.get("winrate_pct")) is not None
        and float(summary["winrate_pct"]) >= breakeven,
        "min_stable_folds": int(window.get("stable_folds") or 0) >= getattr(args, f"min_{prefix}_stable_folds"),
        "max_drawdown_r": float(summary.get("max_drawdown_r") or 0.0)
        >= -abs(getattr(args, f"max_{prefix}_drawdown_r")),
        "cost_stress_positive": finite(stress.get("expectancy_r")) is not None
        and float(stress["expectancy_r"]) > 0.0,
    }
    if train:
        probability = window.get("bootstrap_prob_expectancy_gt_zero")
        checks["bootstrap_probability"] = probability is not None and probability >= args.min_train_bootstrap_prob
    return {"pass": all(checks.values()), "checks": checks}


def rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    summary = row["train"]["summary"]
    stress = row["train"]["cost_stress"]["summary"]
    trades = max(1, int(summary.get("trades") or 0))
    return (
        float(stress.get("expectancy_r") or -999.0) * math.sqrt(trades),
        int(row["train"].get("stable_folds") or 0),
        float(summary.get("expectancy_r") or -999.0),
        trades,
    )


def render_markdown(report: dict[str, Any]) -> str:
    selected = report.get("selected_on_train")
    lines = [
        "# Trend Mix Nested Holdout",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Candidate selection uses only data before the fixed calendar split.",
        "- Exactly one frozen train winner may be evaluated on OOS.",
        "- Existing full-history combo rankings are not reused for selection.",
        "- Research-only: no observer mutation, paper/live permission, credentials, or orders.",
        "",
        "## Result",
        "",
        f"- Data: `{report['data']['first_time']}` to `{report['data']['last_time']}`.",
        f"- Split: `{report['data']['split_ts']}` at row `{report['data']['split_index']}`.",
        f"- Tested / train-qualified: `{report['search']['tested']}` / `{report['search']['train_qualified']}`.",
        f"- Decision: `{report['decision']}`.",
    ]
    if selected:
        train = selected["train"]["summary"]
        oos = report["oos"]["summary"]
        stress = report["oos"]["cost_stress"]["summary"]
        lines.extend(
            [
                f"- Selected: `{selected['strategy_id']}`.",
                f"- Train: `{train.get('trades')}` trades, `{train.get('winrate_pct')}%`, `{train.get('expectancy_r')}`R.",
                f"- OOS: `{oos.get('trades')}` trades, `{oos.get('winrate_pct')}%`, `{oos.get('expectancy_r')}`R.",
                f"- OOS +10bps/side stress: `{stress.get('expectancy_r')}`R.",
            ]
        )
    else:
        lines.append("- No train candidate qualified; OOS remained unopened.")
    lines.extend(["", "- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Leakage-resistant nested validation for the BTCUSDT 4H strategy-mix family")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--split-ts", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--rr", default="1:2,1:3")
    parser.add_argument("--max-holds", default="8,12,16")
    parser.add_argument("--max-combos-per-side", type=int, default=80)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-extra-bps", type=float, default=10.0)
    parser.add_argument("--train-folds", type=int, default=6)
    parser.add_argument("--oos-folds", type=int, default=4)
    parser.add_argument("--bootstrap-iterations", type=int, default=500)
    parser.add_argument("--bootstrap-seed", type=int, default=20260623)
    parser.add_argument("--min-train-trades", type=int, default=60)
    parser.add_argument("--min-train-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-train-stable-folds", type=int, default=4)
    parser.add_argument("--min-train-bootstrap-prob", type=float, default=0.80)
    parser.add_argument("--max-train-drawdown-r", type=float, default=12.0)
    parser.add_argument("--min-oos-trades", type=int, default=20)
    parser.add_argument("--min-oos-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-oos-stable-folds", type=int, default=2)
    parser.add_argument("--max-oos-drawdown-r", type=float, default=8.0)
    parser.add_argument("--take-floor-for-breakeven", type=float, default=2.0)
    parser.add_argument("--out-prefix", default="docs/TREND_MIX_NESTED_HOLDOUT_2026-06-23")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    bars, features, matrix = load_interval_data(cache_dir, "4h", 12, 12)
    split = find_split_index(bars, args.split_ts)
    configs = build_combos(
        "4h",
        parse_rr_list(args.rr),
        [int(item) for item in args.max_holds.split(",") if item.strip()],
        args.max_combos_per_side,
    )
    base_cost = args.fee_bps + args.slippage_bps
    rng = random.Random(args.bootstrap_seed)
    seeds = {config.strategy_id: rng.randrange(1, 10_000_000) for config in configs}
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                evaluate_window,
                config,
                bars=bars,
                features=features,
                matrix=matrix,
                start_index=40,
                end_index=split,
                folds=args.train_folds,
                base_cost_bps=base_cost,
                stress_extra_bps=args.stress_extra_bps,
                bootstrap_iterations=args.bootstrap_iterations,
                bootstrap_seed=seeds[config.strategy_id],
            ): config
            for config in configs
        }
        for future in as_completed(futures):
            config = futures[future]
            train = future.result()
            results.append(
                {
                    "strategy_id": config.strategy_id,
                    "config": asdict(config),
                    "train": train,
                    "train_gate": window_gate(train, train=True, args=args),
                }
            )

    qualified = [row for row in results if row["train_gate"]["pass"]]
    qualified.sort(key=rank_key, reverse=True)
    selected = qualified[0] if qualified else None
    oos = None
    oos_gate = None
    decision = "reject_no_train_candidate"
    if selected:
        config = ComboConfig(**selected["config"])
        oos = evaluate_window(
            config,
            bars=bars,
            features=features,
            matrix=matrix,
            start_index=split,
            end_index=len(bars),
            folds=args.oos_folds,
            base_cost_bps=base_cost,
            stress_extra_bps=args.stress_extra_bps,
            bootstrap_iterations=0,
            bootstrap_seed=args.bootstrap_seed,
        )
        oos_gate = window_gate(oos, train=False, args=args)
        decision = "pass_oos_observer_candidate_not_trade_permission" if oos_gate["pass"] else "reject_oos_gate_failed"

    results.sort(key=rank_key, reverse=True)
    report = {
        "generated_at": now_iso(),
        "method": "train_only_grid_selection_then_single_untouched_calendar_oos",
        "selection_frozen_before_oos": True,
        "runtime_boundary": {"research_only": True, "sends_orders": False, "can_trade": False},
        "data": {
            "cache_dir": str(cache_dir),
            "rows": len(bars),
            "first_time": bars[0].ts,
            "last_time": bars[-1].ts,
            "split_ts": args.split_ts,
            "split_index": split,
        },
        "search": {"tested": len(results), "train_qualified": len(qualified)},
        "selected_on_train": selected,
        "oos": oos,
        "oos_gate": oos_gate,
        "top_train_results": results[:20],
        "decision": decision,
        "next_action": "observer_only_forward_proof" if decision.startswith("pass_oos") else "reject_or_redesign_without_oos_reuse",
        "can_trade": False,
    }
    out = Path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "tested": len(results),
                "train_qualified": len(qualified),
                "selected": selected["strategy_id"] if selected else None,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
