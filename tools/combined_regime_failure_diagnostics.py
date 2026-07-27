#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
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
    safe_float,
    simulate_signals,
)
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_hardening import summarize_trades  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_meta(trades: list[Any], **meta: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in trades:
        row = trade.__dict__.copy()
        row.update(meta)
        rows.append(row)
    return rows


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    class RowTrade:
        def __init__(self, row: dict[str, Any]) -> None:
            self.r_net = float(row["r_net"])
            self.entry_ts = str(row["entry_ts"])

    summary = summarize_trades([RowTrade(row) for row in rows])
    return summary


def grouped_summary(rows: list[dict[str, Any]], keys: list[str], *, min_trades: int = 1) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(row.get(key) for key in keys)].append(row)
    out: list[dict[str, Any]] = []
    for key_values, bucket_rows in buckets.items():
        if len(bucket_rows) < min_trades:
            continue
        item = {key: value for key, value in zip(keys, key_values)}
        item["summary"] = summarize_rows(bucket_rows)
        out.append(item)
    return sorted(
        out,
        key=lambda item: (
            item["summary"]["expectancy_r"] or -999.0,
            item["summary"]["trades"],
            item["summary"]["winrate_pct"] or 0.0,
        ),
        reverse=True,
    )


def verdict_for_bucket(summary: dict[str, Any]) -> str:
    trades = summary.get("trades") or 0
    expectancy = summary.get("expectancy_r") or -999.0
    winrate = summary.get("winrate_pct") or 0.0
    drawdown = summary.get("max_drawdown_r") or 0.0
    if trades >= 100 and expectancy >= 0.03 and winrate >= 51.0 and drawdown >= -20.0:
        return "candidate_for_oos"
    if trades >= 50 and expectancy > 0 and winrate >= 49.0:
        return "watchlist_diagnostic_only"
    if trades >= 100 and expectancy < -0.05:
        return "avoid_or_invert_research"
    return "insufficient_or_mixed"


