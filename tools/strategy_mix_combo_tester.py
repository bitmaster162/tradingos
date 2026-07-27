#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.event_feature_factory import build_features, load_csv_by_time  # noqa: E402
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_hardening import fold_summaries, simulate_trade, summarize_trades  # noqa: E402


@dataclass(frozen=True)
class ComboConfig:
    strategy_id: str
    interval: str
    side: str
    conditions: tuple[str, ...]
    stop_atr: float
    take_atr: float
    max_hold_bars: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_rr_list(value: str) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for raw in parse_csv_list(value):
        if ":" in raw:
            left, right = raw.split(":", 1)
            out.append((float(left), float(right)))
        else:
            out.append((1.0, float(raw)))
    return out


def safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def previous_high_low(bars: list[Any], index: int, lookback: int) -> tuple[float | None, float | None]:
    if index < lookback:
        return None, None
    chunk = bars[index - lookback : index]
    return max(bar.high for bar in chunk), min(bar.low for bar in chunk)


def trend_atr(bars: list[Any], features: list[dict[str, Any]], index: int, lookback: int) -> float | None:
    if index < lookback:
        return None
    atr = features[index].get("atr")
    if atr is None or atr <= 0:
        return None
    return (bars[index].close - bars[index - lookback].close) / atr


def build_condition_matrix(bars: list[Any], features: list[dict[str, Any]]) -> dict[str, list[bool]]:
    matrix: dict[str, list[bool]] = {}
    names = [
        "breakout_up_20",
        "breakout_down_20",
        "breakout_up_40",
        "breakout_down_40",
        "sweep_up_reclaim_20",
        "sweep_down_reclaim_20",
        "near_high_20",
        "near_low_20",
        "trend_up_20",
        "trend_down_20",
        "trend_up_40",
        "trend_down_40",
        "compression_atr",
        "expansion_atr",
        "volume_hot",
        "volume_quiet",
        "body_accept",
        "funding_positive",
        "funding_negative",
        "funding_compressed",
        "oi_up",
        "oi_down",
        "spot_confirms_long",
        "spot_confirms_short",
        "spot_diverges_long",
        "spot_diverges_short",
    ]
    for name in names:
        matrix[name] = []

    for index, bar in enumerate(bars):
        f = features[index]
        high20, low20 = previous_high_low(bars, index, 20)
        high40, low40 = previous_high_low(bars, index, 40)
        atr = f.get("atr")
        width20 = (high20 - low20) if high20 is not None and low20 is not None else None
        trend20 = trend_atr(bars, features, index, 20)
        trend40 = trend_atr(bars, features, index, 40)
        funding = f.get("funding")
        oi_delta = f.get("oi_delta_pct")
        spot_div = f.get("spot_perp_divergence_pct")
        volume_z = f.get("volume_z")
        atr_ratio = f.get("atr_ratio")
        body_pct = f.get("body_pct")

        breakout_up_20 = high20 is not None and bar.close > high20
        breakout_down_20 = low20 is not None and bar.close < low20
        breakout_up_40 = high40 is not None and bar.close > high40
        breakout_down_40 = low40 is not None and bar.close < low40
        sweep_up_reclaim_20 = high20 is not None and bar.high > high20 and bar.close < high20
        sweep_down_reclaim_20 = low20 is not None and bar.low < low20 and bar.close > low20
        near_high_20 = high20 is not None and width20 is not None and width20 > 0 and bar.close >= high20 - width20 * 0.20
        near_low_20 = low20 is not None and width20 is not None and width20 > 0 and bar.close <= low20 + width20 * 0.20

        values = {
            "breakout_up_20": breakout_up_20,
            "breakout_down_20": breakout_down_20,
            "breakout_up_40": breakout_up_40,
            "breakout_down_40": breakout_down_40,
            "sweep_up_reclaim_20": sweep_up_reclaim_20,
            "sweep_down_reclaim_20": sweep_down_reclaim_20,
            "near_high_20": near_high_20,
            "near_low_20": near_low_20,
            "trend_up_20": trend20 is not None and trend20 >= 1.0,
            "trend_down_20": trend20 is not None and trend20 <= -1.0,
            "trend_up_40": trend40 is not None and trend40 >= 1.5,
            "trend_down_40": trend40 is not None and trend40 <= -1.5,
            "compression_atr": atr_ratio is not None and atr_ratio <= 0.85,
            "expansion_atr": atr_ratio is not None and atr_ratio >= 1.15,
            "volume_hot": volume_z is not None and volume_z >= 0.5,
            "volume_quiet": volume_z is not None and volume_z <= 0.0,
            "body_accept": body_pct is not None and body_pct >= 0.35,
            "funding_positive": funding is not None and funding >= 0,
            "funding_negative": funding is not None and funding < 0,
            "funding_compressed": funding is not None and abs(float(funding)) <= 0.0002,
            "oi_up": oi_delta is not None and oi_delta >= 0,
            "oi_down": oi_delta is not None and oi_delta < 0,
            "spot_confirms_long": spot_div is not None and spot_div >= 0,
            "spot_confirms_short": spot_div is not None and spot_div <= 0,
            "spot_diverges_long": spot_div is not None and spot_div <= -0.03,
            "spot_diverges_short": spot_div is not None and spot_div >= 0.03,
        }
        for name in names:
            matrix[name].append(bool(values[name]) and atr is not None and atr > 0)
    return matrix


