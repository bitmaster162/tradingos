from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import (  # noqa: E402
    INTERVAL_MS,
    candle_value,
    completed_rows,
    fetch_binance_klines,
    find_exit,
    htf_bias_from_rows,
    load_cached_klines,
    row_open_ms,
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


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    rank = (len(ordered) - 1) * p
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return round(ordered[int(rank)], 6)
    weight = rank - lo
    return round(ordered[lo] * (1 - weight) + ordered[hi] * weight, 6)


def percentile_rank(values: list[float], value: float) -> float | None:
    clean = [item for item in values if not math.isnan(item)]
    if not clean or math.isnan(value):
        return None
    below_or_equal = sum(1 for item in clean if item <= value)
    return below_or_equal / len(clean)


def ema_at(values: list[float], period: int) -> float:
    if len(values) < period:
        return math.nan
    alpha = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = alpha * value + (1 - alpha) * ema
    return ema


def pct_return(values: list[float], lookback: int) -> float | None:
    if len(values) <= lookback:
        return None
    prev = values[-lookback - 1]
    cur = values[-1]
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


def atr14_at(rows: list[dict[str, str]], idx: int) -> float:
    if idx < 14:
        return math.nan
    prev_close = candle_value(rows[idx - 14], "close")
    trs: list[float] = []
    for row in rows[idx - 13 : idx + 1]:
        high = candle_value(row, "high")
        low = candle_value(row, "low")
        close = candle_value(row, "close")
        if any(math.isnan(v) for v in (high, low, close, prev_close)):
            return math.nan
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    return sum(trs) / len(trs) if trs else math.nan


def spot_window_until(spot_rows: list[dict[str, str]], close_ms: int, interval_ms: int) -> list[dict[str, str]]:
    return [row for row in spot_rows if row_open_ms(row) + interval_ms - 1 <= close_ms]


def spot_volume_ratio_at(spot_rows: list[dict[str, str]]) -> tuple[bool, float | None]:
    if len(spot_rows) < 20:
        return False, None
    volumes = [candle_value(row, "volume") for row in spot_rows[-20:]]
    if any(math.isnan(v) for v in volumes):
        return False, None
    avg = sum(volumes) / 20
    if avg <= 0:
        return False, None
    return True, volumes[-1] / avg


def spot_perp_features(perp_rows: list[dict[str, str]], spot_rows: list[dict[str, str]]) -> dict[str, Any]:
    perp_closes = [candle_value(row, "close") for row in perp_rows if not math.isnan(candle_value(row, "close"))]
    spot_closes = [candle_value(row, "close") for row in spot_rows if not math.isnan(candle_value(row, "close"))]
    if len(perp_closes) < 14 or len(spot_closes) < 14:
        return {
            "spot_ret_3": None,
            "perp_ret_3": None,
            "spot_perp_divergence_3": None,
            "spot_ret_12": None,
            "perp_ret_12": None,
            "spot_perp_divergence_12": None,
            "spot_perp_divergence_12_sign": "unknown",
        }
    spot_ret_3 = pct_return(spot_closes, 3)
    perp_ret_3 = pct_return(perp_closes, 3)
    spot_ret_12 = pct_return(spot_closes, 12)
    perp_ret_12 = pct_return(perp_closes, 12)
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


def summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(trade["net_r"]) for trade in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    losing_streak = 0
    max_losing_streak = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if value <= 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(trades) * 100, 3) if trades else None,
        "expectancy_r": round(sum(values) / len(values), 6) if values else None,
        "net_r_total": round(sum(values), 6) if values else 0.0,
        "avg_win_r": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 6) if losses else None,
        "max_drawdown_r": round(max_drawdown, 6),
        "max_losing_streak": max_losing_streak,
    }


