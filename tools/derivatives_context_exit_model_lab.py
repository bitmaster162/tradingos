#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.derivatives_context_composite_miner import (  # noqa: E402
    CompositeConfig,
    build_configs,
    composite_signal_matches,
    diversified_limit,
    load_interval,
    parse_float_grid,
    parse_int_grid,
    resolve_path,
)
from tools.derivatives_context_signal_frequency_diagnostic import dedupe_entry_configs  # noqa: E402
from tools.derivatives_event_edge_miner import fold_stats, safe_float, split_index, stable_folds, stats  # noqa: E402


@dataclass(frozen=True)
class ExitConfig:
    exit_model: str
    stop_atr: float
    take_atr: float
    max_hold_bars: int
    breakeven_after_r: float = 1.0
    trail_atr: float = 1.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def parse_str_grid(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def trade_r(entry: float, exit_price: float, risk: float, side: str, cost_bps_per_side: float) -> float:
    gross_r = (exit_price - entry) / risk if side == "LONG" else (entry - exit_price) / risk
    fee_r = ((entry + exit_price) * cost_bps_per_side / 10_000.0) / risk
    return gross_r - fee_r


def simulate_exit_model(
    config: CompositeConfig,
    exit_config: ExitConfig,
    rows: list[dict[str, Any]],
    signal_index: int,
    atr: float,
    *,
    cost_bps_per_side: float,
    row_end_index: int | None = None,
) -> dict[str, Any] | None:
    entry_index = signal_index + 1
    row_limit = min(len(rows), row_end_index if row_end_index is not None else len(rows))
    if entry_index >= row_limit:
        return None
    entry = safe_float(rows[entry_index].get("open"))
    if entry is None or atr <= 0:
        return None
    side = config.side.upper()
    if side == "LONG":
        initial_stop = entry - exit_config.stop_atr * atr
        take = entry + exit_config.take_atr * atr
    else:
        initial_stop = entry + exit_config.stop_atr * atr
        take = entry - exit_config.take_atr * atr
    stop = initial_stop
    risk = abs(entry - initial_stop)
    if risk <= 0:
        return None

    exit_price = entry
    exit_reason = "time"
    exit_index = min(row_limit - 1, entry_index + exit_config.max_hold_bars)
    best_favorable_r = 0.0
    for index in range(entry_index, min(row_limit, entry_index + exit_config.max_hold_bars + 1)):
        high = safe_float(rows[index].get("high"))
        low = safe_float(rows[index].get("low"))
        close = safe_float(rows[index].get("close"))
        if high is None or low is None or close is None:
            continue

        if side == "LONG":
            favorable_price = float(high)
            favorable_r = (favorable_price - entry) / risk
            stop_hit = float(low) <= stop
            take_hit = float(high) >= take and exit_config.exit_model in {"fixed_rr", "breakeven_after_r"}
            if stop_hit and take_hit:
                exit_price, exit_reason, exit_index = stop, "same_bar_stop_first", index
                break
            if stop_hit:
                exit_price, exit_reason, exit_index = stop, "stop", index
                break
            if take_hit:
                exit_price, exit_reason, exit_index = take, "take", index
                break
            if exit_config.exit_model == "breakeven_after_r" and favorable_r >= exit_config.breakeven_after_r:
                stop = max(stop, entry)
            if exit_config.exit_model == "atr_trailing":
                stop = max(stop, float(close) - exit_config.trail_atr * atr)
        else:
            favorable_price = float(low)
            favorable_r = (entry - favorable_price) / risk
            stop_hit = float(high) >= stop
            take_hit = float(low) <= take and exit_config.exit_model in {"fixed_rr", "breakeven_after_r"}
            if stop_hit and take_hit:
                exit_price, exit_reason, exit_index = stop, "same_bar_stop_first", index
                break
            if stop_hit:
                exit_price, exit_reason, exit_index = stop, "stop", index
                break
            if take_hit:
                exit_price, exit_reason, exit_index = take, "take", index
                break
            if exit_config.exit_model == "breakeven_after_r" and favorable_r >= exit_config.breakeven_after_r:
                stop = min(stop, entry)
            if exit_config.exit_model == "atr_trailing":
                stop = min(stop, float(close) + exit_config.trail_atr * atr)

        best_favorable_r = max(best_favorable_r, favorable_r)
        exit_price = float(close)
        exit_index = index

    return {
        "entry_index": entry_index,
        "exit_index": exit_index,
        "entry": entry,
        "exit": exit_price,
        "net_r": trade_r(entry, exit_price, risk, side, cost_bps_per_side),
        "exit_reason": exit_reason,
        "best_favorable_r": best_favorable_r,
    }


def simulate_window(
    config: CompositeConfig,
    exit_config: ExitConfig,
    rows: list[dict[str, Any]],
    features: dict[int, dict[int, dict[str, float]]],
    *,
    start_index: int,
    end_index: int,
    cost_bps_per_side: float,
    no_overlap: bool = True,
    signal_indices: list[int] | None = None,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    safe_end = min(end_index, len(rows)) - exit_config.max_hold_bars - 2
    index_iterable: list[int] | range
    if signal_indices is None:
        index_iterable = range(max(0, start_index), max(0, safe_end))
    else:
        index_iterable = signal_indices
    cursor = max(0, start_index)
    for index in index_iterable:
        if index < cursor or index >= safe_end:
            continue
        feature = features.get(index, {}).get(config.lookback)
        if feature is None:
            continue
        if signal_indices is None and not composite_signal_matches(config, feature):
            continue
        outcome = simulate_exit_model(
            config,
            exit_config,
            rows,
            index,
            feature["atr"],
            cost_bps_per_side=cost_bps_per_side,
            row_end_index=end_index,
        )
        if outcome is None:
            continue
        trades.append(
            {
                "strategy_id": config.strategy_id,
                "exit_model": exit_config.exit_model,
                "context_mode": config.context_mode,
                "interval": config.interval,
                "side": config.side,
                "signal_time": rows[index]["time"],
                "entry_time": rows[outcome["entry_index"]]["time"],
                "exit_time": rows[outcome["exit_index"]]["time"],
                "entry": round(float(outcome["entry"]), 8),
                "exit": round(float(outcome["exit"]), 8),
                "net_r": round(float(outcome["net_r"]), 6),
                "exit_reason": outcome["exit_reason"],
                "best_favorable_r": round(float(outcome["best_favorable_r"]), 6),
                "price_move_atr": round(feature["price_move_atr"], 6),
                "funding": round(feature["funding"], 8),
                "spot_perp_divergence_pct": round(feature["spot_perp_divergence_pct"], 6),
            }
        )
        cursor = int(outcome["exit_index"]) + 1 if no_overlap else index + 1
    return trades


def matching_signal_indices(
    config: CompositeConfig,
    features: dict[int, dict[int, dict[str, float]]],
    *,
    start_index: int,
    end_index: int,
) -> list[int]:
    output: list[int] = []
    for index in range(max(0, start_index), max(0, end_index)):
        feature = features.get(index, {}).get(config.lookback)
        if feature is not None and composite_signal_matches(config, feature):
            output.append(index)
    return output


def evaluate(
    config: CompositeConfig,
    exit_config: ExitConfig,
    rows: list[dict[str, Any]],
    features: dict[int, dict[int, dict[str, float]]],
    *,
    start_index: int,
    end_index: int,
    cost_bps_per_side: float,
    folds: int,
    signal_indices: list[int] | None = None,
) -> dict[str, Any]:
    trades = simulate_window(
        config,
        exit_config,
        rows,
        features,
        start_index=start_index,
        end_index=end_index,
        cost_bps_per_side=cost_bps_per_side,
        signal_indices=signal_indices,
    )
    fold_rows = fold_stats(trades, folds)
    return {"summary": stats(trades), "folds": fold_rows, "stable_folds": stable_folds(fold_rows), "sample_trades": trades[:5]}


def build_exit_configs(args: argparse.Namespace) -> list[ExitConfig]:
    output: list[ExitConfig] = []
    seen: set[tuple[str, float, float, int, float, float]] = set()
    for model in parse_str_grid(args.exit_models):
        for stop_atr in parse_float_grid(args.stop_atr):
            for take_atr in parse_float_grid(args.take_atr):
                for max_hold in parse_int_grid(args.max_hold_bars):
                    for breakeven in parse_float_grid(args.breakeven_after_r):
                        for trail in parse_float_grid(args.trail_atr):
                            canonical_take = take_atr if model in {"fixed_rr", "breakeven_after_r"} else 0.0
                            canonical_breakeven = breakeven if model == "breakeven_after_r" else 0.0
                            canonical_trail = trail if model == "atr_trailing" else 0.0
                            key = (
                                model,
                                stop_atr,
                                canonical_take,
                                max_hold,
                                canonical_breakeven,
                                canonical_trail,
                            )
                            if key in seen:
                                continue
                            seen.add(key)
                            output.append(
                                ExitConfig(
                                    exit_model=model,
                                    stop_atr=stop_atr,
                                    take_atr=canonical_take,
                                    max_hold_bars=max_hold,
                                    breakeven_after_r=canonical_breakeven,
                                    trail_atr=canonical_trail,
                                )
                            )
    return output


def gate(summary: dict[str, Any], folds: list[dict[str, Any]], *, min_trades: int, min_expectancy: float, min_stable_folds: int, max_drawdown: float) -> dict[str, Any]:
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= min_trades,
        "min_expectancy": isinstance(summary.get("expectancy_r"), (int, float)) and float(summary["expectancy_r"]) >= min_expectancy,
        "min_stable_folds": stable_folds(folds) >= min_stable_folds,
        "max_drawdown": isinstance(summary.get("max_drawdown_r"), (int, float)) and float(summary["max_drawdown_r"]) >= -abs(max_drawdown),
    }
    return {"pass": all(checks.values()), "checks": checks}


def rank(item: dict[str, Any]) -> tuple[Any, ...]:
    summary = item["validation"]["summary"]
    train_summary = item["train"]["summary"]
    return (
        1 if item["validation_gate"]["pass"] else 0,
        item["validation"].get("stable_folds") or 0,
        summary.get("expectancy_r") if isinstance(summary.get("expectancy_r"), (int, float)) else -999.0,
        summary.get("max_drawdown_r") if isinstance(summary.get("max_drawdown_r"), (int, float)) else -999.0,
        train_summary.get("expectancy_r") if isinstance(train_summary.get("expectancy_r"), (int, float)) else -999.0,
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Derivatives Context Exit Model Lab",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Can trade: `{report.get('can_trade')}`",
        "",
        "## Summary",
        "",
        f"- Entry shapes: `{report.get('summary', {}).get('entry_shapes')}`.",
        f"- Exit configs: `{report.get('summary', {}).get('exit_configs')}`.",
        f"- Tested combinations: `{report.get('summary', {}).get('tested')}`.",
        f"- Validation-qualified: `{report.get('summary', {}).get('validation_qualified')}`.",
        "",
        "## Top Validation",
        "",
        "| strategy | exit | train trades/exp/dd | validation trades/exp/dd | gate |",
        "|---|---|---|---|---|",
    ]
    for item in report.get("top_validation", [])[:15]:
        train = item.get("train", {}).get("summary", {})
        validation = item.get("validation", {}).get("summary", {})
        exit_config = item.get("exit_config", {})
        lines.append(
            "| "
            f"`{item.get('strategy_id')}` | "
            f"`{exit_config.get('exit_model')} s={exit_config.get('stop_atr')} t={exit_config.get('take_atr')} h={exit_config.get('max_hold_bars')}` | "
            f"{train.get('trades')}/{train.get('expectancy_r')}/{train.get('max_drawdown_r')} | "
            f"{validation.get('trades')}/{validation.get('expectancy_r')}/{validation.get('max_drawdown_r')} | "
            f"{item.get('validation_gate')} |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Exploratory exit-model lab only.",
            "- No OOS promotion.",
            "- No observer, paper, or live permission.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Exploratory exit-model lab for derivatives/context entries.")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="1h")
    parser.add_argument("--families", default="funding_extreme_fade")
    parser.add_argument("--sides", default="SHORT")
    parser.add_argument("--regime-filters", default="none")
    parser.add_argument("--context-modes", default="spot_confirm,sweep_confirm")
    parser.add_argument("--train-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2030-01-01T00:00:00+00:00")
    parser.add_argument("--lookbacks", default="6,12")
    parser.add_argument("--price-atr", default="0.4,0.6")
    parser.add_argument("--oi-pct", default="0.15,0.25")
    parser.add_argument("--funding-abs", default="0.0001")
    parser.add_argument("--close-location", default="0.55,0.65")
    parser.add_argument("--spot-divergence-pct", default="0.02,0.05")
    parser.add_argument("--spot-volume-ratio", default="0.5")
    parser.add_argument("--sweep-lookback", default="12,24")
    parser.add_argument("--exit-models", default="fixed_rr,stop_only_time,breakeven_after_r,atr_trailing")
    parser.add_argument("--stop-atr", default="0.75,1.0,1.25")
    parser.add_argument("--take-atr", default="1.0,1.5,2.0,3.0")
    parser.add_argument("--max-hold-bars", default="8,16,24")
    parser.add_argument("--breakeven-after-r", default="0.75,1.0")
    parser.add_argument("--trail-atr", default="0.75,1.0,1.25")
    parser.add_argument("--max-entry-configs-per-interval", type=int, default=120)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--train-min-trades", type=int, default=30)
    parser.add_argument("--train-min-expectancy", type=float, default=0.0)
    parser.add_argument("--train-min-stable-folds", type=int, default=2)
    parser.add_argument("--train-max-drawdown", type=float, default=20.0)
    parser.add_argument("--validation-min-trades", type=int, default=8)
    parser.add_argument("--validation-min-expectancy", type=float, default=0.0)
    parser.add_argument("--validation-min-stable-folds", type=int, default=1)
    parser.add_argument("--validation-max-drawdown", type=float, default=10.0)
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_CONTEXT_EXIT_MODEL_LAB_2026-06-29")
    args = parser.parse_args()

    cache_dir = resolve_path(args.cache_dir)
    intervals = parse_str_grid(args.intervals)
    lookbacks = tuple(parse_int_grid(args.lookbacks))
    sweep_lookbacks = tuple(parse_int_grid(args.sweep_lookback))
    cost_bps = args.fee_bps + args.slippage_bps
    exit_configs = build_exit_configs(args)
    entry_args = argparse.Namespace(**vars(args))
    entry_args.stop_atr = 1.0
    entry_args.take_atr = "1.0"
    entry_args.max_hold_bars = "1"
    interval_payloads: dict[str, tuple[list[dict[str, Any]], dict[int, dict[int, dict[str, float]]], int, int]] = {}
    entry_configs: list[CompositeConfig] = []
    data_meta: list[dict[str, Any]] = []

    for interval in intervals:
        rows, features, meta = load_interval(cache_dir, interval, lookbacks, sweep_lookbacks)
        data_meta.append(meta)
        if not rows or not features:
            continue
        train_end = split_index(rows, args.train_end)
        validation_end = split_index(rows, args.validation_end)
        interval_payloads[interval] = (rows, features, train_end, validation_end)
        entry_configs.extend(
            dedupe_entry_configs(diversified_limit(build_configs(entry_args, interval), max(1, args.max_entry_configs_per_interval)))
        )

    results: list[dict[str, Any]] = []
    for config in entry_configs:
        if config.interval not in interval_payloads:
            continue
        rows, features, train_end, validation_end = interval_payloads[config.interval]
        train_signal_indices = matching_signal_indices(config, features, start_index=0, end_index=train_end)
        validation_signal_indices = matching_signal_indices(config, features, start_index=train_end, end_index=validation_end)
        for exit_config in exit_configs:
            train = evaluate(
                config,
                exit_config,
                rows,
                features,
                start_index=0,
                end_index=train_end,
                cost_bps_per_side=cost_bps,
                folds=4,
                signal_indices=train_signal_indices,
            )
            train_gate = gate(
                train["summary"],
                train["folds"],
                min_trades=args.train_min_trades,
                min_expectancy=args.train_min_expectancy,
                min_stable_folds=args.train_min_stable_folds,
                max_drawdown=args.train_max_drawdown,
            )
            validation = evaluate(
                config,
                exit_config,
                rows,
                features,
                start_index=train_end,
                end_index=validation_end,
                cost_bps_per_side=cost_bps,
                folds=3,
                signal_indices=validation_signal_indices,
            )
            validation_gate = gate(
                validation["summary"],
                validation["folds"],
                min_trades=args.validation_min_trades,
                min_expectancy=args.validation_min_expectancy,
                min_stable_folds=args.validation_min_stable_folds,
                max_drawdown=args.validation_max_drawdown,
            )
            results.append(
                {
                    "strategy_id": config.strategy_id,
                    "entry_config": asdict(config),
                    "exit_config": asdict(exit_config),
                    "train": train,
                    "train_gate": train_gate,
                    "validation": validation,
                    "validation_gate": validation_gate,
                }
            )

    ranked = sorted(results, key=rank, reverse=True)
    validation_qualified = [item for item in ranked if item["validation_gate"]["pass"]]
    decision = "exploratory_exit_model_no_validation_candidate"
    if validation_qualified:
        decision = "exploratory_exit_model_validation_candidates_no_promotion"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "settings": {
            "intervals": intervals,
            "families": args.families,
            "sides": args.sides,
            "context_modes": args.context_modes,
            "train_end": args.train_end,
            "validation_end": args.validation_end,
            "exploratory_only": True,
        },
        "data": data_meta,
        "summary": {
            "entry_shapes": len(entry_configs),
            "exit_configs": len(exit_configs),
            "tested": len(results),
            "validation_qualified": len(validation_qualified),
        },
        "top_validation": ranked[:50],
        "runtime_boundary": {
            "research_only": True,
            "exploratory_only": True,
            "oos_promotion_allowed": False,
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
