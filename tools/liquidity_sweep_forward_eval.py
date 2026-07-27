from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import (
    DEFAULT_CONFIG,
    DetectorParams,
    detect_events,
    load_config_params,
    load_ohlcv,
)


DEFAULT_INPUTS = [
    "data/cache/binance/futures/BTCUSDT/15m_klines.csv",
    "data/cache/binance/futures/BTCUSDT/1h_klines.csv",
    "data/cache/binance/futures/BTCUSDT/4h_klines.csv",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 6)


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def infer_dataset_id(path: Path) -> str:
    parts = path.as_posix().split("/")
    if len(parts) >= 5 and "binance" in parts:
        try:
            idx = parts.index("binance")
            market = parts[idx + 1]
            symbol = parts[idx + 2]
            timeframe = path.stem.replace("_klines", "")
            return f"{market}_{symbol}_{timeframe}"
        except (ValueError, IndexError):
            pass
    return path.stem


def build_params(args: argparse.Namespace) -> DetectorParams:
    config_params = load_config_params(Path(args.config)) if args.config else {}
    eq_detection = config_params.get("eq_detection", {}) if isinstance(config_params, dict) else {}
    return DetectorParams(
        lookback=int(args.lookback or config_params.get("swing_window", 50) or 50),
        eqh_tolerance_pct=float(args.eqh_tolerance_pct if args.eqh_tolerance_pct is not None else eq_detection.get("eqh_tolerance_pct", 0.15)),
        eql_tolerance_pct=float(args.eql_tolerance_pct if args.eql_tolerance_pct is not None else eq_detection.get("eql_tolerance_pct", 0.15)),
        sweep_displacement_ticks=float(
            args.sweep_displacement_ticks
            if args.sweep_displacement_ticks is not None
            else config_params.get("sweep_displacement_ticks", 2)
        ),
        tick_size=float(args.tick_size),
    )


def compute_atr(bars: list[Any], window: int) -> list[float | None]:
    true_ranges: list[float] = []
    atr: list[float | None] = []
    for index, bar in enumerate(bars):
        if index == 0:
            tr = bar.high - bar.low
        else:
            prev_close = bars[index - 1].close
            tr = max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
        true_ranges.append(tr)
        if index + 1 < window:
            atr.append(None)
        else:
            atr.append(sum(true_ranges[index + 1 - window : index + 1]) / window)
    return atr


def evaluate_event(event: dict[str, Any], bars: list[Any], atr_values: list[float | None], forward_bars: int) -> dict[str, Any] | None:
    index = int(event["bar_index"])
    start = index + 1
    end = min(len(bars), index + 1 + forward_bars)
    if start >= end:
        return None
    atr = atr_values[index]
    if atr is None or atr <= 0:
        return None
    entry = float(event["close"])
    future = bars[start:end]
    side = str(event["side_hint"]).upper()
    final_close = future[-1].close

    if side == "LONG":
        close_return_pct = (final_close - entry) / entry * 100.0
        mfe_pct = (max(bar.high for bar in future) - entry) / entry * 100.0
        mae_pct = (min(bar.low for bar in future) - entry) / entry * 100.0
        positive_level = entry + atr
        negative_level = entry - atr
        positive_check = lambda bar: bar.high >= positive_level
        negative_check = lambda bar: bar.low <= negative_level
    else:
        close_return_pct = (entry - final_close) / entry * 100.0
        mfe_pct = (entry - min(bar.low for bar in future)) / entry * 100.0
        mae_pct = (entry - max(bar.high for bar in future)) / entry * 100.0
        positive_level = entry - atr
        negative_level = entry + atr
        positive_check = lambda bar: bar.low <= positive_level
        negative_check = lambda bar: bar.high >= negative_level

    first_touch = "none"
    first_touch_bar_offset: int | None = None
    for offset, bar in enumerate(future, start=1):
        positive = positive_check(bar)
        negative = negative_check(bar)
        if positive and negative:
            first_touch = "ambiguous_same_bar"
            first_touch_bar_offset = offset
            break
        if positive:
            first_touch = "positive_1atr"
            first_touch_bar_offset = offset
            break
        if negative:
            first_touch = "negative_1atr"
            first_touch_bar_offset = offset
            break

    return {
        "event_id": event["event_id"],
        "ts": event["ts"],
        "bar_index": index,
        "direction": event["direction"],
        "side_hint": side,
        "level_type": event["level_type"],
        "entry_close": round(entry, 8),
        "atr": round(atr, 8),
        "forward_bars_available": len(future),
        "close_return_pct_directional": round(close_return_pct, 6),
        "mfe_pct_directional": round(mfe_pct, 6),
        "mae_pct_directional": round(mae_pct, 6),
        "first_touch": first_touch,
        "first_touch_bar_offset": first_touch_bar_offset,
        "positive_level": round(positive_level, 8),
        "negative_level": round(negative_level, 8),
    }