def simulate_v10_1h_weak_bid_short(
    *,
    rows: list[dict[str, str]],
    spot_rows: list[dict[str, str]],
    htf_rows: list[dict[str, str]],
    htf_interval: str,
    warmup_bars: int,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    fee_bps: float,
    slippage_bps: float,
    interval_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    trades: list[dict[str, Any]] = []
    skipped = {
        "warmup": 0,
        "spot_not_ready": 0,
        "bad_atr_or_width": 0,
        "rule_not_matched": 0,
        "no_next_bar": 0,
    }
    i = max(warmup_bars, 90)
    while i < len(rows) - 1:
        if i < warmup_bars:
            skipped["warmup"] += 1
            i += 1
            continue
        close_ms = row_open_ms(rows[i]) + interval_ms - 1
        spot_window = spot_window_until(spot_rows, close_ms, interval_ms)
        spot_ready, spot_volume_ratio = spot_volume_ratio_at(spot_window)
        if not spot_ready or spot_volume_ratio is None:
            skipped["spot_not_ready"] += 1
            i += 1
            continue

        close = candle_value(rows[i], "close")
        closes = [candle_value(row, "close") for row in rows[: i + 1] if not math.isnan(candle_value(row, "close"))]
        prev = rows[i - 55 : i]
        if len(prev) < 55 or math.isnan(close):
            skipped["bad_atr_or_width"] += 1
            i += 1
            continue
        upper = max(candle_value(row, "high") for row in prev)
        lower = min(candle_value(row, "low") for row in prev)
        width = upper - lower
        atr = atr14_at(rows, i)
        if width <= 0 or math.isnan(atr) or atr <= 0:
            skipped["bad_atr_or_width"] += 1
            i += 1
            continue

        width_atr = width / atr
        near_low = close <= lower + max(width * 0.18, atr * 0.9)
        if not (near_low and spot_volume_ratio <= 0.8 and 2.0 <= width_atr <= 8.0):
            skipped["rule_not_matched"] += 1
            i += 1
            continue

        ema50 = ema_at(closes, 50)
        ema200 = ema_at(closes, 200)
        ema200_distance_pct = None if math.isnan(ema200) or ema200 == 0 else (close - ema200) / ema200 * 100
        ema_state = "unknown"
        if not math.isnan(ema50) and not math.isnan(ema200):
            if close < ema50 < ema200:
                ema_state = "below_ema_stack"
            elif close > ema50 > ema200:
                ema_state = "above_ema_stack"
            elif close < ema200:
                ema_state = "below_ema200"
            elif close > ema200:
                ema_state = "above_ema200"
            else:
                ema_state = "mixed"
        trend_strength_20_atr = None
        if len(closes) >= 21 and atr > 0:
            trend_strength_20_atr = (close - closes[-21]) / atr
        atr_pct = atr / close * 100 if close else None
        atr_pct_history: list[float] = []
        width_atr_history: list[float] = []
        lookback_start = max(56, i - 500)
        for hist_i in range(lookback_start, i + 1):
            hist_atr = atr14_at(rows, hist_i)
            hist_close = candle_value(rows[hist_i], "close")
            hist_prev = rows[hist_i - 55 : hist_i]
            if len(hist_prev) < 55 or math.isnan(hist_atr) or hist_atr <= 0 or math.isnan(hist_close) or hist_close == 0:
                continue
            atr_pct_history.append(hist_atr / hist_close * 100)
            hist_width = max(candle_value(row, "high") for row in hist_prev) - min(candle_value(row, "low") for row in hist_prev)
            if hist_width > 0:
                width_atr_history.append(hist_width / hist_atr)
        atr_pct_rank_500 = percentile_rank(atr_pct_history, atr_pct if atr_pct is not None else math.nan)
        width_atr_rank_500 = percentile_rank(width_atr_history, width_atr)
        htf_window = completed_rows(htf_rows, close_ms=close_ms, interval=htf_interval)
        htf = htf_bias_from_rows(htf_window)
        sp_features = spot_perp_features(rows[: i + 1], spot_window)

        next_index = i + 1
        if next_index >= len(rows):
            skipped["no_next_bar"] += 1
            break
        entry_open = candle_value(rows[next_index], "open")
        if math.isnan(entry_open):
            skipped["bad_atr_or_width"] += 1
            i += 1
            continue

        side = "SHORT"
        slip = slippage_bps / 10000
        entry = entry_open * (1 - slip)
        risk = stop_atr * atr
        stop = entry + risk
        take_profit = entry - take_atr * atr
        exit_index, raw_exit, exit_reason = find_exit(
            rows,
            start_index=next_index,
            side=side,
            entry=entry,
            stop=stop,
            take_profit=take_profit,
            max_hold_bars=max_hold_bars,
        )
        exit_price = raw_exit * (1 + slip)
        gross_r = (entry - exit_price) / risk
        fee_cost = ((entry + exit_price) * (fee_bps / 10000)) / risk
        net_r = gross_r - fee_cost
        trades.append(
            {
                "index": len(trades) + 1,
                "signal_row": i,
                "entry_row": next_index,
                "exit_row": exit_index,
                "signal_time": rows[i].get("time", str(i)),
                "entry_time": rows[next_index].get("time", str(next_index)),
                "exit_time": rows[exit_index].get("time", str(exit_index)),
                "side": side,
                "entry": round(entry, 8),
                "stop": round(stop, 8),
                "take_profit": round(take_profit, 8),
                "exit": round(exit_price, 8),
                "exit_reason": exit_reason,
                "bars_held": max(1, exit_index - next_index + 1),
                "gross_r": round(gross_r, 6),
                "net_r": round(net_r, 6),
                "atr14": round(atr, 6),
                "atr_pct": None if atr_pct is None else round(atr_pct, 6),
                "atr_pct_rank_500": None if atr_pct_rank_500 is None else round(atr_pct_rank_500, 6),
                "ema50": None if math.isnan(ema50) else round(ema50, 8),
                "ema200": None if math.isnan(ema200) else round(ema200, 8),
                "ema200_distance_pct": None if ema200_distance_pct is None else round(ema200_distance_pct, 6),
                "ema_state": ema_state,
                "trend_strength_20_atr": None if trend_strength_20_atr is None else round(trend_strength_20_atr, 6),
                "htf_bias": htf.get("bias"),
                "htf_regime": htf.get("regime"),
                "htf_reason": htf.get("reason"),
                "donchian_upper_55": round(upper, 8),
                "donchian_lower_55": round(lower, 8),
                "donchian_width_atr": round(width_atr, 6),
                "donchian_width_atr_rank_500": None if width_atr_rank_500 is None else round(width_atr_rank_500, 6),
                "spot_volume_ratio": round(spot_volume_ratio, 6),
                **sp_features,
                "setup": "V10_1H_WEAK_BID_CONTINUATION_SHORT",
            }
        )
        i = exit_index + 1
    return trades, skipped


def fold_report(trades: list[dict[str, Any]], *, rows_count: int, warmup_bars: int, folds: int) -> list[dict[str, Any]]:
    usable = max(1, rows_count - warmup_bars)
    fold_count = max(1, folds)
    fold_span = max(1, usable // fold_count)
    reports: list[dict[str, Any]] = []
    for fold in range(fold_count):
        start = warmup_bars + fold * fold_span
        end = rows_count if fold == fold_count - 1 else min(rows_count, start + fold_span)
        fold_trades = [trade for trade in trades if start <= int(trade["entry_row"]) < end]
        summary = summarize_trades(fold_trades)
        reports.append(
            {
                "fold": fold + 1,
                "row_start": start,
                "row_end": end,
                "trades": summary["trades"],
                "winrate_pct": summary["winrate_pct"],
                "expectancy_r": summary["expectancy_r"],
                "net_r_total": summary["net_r_total"],
                "max_drawdown_r": summary["max_drawdown_r"],
            }
        )
    return reports


def bootstrap_report(trades: list[dict[str, Any]], *, iterations: int, seed: int) -> dict[str, Any]:
    values = [float(trade["net_r"]) for trade in trades]
    if not values:
        return {"iterations": 0, "reason": "no_trades"}
    rng = random.Random(seed)
    sample_size = len(values)
    expectancies: list[float] = []
    winrates: list[float] = []
    net_totals: list[float] = []
    for _ in range(max(1, iterations)):
        sample = [values[rng.randrange(sample_size)] for _ in range(sample_size)]
        expectancies.append(sum(sample) / sample_size)
        winrates.append(sum(1 for value in sample if value > 0) / sample_size * 100)
        net_totals.append(sum(sample))
    positive = sum(1 for value in expectancies if value > 0)
    return {
        "iterations": max(1, iterations),
        "sample_size": sample_size,
        "seed": seed,
        "expectancy_r": {
            "p05": percentile(expectancies, 0.05),
            "p50": percentile(expectancies, 0.50),
            "p95": percentile(expectancies, 0.95),
            "prob_gt_0": round(positive / len(expectancies), 4),
        },
        "winrate_pct": {
            "p05": percentile(winrates, 0.05),
            "p50": percentile(winrates, 0.50),
            "p95": percentile(winrates, 0.95),
        },
        "net_r_total": {
            "p05": percentile(net_totals, 0.05),
            "p50": percentile(net_totals, 0.50),
            "p95": percentile(net_totals, 0.95),
        },
    }


def load_or_fetch(
    *,
    use_cache: bool,
    cache_dir: Path,
    market: str,
    symbol: str,
    interval: str,
    limit: int,
    pages: int,
) -> tuple[list[dict[str, str]], str]:
    if use_cache:
        rows, source = load_cached_klines(cache_dir, market, symbol, interval)
        if rows:
            return rows, source or f"cache:{market}:{symbol}:{interval}"
    rows = fetch_binance_klines(symbol, interval, limit, market, pages=pages)
    return rows, f"public_binance:{market}:{symbol.upper()}:{interval}:pages={pages}:limit={limit}"


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    gate = report["research_gate"]
    bootstrap = report["bootstrap"]
    lines = [
        "# MAX Core Lite v1.1 Candidate Validation",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Candidate: `{report['candidate']['id']}`",
        f"- Data period: `{report['data']['first_time']}` -> `{report['data']['last_time']}`",
        f"- Rows: `{report['data']['rows']}` futures / `{report['data']['spot_rows']}` spot",
        "",
        "## Summary",
        "",
        f"- Trades: `{summary['trades']}`",
        f"- Winrate: `{summary['winrate_pct']}`",
        f"- Expectancy R/trade: `{summary['expectancy_r']}`",
        f"- Net R total: `{summary['net_r_total']}`",
        f"- Max drawdown R: `{summary['max_drawdown_r']}`",
        f"- Gate verdict: **{gate['verdict']}**",
        "",
        "## Gate",
        "",
        f"- Min trades: `{gate['min_trades']}`",
        f"- Min winrate: `{gate['min_winrate_pct']}`",
        f"- Min expectancy: `{gate['min_expectancy_r']}`",
        f"- Bootstrap probability expectancy > 0: `{gate['min_bootstrap_prob_gt_0']}`",
        f"- Pass: `{gate['pass']}`",
        "",
        "## Bootstrap",
        "",
        f"- Iterations: `{bootstrap.get('iterations')}`",
        f"- Expectancy p05/p50/p95: `{bootstrap.get('expectancy_r', {}).get('p05')}` / `{bootstrap.get('expectancy_r', {}).get('p50')}` / `{bootstrap.get('expectancy_r', {}).get('p95')}`",
        f"- Probability expectancy > 0: `{bootstrap.get('expectancy_r', {}).get('prob_gt_0')}`",
        "",
        "## Folds",
        "",
        "| Fold | Rows | Trades | Winrate | Expectancy | Net R |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for fold in report["folds"]:
        lines.append(
            f"| {fold['fold']} | {fold['row_start']}..{fold['row_end']} | {fold['trades']} | "
            f"{fold['winrate_pct']} | {fold['expectancy_r']} | {fold['net_r_total']} |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            report["decision"],
            "",
            "## Boundary",
            "",
            report["runtime_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite v1.1 validator for v10_1h_weak_bid_short")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--htf-interval", default="4h")
    parser.add_argument("--market", default="futures", choices=["futures"])
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--warmup-bars", type=int, default=220)
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
    parser.add_argument("--out-prefix", default="_dl/v11/MAX_CORE_LITE_V11_1H_WEAK_BID")
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
    trades, skipped = simulate_v10_1h_weak_bid_short(
        rows=rows,
        spot_rows=spot_rows,
        htf_rows=htf_rows,
        htf_interval=args.htf_interval,
        warmup_bars=args.warmup_bars,
        stop_atr=args.stop_atr,
        take_atr=args.take_atr,
        max_hold_bars=args.max_hold_bars,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        interval_ms=interval_ms,
    )
    summary = summarize_trades(trades)
    folds = fold_report(trades, rows_count=len(rows), warmup_bars=args.warmup_bars, folds=args.folds)
    stable_folds = [
        fold
        for fold in folds
        if fold["trades"] > 0 and fold["expectancy_r"] is not None and float(fold["expectancy_r"]) >= args.min_expectancy_r
    ]
    bootstrap = bootstrap_report(trades, iterations=args.bootstrap_iterations, seed=args.bootstrap_seed)
    bootstrap_prob = parse_float((bootstrap.get("expectancy_r") or {}).get("prob_gt_0"), 0.0) if isinstance(bootstrap, dict) else 0.0
    pass_gate = bool(
        summary["trades"] >= args.min_trades
        and summary["expectancy_r"] is not None
        and summary["expectancy_r"] >= args.min_expectancy_r
        and summary["winrate_pct"] is not None
        and summary["winrate_pct"] >= args.min_winrate_pct
        and bootstrap_prob >= args.min_bootstrap_prob_gt_0
        and len(stable_folds) == len(folds)
        and len(folds) > 0
    )
    decision = (
        "Candidate can move to paper-trading design review, not live trading."
        if pass_gate
        else "Candidate remains research-only. Do not paper/live trade until the gate passes."
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_V11_CANDIDATE_VALIDATOR",
        "engine_version": "1.1.0",
        "candidate": {
            "id": "v10_1h_weak_bid_short",
            "setup": "near_low + spot_volume_ratio<=0.8 + donchian_width_atr_between_2_8",
            "side": "SHORT",
            "source": "v0.9 research grid lead, v1.0 hardening survivor but sample-limited",
        },
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
            "warmup_bars": args.warmup_bars,
            "stop_atr": args.stop_atr,
            "take_atr": args.take_atr,
            "max_hold_bars": args.max_hold_bars,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "folds": args.folds,
            "bootstrap_iterations": args.bootstrap_iterations,
            "use_cache": args.use_cache,
            "htf_interval": args.htf_interval,
        },
        "summary": summary,
        "folds": folds,
        "stable_folds": len(stable_folds),
        "bootstrap": bootstrap,
        "skipped": skipped,
        "research_gate": {
            "pass": pass_gate,
            "min_trades": args.min_trades,
            "min_expectancy_r": args.min_expectancy_r,
            "min_winrate_pct": args.min_winrate_pct,
            "min_bootstrap_prob_gt_0": args.min_bootstrap_prob_gt_0,
            "requires_all_folds_non_negative": True,
            "stable_folds": len(stable_folds),
            "fold_count": len(folds),
            "verdict": "candidate_for_paper_design_review" if pass_gate else "do_not_trade",
        },
        "decision": decision,
        "trades": trades,
        "runtime_boundary": (
            "Research-only v1.1 validation. It uses public market data and deterministic simulation; "
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
                "summary": summary,
                "research_gate": report["research_gate"],
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
