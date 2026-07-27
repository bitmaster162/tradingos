#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_forward_eval import compute_atr  # noqa: E402
from tools.liquidity_sweep_hardening import Trade, fold_summaries, simulate_trade, summarize_trades  # noqa: E402


DEFAULT_CACHE_DIR = ROOT / "data" / "cache" / "binance_spot_perp_extended"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CROWD_FADE_POSITIONING_DIAGNOSTIC_2026-06-19"
DEFAULT_CANDIDATE_LOCK = ROOT / "configs" / "CROWD_FADE_FORWARD_LOCK.json"

RATIO_FIELDS = [
    "global_long_short_ratio",
    "top_account_long_short_ratio",
    "top_position_long_short_ratio",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_csv_float_by_time(path: Path, fields: list[str]) -> dict[str, dict[str, float]]:
    by_time: dict[str, dict[str, float]] = {}
    for row in read_csv_rows(path):
        ts = str(row.get("time") or "").strip()
        if not ts:
            continue
        values: dict[str, float] = {}
        for field in fields:
            parsed = safe_float(row.get(field))
            if parsed is not None:
                values[field] = parsed
        if values:
            by_time[ts] = values
    return by_time


def rolling_z(values: list[float | None], window: int) -> list[float | None]:
    out: list[float | None] = []
    for index, value in enumerate(values):
        if value is None or index < window:
            out.append(None)
            continue
        sample = [item for item in values[index - window : index] if item is not None]
        if len(sample) < max(12, window // 2):
            out.append(None)
            continue
        mean = statistics.fmean(sample)
        stdev = statistics.pstdev(sample)
        if stdev <= 0:
            out.append(None)
            continue
        out.append((value - mean) / stdev)
    return out


def pct_change(values: list[float | None], index: int, lookback: int) -> float | None:
    if index - lookback < 0:
        return None
    current = values[index]
    previous = values[index - lookback]
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous


def build_signals(
    *,
    bars: list[Any],
    crowd_by_time: dict[str, dict[str, float]],
    derivatives_by_time: dict[str, dict[str, float]],
    ratio_field: str,
    z_window: int,
    z_threshold: float,
    side_mode: str,
    oi_lookback: int,
    require_oi_expansion: bool,
    require_funding_alignment: bool,
    atr_values: list[float | None],
    prepared: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if prepared is None:
        ratios = [crowd_by_time.get(bar.ts, {}).get(ratio_field) for bar in bars]
        z_values = rolling_z(ratios, z_window)
        oi_values = [derivatives_by_time.get(bar.ts, {}).get("open_interest") for bar in bars]
        oi_delta_values = [pct_change(oi_values, index, oi_lookback) for index in range(len(bars))]
        funding_values = [derivatives_by_time.get(bar.ts, {}).get("funding") for bar in bars]
    else:
        ratios = prepared["ratios_by_field"][ratio_field]
        z_values = prepared["z_by_field_window"][(ratio_field, z_window)]
        oi_delta_values = prepared["oi_delta_values"]
        funding_values = prepared["funding_values"]

    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        atr = atr_values[index]
        z_value = z_values[index]
        ratio = ratios[index]
        if atr is None or atr <= 0 or z_value is None or ratio is None:
            continue

        if side_mode == "crowded_longs_fade_short":
            if z_value < z_threshold:
                continue
            side_hint = "SHORT"
            if require_funding_alignment and (funding_values[index] is None or funding_values[index] < 0):
                continue
        elif side_mode == "crowded_shorts_fade_long":
            if z_value > -z_threshold:
                continue
            side_hint = "LONG"
            if require_funding_alignment and (funding_values[index] is None or funding_values[index] > 0):
                continue
        else:
            raise ValueError(f"unsupported side_mode: {side_mode}")

        oi_delta = oi_delta_values[index]
        if require_oi_expansion and (oi_delta is None or oi_delta <= 0):
            continue

        signals.append(
            {
                "bar_index": index,
                "side_hint": side_hint,
                "atr": atr,
                "ratio": ratio,
                "ratio_z": z_value,
                "oi_delta": oi_delta,
                "funding": funding_values[index],
                "reason": side_mode,
            }
        )
    return signals


def no_overlap_simulate(
    *,
    dataset_id: str,
    strategy_id: str,
    bars: list[Any],
    signals: list[dict[str, Any]],
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    cost_bps_per_side: float,
) -> list[Trade]:
    trades: list[Trade] = []
    last_exit_ts = ""
    last_exit_index = -1
    for signal in sorted(signals, key=lambda item: int(item["bar_index"])):
        if int(signal["bar_index"]) <= last_exit_index:
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
        last_exit_ts = trade.exit_ts
        for index in range(int(signal["bar_index"]) + 1, min(len(bars), int(signal["bar_index"]) + max_hold_bars + 2)):
            if bars[index].ts == last_exit_ts:
                last_exit_index = index
                break
    return trades


def evaluate_strategy(
    *,
    interval: str,
    bars: list[Any],
    signals: list[dict[str, Any]],
    ratio_field: str,
    z_window: int,
    z_threshold: float,
    side_mode: str,
    require_oi_expansion: bool,
    require_funding_alignment: bool,
    stop_atr: float,
    take_atr: float,
    hold: int,
    cost_bps_per_side: float,
) -> dict[str, Any]:
    strategy_id = (
        f"crowd_fade_{interval}_{ratio_field}_{side_mode}_z{z_threshold:g}_w{z_window}"
        f"_oi{int(require_oi_expansion)}_fund{int(require_funding_alignment)}_s{stop_atr:g}_t{take_atr:g}_h{hold}"
    )
    trades = no_overlap_simulate(
        dataset_id=f"futures_BTCUSDT_{interval}",
        strategy_id=strategy_id,
        bars=bars,
        signals=signals,
        stop_atr=stop_atr,
        take_atr=take_atr,
        max_hold_bars=hold,
        cost_bps_per_side=cost_bps_per_side,
    )
    summary = summarize_trades(trades)
    folds = fold_summaries(trades, 3)
    ordered = sorted(trades, key=lambda item: item.entry_ts)
    holdout = ordered[round(len(ordered) * 0.7) :]
    holdout_summary = summarize_trades(holdout)
    stable_folds = sum(1 for item in folds if item.get("stable"))
    return {
        "strategy_id": strategy_id,
        "interval": interval,
        "ratio_field": ratio_field,
        "side_mode": side_mode,
        "z_window": z_window,
        "z_threshold": z_threshold,
        "require_oi_expansion": require_oi_expansion,
        "require_funding_alignment": require_funding_alignment,
        "stop_atr": stop_atr,
        "take_atr": take_atr,
        "rr": round(take_atr / stop_atr, 3) if stop_atr else None,
        "hold": hold,
        "cost_bps_per_side": cost_bps_per_side,
        "signals": len(signals),
        "summary": summary,
        "holdout_summary": holdout_summary,
        "folds": folds,
        "stable_folds": stable_folds,
        "sample_trades": [asdict(item) for item in ordered[-5:]],
    }


def classify_result(result: dict[str, Any], min_trades: int, min_holdout: int) -> str:
    summary = result.get("summary", {})
    holdout = result.get("holdout_summary", {})
    trades = int(summary.get("trades") or 0)
    holdout_trades = int(holdout.get("trades") or 0)
    expectancy = float(summary.get("expectancy_r") or 0.0)
    holdout_expectancy = float(holdout.get("expectancy_r") or 0.0)
    stable_folds = int(result.get("stable_folds") or 0)
    if trades >= min_trades and holdout_trades >= min_holdout and expectancy > 0 and holdout_expectancy > 0 and stable_folds >= 2:
        return "candidate_watchlist_limited_history"
    if trades >= max(15, min_trades // 2) and expectancy > 0 and holdout_expectancy > 0:
        return "research_watchlist_low_sample"
    return "reject"


def coverage_for_interval(interval: str, bars: list[Any], crowd_by_time: dict[str, dict[str, float]]) -> dict[str, Any]:
    matched = [bar for bar in bars if bar.ts in crowd_by_time]
    return {
        "interval": interval,
        "bars": len(bars),
        "crowd_rows": len(crowd_by_time),
        "matched_bars": len(matched),
        "first_match": matched[0].ts if matched else None,
        "last_match": matched[-1].ts if matched else None,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Crowd-Fade Positioning Diagnostic",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Evaluated strategies: `{report['evaluated_count']}`",
        f"- Candidates: `{report['candidate_count']}`",
        f"- Watchlist: `{report['watchlist_count']}`",
        f"- Can trade: `{report['can_trade']}`",
        "",
        "## Coverage",
        "",
        "| Interval | Bars | Crowd rows | Matched bars | First match | Last match |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for item in report["coverage"]:
        lines.append(
            f"| `{item['interval']}` | {item['bars']} | {item['crowd_rows']} | {item['matched_bars']} | "
            f"`{item['first_match']}` | `{item['last_match']}` |"
        )
    lines.extend(["", "## Top Results", ""])
    for item in report["top_results"][:10]:
        summary = item["summary"]
        holdout = item["holdout_summary"]
        lines.extend(
            [
                f"### {item['classification']}: `{item['strategy_id']}`",
                "",
                f"- Trades: `{summary.get('trades')}`, WR: `{summary.get('winrate_pct')}`, EXP: `{summary.get('expectancy_r')}`, DD: `{summary.get('max_drawdown_r')}`",
                f"- Holdout trades: `{holdout.get('trades')}`, holdout EXP: `{holdout.get('expectancy_r')}`",
                f"- RR: `{item.get('rr')}`, stable folds: `{item.get('stable_folds')}`",
                "",
            ]
        )
    locked = report.get("locked_candidate_result")
    if isinstance(locked, dict):
        locked_summary = locked.get("summary", {})
        lines.extend(
            [
                "## Locked Forward Candidate",
                "",
                f"- Strategy: `{locked.get('strategy_id')}`",
                f"- Classification: `{locked.get('classification')}`",
                f"- Trades: `{locked_summary.get('trades')}`",
                f"- Expectancy R: `{locked_summary.get('expectancy_r')}`",
                "- This result is reported for governance; the diagnostic cannot replace the lock.",
                "",
            ]
        )
    lines.extend(
        [
            "## Boundary",
            "",
            "- This is a research diagnostic, not a live strategy.",
            "- Official archive coverage is gapped in 2022; missing ratio points are not forward-filled.",
            "- Historical candidates still require temporal holdout and independent forward proof.",
            "- `can_trade=false` by design.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Test Binance crowd-positioning fade hypotheses on cached BTCUSDT data.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--candidate-lock", default=str(DEFAULT_CANDIDATE_LOCK))
    parser.add_argument("--cost-bps-per-side", type=float, default=5.0)
    parser.add_argument("--min-trades", type=int, default=50)
    parser.add_argument("--min-holdout", type=int, default=12)
    args = parser.parse_args()

    cache_dir = resolve_path(args.cache_dir)
    candidate_lock = read_json(resolve_path(args.candidate_lock))
    locked_candidate = candidate_lock.get("candidate") if isinstance(candidate_lock.get("candidate"), dict) else {}
    locked_strategy_id = str(locked_candidate.get("strategy_id") or "")
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]

    coverage: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for interval in intervals:
        futures_path = cache_dir / "futures" / args.symbol.upper() / f"{interval}_klines.csv"
        derivatives_path = cache_dir / "futures" / args.symbol.upper() / f"{interval}_oi_aligned.csv"
        crowd_path = cache_dir / "futures" / args.symbol.upper() / f"{interval}_crowd_positioning.csv"
        if not futures_path.exists() or not derivatives_path.exists() or not crowd_path.exists():
            coverage.append(
                {
                    "interval": interval,
                    "bars": 0,
                    "crowd_rows": 0,
                    "matched_bars": 0,
                    "first_match": None,
                    "last_match": None,
                    "missing": [str(path) for path in (futures_path, derivatives_path, crowd_path) if not path.exists()],
                }
            )
            continue

        bars = load_ohlcv(futures_path)
        atr_values = compute_atr(bars, 14)
        crowd_by_time = parse_csv_float_by_time(crowd_path, RATIO_FIELDS)
        derivatives_by_time = parse_csv_float_by_time(derivatives_path, ["open_interest", "funding"])
        coverage.append(coverage_for_interval(interval, bars, crowd_by_time))

        z_windows = [48, 96] if interval == "15m" else [24, 72] if interval == "1h" else [12, 30]
        oi_lookback = 16 if interval == "15m" else 6 if interval == "1h" else 3
        holds = [12, 24] if interval == "15m" else [8, 16] if interval == "1h" else [4, 8]

        ratios_by_field = {
            field: [crowd_by_time.get(bar.ts, {}).get(field) for bar in bars]
            for field in RATIO_FIELDS
        }
        z_by_field_window = {
            (field, window): rolling_z(ratios_by_field[field], window)
            for field in RATIO_FIELDS
            for window in z_windows
        }
        oi_values = [derivatives_by_time.get(bar.ts, {}).get("open_interest") for bar in bars]
        prepared = {
            "ratios_by_field": ratios_by_field,
            "z_by_field_window": z_by_field_window,
            "oi_delta_values": [pct_change(oi_values, index, oi_lookback) for index in range(len(bars))],
            "funding_values": [derivatives_by_time.get(bar.ts, {}).get("funding") for bar in bars],
        }

        signal_cache: dict[tuple[str, int, float, str, bool, bool], list[dict[str, Any]]] = {}
        for ratio_field in RATIO_FIELDS:
            for z_window in z_windows:
                for z_threshold in (0.8, 1.0, 1.25, 1.5):
                    for side_mode in ("crowded_longs_fade_short", "crowded_shorts_fade_long"):
                        for require_oi_expansion in (False, True):
                            for require_funding_alignment in (False, True):
                                key = (
                                    ratio_field,
                                    z_window,
                                    z_threshold,
                                    side_mode,
                                    require_oi_expansion,
                                    require_funding_alignment,
                                )
                                signals = signal_cache.get(key)
                                if signals is None:
                                    signals = build_signals(
                                        bars=bars,
                                        crowd_by_time=crowd_by_time,
                                        derivatives_by_time=derivatives_by_time,
                                        ratio_field=ratio_field,
                                        z_window=z_window,
                                        z_threshold=z_threshold,
                                        side_mode=side_mode,
                                        oi_lookback=oi_lookback,
                                        require_oi_expansion=require_oi_expansion,
                                        require_funding_alignment=require_funding_alignment,
                                        atr_values=atr_values,
                                        prepared=prepared,
                                    )
                                    signal_cache[key] = signals
                                if len(signals) < 5:
                                    continue
                                for stop_atr, take_atr in ((1.0, 1.5), (1.0, 2.0), (1.0, 3.0)):
                                    for hold in holds:
                                        result = evaluate_strategy(
                                            interval=interval,
                                            bars=bars,
                                            signals=signals,
                                            ratio_field=ratio_field,
                                            z_window=z_window,
                                            z_threshold=z_threshold,
                                            side_mode=side_mode,
                                            require_oi_expansion=require_oi_expansion,
                                            require_funding_alignment=require_funding_alignment,
                                            stop_atr=stop_atr,
                                            take_atr=take_atr,
                                            hold=hold,
                                            cost_bps_per_side=args.cost_bps_per_side,
                                        )
                                        result["classification"] = classify_result(result, args.min_trades, args.min_holdout)
                                        results.append(result)

    sorted_results = sorted(
        results,
        key=lambda item: (
            item["classification"] == "candidate_watchlist_limited_history",
            item["classification"] == "research_watchlist_low_sample",
            float(item["summary"].get("expectancy_r") or -999),
            int(item["summary"].get("trades") or 0),
        ),
        reverse=True,
    )
    candidates = [item for item in sorted_results if item["classification"] == "candidate_watchlist_limited_history"]
    watchlist = [item for item in sorted_results if item["classification"] == "research_watchlist_low_sample"]
    locked_result = next((item for item in sorted_results if item.get("strategy_id") == locked_strategy_id), None)
    decision = (
        "candidate_watchlist_limited_history_needs_forward_proof"
        if candidates
        else "research_watchlist_low_sample_needs_more_data"
        if watchlist
        else "no_crowd_fade_candidate_found"
    )
    report = {
        "generated_at": now_iso(),
        "engine": "CROWD_FADE_POSITIONING_DIAGNOSTIC",
        "engine_version": "1.0.0",
        "symbol": args.symbol.upper(),
        "intervals": intervals,
        "cache_dir": str(cache_dir),
        "cost_bps_per_side": args.cost_bps_per_side,
        "decision": decision,
        "coverage": coverage,
        "evaluated_count": len(results),
        "candidate_count": len(candidates),
        "watchlist_count": len(watchlist),
        "reject_count": len([item for item in sorted_results if item["classification"] == "reject"]),
        "top_results": sorted_results[:25],
        "candidate_lock_version": candidate_lock.get("version"),
        "locked_strategy_id": locked_strategy_id or None,
        "locked_candidate_result": locked_result,
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
                "evaluated_count": report["evaluated_count"],
                "candidate_count": report["candidate_count"],
                "watchlist_count": report["watchlist_count"],
                "top": [
                    {
                        "strategy_id": item["strategy_id"],
                        "classification": item["classification"],
                        "trades": item["summary"].get("trades"),
                        "expectancy_r": item["summary"].get("expectancy_r"),
                        "winrate_pct": item["summary"].get("winrate_pct"),
                    }
                    for item in sorted_results[:5]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
