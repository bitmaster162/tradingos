#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.combined_regime_hardening import ema  # noqa: E402
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_forward_eval import compute_atr  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(str(value).strip())
    except ValueError:
        return None
    return None if math.isnan(out) else out


def true_ranges(bars: list[Any]) -> list[float]:
    out: list[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            out.append(bar.high - bar.low)
            continue
        prev_close = bars[index - 1].close
        out.append(max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close)))
    return out


def adx(bars: list[Any], length: int = 14) -> list[float | None]:
    if len(bars) <= length + 1:
        return [None] * len(bars)
    trs = true_ranges(bars)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for index in range(1, len(bars)):
        up_move = bars[index].high - bars[index - 1].high
        down_move = bars[index - 1].low - bars[index].low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)

    plus_di: list[float | None] = [None] * len(bars)
    minus_di: list[float | None] = [None] * len(bars)
    dx: list[float | None] = [None] * len(bars)
    for index in range(length, len(bars)):
        tr_sum = sum(trs[index + 1 - length : index + 1])
        if tr_sum <= 0:
            continue
        pdi = 100.0 * sum(plus_dm[index + 1 - length : index + 1]) / tr_sum
        mdi = 100.0 * sum(minus_dm[index + 1 - length : index + 1]) / tr_sum
        plus_di[index] = pdi
        minus_di[index] = mdi
        denom = pdi + mdi
        dx[index] = None if denom <= 0 else 100.0 * abs(pdi - mdi) / denom

    out: list[float | None] = [None] * len(bars)
    for index in range(length * 2 - 1, len(bars)):
        window = [value for value in dx[index + 1 - length : index + 1] if value is not None]
        if len(window) == length:
            out[index] = sum(window) / length
    return out


def sign(value: float) -> float:
    if value > 0:
        return 1.0
    if value < 0:
        return -1.0
    return 0.0


def clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def classify_regimes(
    bars: list[Any],
    *,
    shock_range_atr: float,
    min_adx: float,
    trend_threshold: float,
) -> list[dict[str, Any]]:
    closes = [bar.close for bar in bars]
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    atr14 = compute_atr(bars, 14)
    adx14 = adx(bars, 14)
    regimes: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        atr_value = atr14[index]
        range_atr = None
        if atr_value and atr_value > 0:
            range_atr = true_ranges(bars)[index] / atr_value
        ts = None
        if (
            atr_value
            and atr_value > 0
            and ema50[index] is not None
            and ema200[index] is not None
            and index >= 3
            and ema50[index - 3] is not None
        ):
            distance = (float(ema50[index]) - float(ema200[index])) / (1.5 * atr_value)
            slope = sign(float(ema50[index]) - float(ema50[index - 3]))
            ts = 0.5 * clip(distance, -1.0, 1.0) + 0.5 * slope

        shock = bool(range_atr is not None and range_atr >= shock_range_atr)
        if shock:
            regime = "SHOCK"
        elif ts is not None and (adx14[index] or 0.0) >= min_adx and ts >= trend_threshold:
            regime = "TREND_UP"
        elif ts is not None and (adx14[index] or 0.0) >= min_adx and ts <= -trend_threshold:
            regime = "TREND_DOWN"
        else:
            regime = "RANGE"
        regimes.append(
            {
                "index": index,
                "ts": bar.ts,
                "close": bar.close,
                "regime": regime,
                "trend_strength_score": None if ts is None else round(ts, 6),
                "adx14": None if adx14[index] is None else round(float(adx14[index]), 6),
                "atr14": None if atr_value is None else round(float(atr_value), 8),
                "range_atr": None if range_atr is None else round(float(range_atr), 6),
                "ema50": None if ema50[index] is None else round(float(ema50[index]), 8),
                "ema200": None if ema200[index] is None else round(float(ema200[index]), 8),
            }
        )
    return regimes


