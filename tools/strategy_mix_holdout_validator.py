#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_hardening import fold_summaries, simulate_trade, summarize_trades  # noqa: E402
from tools.strategy_mix_combo_tester import build_condition_matrix, generate_signals, load_interval_data, stable_fold_count  # noqa: E402


@dataclass(frozen=True)
class ReplayConfig:
    strategy_id: str
    interval: str
    side: str
    conditions: tuple[str, ...]
    stop_atr: float
    take_atr: float
    max_hold_bars: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_rr(value: str) -> tuple[float, float]:
    left, right = value.split(":", 1)
    return float(left), float(right)


def result_to_config(item: dict[str, Any]) -> ReplayConfig:
    stop, take = parse_rr(str(item["rr"]))
    return ReplayConfig(
        strategy_id=str(item["strategy_id"]),
        interval=str(item["interval"]),
        side=str(item["side"]),
        conditions=tuple(str(value) for value in item["conditions"]),
        stop_atr=stop,
        take_atr=take,
        max_hold_bars=int(item["max_hold_bars"]),
    )


def replay_segment(
    config: ReplayConfig,
    *,
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    start_index: int,
    end_index: int,
    cost_bps_per_side: float,
    folds_count: int,
    no_overlap: bool,
) -> dict[str, Any]:
    signal_config = type(
        "SignalConfig",
        (),
        {
            "conditions": config.conditions,
            "side": config.side,
            "strategy_id": config.strategy_id,
            "interval": config.interval,
            "stop_atr": config.stop_atr,
            "take_atr": config.take_atr,
            "max_hold_bars": config.max_hold_bars,
        },
    )()
    all_signals = generate_signals(signal_config, bars, features, matrix)
    signals = [item for item in all_signals if start_index <= int(item["bar_index"]) < end_index]
    trades = []
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: int(item["bar_index"])):
        if no_overlap and int(signal["bar_index"]) <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"mix_holdout_BTCUSDT_{config.interval}",
            strategy_id=config.strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=config.stop_atr,
            take_atr=config.take_atr,
            max_hold_bars=config.max_hold_bars,
            cost_bps_per_side=cost_bps_per_side,
        )
        if trade is None:
            continue
        trades.append(trade)
        if no_overlap:
            for offset in range(int(signal["bar_index"]) + 1, min(len(bars), int(signal["bar_index"]) + config.max_hold_bars + 2)):
                if bars[offset].ts == trade.exit_ts:
                    last_exit_bar = offset
                    break
    folds = fold_summaries(trades, folds_count)
    return {
        "signals": len(signals),
        "summary": summarize_trades(trades),
        "stable_folds": stable_fold_count(folds),
        "folds": folds,
        "sample_trades": [trade.__dict__ for trade in trades[:5]],
    }


