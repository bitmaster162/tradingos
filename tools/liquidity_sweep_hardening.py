from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_confluence_eval import build_datasets, build_params, evaluate_dataset  # noqa: E402
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402


@dataclass(frozen=True)
class Trade:
    dataset_id: str
    strategy_id: str
    entry_ts: str
    exit_ts: str
    side: str
    entry: float
    exit: float
    stop: float
    take: float
    atr: float
    r_net: float
    exit_reason: str
    bars_held: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bucket_predicate(name: str) -> Callable[[dict[str, Any]], bool]:
    if name == "short_sweep_all":
        return lambda item: item["side_hint"] == "SHORT"
    if name == "short_sweep_funding_reversal":
        return lambda item: item["side_hint"] == "SHORT" and item["confluence"]["funding_reversal_aligned"]
    if name == "short_sweep_funding_reversal_htf_not_against":
        return (
            lambda item: item["side_hint"] == "SHORT"
            and item["confluence"]["funding_reversal_aligned"]
            and item["confluence"]["htf_not_against"]
        )
    raise ValueError(f"unsupported bucket: {name}")


def max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return round(drawdown, 6)


def max_losing_streak(values: list[float]) -> int:
    streak = 0
    worst = 0
    for value in values:
        if value < 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


def summarize_trades(trades: list[Trade]) -> dict[str, Any]:
    r_values = [trade.r_net for trade in trades]
    wins = [value for value in r_values if value > 0]
    losses = [value for value in r_values if value <= 0]
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(trades) * 100.0, 3) if trades else None,
        "expectancy_r": round(sum(r_values) / len(r_values), 6) if trades else None,
        "net_r_total": round(sum(r_values), 6) if trades else 0.0,
        "avg_win_r": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 6) if losses else None,
        "max_drawdown_r": max_drawdown(r_values),
        "max_losing_streak": max_losing_streak(r_values),
    }


def fold_summaries(trades: list[Trade], folds: int) -> list[dict[str, Any]]:
    if not trades:
        return []
    ordered = sorted(trades, key=lambda item: item.entry_ts)
    result: list[dict[str, Any]] = []
    for fold in range(folds):
        start = round(len(ordered) * fold / folds)
        end = round(len(ordered) * (fold + 1) / folds)
        chunk = ordered[start:end]
        summary = summarize_trades(chunk)
        summary["fold"] = fold + 1
        summary["stable"] = bool(
            summary["trades"] >= 5
            and (summary["expectancy_r"] or 0.0) > 0
            and (summary["winrate_pct"] or 0.0) >= 50.0
        )
        result.append(summary)
    return result


def simulate_trade(
    *,
    dataset_id: str,
    strategy_id: str,
    bars: list[Any],
    signal: dict[str, Any],
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    cost_bps_per_side: float,
) -> Trade | None:
    signal_index = int(signal["bar_index"])
    entry_index = signal_index + 1
    if entry_index >= len(bars):
        return None
    entry_bar = bars[entry_index]
    entry = entry_bar.open
    atr = float(signal["atr"])
    if atr <= 0:
        return None
    side = str(signal["side_hint"]).upper()
    risk = atr * stop_atr
    if risk <= 0:
        return None
    if side == "SHORT":
        stop = entry + risk
        take = entry - atr * take_atr
    else:
        stop = entry - risk
        take = entry + atr * take_atr

    exit_price = bars[min(len(bars) - 1, entry_index + max_hold_bars)].close
    exit_reason = "time_exit"
    exit_index = min(len(bars) - 1, entry_index + max_hold_bars)
    for index in range(entry_index, min(len(bars), entry_index + max_hold_bars + 1)):
        bar = bars[index]
        if side == "SHORT":
            stop_hit = bar.high >= stop
            take_hit = bar.low <= take
        else:
            stop_hit = bar.low <= stop
            take_hit = bar.high >= take
        if stop_hit and take_hit:
            exit_price = stop
            exit_reason = "stop_first_same_bar"
            exit_index = index
            break
        if take_hit:
            exit_price = take
            exit_reason = "take_profit"
            exit_index = index
            break
        if stop_hit:
            exit_price = stop
            exit_reason = "stop_loss"
            exit_index = index
            break

    if side == "SHORT":
        gross_r = (entry - exit_price) / risk
    else:
        gross_r = (exit_price - entry) / risk
    round_turn_cost_quote = (entry + exit_price) * cost_bps_per_side / 10_000.0
    cost_r = round_turn_cost_quote / risk
    r_net = gross_r - cost_r
    return Trade(
        dataset_id=dataset_id,
        strategy_id=strategy_id,
        entry_ts=entry_bar.ts,
        exit_ts=bars[exit_index].ts,
        side=side,
        entry=round(entry, 8),
        exit=round(exit_price, 8),
        stop=round(stop, 8),
        take=round(take, 8),
        atr=round(atr, 8),
        r_net=round(r_net, 6),
        exit_reason=exit_reason,
        bars_held=exit_index - entry_index + 1,
    )


