from __future__ import annotations

import argparse
import bisect
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
    align_derivatives,
    candle_value,
    fetch_funding_history,
    fetch_open_interest_history,
    find_exit,
    htf_bias_from_rows,
    load_cached_oi,
    row_open_ms,
)
from tools.max_v11_candidate_validator import (  # noqa: E402
    atr14_at,
    load_or_fetch,
    spot_volume_ratio_at,
)
from tools.max_v13_structural_candidate import (  # noqa: E402
    gate_candidate,
    parse_float,
    spot_perp_features_fast,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: float | None) -> bool:
    return value is not None and not math.isnan(value)


def pct_change_from_rows(rows: list[dict[str, str]], idx: int, key: str, lookback: int) -> float | None:
    if idx - lookback < 0 or idx >= len(rows):
        return None
    current = parse_float(rows[idx].get(key))
    previous = parse_float(rows[idx - lookback].get(key))
    if math.isnan(current) or math.isnan(previous) or previous == 0:
        return None
    return (current - previous) / previous * 100


def zscore_from_rows(rows: list[dict[str, str]], idx: int, key: str, window: int) -> float | None:
    if idx - window + 1 < 0 or idx >= len(rows):
        return None
    values = [parse_float(row.get(key)) for row in rows[idx - window + 1 : idx + 1]]
    values = [value for value in values if not math.isnan(value)]
    if len(values) < max(20, window // 2):
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(variance)
    current = values[-1]
    if std == 0:
        return 0.0
    return (current - mean) / std


def load_or_fetch_derivatives(
    *,
    use_cache: bool,
    cache_dir: Path,
    symbol: str,
    interval: str,
    rows: list[dict[str, str]],
    limit: int,
    pages: int,
) -> tuple[list[dict[str, str]], str]:
    if use_cache:
        cached, source = load_cached_oi(cache_dir, symbol, interval)
        if cached:
            by_time = {row.get("time", ""): row for row in cached}
            aligned = [
                by_time.get(
                    row.get("time", ""),
                    {
                        "time": row.get("time", ""),
                        "price": row.get("close", ""),
                        "open_interest": "",
                        "volume": row.get("volume", ""),
                        "funding": "",
                    },
                )
                for row in rows
            ]
            return aligned, source or "cache:aligned_derivatives"

    oi_records = fetch_open_interest_history(symbol, interval, limit=limit, pages=pages)
    funding_records = fetch_funding_history(symbol, pages=pages)
    return (
        align_derivatives(rows, interval=interval, oi_records=oi_records, funding_records=funding_records),
        f"binance_public_derivatives:{symbol}:{interval}:oi_rows={len(oi_records)}:funding_rows={len(funding_records)}",
    )


def precompute_htf_bias(
    *,
    rows: list[dict[str, str]],
    htf_rows: list[dict[str, str]],
    interval_ms: int,
    htf_interval: str,
) -> list[dict[str, Any]]:
    if not htf_rows:
        return [{"bias": "NEUTRAL", "regime": "missing_htf", "reason": "no_htf_rows"} for _ in rows]
    htf_ms = INTERVAL_MS.get(htf_interval, 14_400_000)
    htf_close_times = [row_open_ms(row) + htf_ms - 1 for row in htf_rows]
    cache: dict[int, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        close_ms = row_open_ms(row) + interval_ms - 1
        ptr = bisect.bisect_right(htf_close_times, close_ms)
        if ptr not in cache:
            cache[ptr] = htf_bias_from_rows(htf_rows[:ptr]) if ptr > 0 else {
                "bias": "NEUTRAL",
                "regime": "insufficient_htf",
                "reason": "no_completed_htf_bar",
            }
        out.append(cache[ptr])
    return out


def build_trade_features(
    *,
    rows: list[dict[str, str]],
    spot_rows: list[dict[str, str]],
    derivatives_rows: list[dict[str, str]],
    htf_biases: list[dict[str, Any]],
    i: int,
) -> dict[str, Any] | None:
    if i >= len(spot_rows) or i >= len(derivatives_rows):
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

    prev_sweep = rows[i - 20 : i]
    if len(prev_sweep) < 20:
        return None
    prev_high_20 = max(candle_value(row, "high") for row in prev_sweep)
    prev_low_20 = min(candle_value(row, "low") for row in prev_sweep)
    high = candle_value(rows[i], "high")
    low = candle_value(rows[i], "low")
    bullish_sweep = low < prev_low_20 and close > prev_low_20
    bearish_sweep = high > prev_high_20 and close < prev_high_20
    if bullish_sweep and bearish_sweep:
        sweep_side = "both"
    elif bullish_sweep:
        sweep_side = "bullish"
    elif bearish_sweep:
        sweep_side = "bearish"
    else:
        sweep_side = "none"

    oi = parse_float(derivatives_rows[i].get("open_interest"))
    funding = parse_float(derivatives_rows[i].get("funding"))
    oi_delta_3 = pct_change_from_rows(derivatives_rows, i, "open_interest", 3)
    oi_delta_12 = pct_change_from_rows(derivatives_rows, i, "open_interest", 12)
    oi_z = zscore_from_rows(derivatives_rows, i, "open_interest", 100)
    htf = htf_biases[i] if i < len(htf_biases) else {"bias": "NEUTRAL", "regime": "missing_htf"}

    atr_pct = atr / close * 100 if close else None
    trend_strength_20_atr = None
    if i >= 20 and atr > 0:
        prev_close_20 = candle_value(rows[i - 20], "close")
        if not math.isnan(prev_close_20):
            trend_strength_20_atr = (close - prev_close_20) / atr

    return {
        "signal_time": rows[i].get("time", str(i)),
        "signal_row": i,
        "close": close,
        "atr14": atr,
        "atr_pct": atr_pct,
        "trend_strength_20_atr": trend_strength_20_atr,
        "donchian_upper_55": upper,
        "donchian_lower_55": lower,
        "donchian_width_atr": width_atr,
        "near_low": near_low,
        "near_high": near_high,
        "prev_high_20": prev_high_20,
        "prev_low_20": prev_low_20,
        "bullish_liquidity_sweep": bullish_sweep,
        "bearish_liquidity_sweep": bearish_sweep,
        "sweep_side": sweep_side,
        "spot_volume_ratio": spot_volume_ratio,
        "open_interest": None if math.isnan(oi) else oi,
        "oi_delta_3_pct": None if oi_delta_3 is None else round(oi_delta_3, 6),
        "oi_delta_12_pct": None if oi_delta_12 is None else round(oi_delta_12, 6),
        "oi_zscore_100": None if oi_z is None else round(oi_z, 6),
        "oi_zscore_100_abs": None if oi_z is None else round(abs(oi_z), 6),
        "funding": None if math.isnan(funding) else funding,
        "funding_abs": None if math.isnan(funding) else abs(funding),
        "derivatives_ready": not math.isnan(oi) and not math.isnan(funding),
        "htf_bias": htf.get("bias", "NEUTRAL"),
        "htf_regime": htf.get("regime", "unknown"),
        "htf_reason": htf.get("reason", "unknown"),
        **spot_perp_features_fast(rows=rows, spot_rows=spot_rows, idx=i),
    }


def candidate_specs(args: argparse.Namespace) -> list[dict[str, Any]]:
    strict_width = (args.strict_width_lower, args.strict_width_upper)
    broad_width = (args.broad_width_lower, args.broad_width_upper)

    def width_between(features: dict[str, Any], band: tuple[float, float]) -> bool:
        width = parse_float(features.get("donchian_width_atr"))
        return band[0] <= width <= band[1]

    def derivatives_ready(features: dict[str, Any]) -> bool:
        return bool(features.get("derivatives_ready"))

    def div_min(features: dict[str, Any]) -> bool:
        return parse_float(features.get("spot_perp_divergence_12")) >= args.divergence_min

    def div_max(features: dict[str, Any]) -> bool:
        return parse_float(features.get("spot_perp_divergence_12")) <= -args.divergence_min

    def spot_quiet(features: dict[str, Any]) -> bool:
        return parse_float(features.get("spot_volume_ratio")) <= args.spot_volume_max

    def funding_long_ok(features: dict[str, Any]) -> bool:
        return parse_float(features.get("funding"), 999.0) <= args.max_long_funding

    def funding_short_ok(features: dict[str, Any]) -> bool:
        return parse_float(features.get("funding"), -999.0) >= args.min_short_funding

    def htf_not_against_long(features: dict[str, Any]) -> bool:
        return str(features.get("htf_bias")) != "SHORT"

    def htf_not_against_short(features: dict[str, Any]) -> bool:
        return str(features.get("htf_bias")) != "LONG"

    def oi_reset_or_extreme(features: dict[str, Any]) -> bool:
        delta = parse_float(features.get("oi_delta_12_pct"))
        z_abs = parse_float(features.get("oi_zscore_100_abs"))
        return (finite(delta) and delta <= args.oi_reset_max_delta_pct) or (
            finite(z_abs) and z_abs >= args.oi_extreme_z
        )

    def oi_short_squeeze_risk(features: dict[str, Any]) -> bool:
        delta = parse_float(features.get("oi_delta_12_pct"))
        z_abs = parse_float(features.get("oi_zscore_100_abs"))
        return (finite(delta) and delta >= args.oi_build_min_delta_pct) or (
            finite(z_abs) and z_abs >= args.oi_extreme_z
        )

    return [
        {
            "id": "v15_current_lead_short_state_filtered",
            "side": "SHORT",
            "requires": [
                "near_low",
                "no bullish liquidity sweep",
                f"spot_volume_ratio <= {args.spot_volume_max}",
                f"{strict_width[0]} <= donchian_width_atr <= {strict_width[1]}",
                f"spot_perp_divergence_12 >= {args.divergence_min}",
                f"funding >= {args.min_short_funding}",
                "OI reset/extreme",
                "HTF bias != LONG",
            ],
            "predicate": lambda f: bool(
                derivatives_ready(f)
                and f.get("near_low")
                and not f.get("bullish_liquidity_sweep")
                and spot_quiet(f)
                and width_between(f, strict_width)
                and div_min(f)
                and funding_short_ok(f)
                and oi_reset_or_extreme(f)
                and htf_not_against_short(f)
            ),
        },
        {
            "id": "v15_long_bullish_sweep_oi_build",
            "side": "LONG",
            "requires": [
                "near_low",
                "bullish liquidity sweep",
                f"{broad_width[0]} <= donchian_width_atr <= {broad_width[1]}",
                f"spot_perp_divergence_12 >= {args.divergence_min}",
                f"funding <= {args.max_long_funding}",
                "OI build/extreme",
                "HTF bias != SHORT",
            ],
            "predicate": lambda f: bool(
                derivatives_ready(f)
                and f.get("near_low")
                and f.get("bullish_liquidity_sweep")
                and width_between(f, broad_width)
                and div_min(f)
                and funding_long_ok(f)
                and oi_short_squeeze_risk(f)
                and htf_not_against_long(f)
            ),
        },
        {
            "id": "v15_short_bearish_sweep_oi_reset",
            "side": "SHORT",
            "requires": [
                "near_high",
                "bearish liquidity sweep",
                f"{broad_width[0]} <= donchian_width_atr <= {broad_width[1]}",
                f"spot_perp_divergence_12 <= -{args.divergence_min}",
                f"funding >= {args.min_short_funding}",
                "OI reset/extreme",
                "HTF bias != LONG",
            ],
            "predicate": lambda f: bool(
                derivatives_ready(f)
                and f.get("near_high")
                and f.get("bearish_liquidity_sweep")
                and width_between(f, broad_width)
                and div_max(f)
                and funding_short_ok(f)
                and oi_reset_or_extreme(f)
                and htf_not_against_short(f)
            ),
        },
        {
            "id": "v15_long_state_filtered_near_low",
            "side": "LONG",
            "requires": [
                "near_low",
                "no bearish liquidity sweep",
                f"{broad_width[0]} <= donchian_width_atr <= {broad_width[1]}",
                f"spot_perp_divergence_12 >= {args.divergence_min}",
                f"funding <= {args.max_long_funding}",
                "HTF bias != SHORT",
            ],
            "predicate": lambda f: bool(
                derivatives_ready(f)
                and f.get("near_low")
                and not f.get("bearish_liquidity_sweep")
                and width_between(f, broad_width)
                and div_min(f)
                and funding_long_ok(f)
                and htf_not_against_long(f)
            ),
        },
        {
            "id": "v15_short_state_filtered_near_high",
            "side": "SHORT",
            "requires": [
                "near_high",
                "no bullish liquidity sweep",
                f"{broad_width[0]} <= donchian_width_atr <= {broad_width[1]}",
                f"spot_perp_divergence_12 <= -{args.divergence_min}",
                f"funding >= {args.min_short_funding}",
                "HTF bias != LONG",
            ],
            "predicate": lambda f: bool(
                derivatives_ready(f)
                and f.get("near_high")
                and not f.get("bullish_liquidity_sweep")
                and width_between(f, broad_width)
                and div_max(f)
                and funding_short_ok(f)
                and htf_not_against_short(f)
            ),
        },
        {
            "id": "v15_long_spot_accumulation_quiet",
            "side": "LONG",
            "requires": [
                "near_low",
                f"spot_volume_ratio <= {args.accumulation_spot_volume_max}",
                f"{broad_width[0]} <= donchian_width_atr <= {broad_width[1]}",
                f"spot_perp_divergence_12 >= {args.divergence_min}",
                f"funding <= {args.max_long_funding}",
                "HTF bias != SHORT",
            ],
            "predicate": lambda f: bool(
                derivatives_ready(f)
                and f.get("near_low")
                and parse_float(f.get("spot_volume_ratio")) <= args.accumulation_spot_volume_max
                and width_between(f, broad_width)
                and div_min(f)
                and funding_long_ok(f)
                and htf_not_against_long(f)
            ),
        },
    ]


def simulate_candidate(
    *,
    spec: dict[str, Any],
    rows: list[dict[str, str]],
    spot_rows: list[dict[str, str]],
    derivatives_rows: list[dict[str, str]],
    htf_biases: list[dict[str, Any]],
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
        features = build_trade_features(
            rows=rows,
            spot_rows=spot_rows,
            derivatives_rows=derivatives_rows,
            htf_biases=htf_biases,
            i=i,
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


def rank_key(item: dict[str, Any]) -> tuple[int, float, float, int]:
    gate = item["research_gate"]
    summary = item["summary"]
    return (
        1 if gate.get("pass") else 0,
        float(summary.get("expectancy_r") or -999.0),
        float(summary.get("winrate_pct") or 0.0),
        int(summary.get("trades") or 0),
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite v1.5 State Filters",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Engine: `{report['engine_version']}`",
        f"- Data: `{report['data']['first_time']}` -> `{report['data']['last_time']}`",
        f"- Rows: `{report['data']['rows']}` futures / `{report['data']['spot_rows']}` spot / `{report['data']['derivatives_rows']}` derivatives",
        "",
        "## Purpose",
        "",
        "Tests whether OI/funding, liquidity-sweep and HTF-regime filters improve the v1.3/v1.4 structural lead.",
        "",
        "## Results",
        "",
        "| Candidate | Side | Trades | Winrate | Expectancy | Net R | Bootstrap P>0 | Stable Folds | Verdict |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["candidates"]:
        summary = item["summary"]
        gate = item["research_gate"]
        prob = (item.get("bootstrap", {}).get("expectancy_r") or {}).get("prob_gt_0")
        lines.append(
            f"| `{item['id']}` | {item['side']} | {summary['trades']} | {summary['winrate_pct']} | "
            f"{summary['expectancy_r']} | {summary['net_r_total']} | {prob} | "
            f"{gate['stable_folds']}/{gate['fold_count']} | `{gate['verdict']}` |"
        )
    best = report.get("best_candidate")
    lines.extend(["", "## Best Candidate", ""])
    if best:
        lines.extend(
            [
                f"- ID: `{best['id']}`",
                f"- Side: `{best['side']}`",
                f"- Trades: `{best['summary']['trades']}`",
                f"- Winrate: `{best['summary']['winrate_pct']}`",
                f"- Expectancy: `{best['summary']['expectancy_r']}`",
                f"- Bootstrap P>0: `{(best.get('bootstrap', {}).get('expectancy_r') or {}).get('prob_gt_0')}`",
                f"- Stable folds: `{best['research_gate']['stable_folds']}/{best['research_gate']['fold_count']}`",
                f"- Verdict: `{best['research_gate']['verdict']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Decision",
            "",
            report["decision"],
            "",
            "## Filters Tested",
            "",
            "- OI reset/extreme: OI 12-bar delta below reset threshold or OI z-score extreme.",
            "- OI build/extreme: OI 12-bar delta above build threshold or OI z-score extreme.",
            "- Funding crowding: long avoids strongly positive funding, short avoids strongly negative funding.",
            "- Liquidity: 20-bar bullish/bearish sweep flags.",
            "- HTF: 4H EMA-stack bias must not be directly against the trade.",
            "",
            "## Boundary",
            "",
            report["runtime_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite v1.5 state-filter candidate validator")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--htf-interval", default="4h")
    parser.add_argument("--market", default="futures", choices=["futures"])
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--htf-pages", type=int, default=8)
    parser.add_argument("--derivatives-pages", type=int, default=48)
    parser.add_argument("--derivatives-limit", type=int, default=500)
    parser.add_argument("--warmup-bars", type=int, default=220)
    parser.add_argument("--strict-width-lower", type=float, default=6.0)
    parser.add_argument("--strict-width-upper", type=float, default=7.0)
    parser.add_argument("--broad-width-lower", type=float, default=4.0)
    parser.add_argument("--broad-width-upper", type=float, default=9.0)
    parser.add_argument("--divergence-min", type=float, default=0.0)
    parser.add_argument("--spot-volume-max", type=float, default=0.8)
    parser.add_argument("--accumulation-spot-volume-max", type=float, default=1.2)
    parser.add_argument("--max-long-funding", type=float, default=0.0008)
    parser.add_argument("--min-short-funding", type=float, default=-0.0008)
    parser.add_argument("--oi-reset-max-delta-pct", type=float, default=0.0)
    parser.add_argument("--oi-build-min-delta-pct", type=float, default=0.0)
    parser.add_argument("--oi-extreme-z", type=float, default=1.5)
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=1.5)
    parser.add_argument("--max-hold-bars", type=int, default=16)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=8)
    parser.add_argument("--bootstrap-iterations", type=int, default=5000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260602)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-winrate-pct", type=float, default=50.0)
    parser.add_argument("--min-bootstrap-prob-gt-0", type=float, default=0.8)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache/binance")
    parser.add_argument("--out-prefix", default="_dl/v15/MAX_CORE_LITE_V15_STATE_FILTERS")
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
        pages=args.htf_pages,
    )
    derivatives_rows, derivatives_source = load_or_fetch_derivatives(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        symbol=args.symbol,
        interval=args.interval,
        rows=rows,
        limit=args.derivatives_limit,
        pages=args.derivatives_pages,
    )
    interval_ms = INTERVAL_MS.get(args.interval, 3_600_000)
    htf_biases = precompute_htf_bias(
        rows=rows,
        htf_rows=htf_rows,
        interval_ms=interval_ms,
        htf_interval=args.htf_interval,
    )

    rng = random.Random(args.bootstrap_seed)
    candidate_results: list[dict[str, Any]] = []
    for spec in candidate_specs(args):
        trades, skipped = simulate_candidate(
            spec=spec,
            rows=rows,
            spot_rows=spot_rows,
            derivatives_rows=derivatives_rows,
            htf_biases=htf_biases,
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

    best = max(candidate_results, key=rank_key) if candidate_results else None
    passed = [item for item in candidate_results if item["research_gate"].get("pass")]
    decision = (
        "At least one v1.5 state-filter candidate passed the research gate and can move to paper-trading design review."
        if passed
        else "No v1.5 state-filter candidate passed the research gate. Do not paper/live trade; keep the strategy research-only."
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_V15_STATE_FILTERS",
        "engine_version": "1.5.0",
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
        },
        "params": {
            "pages": args.pages,
            "limit": args.limit,
            "htf_pages": args.htf_pages,
            "derivatives_pages": args.derivatives_pages,
            "interval": args.interval,
            "htf_interval": args.htf_interval,
            "strict_width_lower": args.strict_width_lower,
            "strict_width_upper": args.strict_width_upper,
            "broad_width_lower": args.broad_width_lower,
            "broad_width_upper": args.broad_width_upper,
            "divergence_min": args.divergence_min,
            "spot_volume_max": args.spot_volume_max,
            "max_long_funding": args.max_long_funding,
            "min_short_funding": args.min_short_funding,
            "oi_reset_max_delta_pct": args.oi_reset_max_delta_pct,
            "oi_build_min_delta_pct": args.oi_build_min_delta_pct,
            "oi_extreme_z": args.oi_extreme_z,
            "stop_atr": args.stop_atr,
            "take_atr": args.take_atr,
            "max_hold_bars": args.max_hold_bars,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "folds": args.folds,
            "bootstrap_iterations": args.bootstrap_iterations,
            "use_cache": args.use_cache,
        },
        "source_lead": "v1.3/v1.4 structural lead plus reusable state filters: OI, funding, sweep/liquidity and HTF bias.",
        "candidates": candidate_results,
        "best_candidate": best,
        "passed": passed,
        "decision": decision,
        "runtime_boundary": (
            "Research-only public-data candidate validation. It fetches public Binance market data, "
            "uses deterministic simulation, does not use API keys, does not place orders, and does not approve live trading."
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
                    "side": best.get("side") if best else None,
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