def classify(summary: dict[str, Any]) -> str:
    eligible = int(summary["eligible_events"])
    positive_touch_pct = summary["positive_1atr_touch_pct"]
    avg_close = summary["avg_close_return_pct_directional"]
    if eligible == 0:
        return "no_eligible_events"
    if eligible < 30:
        if positive_touch_pct is not None and positive_touch_pct >= 55 and avg_close is not None and avg_close > 0:
            return "watchlist_positive_insufficient_sample"
        return "insufficient_sample"
    if positive_touch_pct is not None and positive_touch_pct >= 55 and avg_close is not None and avg_close > 0:
        return "candidate_for_hardening"
    if positive_touch_pct is not None and (positive_touch_pct <= 45 or (avg_close is not None and avg_close < 0)):
        return "negative_or_mixed"
    return "neutral_inconclusive"


def summarize_outcomes(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    touch_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}
    close_returns = [float(item["close_return_pct_directional"]) for item in outcomes]
    mfe_values = [float(item["mfe_pct_directional"]) for item in outcomes]
    mae_values = [float(item["mae_pct_directional"]) for item in outcomes]
    for item in outcomes:
        touch_counts[item["first_touch"]] = touch_counts.get(item["first_touch"], 0) + 1
        direction_counts[item["direction"]] = direction_counts.get(item["direction"], 0) + 1
    eligible = len(outcomes)
    positive = touch_counts.get("positive_1atr", 0)
    negative = touch_counts.get("negative_1atr", 0)
    ambiguous = touch_counts.get("ambiguous_same_bar", 0)
    summary = {
        "eligible_events": eligible,
        "direction_counts": direction_counts,
        "first_touch_counts": touch_counts,
        "positive_1atr_touch_pct": round(positive / eligible * 100.0, 3) if eligible else None,
        "negative_1atr_touch_pct": round(negative / eligible * 100.0, 3) if eligible else None,
        "ambiguous_same_bar_pct": round(ambiguous / eligible * 100.0, 3) if eligible else None,
        "avg_close_return_pct_directional": average(close_returns),
        "median_close_return_pct_directional": median(close_returns),
        "avg_mfe_pct_directional": average(mfe_values),
        "avg_mae_pct_directional": average(mae_values),
    }
    summary["classification"] = classify(summary)
    return summary