def simulate_strategy(
    *,
    dataset_id: str,
    bars: list[Any],
    signals: list[dict[str, Any]],
    bucket: str,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    cost_bps_per_side: float,
    no_overlap: bool,
) -> list[Trade]:
    predicate = bucket_predicate(bucket)
    strategy_id = f"{bucket}_s{stop_atr:g}_t{take_atr:g}_h{max_hold_bars}"
    trades: list[Trade] = []
    last_exit_bar = -1
    for signal in sorted([item for item in signals if predicate(item)], key=lambda item: int(item["bar_index"])):
        if no_overlap and int(signal["bar_index"]) <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=stop_atr,
            take_atr=take_atr,
            max_hold_bars=max_hold_bars,
            cost_bps_per_side=cost_bps_per_side,
        )
        if trade is None:
            continue
        trades.append(trade)
        if no_overlap:
            # Convert exit timestamp back to bar index by searching near the signal window.
            for index in range(int(signal["bar_index"]) + 1, min(len(bars), int(signal["bar_index"]) + max_hold_bars + 2)):
                if bars[index].ts == trade.exit_ts:
                    last_exit_bar = index
                    break
    return trades


def gate(summary: dict[str, Any], folds: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    stable_folds = sum(1 for item in folds if item.get("stable"))
    checks = {
        "min_trades": (summary["trades"] or 0) >= args.min_trades,
        "min_expectancy_r": (summary["expectancy_r"] or -999.0) >= args.min_expectancy_r,
        "min_winrate_pct": (summary["winrate_pct"] or 0.0) >= args.min_winrate_pct,
        "min_stable_folds": stable_folds >= args.min_stable_folds,
        "max_drawdown_r": (summary["max_drawdown_r"] or 0.0) >= -abs(args.max_drawdown_r),
    }
    passed = all(checks.values())
    return {
        "pass": passed,
        "verdict": "paper_candidate" if passed else "do_not_trade",
        "checks": checks,
        "stable_folds": stable_folds,
        "required": {
            "min_trades": args.min_trades,
            "min_expectancy_r": args.min_expectancy_r,
            "min_winrate_pct": args.min_winrate_pct,
            "min_stable_folds": args.min_stable_folds,
            "max_drawdown_r": -abs(args.max_drawdown_r),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidity Sweep Hardening",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research-only hardening backtest.",
        "- Entry is delayed to the next bar open after the detected event to reduce candle-close leakage.",
        "- Same-bar stop/take ambiguity is resolved conservatively as stop first.",
        "- Fees/slippage are included as bps cost per side.",
        "- No orders are sent and no live permission is granted.",
        "",
        "## Top Results",
        "",
        "| Strategy | Trades | Winrate | Exp R | Net R | Stable Folds | Verdict |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["top_results"]:
        summary = item["summary"]
        gate_result = item["gate"]
        lines.append(
            f"| `{item['strategy_id']}` | `{summary['trades']}` | `{summary['winrate_pct']}` | "
            f"`{summary['expectancy_r']}` | `{summary['net_r_total']}` | "
            f"`{gate_result['stable_folds']}` | `{gate_result['verdict']}` |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Passed strategies: `{report['passed_count']}`.",
            "- If `passed_count` is `0`, keep all sweep-confluence buckets blocked from paper/live trading.",
            "- If any strategy passes, next step is separate out-of-sample/paper validation, not live trading.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardening backtest for liquidity_sweep_eq confluence candidates")
    parser.add_argument("--out-prefix", default="docs/LIQUIDITY_SWEEP_HARDENING_2026-06-03")
    parser.add_argument("--cache-dir", default="data/cache/binance")
    parser.add_argument("--buckets", default="short_sweep_all,short_sweep_funding_reversal,short_sweep_funding_reversal_htf_not_against")
    parser.add_argument("--stop-grid", default="1.0,1.25,1.5")
    parser.add_argument("--take-grid", default="1.0,1.5,2.0")
    parser.add_argument("--hold-grid", default="6,12,18")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-trades", type=int, default=50)
    parser.add_argument("--min-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-winrate-pct", type=float, default=52.0)
    parser.add_argument("--min-stable-folds", type=int, default=3)
    parser.add_argument("--max-drawdown-r", type=float, default=8.0)
    parser.add_argument("--no-overlap", action=argparse.BooleanOptionalAction, default=True)

    # Confluence args reused by the evaluator.
    parser.add_argument("--config", default="configs/BitEvo_composite_config.json")
    parser.add_argument("--lookback", type=int, default=None)
    parser.add_argument("--eqh-tolerance-pct", type=float, default=None)
    parser.add_argument("--eql-tolerance-pct", type=float, default=None)
    parser.add_argument("--sweep-displacement-ticks", type=float, default=None)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--forward-bars", type=int, default=12)
    parser.add_argument("--atr-window", type=int, default=14)
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--oi-spike-abs-pct", type=float, default=None)
    parser.add_argument("--funding-hot-abs", type=float, default=0.00005)
    parser.add_argument("--funding-neutral-abs", type=float, default=0.00002)
    parser.add_argument("--min-events", type=int, default=15)
    args = parser.parse_args()

    params = build_params(args)
    bucket_names = [item.strip() for item in args.buckets.split(",") if item.strip()]
    stop_grid = [float(item) for item in args.stop_grid.split(",") if item.strip()]
    take_grid = [float(item) for item in args.take_grid.split(",") if item.strip()]
    hold_grid = [int(item) for item in args.hold_grid.split(",") if item.strip()]
    cost_bps_per_side = args.fee_bps + args.slippage_bps

    dataset_payloads = []
    bars_by_dataset: dict[str, list[Any]] = {}
    signals_by_dataset: dict[str, list[dict[str, Any]]] = {}
    for dataset in build_datasets(args.cache_dir):
        evaluated = evaluate_dataset(dataset, args, params)
        dataset_id = evaluated["dataset_id"]
        bars_by_dataset[dataset_id] = load_ohlcv(Path(dataset["klines"]))
        signals_by_dataset[dataset_id] = evaluated["_all_outcomes"]
        dataset_payloads.append(
            {
                "dataset_id": dataset_id,
                "signals": len(evaluated["_all_outcomes"]),
                "derivative_coverage": evaluated["derivative_coverage"],
            }
        )

    results: list[dict[str, Any]] = []
    for bucket in bucket_names:
        for stop_atr in stop_grid:
            for take_atr in take_grid:
                for max_hold_bars in hold_grid:
                    trades: list[Trade] = []
                    for dataset_id, signals in signals_by_dataset.items():
                        trades.extend(
                            simulate_strategy(
                                dataset_id=dataset_id,
                                bars=bars_by_dataset[dataset_id],
                                signals=signals,
                                bucket=bucket,
                                stop_atr=stop_atr,
                                take_atr=take_atr,
                                max_hold_bars=max_hold_bars,
                                cost_bps_per_side=cost_bps_per_side,
                                no_overlap=args.no_overlap,
                            )
                        )
                    trades = sorted(trades, key=lambda item: item.entry_ts)
                    summary = summarize_trades(trades)
                    folds = fold_summaries(trades, args.folds)
                    strategy_id = f"{bucket}_s{stop_atr:g}_t{take_atr:g}_h{max_hold_bars}"
                    results.append(
                        {
                            "strategy_id": strategy_id,
                            "bucket": bucket,
                            "params": {
                                "stop_atr": stop_atr,
                                "take_atr": take_atr,
                                "max_hold_bars": max_hold_bars,
                                "fee_bps": args.fee_bps,
                                "slippage_bps": args.slippage_bps,
                                "no_overlap": args.no_overlap,
                            },
                            "summary": summary,
                            "folds": folds,
                            "gate": gate(summary, folds, args),
                            "sample_trades": [trade.__dict__ for trade in trades[:20]],
                        }
                    )

    ranked = sorted(
        results,
        key=lambda item: (
            1 if item["gate"]["pass"] else 0,
            item["summary"]["expectancy_r"] or -999.0,
            item["gate"]["stable_folds"],
            item["summary"]["trades"],
            item["summary"]["winrate_pct"] or 0.0,
        ),
        reverse=True,
    )
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_hardening_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "datasets": dataset_payloads,
        "gate_requirements": {
            "min_trades": args.min_trades,
            "min_expectancy_r": args.min_expectancy_r,
            "min_winrate_pct": args.min_winrate_pct,
            "min_stable_folds": args.min_stable_folds,
            "max_drawdown_r": -abs(args.max_drawdown_r),
        },
        "passed_count": sum(1 for item in results if item["gate"]["pass"]),
        "top_results": ranked[:12],
        "all_results": results,
    }
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(
        {
            "passed_count": report["passed_count"],
            "top_results": [
                {
                    "strategy_id": item["strategy_id"],
                    "summary": item["summary"],
                    "gate": item["gate"],
                }
                for item in ranked[:5]
            ],
            "json": str(out_prefix.with_suffix(".json")),
            "md": str(out_prefix.with_suffix(".md")),
            "can_trade": False,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
