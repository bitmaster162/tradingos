#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crowd_fade_positioning_diagnostic import (
    RATIO_FIELDS,
    build_signals,
    coverage_for_interval,
    evaluate_strategy,
    parse_csv_float_by_time,
    pct_change,
    rolling_z,
)
from liquidity_sweep_detector import load_ohlcv
from liquidity_sweep_forward_eval import compute_atr


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def qualifies_train(result: dict[str, Any], min_trades: int, min_expectancy_r: float, min_stable_folds: int) -> bool:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return (
        int(summary.get("trades") or 0) >= min_trades
        and float(summary.get("expectancy_r") or 0.0) >= min_expectancy_r
        and int(result.get("stable_folds") or 0) >= min_stable_folds
    )


def oos_decision(result: dict[str, Any], min_trades: int, min_expectancy_r: float, min_stable_folds: int, max_drawdown_r: float) -> str:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    trades = int(summary.get("trades") or 0)
    expectancy = safe_float(summary.get("expectancy_r"))
    drawdown = safe_float(summary.get("max_drawdown_r"))
    stable_folds = int(result.get("stable_folds") or 0)
    if trades < min_trades:
        return "reject_oos_insufficient_trades"
    if expectancy is None or expectancy < min_expectancy_r:
        return "reject_oos_expectancy"
    if drawdown is None or drawdown < -abs(max_drawdown_r):
        return "reject_oos_drawdown"
    if stable_folds < min_stable_folds:
        return "reject_oos_fold_instability"
    return "oos_candidate_for_separate_forward_design_review"


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": result.get("strategy_id"),
        "ratio_field": result.get("ratio_field"),
        "side_mode": result.get("side_mode"),
        "z_window": result.get("z_window"),
        "z_threshold": result.get("z_threshold"),
        "require_oi_expansion": result.get("require_oi_expansion"),
        "require_funding_alignment": result.get("require_funding_alignment"),
        "stop_atr": result.get("stop_atr"),
        "take_atr": result.get("take_atr"),
        "hold": result.get("hold"),
        "summary": result.get("summary"),
        "holdout_summary": result.get("holdout_summary"),
        "stable_folds": result.get("stable_folds"),
        "folds": result.get("folds"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    train = report.get("selected_train") or {}
    test = report.get("oos_test") or {}
    train_summary = train.get("summary") or {}
    test_summary = test.get("summary") or {}
    return "\n".join(
        [
            "# Crowd Fade Nested Holdout",
            "",
            f"- Generated: `{report.get('generated_at')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Split: train before `{report.get('split_time')}`, OOS from that timestamp onward.",
            f"- Train variants evaluated: `{report.get('evaluated_train_variants')}`",
            f"- Train-qualified variants: `{report.get('qualified_train_variants')}`",
            "",
            "## Selected On Train Only",
            "",
            f"- Strategy: `{train.get('strategy_id')}`",
            f"- Trades / expectancy / folds: `{train_summary.get('trades')}` / `{train_summary.get('expectancy_r')}`R / `{train.get('stable_folds')}`",
            "",
            "## Untouched OOS Result",
            "",
            f"- Trades: `{test_summary.get('trades')}`",
            f"- Win rate: `{test_summary.get('winrate_pct')}`",
            f"- Expectancy: `{test_summary.get('expectancy_r')}`R",
            f"- Drawdown: `{test_summary.get('max_drawdown_r')}`R",
            f"- Stable folds: `{test.get('stable_folds')}`",
            "",
            "## Boundary",
            "",
            "- OOS metrics are never used to select parameters.",
            "- Passing allows manual forward-design review only, never execution.",
            "- No candidate lock or observer is changed by this tool.",
            "- Can trade: `false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Select Crowd Fade parameters on train only and test one winner on untouched OOS data.")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--split-time", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--cost-bps-per-side", type=float, default=5.0)
    parser.add_argument("--min-train-trades", type=int, default=50)
    parser.add_argument("--min-train-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-train-stable-folds", type=int, default=2)
    parser.add_argument("--min-oos-trades", type=int, default=20)
    parser.add_argument("--min-oos-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-oos-stable-folds", type=int, default=2)
    parser.add_argument("--max-oos-drawdown-r", type=float, default=6.0)
    parser.add_argument("--out-prefix", default="docs/CROWD_FADE_NESTED_HOLDOUT_2026-06-23")
    args = parser.parse_args()

    cache_dir = resolve_path(args.cache_dir)
    symbol_dir = cache_dir / "futures" / args.symbol.upper()
    bars = load_ohlcv(symbol_dir / f"{args.interval}_klines.csv")
    crowd = parse_csv_float_by_time(symbol_dir / f"{args.interval}_crowd_positioning.csv", RATIO_FIELDS)
    derivatives = parse_csv_float_by_time(symbol_dir / f"{args.interval}_oi_aligned.csv", ["open_interest", "funding"])
    atr_values = compute_atr(bars, 14)
    split_index = next((index for index, bar in enumerate(bars) if bar.ts >= args.split_time), len(bars))
    z_windows = [24, 72]
    oi_lookback = 6
    holds = [8, 16]
    ratios_by_field = {field: [crowd.get(bar.ts, {}).get(field) for bar in bars] for field in RATIO_FIELDS}
    z_by_field_window = {
        (field, window): rolling_z(ratios_by_field[field], window)
        for field in RATIO_FIELDS
        for window in z_windows
    }
    oi_values = [derivatives.get(bar.ts, {}).get("open_interest") for bar in bars]
    prepared = {
        "ratios_by_field": ratios_by_field,
        "z_by_field_window": z_by_field_window,
        "oi_delta_values": [pct_change(oi_values, index, oi_lookback) for index in range(len(bars))],
        "funding_values": [derivatives.get(bar.ts, {}).get("funding") for bar in bars],
    }

    train_results: list[dict[str, Any]] = []
    signal_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for ratio_field in RATIO_FIELDS:
        for z_window in z_windows:
            for z_threshold in (0.8, 1.0, 1.25, 1.5):
                for side_mode in ("crowded_longs_fade_short", "crowded_shorts_fade_long"):
                    for require_oi in (False, True):
                        for require_funding in (False, True):
                            key = (ratio_field, z_window, z_threshold, side_mode, require_oi, require_funding)
                            signals = build_signals(
                                bars=bars,
                                crowd_by_time=crowd,
                                derivatives_by_time=derivatives,
                                ratio_field=ratio_field,
                                z_window=z_window,
                                z_threshold=z_threshold,
                                side_mode=side_mode,
                                oi_lookback=oi_lookback,
                                require_oi_expansion=require_oi,
                                require_funding_alignment=require_funding,
                                atr_values=atr_values,
                                prepared=prepared,
                            )
                            signal_cache[key] = signals
                            for stop_atr, take_atr in ((1.0, 1.5), (1.0, 2.0), (1.0, 3.0)):
                                for hold in holds:
                                    train_signals = [
                                        signal for signal in signals if int(signal["bar_index"]) < max(0, split_index - hold - 1)
                                    ]
                                    result = evaluate_strategy(
                                        interval=args.interval,
                                        bars=bars,
                                        signals=train_signals,
                                        ratio_field=ratio_field,
                                        z_window=z_window,
                                        z_threshold=z_threshold,
                                        side_mode=side_mode,
                                        require_oi_expansion=require_oi,
                                        require_funding_alignment=require_funding,
                                        stop_atr=stop_atr,
                                        take_atr=take_atr,
                                        hold=hold,
                                        cost_bps_per_side=args.cost_bps_per_side,
                                    )
                                    train_results.append(result)

    qualified = [
        result
        for result in train_results
        if qualifies_train(result, args.min_train_trades, args.min_train_expectancy_r, args.min_train_stable_folds)
    ]
    qualified.sort(
        key=lambda item: (
            float(item.get("summary", {}).get("expectancy_r") or -999.0) * math.sqrt(int(item.get("summary", {}).get("trades") or 0)),
            int(item.get("summary", {}).get("trades") or 0),
        ),
        reverse=True,
    )
    selected = qualified[0] if qualified else None
    oos: dict[str, Any] | None = None
    if selected:
        key = (
            selected["ratio_field"],
            selected["z_window"],
            selected["z_threshold"],
            selected["side_mode"],
            selected["require_oi_expansion"],
            selected["require_funding_alignment"],
        )
        test_signals = [signal for signal in signal_cache[key] if int(signal["bar_index"]) >= split_index]
        oos = evaluate_strategy(
            interval=args.interval,
            bars=bars,
            signals=test_signals,
            ratio_field=selected["ratio_field"],
            z_window=int(selected["z_window"]),
            z_threshold=float(selected["z_threshold"]),
            side_mode=selected["side_mode"],
            require_oi_expansion=bool(selected["require_oi_expansion"]),
            require_funding_alignment=bool(selected["require_funding_alignment"]),
            stop_atr=float(selected["stop_atr"]),
            take_atr=float(selected["take_atr"]),
            hold=int(selected["hold"]),
            cost_bps_per_side=args.cost_bps_per_side,
        )
    decision = "reject_no_train_candidate"
    if selected and oos:
        decision = oos_decision(
            oos,
            args.min_oos_trades,
            args.min_oos_expectancy_r,
            args.min_oos_stable_folds,
            args.max_oos_drawdown_r,
        )
    report = {
        "generated_at": now_iso(),
        "engine": "CROWD_FADE_NESTED_HOLDOUT",
        "engine_version": "1.0.0",
        "split_time": args.split_time,
        "coverage": coverage_for_interval(args.interval, bars, crowd),
        "thresholds": {
            "min_train_trades": args.min_train_trades,
            "min_train_expectancy_r": args.min_train_expectancy_r,
            "min_train_stable_folds": args.min_train_stable_folds,
            "min_oos_trades": args.min_oos_trades,
            "min_oos_expectancy_r": args.min_oos_expectancy_r,
            "min_oos_stable_folds": args.min_oos_stable_folds,
            "max_oos_drawdown_r": args.max_oos_drawdown_r,
        },
        "evaluated_train_variants": len(train_results),
        "qualified_train_variants": len(qualified),
        "top_train_candidates": [compact_result(item) for item in qualified[:10]],
        "selected_train": compact_result(selected) if selected else None,
        "oos_test": compact_result(oos) if oos else None,
        "decision": decision,
        "boundaries": {
            "changes_candidate_lock": False,
            "changes_strategy_parameters": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "sends_orders": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "train_qualified": len(qualified),
                "selected": selected.get("strategy_id") if selected else None,
                "oos_summary": oos.get("summary") if oos else None,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
