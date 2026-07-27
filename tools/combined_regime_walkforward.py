#!/usr/bin/env python3
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

from tools.combined_regime_hardening import (  # noqa: E402
    generate_signals,
    load_csv_by_time,
    parse_list,
    simulate_signals,
    safe_float,
)
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_hardening import summarize_trades  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def split_by_time(trades: list[Any], splits: int) -> list[list[Any]]:
    ordered = sorted(trades, key=lambda item: item.entry_ts)
    chunks: list[list[Any]] = []
    for fold in range(splits):
        start = round(len(ordered) * fold / splits)
        end = round(len(ordered) * (fold + 1) / splits)
        chunks.append(ordered[start:end])
    return chunks


def passes_gate(
    summary: dict[str, Any],
    *,
    min_trades: int,
    min_winrate_pct: float,
    min_expectancy_r: float,
    max_drawdown_r: float,
) -> bool:
    return bool(
        (summary.get("trades") or 0) >= min_trades
        and (summary.get("winrate_pct") or 0.0) >= min_winrate_pct
        and (summary.get("expectancy_r") or -999.0) >= min_expectancy_r
        and (summary.get("max_drawdown_r") or 0.0) >= -abs(max_drawdown_r)
    )


def walk_forward_result(
    trades: list[Any],
    *,
    splits: int,
    train_min_trades: int,
    test_min_trades: int,
    min_winrate_pct: float,
    min_expectancy_r: float,
    max_drawdown_r: float,
) -> dict[str, Any]:
    chunks = split_by_time(trades, splits)
    windows: list[dict[str, Any]] = []
    selected_test_trades: list[Any] = []
    for index in range(1, len(chunks)):
        train = [trade for chunk in chunks[:index] for trade in chunk]
        test = chunks[index]
        train_summary = summarize_trades(train)
        test_summary = summarize_trades(test)
        train_selected = passes_gate(
            train_summary,
            min_trades=train_min_trades,
            min_winrate_pct=min_winrate_pct,
            min_expectancy_r=min_expectancy_r,
            max_drawdown_r=max_drawdown_r,
        )
        test_pass = bool(
            train_selected
            and passes_gate(
                test_summary,
                min_trades=test_min_trades,
                min_winrate_pct=min_winrate_pct,
                min_expectancy_r=min_expectancy_r,
                max_drawdown_r=max_drawdown_r,
            )
        )
        if train_selected:
            selected_test_trades.extend(test)
        windows.append(
            {
                "window": index,
                "train_folds": list(range(1, index + 1)),
                "test_fold": index + 1,
                "train_selected": train_selected,
                "test_pass": test_pass,
                "train_summary": train_summary,
                "test_summary": test_summary,
            }
        )

    selected_windows = [item for item in windows if item["train_selected"]]
    test_passes = [item for item in windows if item["test_pass"]]
    selected_test_summary = summarize_trades(selected_test_trades)
    selected_test_expectancies = [
        item["test_summary"]["expectancy_r"]
        for item in selected_windows
        if item["test_summary"].get("expectancy_r") is not None
    ]
    return {
        "all_summary": summarize_trades(trades),
        "windows": windows,
        "selected_windows": len(selected_windows),
        "test_passes": len(test_passes),
        "selected_test_summary": selected_test_summary,
        "selected_test_expectancy_values": selected_test_expectancies,
        "selected_test_expectancy_median": (
            round(statistics.median(selected_test_expectancies), 6) if selected_test_expectancies else None
        ),
    }