def classify_holdout(full_summary: dict[str, Any], holdout_summary: dict[str, Any], *, min_holdout_trades: int, min_holdout_expectancy: float) -> str:
    holdout_trades = int(holdout_summary.get("trades") or 0)
    holdout_exp = float(holdout_summary.get("expectancy_r") if holdout_summary.get("expectancy_r") is not None else -999.0)
    full_exp = float(full_summary.get("expectancy_r") if full_summary.get("expectancy_r") is not None else -999.0)
    if holdout_trades >= min_holdout_trades and holdout_exp >= min_holdout_expectancy and holdout_exp >= full_exp * 0.35:
        return "holdout_watchlist_positive"
    if holdout_trades < min_holdout_trades:
        return "holdout_too_few_trades"
    if holdout_exp < 0:
        return "holdout_failed_negative"
    return "holdout_weak"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Mix Holdout Validation",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only holdout check for mined combo candidates.",
        "- This does not grant paper/live permission.",
        "- Holdout uses the last configured fraction of bars as a fresh segment after grid discovery.",
        "",
        "## Summary",
        "",
        f"- Source: `{report['source_report']}`.",
        f"- Tested candidates: `{report['tested']}`.",
        f"- Holdout positive: `{report['holdout_positive']}`.",
        f"- Decision: `{report['decision']}`.",
        "",
        "## Results",
        "",
        "| Verdict | Strategy | TF | RR | Full Trades | Full Exp | Holdout Trades | Holdout Winrate | Holdout Exp | Holdout Net R |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["results"]:
        fs = item["full"]["summary"]
        hs = item["holdout"]["summary"]
        lines.append(
            f"| `{item['verdict']}` | `{item['strategy_id']}` | `{item['interval']}` | `{item['rr']}` | "
            f"`{fs['trades']}` | `{fs['expectancy_r']}` | `{hs['trades']}` | `{hs['winrate_pct']}` | "
            f"`{hs['expectancy_r']}` | `{hs['net_r_total']}` |"
        )
    lines.extend(["", "## Next Action", "", f"- `{report['next_action']}`.", ""])
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Holdout validator for strategy_mix_combo_tester candidates")
    parser.add_argument("--source-report", default="docs/STRATEGY_MIX_COMBO_TESTER_2026-06-08.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--candidate-verdicts", default="candidate_needs_holdout,watchlist_only")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--holdout-fraction", type=float, default=0.25)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-holdout-trades", type=int, default=20)
    parser.add_argument("--min-holdout-expectancy", type=float, default=0.05)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_HOLDOUT_VALIDATION_2026-06-08")
    args = parser.parse_args()

    source_path = Path(args.source_report)
    source = json.loads(source_path.read_text(encoding="utf-8-sig"))
    allowed = {item.strip() for item in args.candidate_verdicts.split(",") if item.strip()}
    source_results = [item for item in source.get("all_results", []) if item.get("verdict") in allowed]
    source_results = source_results[: max(1, args.top)]
    interval_cache: dict[str, tuple[list[Any], list[dict[str, Any]], dict[str, list[bool]]]] = {}
    results: list[dict[str, Any]] = []
    for source_item in source_results:
        config = result_to_config(source_item)
        if config.interval not in interval_cache:
            interval_cache[config.interval] = load_interval_data(Path(args.cache_dir), config.interval, oi_lag=12, spot_perp_lookback=12)
        bars, features, matrix = interval_cache[config.interval]
        split = max(1, min(len(bars) - 2, int(len(bars) * (1.0 - args.holdout_fraction))))
        full = replay_segment(
            config,
            bars=bars,
            features=features,
            matrix=matrix,
            start_index=0,
            end_index=len(bars) - config.max_hold_bars - 1,
            cost_bps_per_side=args.fee_bps + args.slippage_bps,
            folds_count=args.folds,
            no_overlap=not args.allow_overlap,
        )
        holdout = replay_segment(
            config,
            bars=bars,
            features=features,
            matrix=matrix,
            start_index=split,
            end_index=len(bars) - config.max_hold_bars - 1,
            cost_bps_per_side=args.fee_bps + args.slippage_bps,
            folds_count=args.folds,
            no_overlap=not args.allow_overlap,
        )
        verdict = classify_holdout(
            full["summary"],
            holdout["summary"],
            min_holdout_trades=args.min_holdout_trades,
            min_holdout_expectancy=args.min_holdout_expectancy,
        )
        results.append(
            {
                "strategy_id": config.strategy_id,
                "interval": config.interval,
                "side": config.side,
                "conditions": list(config.conditions),
                "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
                "max_hold_bars": config.max_hold_bars,
                "verdict": verdict,
                "source_verdict": source_item.get("verdict"),
                "full": full,
                "holdout": holdout,
            }
        )
    results.sort(
        key=lambda item: (
            item["verdict"] == "holdout_watchlist_positive",
            item["holdout"]["summary"]["expectancy_r"] if item["holdout"]["summary"]["expectancy_r"] is not None else -999.0,
            item["holdout"]["summary"]["trades"] or 0,
        ),
        reverse=True,
    )
    positive = sum(1 for item in results if item["verdict"] == "holdout_watchlist_positive")
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_holdout_validation_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "source_report": str(source_path),
        "tested": len(results),
        "holdout_positive": positive,
        "decision": "do_not_trade",
        "next_action": "deepen_positive_holdout_candidates" if positive else "reject_or_redesign_current_combo_grid",
        "results": results,
        "can_trade": False,
    }
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "tested": len(results),
                "holdout_positive": positive,
                "best": results[0] if results else None,
                "decision": "do_not_trade",
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
