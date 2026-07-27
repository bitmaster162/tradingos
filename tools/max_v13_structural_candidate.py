from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import (  # noqa: E402
    INTERVAL_MS,
    candle_value,
    find_exit,
)
from tools.max_v11_candidate_validator import (  # noqa: E402
    atr14_at,
    bootstrap_report,
    fold_report,
    load_or_fetch,
    spot_volume_ratio_at,
    summarize_trades,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def pct_return_fast(rows: list[dict[str, str]], idx: int, lookback: int) -> float | None:
    if idx - lookback < 0:
        return None
    prev = candle_value(rows[idx - lookback], "close")
    cur = candle_value(rows[idx], "close")
    if prev == 0 or math.isnan(prev) or math.isnan(cur):
        return None
    return (cur - prev) / prev * 100


def divergence_sign(value: float | None, threshold: float = 0.03) -> str:
    if value is None or math.isnan(value):
        return "unknown"
    if value >= threshold:
        return "spot_stronger"
    if value <= -threshold:
        return "spot_weaker"
    return "neutral"


def spot_perp_features_fast(
    *,
    rows: list[dict[str, str]],
    spot_rows: list[dict[str, str]],
    idx: int,
) -> dict[str, Any]:
    if idx >= len(spot_rows):
        return {
            "spot_ret_3": None,
            "perp_ret_3": None,
            "spot_perp_divergence_3": None,
            "spot_ret_12": None,
            "perp_ret_12": None,
            "spot_perp_divergence_12": None,
            "spot_perp_divergence_12_sign": "unknown",
        }
    spot_ret_3 = pct_return_fast(spot_rows, idx, 3)
    perp_ret_3 = pct_return_fast(rows, idx, 3)
    spot_ret_12 = pct_return_fast(spot_rows, idx, 12)
    perp_ret_12 = pct_return_fast(rows, idx, 12)
    div3 = None if spot_ret_3 is None or perp_ret_3 is None else spot_ret_3 - perp_ret_3
    div12 = None if spot_ret_12 is None or perp_ret_12 is None else spot_ret_12 - perp_ret_12
    return {
        "spot_ret_3": None if spot_ret_3 is None else round(spot_ret_3, 6),
        "perp_ret_3": None if perp_ret_3 is None else round(perp_ret_3, 6),
        "spot_perp_divergence_3": None if div3 is None else round(div3, 6),
        "spot_ret_12": None if spot_ret_12 is None else round(spot_ret_12, 6),
        "perp_ret_12": None if perp_ret_12 is None else round(perp_ret_12, 6),
        "spot_perp_divergence_12": None if div12 is None else round(div12, 6),
        "spot_perp_divergence_12_sign": divergence_sign(div12),
    }


def build_trade_features(
    *,
    rows: list[dict[str, str]],
    spot_rows: list[dict[str, str]],
    htf_rows: list[dict[str, str]],
    htf_interval: str,
    i: int,
    interval_ms: int,
) -> dict[str, Any] | None:
    if i >= len(spot_rows):
        return None
    spot_window = spot_rows[: i + 1]
    spot_ready, spot_volume_ratio = spot_volume_ratio_at(spot_window)
    if not spot_ready or spot_volume_ratio is None:
        return None

    close = candle_value(rows[i], "close")
    prev = rows[i - 55 : i]
    if len(prev) < 55 or math.isnan(close):
        return None
    upper = max(candle_value(row, "high") for row in prev)
    lower = min(candle_value(row, "low") for row in prev)
    width = upper - lower
    atr = atr14_at(rows, i)
    if width <= 0 or math.isnan(atr) or atr <= 0:
        return None

    width_atr = width / atr
    near_low = close <= lower + max(width * 0.18, atr * 0.9)
    near_high = close >= upper - max(width * 0.18, atr * 0.9)

    trend_strength_20_atr = None
    if i >= 20 and atr > 0:
        prev_close_20 = candle_value(rows[i - 20], "close")
        if not math.isnan(prev_close_20):
            trend_strength_20_atr = (close - prev_close_20) / atr
    atr_pct = atr / close * 100 if close else None

    return {
        "signal_time": rows[i].get("time", str(i)),
        "signal_row": i,
        "close": close,
        "atr14": atr,
        "atr_pct": atr_pct,
        "atr_pct_rank_500": None,
        "ema50": None,
        "ema200": None,
        "ema200_distance_pct": None,
        "ema_state": "not_computed_v13_fast_path",
        "trend_strength_20_atr": trend_strength_20_atr,
        "htf_bias": "not_computed_v13_fast_path",
        "htf_regime": "not_computed_v13_fast_path",
        "htf_reason": "not_computed_v13_fast_path",
        "donchian_upper_55": upper,
        "donchian_lower_55": lower,
        "donchian_width_atr": width_atr,
        "donchian_width_atr_rank_500": None,
        "near_low": near_low,
        "near_high": near_high,
        "spot_volume_ratio": spot_volume_ratio,
        **spot_perp_features_fast(rows=rows, spot_rows=spot_rows, idx=i),
    }


def default_candidate_specs(args: argparse.Namespace) -> list[dict[str, Any]]:
    return [
        {
            "id": "v13_structural_weak_bid_short",
            "side": "SHORT",
            "requires": [
                "near_low",
                f"spot_volume_ratio <= {args.spot_volume_max}",
                f"{args.width_lower} <= donchian_width_atr <= {args.width_upper}",
                f"spot_perp_divergence_12 >= {args.divergence_min}",
            ],
            "predicate": lambda f: bool(
                f.get("near_low")
                and parse_float(f.get("spot_volume_ratio")) <= args.spot_volume_max
                and args.width_lower <= parse_float(f.get("donchian_width_atr")) <= args.width_upper
                and parse_float(f.get("spot_perp_divergence_12")) >= args.divergence_min
            ),
        },
        {
            "id": "v13_structural_near_low_short",
            "side": "SHORT",
            "requires": [
                "near_low",
                f"{args.width_lower} <= donchian_width_atr <= {args.width_upper}",
                f"spot_perp_divergence_12 >= {args.divergence_min}",
            ],
            "predicate": lambda f: bool(
                f.get("near_low")
                and args.width_lower <= parse_float(f.get("donchian_width_atr")) <= args.width_upper
                and parse_float(f.get("spot_perp_divergence_12")) >= args.divergence_min
            ),
        },
        {
            "id": "v13_structural_only_short",
            "side": "SHORT",
            "requires": [
                f"{args.width_lower} <= donchian_width_atr <= {args.width_upper}",
                f"spot_perp_divergence_12 >= {args.divergence_min}",
            ],
            "predicate": lambda f: bool(
                args.width_lower <= parse_float(f.get("donchian_width_atr")) <= args.width_upper
                and parse_float(f.get("spot_perp_divergence_12")) >= args.divergence_min
            ),
        },
    ]


def simulate_candidate(
    *,
    spec: dict[str, Any],
    rows: list[dict[str, str]],
    spot_rows: list[dict[str, str]],
    htf_rows: list[dict[str, str]],
    htf_interval: str,
    interval_ms: int,
    warmup_bars: int,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    fee_bps: float,
    slippage_bps: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    trades: list[dict[str, Any]] = []
    skipped = {
        "warmup": 0,
        "feature_not_ready": 0,
        "rule_not_matched": 0,
        "no_next_bar": 0,
        "bad_entry": 0,
    }
    predicate: Callable[[dict[str, Any]], bool] = spec["predicate"]
    side = str(spec["side"])
    i = max(warmup_bars, 220)
    while i < len(rows) - 1:
        if i < warmup_bars:
            skipped["warmup"] += 1
            i += 1
            continue
        features = build_trade_features(
            rows=rows,
            spot_rows=spot_rows,
            htf_rows=htf_rows,
            htf_interval=htf_interval,
            i=i,
            interval_ms=interval_ms,
        )
        if features is None:
            skipped["feature_not_ready"] += 1
            i += 1
            continue
        if not predicate(features):
            skipped["rule_not_matched"] += 1
            i += 1
            continue
        next_index = i + 1
        if next_index >= len(rows):
            skipped["no_next_bar"] += 1
            break
        entry_open = candle_value(rows[next_index], "open")
        atr = parse_float(features.get("atr14"))
        if math.isnan(entry_open) or math.isnan(atr) or atr <= 0:
            skipped["bad_entry"] += 1
            i += 1
            continue

        slip = slippage_bps / 10000
        if side == "SHORT":
            entry = entry_open * (1 - slip)
            risk = stop_atr * atr
            stop = entry + risk
            take_profit = entry - take_atr * atr
        else:
            entry = entry_open * (1 + slip)
            risk = stop_atr * atr
            stop = entry - risk
            take_profit = entry + take_atr * atr
        exit_index, raw_exit, exit_reason = find_exit(
            rows,
            start_index=next_index,
            side=side,
            entry=entry,
            stop=stop,
            take_profit=take_profit,
            max_hold_bars=max_hold_bars,
        )
        exit_price = raw_exit * (1 + slip) if side == "SHORT" else raw_exit * (1 - slip)
        gross_r = (entry - exit_price) / risk if side == "SHORT" else (exit_price - entry) / risk
        fee_cost = ((entry + exit_price) * (fee_bps / 10000)) / risk
        net_r = gross_r - fee_cost
        trades.append(
            {
                "index": len(trades) + 1,
                "candidate_id": spec["id"],
                "side": side,
                "setup": spec["id"],
                "signal_row": i,
                "entry_row": next_index,
                "exit_row": exit_index,
                "signal_time": features["signal_time"],
                "entry_time": rows[next_index].get("time", str(next_index)),
                "exit_time": rows[exit_index].get("time", str(exit_index)),
                "entry": round(entry, 8),
                "stop": round(stop, 8),
                "take_profit": round(take_profit, 8),
                "exit": round(exit_price, 8),
                "exit_reason": exit_reason,
                "bars_held": max(1, exit_index - next_index + 1),
                "gross_r": round(gross_r, 6),
                "net_r": round(net_r, 6),
                **{
                    key: (round(value, 8) if isinstance(value, float) and not math.isnan(value) else value)
                    for key, value in features.items()
                    if key not in {"close"}
                },
            }
        )
        i = exit_index + 1
    return trades, skipped


def gate_candidate(
    *,
    trades: list[dict[str, Any]],
    rows_count: int,
    warmup_bars: int,
    folds: int,
    bootstrap_iterations: int,
    bootstrap_seed: int,
    min_trades: int,
    min_expectancy_r: float,
    min_winrate_pct: float,
    min_bootstrap_prob_gt_0: float,
) -> dict[str, Any]:
    summary = summarize_trades(trades)
    fold_rows = fold_report(trades, rows_count=rows_count, warmup_bars=warmup_bars, folds=folds)
    stable_folds = [
        fold
        for fold in fold_rows
        if fold["trades"] > 0
        and fold["expectancy_r"] is not None
        and float(fold["expectancy_r"]) >= min_expectancy_r
    ]
    bootstrap = bootstrap_report(trades, iterations=bootstrap_iterations, seed=bootstrap_seed)
    bootstrap_prob = parse_float((bootstrap.get("expectancy_r") or {}).get("prob_gt_0"), 0.0)
    pass_gate = bool(
        summary["trades"] >= min_trades
        and summary["expectancy_r"] is not None
        and summary["expectancy_r"] >= min_expectancy_r
        and summary["winrate_pct"] is not None
        and summary["winrate_pct"] >= min_winrate_pct
        and bootstrap_prob >= min_bootstrap_prob_gt_0
        and len(stable_folds) == len(fold_rows)
        and len(fold_rows) > 0
    )
    return {
        "summary": summary,
        "folds": fold_rows,
        "stable_folds": len(stable_folds),
        "bootstrap": bootstrap,
        "research_gate": {
            "pass": pass_gate,
            "min_trades": min_trades,
            "min_expectancy_r": min_expectancy_r,
            "min_winrate_pct": min_winrate_pct,
            "min_bootstrap_prob_gt_0": min_bootstrap_prob_gt_0,
            "requires_all_folds_non_negative": True,
            "stable_folds": len(stable_folds),
            "fold_count": len(fold_rows),
            "verdict": "candidate_for_paper_design_review" if pass_gate else "do_not_trade",
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite v1.3 Structural Candidate",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Engine: `{report['engine_version']}`",
        f"- Data: `{report['data']['first_time']}` -> `{report['data']['last_time']}`",
        "",
        "## Purpose",
        "",
        "Tests the best v1.2 structural lead as a fresh raw-data candidate, not as a post-hoc slice.",
        "",
        "## Candidates",
        "",
        "| Candidate | Trades | Winrate | Expectancy | Net R | Bootstrap P>0 | Verdict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["candidates"]:
        summary = item["summary"]
        gate = item["research_gate"]
        prob = (item.get("bootstrap", {}).get("expectancy_r") or {}).get("prob_gt_0")
        lines.append(
            f"| `{item['id']}` | {summary['trades']} | {summary['winrate_pct']} | "
            f"{summary['expectancy_r']} | {summary['net_r_total']} | {prob} | `{gate['verdict']}` |"
        )
    lines.extend(["", "## Best Candidate", ""])
    best = report.get("best_candidate")
    if best:
        lines.extend(
            [
                f"- ID: `{best['id']}`",
                f"- Trades: `{best['summary']['trades']}`",
                f"- Winrate: `{best['summary']['winrate_pct']}`",
                f"- Expectancy: `{best['summary']['expectancy_r']}`",
                f"- Verdict: `{best['research_gate']['verdict']}`",
                "",
            ]
        )
    lines.extend(["## Decision", "", report["decision"], "", "## Boundary", "", report["runtime_boundary"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite v1.3 structural candidate validator")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--htf-interval", default="4h")
    parser.add_argument("--market", default="futures", choices=["futures"])
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--warmup-bars", type=int, default=220)
    parser.add_argument("--width-lower", type=float, default=6.0)
    parser.add_argument("--width-upper", type=float, default=7.0)
    parser.add_argument("--divergence-min", type=float, default=0.0)
    parser.add_argument("--spot-volume-max", type=float, default=0.8)
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=1.5)
    parser.add_argument("--max-hold-bars", type=int, default=16)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--bootstrap-iterations", type=int, default=5000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260602)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-winrate-pct", type=float, default=50.0)
    parser.add_argument("--min-bootstrap-prob-gt-0", type=float, default=0.8)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache/binance")
    parser.add_argument("--out-prefix", default="_dl/v13/MAX_CORE_LITE_V13_STRUCTURAL_CANDIDATE")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    rows, source = load_or_fetch(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        market=args.market,
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        pages=args.pages,
    )
    spot_rows, spot_source = load_or_fetch(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        market="spot",
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        pages=args.pages,
    )
    htf_rows, htf_source = load_or_fetch(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        market=args.market,
        symbol=args.symbol,
        interval=args.htf_interval,
        limit=args.limit,
        pages=args.pages,
    )
    interval_ms = INTERVAL_MS.get(args.interval, 3_600_000)
    candidate_results: list[dict[str, Any]] = []
    rng = random.Random(args.bootstrap_seed)
    for spec in default_candidate_specs(args):
        trades, skipped = simulate_candidate(
            spec=spec,
            rows=rows,
            spot_rows=spot_rows,
            htf_rows=htf_rows,
            htf_interval=args.htf_interval,
            interval_ms=interval_ms,
            warmup_bars=args.warmup_bars,
            stop_atr=args.stop_atr,
            take_atr=args.take_atr,
            max_hold_bars=args.max_hold_bars,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
        )
        gate = gate_candidate(
            trades=trades,
            rows_count=len(rows),
            warmup_bars=args.warmup_bars,
            folds=args.folds,
            bootstrap_iterations=args.bootstrap_iterations,
            bootstrap_seed=rng.randrange(1, 10_000_000),
            min_trades=args.min_trades,
            min_expectancy_r=args.min_expectancy_r,
            min_winrate_pct=args.min_winrate_pct,
            min_bootstrap_prob_gt_0=args.min_bootstrap_prob_gt_0,
        )
        candidate_results.append(
            {
                "id": spec["id"],
                "side": spec["side"],
                "requires": spec["requires"],
                "summary": gate["summary"],
                "folds": gate["folds"],
                "stable_folds": gate["stable_folds"],
                "bootstrap": gate["bootstrap"],
                "research_gate": gate["research_gate"],
                "skipped": skipped,
                "trades": trades,
            }
        )

    def rank_key(item: dict[str, Any]) -> tuple[int, float, float, int]:
        gate = item["research_gate"]
        summary = item["summary"]
        return (
            1 if gate.get("pass") else 0,
            float(summary.get("expectancy_r") or -999.0),
            float(summary.get("winrate_pct") or 0.0),
            int(summary.get("trades") or 0),
        )

    best = max(candidate_results, key=rank_key) if candidate_results else None
    passed = [item for item in candidate_results if item["research_gate"].get("pass")]
    decision = (
        "At least one v1.3 structural candidate passed the research gate and can move to paper-trading design review."
        if passed
        else "No v1.3 structural candidate passed the research gate. Keep the lead research-only; do not paper/live trade."
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_V13_STRUCTURAL_CANDIDATE",
        "engine_version": "1.3.0",
        "data": {
            "rows": len(rows),
            "spot_rows": len(spot_rows),
            "htf_rows": len(htf_rows),
            "first_time": rows[0].get("time") if rows else None,
            "last_time": rows[-1].get("time") if rows else None,
            "source": source,
            "spot_source": spot_source,
            "htf_source": htf_source,
        },
        "params": {
            "pages": args.pages,
            "limit": args.limit,
            "interval": args.interval,
            "htf_interval": args.htf_interval,
            "width_lower": args.width_lower,
            "width_upper": args.width_upper,
            "divergence_min": args.divergence_min,
            "spot_volume_max": args.spot_volume_max,
            "stop_atr": args.stop_atr,
            "take_atr": args.take_atr,
            "max_hold_bars": args.max_hold_bars,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "folds": args.folds,
            "bootstrap_iterations": args.bootstrap_iterations,
            "use_cache": args.use_cache,
        },
        "source_lead": "v1.2 best structural soft slice: spot_div12>=0 + medium Donchian width in ATR",
        "candidates": candidate_results,
        "best_candidate": best,
        "passed": passed,
        "decision": decision,
        "runtime_boundary": (
            "Research-only raw-data candidate validation. It uses public market data and deterministic simulation; "
            "it does not use private keys, does not place orders, and does not approve live trading."
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
                "best_candidate": {
                    "id": best.get("id") if best else None,
                    "summary": best.get("summary") if best else None,
                    "research_gate": best.get("research_gate") if best else None,
                },
                "passed": len(passed),
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