def load_trades(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["r_net"] = safe_float(row.get("r_net"))
    return rows


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    r_values = [float(row.get("r_net") or 0.0) for row in trades]
    wins = sum(1 for value in r_values if value > 0)
    losses = len(r_values) - wins
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    losing_streak = 0
    max_losing_streak = 0
    for value in r_values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        if value <= 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    return {
        "trades": len(r_values),
        "wins": wins,
        "losses": losses,
        "winrate_pct": round(wins / len(r_values) * 100.0, 3) if r_values else None,
        "expectancy_r": round(sum(r_values) / len(r_values), 6) if r_values else None,
        "net_r_total": round(sum(r_values), 6),
        "max_drawdown_r": round(max_dd, 6),
        "max_losing_streak": max_losing_streak,
    }


def fold_summary(trades: list[dict[str, Any]], folds_count: int) -> dict[str, Any]:
    if not trades:
        return {"folds": [], "stable_folds": 0}
    folds: list[dict[str, Any]] = []
    size = max(1, math.ceil(len(trades) / folds_count))
    for index in range(folds_count):
        chunk = trades[index * size : (index + 1) * size]
        item = summarize(chunk)
        item["fold_index"] = index + 1
        item["stable"] = bool((item.get("trades") or 0) > 0 and (item.get("expectancy_r") or -999.0) > 0)
        folds.append(item)
    return {"folds": folds, "stable_folds": sum(1 for item in folds if item["stable"])}


def attach_regimes(trades: list[dict[str, Any]], regimes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_time = {item["ts"]: item for item in regimes}
    enriched: list[dict[str, Any]] = []
    for row in trades:
        regime = by_time.get(str(row.get("entry_ts") or ""))
        item = dict(row)
        if regime is None:
            item["canonical_regime"] = "MISSING"
        else:
            item["canonical_regime"] = regime["regime"]
            item["canonical_trend_strength_score"] = regime["trend_strength_score"]
            item["canonical_adx14"] = regime["adx14"]
            item["canonical_range_atr"] = regime["range_atr"]
        enriched.append(item)
    return enriched


def group_results(enriched: list[dict[str, Any]], folds_count: int) -> list[dict[str, Any]]:
    filters = {
        "all_trades": lambda row: True,
        "allow_long_trend_up_only": lambda row: row.get("canonical_regime") == "TREND_UP",
        "allow_no_shock": lambda row: row.get("canonical_regime") != "SHOCK",
        "allow_trend_up_no_shock": lambda row: row.get("canonical_regime") == "TREND_UP",
        "range_only": lambda row: row.get("canonical_regime") == "RANGE",
        "shock_only": lambda row: row.get("canonical_regime") == "SHOCK",
        "missing_only": lambda row: row.get("canonical_regime") == "MISSING",
    }
    out: list[dict[str, Any]] = []
    for name, predicate in filters.items():
        selected = [row for row in enriched if predicate(row)]
        folded = fold_summary(selected, folds_count)
        out.append(
            {
                "filter": name,
                "summary": summarize(selected),
                "stable_folds": folded["stable_folds"],
                "folds": folded["folds"],
            }
        )
    return out


def choose_decision(results: list[dict[str, Any]], min_trades: int, min_expectancy_improvement: float) -> dict[str, Any]:
    base = next(item for item in results if item["filter"] == "all_trades")
    base_exp = base["summary"].get("expectancy_r") or 0.0
    candidates = []
    for item in results:
        if item["filter"] == "all_trades":
            continue
        summary = item["summary"]
        exp = summary.get("expectancy_r")
        if (summary.get("trades") or 0) >= min_trades and exp is not None:
            candidates.append((float(exp) - float(base_exp), item))
    candidates.sort(key=lambda pair: (pair[0], pair[1]["stable_folds"], pair[1]["summary"].get("trades") or 0), reverse=True)
    if candidates and candidates[0][0] >= min_expectancy_improvement:
        return {
            "verdict": "guard_candidate_needs_forward_replay",
            "best_filter": candidates[0][1]["filter"],
            "expectancy_improvement_r": round(candidates[0][0], 6),
            "reason": "A canonical regime filter improved expectancy on trade-level history, but it still needs independent forward replay.",
        }
    return {
        "verdict": "research_only_no_guard_promotion",
        "best_filter": candidates[0][1]["filter"] if candidates else None,
        "expectancy_improvement_r": round(candidates[0][0], 6) if candidates else None,
        "reason": "No regime filter met the minimum trade count and expectancy-improvement gate.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Canonical Regime Gate Overlay Test",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only guard test.",
        "- Applies the Canonical Bot-Safe regime concept to existing trade-level results.",
        "- Does not create entries, send orders or grant paper/live permission.",
        "",
        "## Result",
        "",
        f"- Trades file: `{report['trades_path']}`.",
        f"- OHLCV file: `{report['ohlcv_path']}`.",
        f"- Decision: `{report['decision']['verdict']}`.",
        f"- Best filter: `{report['decision'].get('best_filter')}`.",
        f"- Reason: {report['decision']['reason']}",
        "",
        "## Regime Distribution",
        "",
    ]
    for key, value in report["regime_counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Filter Comparison",
            "",
            "| Filter | Trades | Winrate | Exp R | Net R | Max DD R | Stable Folds |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report["results"]:
        summary = item["summary"]
        lines.append(
            f"| `{item['filter']}` | `{summary['trades']}` | `{summary['winrate_pct']}` | "
            f"`{summary['expectancy_r']}` | `{summary['net_r_total']}` | `{summary['max_drawdown_r']}` | `{item['stable_folds']}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- If a filter improves expectancy but leaves too few trades, it is a clue, not a deployable guard.",
            "- If `SHOCK` has poor results, the next coding target is a shock abstention gate in forward-paper mode.",
            "- Any promoted guard must be rechecked on fresh forward observations before execution design.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only Canonical Bot-Safe regime gate overlay test")
    parser.add_argument("--trades-csv", default="docs/STRATEGY_MIX_PAPER_REPLAY_2026-06-08_trades.csv")
    parser.add_argument("--ohlcv-csv", default="data/cache/binance_spot_perp_extended/futures/BTCUSDT/4h_klines.csv")
    parser.add_argument("--shock-range-atr", type=float, default=2.5)
    parser.add_argument("--min-adx", type=float, default=18.0)
    parser.add_argument("--trend-threshold", type=float, default=0.5)
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--min-trades", type=int, default=30)
    parser.add_argument("--min-expectancy-improvement", type=float, default=0.05)
    parser.add_argument("--out-prefix", default="docs/CANONICAL_REGIME_GATE_OVERLAY_2026-06-09")
    args = parser.parse_args()

    trades_path = Path(args.trades_csv)
    ohlcv_path = Path(args.ohlcv_csv)
    trades = load_trades(trades_path)
    bars = load_ohlcv(ohlcv_path)
    regimes = classify_regimes(
        bars,
        shock_range_atr=args.shock_range_atr,
        min_adx=args.min_adx,
        trend_threshold=args.trend_threshold,
    )
    enriched = attach_regimes(trades, regimes)
    results = group_results(enriched, args.folds)
    decision = choose_decision(results, args.min_trades, args.min_expectancy_improvement)
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "canonical_regime_gate_overlay_research_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "trades_path": str(trades_path),
        "ohlcv_path": str(ohlcv_path),
        "settings": vars(args),
        "regime_counts": dict(Counter(row.get("canonical_regime") for row in enriched)),
        "decision": decision,
        "results": results,
        "can_trade": False,
    }

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "regime_counts": report["regime_counts"],
                "json": str(json_path),
                "md": str(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