def annotate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in items:
        copy = dict(item)
        copy["diagnostic_verdict"] = verdict_for_bucket(item["summary"])
        result.append(copy)
    return result


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Combined Regime Failure Diagnostics",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research diagnostics only.",
        "- Replays the same bounded combined-regime families and groups losses/wins by cause.",
        "- Does not send orders and does not unlock paper/live trading.",
        "",
        "## Executive Result",
        "",
        f"- Total tested trades: `{report['overall']['trades']}`.",
        f"- Overall winrate: `{report['overall']['winrate_pct']}`.",
        f"- Overall expectancy: `{report['overall']['expectancy_r']}R`.",
        f"- Strong buckets found: `{report['strong_bucket_count']}`.",
        f"- Avoid/invert buckets found: `{report['avoid_bucket_count']}`.",
        "",
        "## Data Coverage",
        "",
    ]
    for item in report["datasets"]:
        lines.append(
            f"- `{item['interval']}` rows=`{item['rows']}` signals=`{item['signals']}` "
            f"OI coverage=`{item['oi_coverage_pct']}`% funding coverage=`{item['funding_coverage_pct']}`%."
        )
    lines.extend(
        [
            "",
            "## Best Buckets By Timeframe + Family + Filter",
            "",
            "| TF | Family | Filter | Trades | Winrate | Exp R | DD R | Verdict |",
            "|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["by_interval_family_filter"][:12]:
        summary = item["summary"]
        lines.append(
            f"| `{item['interval']}` | `{item['family']}` | `{item['filter_mode']}` | "
            f"`{summary['trades']}` | `{summary['winrate_pct']}` | `{summary['expectancy_r']}` | "
            f"`{summary['max_drawdown_r']}` | `{item['diagnostic_verdict']}` |"
        )
    lines.extend(
        [
            "",
            "## Worst Buckets",
            "",
            "| TF | Family | Filter | Trades | Winrate | Exp R | DD R | Verdict |",
            "|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["worst_interval_family_filter"][:10]:
        summary = item["summary"]
        lines.append(
            f"| `{item['interval']}` | `{item['family']}` | `{item['filter_mode']}` | "
            f"`{summary['trades']}` | `{summary['winrate_pct']}` | `{summary['expectancy_r']}` | "
            f"`{summary['max_drawdown_r']}` | `{item['diagnostic_verdict']}` |"
        )
    lines.extend(
        [
            "",
            "## Side Diagnostics",
            "",
            "| Side | Trades | Winrate | Exp R | DD R | Verdict |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["by_side"]:
        summary = item["summary"]
        lines.append(
            f"| `{item['side']}` | `{summary['trades']}` | `{summary['winrate_pct']}` | "
            f"`{summary['expectancy_r']}` | `{summary['max_drawdown_r']}` | `{item['diagnostic_verdict']}` |"
        )
    lines.extend(
        [
            "",
            "## Exit Diagnostics",
            "",
            "| Exit | Trades | Winrate | Exp R | DD R | Verdict |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["by_exit_reason"]:
        summary = item["summary"]
        lines.append(
            f"| `{item['exit_reason']}` | `{summary['trades']}` | `{summary['winrate_pct']}` | "
            f"`{summary['expectancy_r']}` | `{summary['max_drawdown_r']}` | `{item['diagnostic_verdict']}` |"
        )
    lines.extend(
        [
            "",
            "## Next Action",
            "",
            f"- Recommended next move: `{report['next_action']['id']}`.",
            f"- Reason: {report['next_action']['reason']}",
            "",
        ]
    )
    return "\n".join(lines)


def choose_next_action(report: dict[str, Any]) -> dict[str, str]:
    strong = report["strong_bucket_count"]
    avoid = report["avoid_bucket_count"]
    by_side = {item["side"]: item["summary"] for item in report["by_side"]}
    long_exp = (by_side.get("LONG") or {}).get("expectancy_r")
    short_exp = (by_side.get("SHORT") or {}).get("expectancy_r")
    if strong > 0:
        return {
            "id": "promote_strong_bucket_to_independent_oos",
            "reason": "At least one diagnostic bucket passed broad research thresholds; isolate it and replay with independent OOS.",
        }
    if long_exp is not None and short_exp is not None and long_exp < -0.05 and short_exp < -0.05:
        return {
            "id": "stop_tuning_trend_continuation_build_range_or_event_first_baseline",
            "reason": "Both long and short combined trend/continuation sides are negative; changing stops is unlikely to fix the hypothesis class.",
        }
    if avoid >= 3:
        return {
            "id": "invert_or_veto_map_before_new_signals",
            "reason": "Several large buckets are consistently negative; they are more useful as vetoes or inversion research than entries.",
        }
    return {
        "id": "improve_data_coverage_then_rerun",
        "reason": "No robust positive bucket was found; improve OI/derivatives coverage and rerun before adding more complexity.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Failure diagnostics for combined BTC regime candidates")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="15m,1h,4h")
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
    parser.add_argument("--no-overlap", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--out-prefix", default="docs/COMBINED_REGIME_FAILURE_DIAGNOSTICS_2026-06-04")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    intervals = parse_list(args.intervals, str)
    families = parse_list(args.families, str)
    filter_modes = parse_list(args.filter_modes, str)
    stop_grid = parse_list(args.stop_grid, float)
    take_grid = parse_list(args.take_grid, float)
    hold_grid = parse_list(args.hold_grid, int)
    cost_bps_per_side = args.fee_bps + args.slippage_bps

    rows: list[dict[str, Any]] = []
    dataset_infos: list[dict[str, Any]] = []
    for interval in intervals:
        dataset_id = f"diag_combined_BTCUSDT_{interval}"
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
                signal_count += len(signals)
                for stop_atr in stop_grid:
                    for take_atr in take_grid:
                        for max_hold_bars in hold_grid:
                            strategy_id = f"{family}_{filter_mode}_s{stop_atr:g}_t{take_atr:g}_h{max_hold_bars}"
                            trades = simulate_signals(
                                dataset_id=dataset_id,
                                strategy_id=strategy_id,
                                bars=bars,
                                signals=signals,
                                stop_atr=stop_atr,
                                take_atr=take_atr,
                                max_hold_bars=max_hold_bars,
                                cost_bps_per_side=cost_bps_per_side,
                                no_overlap=args.no_overlap,
                            )
                            rows.extend(
                                add_meta(
                                    trades,
                                    interval=interval,
                                    family=family,
                                    filter_mode=filter_mode,
                                    stop_atr=stop_atr,
                                    take_atr=take_atr,
                                    max_hold_bars=max_hold_bars,
                                )
                            )
        total_rows = len(bars)
        oi_present = sum(1 for row in derivatives_by_time.values() if safe_float(row.get("open_interest")) is not None)
        funding_present = sum(1 for row in derivatives_by_time.values() if safe_float(row.get("funding")) is not None)
        dataset_infos.append(
            {
                "dataset_id": dataset_id,
                "interval": interval,
                "rows": total_rows,
                "signals": signal_count,
                "oi_coverage_pct": round(oi_present / total_rows * 100.0, 3) if total_rows else 0.0,
                "funding_coverage_pct": round(funding_present / total_rows * 100.0, 3) if total_rows else 0.0,
            }
        )

    by_interval_family_filter = annotate(grouped_summary(rows, ["interval", "family", "filter_mode"], min_trades=1))
    worst_interval_family_filter = sorted(
        by_interval_family_filter,
        key=lambda item: (
            item["summary"]["expectancy_r"] or 999.0,
            -(item["summary"]["trades"] or 0),
        ),
    )
    by_side = annotate(grouped_summary(rows, ["side"], min_trades=1))
    by_exit_reason = annotate(grouped_summary(rows, ["exit_reason"], min_trades=1))
    by_family = annotate(grouped_summary(rows, ["family"], min_trades=1))
    by_interval = annotate(grouped_summary(rows, ["interval"], min_trades=1))
    strong_bucket_count = sum(1 for item in by_interval_family_filter if item["diagnostic_verdict"] == "candidate_for_oos")
    avoid_bucket_count = sum(1 for item in by_interval_family_filter if item["diagnostic_verdict"] == "avoid_or_invert_research")

    report: dict[str, Any] = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_failure_diagnostics_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "datasets": dataset_infos,
        "overall": summarize_rows(rows),
        "strong_bucket_count": strong_bucket_count,
        "avoid_bucket_count": avoid_bucket_count,
        "by_interval_family_filter": by_interval_family_filter,
        "worst_interval_family_filter": worst_interval_family_filter,
        "by_family": by_family,
        "by_interval": by_interval,
        "by_side": by_side,
        "by_exit_reason": by_exit_reason,
        "sample_trades": rows[:20],
    }
    report["next_action"] = choose_next_action(report)

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(
        {
            "overall": report["overall"],
            "strong_bucket_count": strong_bucket_count,
            "avoid_bucket_count": avoid_bucket_count,
            "next_action": report["next_action"],
            "top_buckets": [
                {
                    "interval": item["interval"],
                    "family": item["family"],
                    "filter_mode": item["filter_mode"],
                    "summary": item["summary"],
                    "diagnostic_verdict": item["diagnostic_verdict"],
                }
                for item in by_interval_family_filter[:5]
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