LONG_STRUCTURES = ("breakout_up_20", "breakout_up_40", "sweep_down_reclaim_20", "near_low_20")
SHORT_STRUCTURES = ("breakout_down_20", "breakout_down_40", "sweep_up_reclaim_20", "near_high_20")
LONG_CONTEXT = (
    "trend_up_20",
    "trend_down_20",
    "trend_up_40",
    "compression_atr",
    "expansion_atr",
    "volume_hot",
    "volume_quiet",
    "body_accept",
    "funding_negative",
    "funding_positive",
    "funding_compressed",
    "oi_up",
    "oi_down",
    "spot_confirms_long",
    "spot_diverges_long",
)
SHORT_CONTEXT = (
    "trend_down_20",
    "trend_up_20",
    "trend_down_40",
    "compression_atr",
    "expansion_atr",
    "volume_hot",
    "volume_quiet",
    "body_accept",
    "funding_positive",
    "funding_negative",
    "funding_compressed",
    "oi_up",
    "oi_down",
    "spot_confirms_short",
    "spot_diverges_short",
)


def build_combos(interval: str, rr_pairs: list[tuple[float, float]], max_holds: list[int], max_combos_per_side: int) -> list[ComboConfig]:
    configs: list[ComboConfig] = []
    for side, structures, contexts in (("LONG", LONG_STRUCTURES, LONG_CONTEXT), ("SHORT", SHORT_STRUCTURES, SHORT_CONTEXT)):
        base_condition_sets: list[tuple[str, ...]] = []
        for structure in structures:
            for combo_size in (1, 2):
                for context_combo in itertools.combinations(contexts, combo_size):
                    base_condition_sets.append((structure, *context_combo))
        base_condition_sets = base_condition_sets[:max_combos_per_side]
        for conditions in base_condition_sets:
            for stop, take in rr_pairs:
                for hold in max_holds:
                    strategy_id = f"mix_{interval}_{side.lower()}_{'_'.join(conditions)}_rr{stop:g}x{take:g}_h{hold}"
                    configs.append(
                        ComboConfig(
                            strategy_id=strategy_id,
                            interval=interval,
                            side=side,
                            conditions=conditions,
                            stop_atr=stop,
                            take_atr=take,
                            max_hold_bars=hold,
                        )
                    )
    return configs


