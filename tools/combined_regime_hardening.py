from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_forward_eval import compute_atr  # noqa: E402
from tools.liquidity_sweep_hardening import fold_summaries, gate, simulate_trade, summarize_trades  # noqa: E402


@dataclass(frozen=True)
class CombinedSignal:
    dataset_id: str
    family: str
    filter_mode: str
    ts: str
    bar_index: int
    side_hint: str
    atr: float
    reason: str
    funding: float | None
    oi_delta_pct: float | None
    spot_perp_divergence_pct: float | None
    recent_sweep_against: bool


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    if math.isnan(result):
        return None
    return result


def parse_list(value: str, cast: Any) -> list[Any]:
    return [cast(item.strip()) for item in value.split(",") if item.strip()]


def ema(values: list[float], length: int) -> list[float | None]:
    if not values:
        return []
    alpha = 2.0 / (length + 1.0)
    out: list[float | None] = []
    current: float | None = None
    for index, value in enumerate(values):
        if index + 1 < length:
            out.append(None)
            continue
        if current is None:
            current = sum(values[index + 1 - length : index + 1]) / length
        else:
            current = value * alpha + current * (1.0 - alpha)
        out.append(current)
    return out


def rsi(values: list[float], length: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= length:
        return out
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
        if index < length:
            continue
        if index == length:
            avg_gain = sum(gains[:length]) / length
            avg_loss = sum(losses[:length]) / length
        else:
            avg_gain = ((out_gain * (length - 1)) + gains[-1]) / length
            avg_loss = ((out_loss * (length - 1)) + losses[-1]) / length
        out_gain = avg_gain
        out_loss = avg_loss
        if avg_loss == 0:
            out[index] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[index] = 100.0 - (100.0 / (1.0 + rs))
    return out


def load_csv_by_time(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {str(row.get("time", "")).strip(): row for row in csv.DictReader(handle)}


def oi_delta(rows_by_time: dict[str, dict[str, str]], ordered_times: list[str], index: int, lag: int) -> float | None:
    if index < lag:
        return None
    current_row = rows_by_time.get(ordered_times[index])
    previous_row = rows_by_time.get(ordered_times[index - lag])
    if current_row is None or previous_row is None:
        return None
    current = safe_float(current_row.get("open_interest"))
    previous = safe_float(previous_row.get("open_interest"))
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100.0


def spot_perp_divergence(
    spot_by_time: dict[str, Any],
    bars: list[Any],
    index: int,
    lookback: int,
) -> float | None:
    if index < lookback:
        return None
    now_perp = bars[index]
    prev_perp = bars[index - lookback]
    now_spot = spot_by_time.get(now_perp.ts)
    prev_spot = spot_by_time.get(prev_perp.ts)
    if now_spot is None or prev_spot is None or prev_perp.close <= 0 or prev_spot.close <= 0:
        return None
    perp_ret = (now_perp.close - prev_perp.close) / prev_perp.close * 100.0
    spot_ret = (now_spot.close - prev_spot.close) / prev_spot.close * 100.0
    return spot_ret - perp_ret


def previous_high_low(bars: list[Any], index: int, lookback: int) -> tuple[float | None, float | None]:
    if index < lookback:
        return None, None
    window = bars[index - lookback : index]
    return max(bar.high for bar in window), min(bar.low for bar in window)


def sweep_flags(bars: list[Any], index: int, lookback: int) -> tuple[bool, bool]:
    high, low = previous_high_low(bars, index, lookback)
    if high is None or low is None:
        return False, False
    bar = bars[index]
    bearish_sweep = bar.high > high and bar.close < high
    bullish_sweep = bar.low < low and bar.close > low
    return bullish_sweep, bearish_sweep


def recent_sweep_against_side(bars: list[Any], index: int, side: str, lookback: int, sweep_lookback: int) -> bool:
    start = max(0, index - sweep_lookback + 1)
    for offset in range(start, index + 1):
        bullish, bearish = sweep_flags(bars, offset, lookback)
        if side == "LONG" and bearish:
            return True
        if side == "SHORT" and bullish:
            return True
    return False


def htf_bias_for_index(
    htf_bars: list[Any],
    htf_ema20: list[float | None],
    htf_ema50: list[float | None],
    htf_ema200: list[float | None],
    ts: str,
) -> str:
    selected = -1
    for index, bar in enumerate(htf_bars):
        if bar.ts <= ts:
            selected = index
        else:
            break
    if selected < 0:
        return "UNKNOWN"
    if htf_ema20[selected] is None or htf_ema50[selected] is None or htf_ema200[selected] is None:
        return "UNKNOWN"
    close = htf_bars[selected].close
    if close > htf_ema200[selected] and htf_ema20[selected] > htf_ema50[selected]:
        return "LONG"
    if close < htf_ema200[selected] and htf_ema20[selected] < htf_ema50[selected]:
        return "SHORT"
    return "NEUTRAL"


def htf_bias_series(
    *,
    target_bars: list[Any],
    htf_bars: list[Any],
    htf_ema20: list[float | None],
    htf_ema50: list[float | None],
    htf_ema200: list[float | None],
) -> list[str]:
    biases: list[str] = []
    selected = -1
    for bar in target_bars:
        while selected + 1 < len(htf_bars) and htf_bars[selected + 1].ts <= bar.ts:
            selected += 1
        if selected < 0:
            biases.append("UNKNOWN")
            continue
        if htf_ema20[selected] is None or htf_ema50[selected] is None or htf_ema200[selected] is None:
            biases.append("UNKNOWN")
            continue
        close = htf_bars[selected].close
        if close > htf_ema200[selected] and htf_ema20[selected] > htf_ema50[selected]:
            biases.append("LONG")
        elif close < htf_ema200[selected] and htf_ema20[selected] < htf_ema50[selected]:
            biases.append("SHORT")
        else:
            biases.append("NEUTRAL")
    return biases


def filter_ok(
    *,
    mode: str,
    side: str,
    funding: float | None,
    oi_delta_pct: float | None,
    spot_perp_divergence_pct: float | None,
    recent_sweep_against: bool,
    funding_hot_abs: float,
) -> bool:
    if mode == "none":
        return True
    if mode in {"risk_filters", "all_filters"}:
        if funding is not None:
            if side == "LONG" and funding >= funding_hot_abs:
                return False
            if side == "SHORT" and funding <= -funding_hot_abs:
                return False
        if spot_perp_divergence_pct is not None:
            if side == "LONG" and spot_perp_divergence_pct < -0.05:
                return False
            if side == "SHORT" and spot_perp_divergence_pct > 0.05:
                return False
    if mode in {"oi_confirmation", "all_filters"}:
        if oi_delta_pct is None or oi_delta_pct <= 0:
            return False
    if mode == "all_filters" and recent_sweep_against:
        return False
    if mode not in {"none", "risk_filters", "oi_confirmation", "all_filters"}:
        raise ValueError(f"unsupported filter mode: {mode}")
    return True


def generate_family_signal(
    *,
    family: str,
    bars: list[Any],
    index: int,
    ema20: list[float | None],
    ema50: list[float | None],
    ema200: list[float | None],
    rsi14: list[float | None],
    htf_bias: str,
    structure_lookback: int,
) -> tuple[str, str] | None:
    if index < max(structure_lookback, 220):
        return None
    bar = bars[index]
    prev = bars[index - 1]
    if ema20[index] is None or ema50[index] is None or ema200[index] is None:
        return None
    high, low = previous_high_low(bars, index, structure_lookback)
    if high is None or low is None:
        return None

    trend_up = bar.close > ema200[index] and ema20[index] > ema50[index]
    trend_down = bar.close < ema200[index] and ema20[index] < ema50[index]

    if family == "donchian_breakout":
        if bar.close > high and trend_up and htf_bias in {"LONG", "NEUTRAL"}:
            return "LONG", "donchian_breakout_up_trend_aligned"
        if bar.close < low and trend_down and htf_bias in {"SHORT", "NEUTRAL"}:
            return "SHORT", "donchian_breakout_down_trend_aligned"
        return None

    if family == "ema_pullback_continuation":
        if trend_up and htf_bias in {"LONG", "NEUTRAL"} and prev.close < (ema20[index - 1] or prev.close) and bar.close > ema20[index] and (rsi14[index] or 50) >= 45:
            return "LONG", "ema20_reclaim_in_uptrend"
        if trend_down and htf_bias in {"SHORT", "NEUTRAL"} and prev.close > (ema20[index - 1] or prev.close) and bar.close < ema20[index] and (rsi14[index] or 50) <= 55:
            return "SHORT", "ema20_reject_in_downtrend"
        return None

    if family == "short_continuation_pressure":
        if trend_down and htf_bias in {"SHORT", "NEUTRAL"} and bar.close < ema20[index] and (rsi14[index] or 50) <= 48:
            return "SHORT", "trend_down_close_below_ema20_rsi_weak"
        return None

    raise ValueError(f"unsupported family: {family}")


def generate_signals(
    *,
    dataset_id: str,
    bars: list[Any],
    spot_by_time: dict[str, Any],
    derivatives_by_time: dict[str, dict[str, str]],
    htf_bars: list[Any],
    family: str,
    filter_mode: str,
    structure_lookback: int,
    sweep_lookback: int,
    spot_perp_lookback: int,
    oi_lag: int,
    funding_hot_abs: float,
) -> list[CombinedSignal]:
    closes = [bar.close for bar in bars]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi14 = rsi(closes, 14)
    atr = compute_atr(bars, 14)
    htf_closes = [bar.close for bar in htf_bars]
    htf_ema20 = ema(htf_closes, 20)
    htf_ema50 = ema(htf_closes, 50)
    htf_ema200 = ema(htf_closes, 200)
    htf_biases = htf_bias_series(
        target_bars=bars,
        htf_bars=htf_bars,
        htf_ema20=htf_ema20,
        htf_ema50=htf_ema50,
        htf_ema200=htf_ema200,
    )
    ordered_times = [bar.ts for bar in bars]

    signals: list[CombinedSignal] = []
    for index in range(len(bars)):
        if atr[index] is None or atr[index] <= 0:
            continue
        htf_bias = htf_biases[index]
        family_signal = generate_family_signal(
            family=family,
            bars=bars,
            index=index,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200,
            rsi14=rsi14,
            htf_bias=htf_bias,
            structure_lookback=structure_lookback,
        )
        if family_signal is None:
            continue
        side, reason = family_signal
        derivative_row = derivatives_by_time.get(bars[index].ts, {})
        funding = safe_float(derivative_row.get("funding"))
        delta = oi_delta(derivatives_by_time, ordered_times, index, oi_lag)
        divergence = spot_perp_divergence(spot_by_time, bars, index, spot_perp_lookback)
        recent_against = recent_sweep_against_side(bars, index, side, structure_lookback, sweep_lookback)
        if not filter_ok(
            mode=filter_mode,
            side=side,
            funding=funding,
            oi_delta_pct=delta,
            spot_perp_divergence_pct=divergence,
            recent_sweep_against=recent_against,
            funding_hot_abs=funding_hot_abs,
        ):
            continue
        signals.append(
            CombinedSignal(
                dataset_id=dataset_id,
                family=family,
                filter_mode=filter_mode,
                ts=bars[index].ts,
                bar_index=index,
                side_hint=side,
                atr=atr[index] or 0.0,
                reason=reason,
                funding=funding,
                oi_delta_pct=None if delta is None else round(delta, 6),
                spot_perp_divergence_pct=None if divergence is None else round(divergence, 6),
                recent_sweep_against=recent_against,
            )
        )
    return signals


def simulate_signals(
    *,
    dataset_id: str,
    strategy_id: str,
    bars: list[Any],
    signals: list[CombinedSignal],
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    cost_bps_per_side: float,
    no_overlap: bool,
) -> list[Any]:
    trades = []
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: item.bar_index):
        if no_overlap and signal.bar_index <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            bars=bars,
            signal={"bar_index": signal.bar_index, "side_hint": signal.side_hint, "atr": signal.atr},
            stop_atr=stop_atr,
            take_atr=take_atr,
            max_hold_bars=max_hold_bars,
            cost_bps_per_side=cost_bps_per_side,
        )
        if trade is None:
            continue
        trades.append(trade)
        if no_overlap:
            for index in range(signal.bar_index + 1, min(len(bars), signal.bar_index + max_hold_bars + 2)):
                if bars[index].ts == trade.exit_ts:
                    last_exit_bar = index
                    break
    return trades


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Combined Regime Hardening",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research-only combined regime backtest.",
        "- Structure/trend is the primary signal.",
        "- Funding/OI, spot/perp divergence and sweeps are filters/vetoes, not entries.",
        "- Entry is delayed to next bar open.",
        "- Fees/slippage are included.",
        "- No orders are sent and no live permission is granted.",
        "",
        "## Data Coverage",
        "",
    ]
    for dataset in report["datasets"]:
        lines.append(
            f"- `{dataset['dataset_id']}`: rows=`{dataset['rows']}`, signals=`{dataset['signals']}`, "
            f"OI coverage=`{dataset['oi_coverage_pct']}`%, funding coverage=`{dataset['funding_coverage_pct']}`%."
        )
    lines.extend(
        [
            "",
            "## Top Results",
            "",
            "| Strategy | Trades | Winrate | Exp R | Net R | Stable Folds | Verdict |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["top_results"]:
        summary = item["summary"]
        gate_result = item["gate"]
        lines.append(
            f"| `{item['strategy_id']}` | `{summary['trades']}` | `{summary['winrate_pct']}` | "
            f"`{summary['expectancy_r']}` | `{summary['net_r_total']}` | "
            f"`{gate_result['stable_folds']}` | `{gate_result['verdict']}` |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Passed strategies: `{report['passed_count']}`.",
            "- If `passed_count=0`, keep combined regime in research and do not paper trade.",
            "- If any strategy passes, next step is a separate out-of-sample/paper validation step.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardening backtest for combined BTC regime model")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--families", default="donchian_breakout,ema_pullback_continuation,short_continuation_pressure")
    parser.add_argument("--filter-modes", default="none,risk_filters,all_filters")
    parser.add_argument("--structure-lookback", type=int, default=50)
    parser.add_argument("--sweep-lookback", type=int, default=3)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--funding-hot-abs", type=float, default=0.00005)
    parser.add_argument("--stop-grid", default="1.0,1.5")
    parser.add_argument("--take-grid", default="1.5,2.0")
    parser.add_argument("--hold-grid", default="6,12")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-expectancy-r", type=float, default=0.03)
    parser.add_argument("--min-winrate-pct", type=float, default=51.0)
    parser.add_argument("--min-stable-folds", type=int, default=3)
    parser.add_argument("--max-drawdown-r", type=float, default=20.0)
    parser.add_argument("--no-overlap", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--out-prefix", default="docs/COMBINED_REGIME_HARDENING_2026-06-03")
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
    signals_by_key: dict[tuple[str, str, str], list[CombinedSignal]] = {}
    dataset_infos: list[dict[str, Any]] = []
    for interval in intervals:
        dataset_id = f"combined_BTCUSDT_{interval}"
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
                        trades = sorted(trades, key=lambda item: item.entry_ts)
                        summary = summarize_trades(trades)
                        folds = fold_summaries(trades, args.folds)
                        gate_result = gate(summary, folds, args)
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
                                "summary": summary,
                                "folds": folds,
                                "gate": gate_result,
                                "sample_trades": [trade.__dict__ for trade in trades[:12]],
                            }
                        )

    ranked = sorted(
        results,
        key=lambda item: (
            1 if item["gate"]["pass"] else 0,
            item["summary"]["expectancy_r"] or -999.0,
            item["gate"]["stable_folds"],
            item["summary"]["trades"],
            item["summary"]["winrate_pct"] or 0.0,
        ),
        reverse=True,
    )
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_hardening_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "datasets": dataset_infos,
        "gate_requirements": {
            "min_trades": args.min_trades,
            "min_expectancy_r": args.min_expectancy_r,
            "min_winrate_pct": args.min_winrate_pct,
            "min_stable_folds": args.min_stable_folds,
            "max_drawdown_r": -abs(args.max_drawdown_r),
        },
        "passed_count": sum(1 for item in results if item["gate"]["pass"]),
        "top_results": ranked[:15],
        "all_results": results,
    }
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(
        {
            "passed_count": report["passed_count"],
            "top_results": [
                {
                    "strategy_id": item["strategy_id"],
                    "summary": item["summary"],
                    "gate": item["gate"],
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