def evaluate_dataset(path: Path, params: DetectorParams, forward_bars: int, atr_window: int) -> dict[str, Any]:
    bars = load_ohlcv(path)
    events = detect_events(bars, params)
    atr_values = compute_atr(bars, atr_window)
    outcomes: list[dict[str, Any]] = []
    skipped = 0
    for event in events:
        outcome = evaluate_event(event, bars, atr_values, forward_bars)
        if outcome is None:
            skipped += 1
            continue
        outcomes.append(outcome)
    return {
        "dataset_id": infer_dataset_id(path),
        "path": str(path),
        "rows": len(bars),
        "events_detected": len(events),
        "events_skipped_no_forward_or_atr": skipped,
        "summary": summarize_outcomes(outcomes),
        "sample_outcomes": outcomes[:20],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidity Sweep EQ Forward Evaluation",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Forward bars: `{report['forward_bars']}`",
        f"ATR window: `{report['atr_window']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research/evidence-only evaluation.",
        "- Uses local cached OHLCV files only.",
        "- Does not grant trade permission, does not size positions and does not send orders.",
        "- A positive result here would only justify stricter hardening, not live trading.",
        "",
        "## Dataset Results",
        "",
    ]
    for dataset in report["datasets"]:
        summary = dataset["summary"]
        lines.extend(
            [
                f"### {dataset['dataset_id']}",
                "",
                f"- Source: `{dataset['path']}`",
                f"- Rows: `{dataset['rows']}`",
                f"- Events detected: `{dataset['events_detected']}`",
                f"- Eligible events: `{summary['eligible_events']}`",
                f"- Direction counts: `{summary['direction_counts']}`",
                f"- First-touch counts: `{summary['first_touch_counts']}`",
                f"- Positive +1ATR first-touch: `{summary['positive_1atr_touch_pct']}`%",
                f"- Avg directional close return: `{summary['avg_close_return_pct_directional']}`%",
                f"- Median directional close return: `{summary['median_close_return_pct_directional']}`%",
                f"- Classification: `{summary['classification']}`",
                "",
            ]
        )
    aggregate = report["aggregate_summary"]
    lines.extend(
        [
            "## Aggregate",
            "",
            f"- Eligible events: `{aggregate['eligible_events']}`",
            f"- Direction counts: `{aggregate['direction_counts']}`",
            f"- First-touch counts: `{aggregate['first_touch_counts']}`",
            f"- Positive +1ATR first-touch: `{aggregate['positive_1atr_touch_pct']}`%",
            f"- Avg directional close return: `{aggregate['avg_close_return_pct_directional']}`%",
            f"- Classification: `{aggregate['classification']}`",
            "",
            "## Decision",
            "",
            "- Keep `liquidity_sweep_eq` blocked from trading.",
            "- If a dataset classifies as `candidate_for_hardening`, the next step is a real entry/exit backtest with fees, slippage and out-of-sample folds.",
            "- If results are mixed or sample is small, keep the detector as context only.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate forward outcomes after liquidity_sweep_eq events")
    parser.add_argument("--inputs", nargs="+", default=DEFAULT_INPUTS)
    parser.add_argument("--out-prefix", default="docs/LIQUIDITY_SWEEP_FORWARD_EVAL_2026-06-03")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--lookback", type=int, default=None)
    parser.add_argument("--eqh-tolerance-pct", type=float, default=None)
    parser.add_argument("--eql-tolerance-pct", type=float, default=None)
    parser.add_argument("--sweep-displacement-ticks", type=float, default=None)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--forward-bars", type=int, default=12)
    parser.add_argument("--atr-window", type=int, default=14)
    args = parser.parse_args()

    params = build_params(args)
    datasets = [evaluate_dataset(Path(path), params, args.forward_bars, args.atr_window) for path in args.inputs]
    aggregate_outcomes = [
        outcome
        for dataset in datasets
        for outcome in dataset["sample_outcomes"]
    ]
    # Re-read full outcomes for aggregate without limiting output size.
    full_outcomes: list[dict[str, Any]] = []
    for path in args.inputs:
        bars = load_ohlcv(Path(path))
        events = detect_events(bars, params)
        atr_values = compute_atr(bars, args.atr_window)
        for event in events:
            outcome = evaluate_event(event, bars, atr_values, args.forward_bars)
            if outcome is not None:
                full_outcomes.append(outcome)
    report = {
        "generated_at": now_iso(),
        "inputs": args.inputs,
        "forward_bars": args.forward_bars,
        "atr_window": args.atr_window,
        "params": {
            "lookback": params.lookback,
            "eqh_tolerance_pct": params.eqh_tolerance_pct,
            "eql_tolerance_pct": params.eql_tolerance_pct,
            "sweep_displacement_ticks": params.sweep_displacement_ticks,
            "tick_size": params.tick_size,
        },
        "runtime_boundary": {
            "classification": "research_evidence_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "datasets": datasets,
        "aggregate_summary": summarize_outcomes(full_outcomes),
    }
    # Keep aggregate sample separate and small for readable JSON.
    report["aggregate_sample_outcomes"] = aggregate_outcomes[:20]

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(
        {
            "datasets": [
                {
                    "dataset_id": item["dataset_id"],
                    "events_detected": item["events_detected"],
                    "eligible_events": item["summary"]["eligible_events"],
                    "positive_1atr_touch_pct": item["summary"]["positive_1atr_touch_pct"],
                    "avg_close_return_pct_directional": item["summary"]["avg_close_return_pct_directional"],
                    "classification": item["summary"]["classification"],
                }
                for item in datasets
            ],
            "aggregate": report["aggregate_summary"],
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