def generate_signals(config: ComboConfig, bars: list[Any], features: list[dict[str, Any]], matrix: dict[str, list[bool]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for index in range(len(bars)):
        if not all(matrix[name][index] for name in config.conditions):
            continue
        atr = features[index].get("atr")
        if atr is None or atr <= 0:
            continue
        signals.append(
            {
                "bar_index": index,
                "side_hint": config.side,
                "atr": atr,
                "reason": "+".join(config.conditions),
                "feature_snapshot": {
                    "conditions": list(config.conditions),
                    "funding": features[index].get("funding"),
                    "oi_delta_pct": features[index].get("oi_delta_pct"),
                    "spot_perp_divergence_pct": features[index].get("spot_perp_divergence_pct"),
                    "volume_z": features[index].get("volume_z"),
                    "atr_ratio": features[index].get("atr_ratio"),
                },
            }
        )
    return signals


def stable_fold_count(folds: list[dict[str, Any]]) -> int:
    return sum(1 for item in folds if item.get("stable"))


def classify(summary: dict[str, Any], folds: list[dict[str, Any]], *, min_trades: int, rr: float) -> str:
    trades = int(summary.get("trades") or 0)
    expectancy = float(summary.get("expectancy_r") if summary.get("expectancy_r") is not None else -999.0)
    winrate = float(summary.get("winrate_pct") if summary.get("winrate_pct") is not None else 0.0)
    drawdown = float(summary.get("max_drawdown_r") or 0.0)
    stable = stable_fold_count(folds)
    breakeven = 100.0 / (1.0 + rr)
    if trades >= min_trades and expectancy >= 0.10 and winrate >= breakeven + 8.0 and stable >= max(3, len(folds) // 2) and drawdown >= -30:
        return "candidate_needs_holdout"
    if trades >= max(30, min_trades // 2) and expectancy > 0 and winrate > breakeven + 4.0 and stable >= 2:
        return "watchlist_only"
    if trades >= min_trades and expectancy < 0:
        return "reject_negative_large_sample"
    return "research_only"


def evaluate_config(
    config: ComboConfig,
    *,
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    cost_bps_per_side: float,
    folds_count: int,
    min_trades: int,
    no_overlap: bool,
) -> dict[str, Any]:
    signals = generate_signals(config, bars, features, matrix)
    trades = []
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: int(item["bar_index"])):
        if no_overlap and int(signal["bar_index"]) <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"mix_combo_BTCUSDT_{config.interval}",
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
    summary = summarize_trades(trades)
    folds = fold_summaries(trades, folds_count)
    rr = config.take_atr / config.stop_atr if config.stop_atr > 0 else 0.0
    breakeven = 100.0 / (1.0 + rr) if rr > 0 else None
    return {
        "strategy_id": config.strategy_id,
        "interval": config.interval,
        "side": config.side,
        "conditions": list(config.conditions),
        "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
        "rr_ratio": round(rr, 6),
        "breakeven_winrate_pct_before_costs": round(breakeven, 3) if breakeven is not None else None,
        "max_hold_bars": config.max_hold_bars,
        "signals": len(signals),
        "summary": summary,
        "folds": folds,
        "stable_folds": stable_fold_count(folds),
        "verdict": classify(summary, folds, min_trades=min_trades, rr=rr),
        "sample_trades": [trade.__dict__ for trade in trades[:5]],
    }


def load_interval_data(cache_dir: Path, interval: str, oi_lag: int, spot_perp_lookback: int) -> tuple[list[Any], list[dict[str, Any]], dict[str, list[bool]]]:
    futures_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv"
    spot_path = cache_dir / "spot" / "BTCUSDT" / f"{interval}_klines.csv"
    derivatives_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_oi_aligned.csv"
    bars = load_ohlcv(futures_path)
    spot_bars = load_ohlcv(spot_path) if spot_path.exists() else []
    spot_by_time = {bar.ts: bar for bar in spot_bars}
    derivatives_by_time = load_csv_by_time(derivatives_path)
    features = build_features(
        bars=bars,
        spot_by_time=spot_by_time,
        derivatives_by_time=derivatives_by_time,
        oi_lag=oi_lag,
        spot_perp_lookback=spot_perp_lookback,
        volume_window=20,
        atr_window=20,
    )
    return bars, features, build_condition_matrix(bars, features)


def family_key(result: dict[str, Any]) -> str:
    conditions = result["conditions"]
    if any("sweep" in item or "near_" in item for item in conditions):
        return "range_sweep"
    if any("breakout" in item for item in conditions):
        return "breakout"
    return "mixed"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Mix Combo Tester",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only combo grid over closed-bar BTCUSDT public/cache data.",
        "- No private credentials, no orders, no paper/live permission.",
        "- RR is simulated with ATR stop/take; time exits and costs mean realized average win is not guaranteed to equal target RR.",
        "",
        "## Why Winrate Alone Does Not Pass",
        "",
        "- A `60%+` winrate can still fail if sample size is small, folds are unstable, drawdown is large, or average loss is too large.",
        "- With RR `1:3`, breakeven is about `25%` before costs, but only if winners reach close to `+3R` and losers stay near `-1R`.",
        "- The tester therefore gates on expectancy, trades, stable folds and costs, not winrate alone.",
        "",
        "## Run Summary",
        "",
        f"- Combos requested: `{report['requested']}`.",
        f"- Completed: `{report['completed']}`.",
        f"- Candidate needs holdout: `{report['candidate_count']}`.",
        f"- Watchlist only: `{report['watchlist_count']}`.",
        f"- RR pairs: `{', '.join(report['settings']['rr_pairs'])}`.",
        "",
        "## Top Results",
        "",
        "| Verdict | Strategy | TF | Side | RR | Signals | Trades | Winrate | Exp R | Net R | Avg Win | Avg Loss | Stable Folds |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["top_results"]:
        s = item["summary"]
        lines.append(
            f"| `{item['verdict']}` | `{item['strategy_id']}` | `{item['interval']}` | `{item['side']}` | `{item['rr']}` | "
            f"`{item['signals']}` | `{s['trades']}` | `{s['winrate_pct']}` | `{s['expectancy_r']}` | `{s['net_r_total']}` | "
            f"`{s['avg_win_r']}` | `{s['avg_loss_r']}` | `{item['stable_folds']}` |"
        )
    lines.extend(["", "## Top RR 1:3 Results", "", "| Verdict | Strategy | TF | Side | Trades | Winrate | Exp R | Stable Folds |", "|---|---|---|---|---:|---:|---:|---:|"])
    for item in report["top_rr_1x3_results"]:
        s = item["summary"]
        lines.append(
            f"| `{item['verdict']}` | `{item['strategy_id']}` | `{item['interval']}` | `{item['side']}` | "
            f"`{s['trades']}` | `{s['winrate_pct']}` | `{s['expectancy_r']}` | `{item['stable_folds']}` |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Decision: `{report['decision']}`.",
            f"- Next action: `{report['next_action']}`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Research-only mix/combo strategy tester over BTCUSDT cache")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--rr", default="1:1,1:1.5,1:2,1:3")
    parser.add_argument("--max-holds", default="8,12,16")
    parser.add_argument("--max-combos-per-side", type=int, default=80)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=8)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_COMBO_TESTER_2026-06-08")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    rr_pairs = parse_rr_list(args.rr)
    max_holds = [int(item) for item in parse_csv_list(args.max_holds)]
    intervals = parse_csv_list(args.intervals)

    interval_payload: dict[str, tuple[list[Any], list[dict[str, Any]], dict[str, list[bool]]]] = {}
    configs: list[ComboConfig] = []
    for interval in intervals:
        interval_payload[interval] = load_interval_data(cache_dir, interval, args.oi_lag, args.spot_perp_lookback)
        configs.extend(build_combos(interval, rr_pairs, max_holds, args.max_combos_per_side))

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {}
        for config in configs:
            bars, features, matrix = interval_payload[config.interval]
            futures[
                executor.submit(
                    evaluate_config,
                    config,
                    bars=bars,
                    features=features,
                    matrix=matrix,
                    cost_bps_per_side=args.fee_bps + args.slippage_bps,
                    folds_count=args.folds,
                    min_trades=args.min_trades,
                    no_overlap=not args.allow_overlap,
                )
            ] = config
        for future in as_completed(futures):
            config = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append({"strategy_id": config.strategy_id, "error": str(exc)})

    results.sort(
        key=lambda item: (
            item["verdict"] == "candidate_needs_holdout",
            item["verdict"] == "watchlist_only",
            item["summary"]["expectancy_r"] if item["summary"]["expectancy_r"] is not None else -999.0,
            item["stable_folds"],
            item["summary"]["trades"] or 0,
        ),
        reverse=True,
    )
    candidate_count = sum(1 for item in results if item["verdict"] == "candidate_needs_holdout")
    watchlist_count = sum(1 for item in results if item["verdict"] == "watchlist_only")
    top_rr_1x3 = [item for item in results if item["rr"] == "1:3"][:25]
    decision = "do_not_trade"
    next_action = "run_holdout_for_candidates" if candidate_count else "inspect_watchlist_or_generate_more_features" if watchlist_count else "reject_current_combo_grid"
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_mix_combo_grid_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "settings": {
            "cache_dir": str(cache_dir),
            "intervals": intervals,
            "rr_pairs": [f"{stop:g}:{take:g}" for stop, take in rr_pairs],
            "max_holds": max_holds,
            "min_trades": args.min_trades,
            "folds": args.folds,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "no_overlap": not args.allow_overlap,
        },
        "requested": len(configs),
        "completed": len(results),
        "errors": errors[:50],
        "candidate_count": candidate_count,
        "watchlist_count": watchlist_count,
        "decision": decision,
        "next_action": next_action,
        "top_results": results[:40],
        "top_rr_1x3_results": top_rr_1x3,
        "all_results": results,
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
                "requested": report["requested"],
                "completed": report["completed"],
                "candidate_count": candidate_count,
                "watchlist_count": watchlist_count,
                "best": results[0] if results else None,
                "best_rr_1x3": top_rr_1x3[0] if top_rr_1x3 else None,
                "decision": decision,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
