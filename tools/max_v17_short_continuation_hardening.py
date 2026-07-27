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

from tools.max_backtest import INTERVAL_MS, candle_value, find_exit  # noqa: E402
from tools.max_v11_candidate_validator import load_or_fetch  # noqa: E402
from tools.max_v13_structural_candidate import gate_candidate, parse_float  # noqa: E402
from tools.max_v15_state_filters import (  # noqa: E402
    build_trade_features,
    load_or_fetch_derivatives,
    precompute_htf_bias,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_csv_floats(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_csv_ints(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_csv_strings(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def finite(value: Any) -> bool:
    parsed = parse_float(value)
    return not math.isnan(parsed)


def short_exit(
    *,
    rows: list[dict[str, str]],
    signal_row: int,
    atr: float,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any] | None:
    entry_row = signal_row + 1
    if entry_row >= len(rows) or math.isnan(atr) or atr <= 0:
        return None
    entry_open = candle_value(rows[entry_row], "open")
    if math.isnan(entry_open):
        return None
    slip = slippage_bps / 10000
    entry = entry_open * (1 - slip)
    risk = stop_atr * atr
    stop = entry + risk
    take_profit = entry - take_atr * atr
    exit_row, raw_exit, exit_reason = find_exit(
        rows,
        start_index=entry_row,
        side="SHORT",
        entry=entry,
        stop=stop,
        take_profit=take_profit,
        max_hold_bars=max_hold_bars,
    )
    exit_price = raw_exit * (1 + slip)
    gross_r = (entry - exit_price) / risk
    fee_cost = ((entry + exit_price) * (fee_bps / 10000)) / risk
    net_r = gross_r - fee_cost
    return {
        "entry_row": entry_row,
        "exit_row": exit_row,
        "entry": entry,
        "stop": stop,
        "take_profit": take_profit,
        "exit": exit_price,
        "exit_reason": exit_reason,
        "bars_held": max(1, exit_row - entry_row + 1),
        "gross_r": gross_r,
        "net_r": net_r,
    }


def variant_specs(args: argparse.Namespace) -> list[dict[str, Any]]:
    def trend_down(features: dict[str, Any]) -> bool:
        return parse_float(features.get("trend_strength_20_atr")) <= args.trend_down_atr

    def htf_short(features: dict[str, Any]) -> bool:
        return str(features.get("htf_bias")) == "SHORT"

    def oi_up(features: dict[str, Any]) -> bool:
        return finite(features.get("oi_delta_12_pct")) and parse_float(features.get("oi_delta_12_pct")) >= args.oi_up_min_delta_pct

    def oi_up_strong(features: dict[str, Any]) -> bool:
        return finite(features.get("oi_delta_12_pct")) and parse_float(features.get("oi_delta_12_pct")) >= args.oi_up_strong_delta_pct

    def funding_positive(features: dict[str, Any]) -> bool:
        return finite(features.get("funding")) and parse_float(features.get("funding")) >= 0.0

    def no_sweep(features: dict[str, Any]) -> bool:
        return str(features.get("sweep_side")) == "none"

    def near_low(features: dict[str, Any]) -> bool:
        return bool(features.get("near_low"))

    def spot_quiet(features: dict[str, Any]) -> bool:
        return finite(features.get("spot_volume_ratio")) and parse_float(features.get("spot_volume_ratio")) <= args.spot_volume_max

    return [
        {
            "id": "v17_short_htf_short_oi_up_trend_down",
            "labels": ["HTF SHORT", f"OI delta12 >= {args.oi_up_min_delta_pct}", f"trend20 <= {args.trend_down_atr} ATR"],
            "predicate": lambda f: htf_short(f) and oi_up(f) and trend_down(f),
        },
        {
            "id": "v17_short_htf_short_oi_up_strong_trend_down",
            "labels": ["HTF SHORT", f"OI delta12 >= {args.oi_up_strong_delta_pct}", f"trend20 <= {args.trend_down_atr} ATR"],
            "predicate": lambda f: htf_short(f) and oi_up_strong(f) and trend_down(f),
        },
        {
            "id": "v17_short_htf_short_oi_up_trend_down_funding_pos",
            "labels": ["HTF SHORT", "OI delta12 >= 0", "trend20 down", "funding >= 0"],
            "predicate": lambda f: htf_short(f) and oi_up(f) and trend_down(f) and funding_positive(f),
        },
        {
            "id": "v17_short_htf_short_oi_up_trend_down_no_sweep",
            "labels": ["HTF SHORT", "OI delta12 >= 0", "trend20 down", "no sweep"],
            "predicate": lambda f: htf_short(f) and oi_up(f) and trend_down(f) and no_sweep(f),
        },
        {
            "id": "v17_short_near_low_funding_pos_trend_down",
            "labels": ["near Donchian low", "funding >= 0", "trend20 down"],
            "predicate": lambda f: near_low(f) and funding_positive(f) and trend_down(f),
        },
        {
            "id": "v17_short_spot_quiet_trend_down",
            "labels": ["spot quiet", "trend20 down"],
            "predicate": lambda f: spot_quiet(f) and trend_down(f),
        },
    ]


def simulate_variant(
    *,
    variant: dict[str, Any],
    interval: str,
    rows: list[dict[str, str]],
    features_by_signal_row: dict[int, dict[str, Any]],
    warmup_bars: int,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    fee_bps: float,
    slippage_bps: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    predicate: Callable[[dict[str, Any]], bool] = variant["predicate"]
    trades: list[dict[str, Any]] = []
    skipped = {"feature_not_ready": 0, "rule_not_matched": 0, "bad_outcome": 0}
    i = max(warmup_bars, 220)
    while i < len(rows) - 1:
        features = features_by_signal_row.get(i)
        if features is None:
            skipped["feature_not_ready"] += 1
            i += 1
            continue
        if not predicate(features):
            skipped["rule_not_matched"] += 1
            i += 1
            continue
        outcome = short_exit(
            rows=rows,
            signal_row=i,
            atr=parse_float(features.get("atr14")),
            stop_atr=stop_atr,
            take_atr=take_atr,
            max_hold_bars=max_hold_bars,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        if outcome is None:
            skipped["bad_outcome"] += 1
            i += 1
            continue
        candidate_id = f"{variant['id']}__{interval}__tp{take_atr:g}__hold{max_hold_bars}"
        trades.append(
            {
                "index": len(trades) + 1,
                "candidate_id": candidate_id,
                "variant_id": variant["id"],
                "side": "SHORT",
                "setup": candidate_id,
                "interval": interval,
                "signal_row": i,
                "entry_row": outcome["entry_row"],
                "exit_row": outcome["exit_row"],
                "signal_time": features["signal_time"],
                "entry_time": rows[outcome["entry_row"]].get("time", str(outcome["entry_row"])),
                "exit_time": rows[outcome["exit_row"]].get("time", str(outcome["exit_row"])),
                "entry": round(outcome["entry"], 8),
                "stop": round(outcome["stop"], 8),
                "take_profit": round(outcome["take_profit"], 8),
                "exit": round(outcome["exit"], 8),
                "exit_reason": outcome["exit_reason"],
                "bars_held": outcome["bars_held"],
                "gross_r": round(outcome["gross_r"], 6),
                "net_r": round(outcome["net_r"], 6),
                **{
                    key: (round(value, 8) if isinstance(value, float) and not math.isnan(value) else value)
                    for key, value in features.items()
                    if key not in {"close"}
                },
            }
        )
        i = int(outcome["exit_row"]) + 1
    return trades, skipped


def run_interval(
    *,
    args: argparse.Namespace,
    interval: str,
    variants: list[dict[str, Any]],
    cache_dir: Path,
    rng: random.Random,
) -> dict[str, Any]:
    rows, source = load_or_fetch(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        market=args.market,
        symbol=args.symbol,
        interval=interval,
        limit=args.limit,
        pages=args.pages,
    )
    spot_rows, spot_source = load_or_fetch(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        market="spot",
        symbol=args.symbol,
        interval=interval,
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
        pages=args.htf_pages,
    )
    derivatives_rows, derivatives_source = load_or_fetch_derivatives(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        symbol=args.symbol,
        interval=interval,
        rows=rows,
        limit=args.derivatives_limit,
        pages=args.derivatives_pages,
    )
    interval_ms = INTERVAL_MS.get(interval, 3_600_000)
    htf_biases = precompute_htf_bias(rows=rows, htf_rows=htf_rows, interval_ms=interval_ms, htf_interval=args.htf_interval)
    features_by_signal_row: dict[int, dict[str, Any]] = {}
    feature_skipped = 0
    for i in range(max(args.warmup_bars, 220), len(rows) - 1):
        features = build_trade_features(
            rows=rows,
            spot_rows=spot_rows,
            derivatives_rows=derivatives_rows,
            htf_biases=htf_biases,
            i=i,
        )
        if features is None or not features.get("derivatives_ready"):
            feature_skipped += 1
            continue
        features_by_signal_row[i] = features

    results: list[dict[str, Any]] = []
    for variant in variants:
        for stop_atr in parse_csv_floats(args.stop_atrs):
            for take_atr in parse_csv_floats(args.take_atrs):
                for max_hold_bars in parse_csv_ints(args.max_hold_bars_grid):
                    trades, skipped = simulate_variant(
                        variant=variant,
                        interval=interval,
                        rows=rows,
                        features_by_signal_row=features_by_signal_row,
                        warmup_bars=args.warmup_bars,
                        stop_atr=stop_atr,
                        take_atr=take_atr,
                        max_hold_bars=max_hold_bars,
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
                    results.append(
                        {
                            "id": f"{variant['id']}__{interval}__sl{stop_atr:g}__tp{take_atr:g}__hold{max_hold_bars}",
                            "variant_id": variant["id"],
                            "labels": variant["labels"],
                            "side": "SHORT",
                            "interval": interval,
                            "stop_atr": stop_atr,
                            "take_atr": take_atr,
                            "max_hold_bars": max_hold_bars,
                            "summary": gate["summary"],
                            "folds": gate["folds"],
                            "stable_folds": gate["stable_folds"],
                            "bootstrap": gate["bootstrap"],
                            "research_gate": gate["research_gate"],
                            "skipped": skipped,
                            "trades": trades,
                        }
                    )

    results.sort(
        key=lambda item: (
            1 if item["research_gate"].get("pass") else 0,
            parse_float(item["summary"].get("expectancy_r"), -999.0),
            parse_float(item["summary"].get("winrate_pct"), 0.0),
            int(item["summary"].get("trades") or 0),
        ),
        reverse=True,
    )
    return {
        "interval": interval,
        "data": {
            "rows": len(rows),
            "spot_rows": len(spot_rows),
            "htf_rows": len(htf_rows),
            "derivatives_rows": len(derivatives_rows),
            "first_time": rows[0].get("time") if rows else None,
            "last_time": rows[-1].get("time") if rows else None,
            "source": source,
            "spot_source": spot_source,
            "htf_source": htf_source,
            "derivatives_source": derivatives_source,
            "feature_ready_rows": len(features_by_signal_row),
            "feature_skipped_rows": feature_skipped,
        },
        "results": results,
        "best": results[0] if results else None,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite v1.7 Short-Continuation Hardening",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Engine: `{report['engine_version']}`",
        "",
        "## Purpose",
        "",
        "Hardens the v1.6 lead as a narrow short-continuation module: HTF short pressure, rising OI and local downside trend. This is not a general miner.",
        "",
        "## Top Results",
        "",
        "| Rank | Candidate | TF | Trades | Winrate | Expectancy | Net R | Bootstrap P>0 | Stable Folds | Verdict |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for idx, item in enumerate(report["top_results"], start=1):
        summary = item["summary"]
        gate = item["research_gate"]
        prob = (item.get("bootstrap", {}).get("expectancy_r") or {}).get("prob_gt_0")
        lines.append(
            f"| {idx} | `{item['id']}` | `{item['interval']}` | {summary['trades']} | {summary['winrate_pct']} | "
            f"{summary['expectancy_r']} | {summary['net_r_total']} | {prob} | "
            f"{gate['stable_folds']}/{gate['fold_count']} | `{gate['verdict']}` |"
        )
    best = report.get("best_candidate")
    lines.extend(["", "## Best Candidate", ""])
    if best:
        lines.extend(
            [
                f"- ID: `{best['id']}`",
                f"- Conditions: `{', '.join(best['labels'])}`",
                f"- TF: `{best['interval']}`",
                f"- Stop ATR: `{best['stop_atr']}`",
                f"- Take ATR: `{best['take_atr']}`",
                f"- Max hold bars: `{best['max_hold_bars']}`",
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
    parser = argparse.ArgumentParser(description="MAX Core Lite v1.7 targeted short-continuation hardening")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--intervals", default="30m,1h,2h")
    parser.add_argument("--htf-interval", default="4h")
    parser.add_argument("--market", default="futures", choices=["futures"])
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--htf-pages", type=int, default=8)
    parser.add_argument("--derivatives-pages", type=int, default=48)
    parser.add_argument("--derivatives-limit", type=int, default=500)
    parser.add_argument("--warmup-bars", type=int, default=220)
    parser.add_argument("--trend-down-atr", type=float, default=-1.0)
    parser.add_argument("--oi-up-min-delta-pct", type=float, default=0.0)
    parser.add_argument("--oi-up-strong-delta-pct", type=float, default=0.1)
    parser.add_argument("--spot-volume-max", type=float, default=0.8)
    parser.add_argument("--stop-atrs", default="1.0")
    parser.add_argument("--take-atrs", default="1.0,1.2,1.5,2.0")
    parser.add_argument("--max-hold-bars-grid", default="8,12,16,24")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=8)
    parser.add_argument("--bootstrap-iterations", type=int, default=3000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260602)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-winrate-pct", type=float, default=50.0)
    parser.add_argument("--min-bootstrap-prob-gt-0", type=float, default=0.8)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache/binance")
    parser.add_argument("--out-prefix", default="_dl/v17/MAX_CORE_LITE_V17_SHORT_CONTINUATION_HARDENING")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    variants = variant_specs(args)
    rng = random.Random(args.bootstrap_seed)
    interval_reports = [
        run_interval(args=args, interval=interval, variants=variants, cache_dir=cache_dir, rng=rng)
        for interval in parse_csv_strings(args.intervals)
    ]
    all_results = [item for report in interval_reports for item in report["results"]]
    all_results.sort(
        key=lambda item: (
            1 if item["research_gate"].get("pass") else 0,
            parse_float(item["summary"].get("expectancy_r"), -999.0),
            parse_float(item["summary"].get("winrate_pct"), 0.0),
            int(item["summary"].get("trades") or 0),
        ),
        reverse=True,
    )
    passed = [item for item in all_results if item["research_gate"].get("pass")]
    best = all_results[0] if all_results else None
    decision = (
        "At least one v1.7 short-continuation candidate passed the research gate and can move to paper-trading design review."
        if passed
        else "No v1.7 short-continuation candidate passed the research gate. Keep this research-only; do not paper/live trade."
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_V17_SHORT_CONTINUATION_HARDENING",
        "engine_version": "1.7.0",
        "params": {
            "symbol": args.symbol,
            "intervals": parse_csv_strings(args.intervals),
            "htf_interval": args.htf_interval,
            "pages": args.pages,
            "limit": args.limit,
            "derivatives_pages": args.derivatives_pages,
            "trend_down_atr": args.trend_down_atr,
            "oi_up_min_delta_pct": args.oi_up_min_delta_pct,
            "oi_up_strong_delta_pct": args.oi_up_strong_delta_pct,
            "stop_atrs": parse_csv_floats(args.stop_atrs),
            "take_atrs": parse_csv_floats(args.take_atrs),
            "max_hold_bars_grid": parse_csv_ints(args.max_hold_bars_grid),
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "folds": args.folds,
            "bootstrap_iterations": args.bootstrap_iterations,
        },
        "interval_reports": interval_reports,
        "top_results": all_results[: args.top],
        "best_candidate": best,
        "passed": passed,
        "decision": decision,
        "runtime_boundary": (
            "Research-only targeted hardening. It uses public Binance data and deterministic simulation; "
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
                "tested": len(all_results),
                "best_candidate": {
                    "id": best.get("id") if best else None,
                    "interval": best.get("interval") if best else None,
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