def verdict_for_walkforward(
    result: dict[str, Any],
    *,
    min_selected_windows: int,
    min_test_passes: int,
    test_min_trades_total: int,
    min_winrate_pct: float,
    min_expectancy_r: float,
) -> str:
    test_summary = result["selected_test_summary"]
    if (
        result["selected_windows"] >= min_selected_windows
        and result["test_passes"] >= min_test_passes
        and (test_summary.get("trades") or 0) >= test_min_trades_total
        and (test_summary.get("winrate_pct") or 0.0) >= min_winrate_pct
        and (test_summary.get("expectancy_r") or -999.0) >= min_expectancy_r
    ):
        return "paper_candidate_after_oos_replay"
    if result["selected_windows"] > 0 and (test_summary.get("expectancy_r") or -999.0) > 0:
        return "watchlist_only"
    return "research_only_or_reject"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Combined Regime Walk-Forward",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research-only walk-forward test.",
        "- Train windows can select a strategy; only later test windows count as out-of-sample evidence.",
        "- No orders are sent and no paper/live permission is granted.",
        "",
        "## Result",
        "",
        f"- OOS pass candidates: `{report['oos_pass_count']}`.",
        f"- Watchlist-only candidates: `{report['watchlist_count']}`.",
        f"- Blocked/research-only candidates: `{report['blocked_count']}`.",
        "",
        "## Data Coverage",
        "",
    ]
    for dataset in report["datasets"]:
        lines.append(
            f"- `{dataset['dataset_id']}` rows=`{dataset['rows']}` signals=`{dataset['signals']}` "
            f"OI coverage=`{dataset['oi_coverage_pct']}`% funding coverage=`{dataset['funding_coverage_pct']}`%."
        )
    lines.extend(
        [
            "",
            "## Top Walk-Forward Results",
            "",
            "| Strategy | All Trades | All Exp R | Selected Windows | Test Passes | OOS Trades | OOS Winrate | OOS Exp R | Verdict |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["top_results"]:
        wf = item["walk_forward"]
        all_summary = wf["all_summary"]
        test_summary = wf["selected_test_summary"]
        lines.append(
            f"| `{item['strategy_id']}` | `{all_summary.get('trades')}` | `{all_summary.get('expectancy_r')}` | "
            f"`{wf['selected_windows']}` | `{wf['test_passes']}` | `{test_summary.get('trades')}` | "
            f"`{test_summary.get('winrate_pct')}` | `{test_summary.get('expectancy_r')}` | "
            f"`{item['verdict']}` |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "- If `oos_pass_count=0`, do not paper trade.",
            "- A watchlist result is only a direction for deeper research, not a deployable signal.",
            "- Next useful step after a zero-pass walk-forward is data coverage improvement and broader-but-faster replay.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward validation for combined BTC regime candidates")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="1h,4h")
    parser.add_argument("--families", default="donchian_breakout,ema_pullback_continuation,short_continuation_pressure")
    parser.add_argument("--filter-modes", default="none,risk_filters,all_filters")
    parser.add_argument("--structure-lookback", type=int, default=50)
    parser.add_argument("--sweep-lookback", type=int, default=3)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--funding-hot-abs", type=float, default=0.00005)
    parser.add_argument("--stop-grid", default="1.5")
    parser.add_argument("--take-grid", default="2.0")
    parser.add_argument("--hold-grid", default="12")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--splits", type=int, default=5)
    parser.add_argument("--train-min-trades", type=int, default=50)
    parser.add_argument("--test-min-trades", type=int, default=10)
    parser.add_argument("--test-min-trades-total", type=int, default=50)
    parser.add_argument("--min-winrate-pct", type=float, default=51.0)
    parser.add_argument("--min-expectancy-r", type=float, default=0.03)
    parser.add_argument("--max-drawdown-r", type=float, default=20.0)
    parser.add_argument("--min-selected-windows", type=int, default=2)
    parser.add_argument("--min-test-passes", type=int, default=2)
    parser.add_argument("--no-overlap", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--out-prefix", default="docs/COMBINED_REGIME_WALKFORWARD_2026-06-03")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    intervals = parse_list(args.intervals, str)
    families = parse_list(args.families, str)
    filter_modes = parse_list(args.filter_modes, str)
    stop_grid = parse_list(args.stop_grid, float)
    take_grid = parse_list(args.take_grid, float)
    hold_grid = parse_list(args.hold_grid, int)
    cost_bps_per_side = args.fee_bps + args.slippage_bps

    bars_by_dataset: dict[str, list[Any]] = {}
    signals_by_key: dict[tuple[str, str, str], list[Any]] = {}
    dataset_infos: list[dict[str, Any]] = []
    for interval in intervals:
        dataset_id = f"wf_combined_BTCUSDT_{interval}"
        futures_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv"
        spot_path = cache_dir / "spot" / "BTCUSDT" / f"{interval}_klines.csv"
        derivatives_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_oi_aligned.csv"
        htf_interval = "1h" if interval == "15m" else "4h"
        htf_path = cache_dir / "futures" / "BTCUSDT" / f"{htf_interval}_klines.csv"
        bars = load_ohlcv(futures_path)
        spot_bars = load_ohlcv(spot_path)
        spot_by_time = {bar.ts: bar for bar in spot_bars}
        derivatives_by_time = load_csv_by_time(derivatives_path)
        htf_bars = load_ohlcv(htf_path)
        bars_by_dataset[dataset_id] = bars

        total_rows = len(bars)
        oi_present = sum(1 for row in derivatives_by_time.values() if safe_float(row.get("open_interest")) is not None)
        funding_present = sum(1 for row in derivatives_by_time.values() if safe_float(row.get("funding")) is not None)
        signal_count = 0
        for family in families:
            for filter_mode in filter_modes:
                signals = generate_signals(
                    dataset_id=dataset_id,
                    bars=bars,
                    spot_by_time=spot_by_time,
                    derivatives_by_time=derivatives_by_time,
                    htf_bars=htf_bars,
                    family=family,
                    filter_mode=filter_mode,
                    structure_lookback=args.structure_lookback,
                    sweep_lookback=args.sweep_lookback,
                    spot_perp_lookback=args.spot_perp_lookback,
                    oi_lag=args.oi_lag,
                    funding_hot_abs=args.funding_hot_abs,
                )
                signals_by_key[(dataset_id, family, filter_mode)] = signals
                signal_count += len(signals)
        dataset_infos.append(
            {
                "dataset_id": dataset_id,
                "interval": interval,
                "rows": total_rows,
                "signals": signal_count,
                "oi_coverage_pct": round(oi_present / total_rows * 100.0, 3) if total_rows else 0.0,
                "funding_coverage_pct": round(funding_present / total_rows * 100.0, 3) if total_rows else 0.0,
                "paths": {
                    "futures": str(futures_path),
                    "spot": str(spot_path),
                    "derivatives": str(derivatives_path),
                    "htf": str(htf_path),
                },
            }
        )

    results: list[dict[str, Any]] = []
    for family in families:
        for filter_mode in filter_modes:
            for stop_atr in stop_grid:
                for take_atr in take_grid:
                    for max_hold_bars in hold_grid:
                        strategy_id = f"{family}_{filter_mode}_s{stop_atr:g}_t{take_atr:g}_h{max_hold_bars}"
                        trades = []
                        for dataset_id, bars in bars_by_dataset.items():
                            trades.extend(
                                simulate_signals(
                                    dataset_id=dataset_id,
                                    strategy_id=strategy_id,
                                    bars=bars,
                                    signals=signals_by_key.get((dataset_id, family, filter_mode), []),
                                    stop_atr=stop_atr,
                                    take_atr=take_atr,
                                    max_hold_bars=max_hold_bars,
                                    cost_bps_per_side=cost_bps_per_side,
                                    no_overlap=args.no_overlap,
                                )
                            )
                        wf = walk_forward_result(
                            sorted(trades, key=lambda item: item.entry_ts),
                            splits=args.splits,
                            train_min_trades=args.train_min_trades,
                            test_min_trades=args.test_min_trades,
                            min_winrate_pct=args.min_winrate_pct,
                            min_expectancy_r=args.min_expectancy_r,
                            max_drawdown_r=args.max_drawdown_r,
                        )
                        verdict = verdict_for_walkforward(
                            wf,
                            min_selected_windows=args.min_selected_windows,
                            min_test_passes=args.min_test_passes,
                            test_min_trades_total=args.test_min_trades_total,
                            min_winrate_pct=args.min_winrate_pct,
                            min_expectancy_r=args.min_expectancy_r,
                        )
                        results.append(
                            {
                                "strategy_id": strategy_id,
                                "params": {
                                    "family": family,
                                    "filter_mode": filter_mode,
                                    "stop_atr": stop_atr,
                                    "take_atr": take_atr,
                                    "max_hold_bars": max_hold_bars,
                                    "fee_bps": args.fee_bps,
                                    "slippage_bps": args.slippage_bps,
                                    "no_overlap": args.no_overlap,
                                },
                                "walk_forward": wf,
                                "verdict": verdict,
                            }
                        )

    ranked = sorted(
        results,
        key=lambda item: (
            1 if item["verdict"] == "paper_candidate_after_oos_replay" else 0,
            1 if item["verdict"] == "watchlist_only" else 0,
            item["walk_forward"]["selected_windows"],
            item["walk_forward"]["test_passes"],
            item["walk_forward"]["selected_test_summary"].get("expectancy_r") or -999.0,
            item["walk_forward"]["all_summary"].get("expectancy_r") or -999.0,
        ),
        reverse=True,
    )
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_walk_forward_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "datasets": dataset_infos,
        "walk_forward_requirements": {
            "splits": args.splits,
            "train_min_trades": args.train_min_trades,
            "test_min_trades": args.test_min_trades,
            "test_min_trades_total": args.test_min_trades_total,
            "min_winrate_pct": args.min_winrate_pct,
            "min_expectancy_r": args.min_expectancy_r,
            "min_selected_windows": args.min_selected_windows,
            "min_test_passes": args.min_test_passes,
            "max_drawdown_r": -abs(args.max_drawdown_r),
        },
        "oos_pass_count": sum(1 for item in results if item["verdict"] == "paper_candidate_after_oos_replay"),
        "watchlist_count": sum(1 for item in results if item["verdict"] == "watchlist_only"),
        "blocked_count": sum(1 for item in results if item["verdict"] == "research_only_or_reject"),
        "top_results": ranked[:15],
        "all_results": results,
    }
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(
        {
            "oos_pass_count": report["oos_pass_count"],
            "watchlist_count": report["watchlist_count"],
            "blocked_count": report["blocked_count"],
            "top_results": [
                {
                    "strategy_id": item["strategy_id"],
                    "verdict": item["verdict"],
                    "all_summary": item["walk_forward"]["all_summary"],
                    "selected_windows": item["walk_forward"]["selected_windows"],
                    "test_passes": item["walk_forward"]["test_passes"],
                    "selected_test_summary": item["walk_forward"]["selected_test_summary"],
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
