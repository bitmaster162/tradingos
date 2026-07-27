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
class RegimeSignal:
    dataset_id: str
    family: str
    oi_filter: str
    ts: str
    bar_index: int
    side_hint: str
    atr: float
    funding: float
    oi_delta_pct: float | None
    funding_state: str


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


def load_derivative_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    oi = sum(1 for row in rows if safe_float(row.get("open_interest")) is not None)
    funding = sum(1 for row in rows if safe_float(row.get("funding")) is not None)
    return {
        "rows": total,
        "oi_coverage_pct": round(oi / total * 100.0, 3) if total else 0.0,
        "funding_coverage_pct": round(funding / total * 100.0, 3) if total else 0.0,
    }


def funding_state(funding: float, hot_abs: float, neutral_abs: float) -> str:
    if funding >= hot_abs:
        return "positive_hot"
    if funding <= -hot_abs:
        return "negative_hot"
    if abs(funding) <= neutral_abs:
        return "neutral"
    return "positive_mild" if funding > 0 else "negative_mild"


def oi_delta(rows: list[dict[str, Any]], index: int, lag: int) -> float | None:
    if index < lag:
        return None
    current = safe_float(rows[index].get("open_interest"))
    previous = safe_float(rows[index - lag].get("open_interest"))
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100.0


def median_abs_oi_delta(rows: list[dict[str, Any]], lag: int) -> float:
    values: list[float] = []
    for index in range(lag, len(rows)):
        value = oi_delta(rows, index, lag)
        if value is not None:
            values.append(abs(value))
    if not values:
        return 0.0
    return float(statistics.median(values))


def oi_filter_ok(name: str, delta_pct: float | None, spike_abs_pct: float) -> bool:
    if name == "none":
        return True
    if delta_pct is None:
        return False
    if name == "oi_expansion":
        return delta_pct > 0
    if name == "oi_contraction":
        return delta_pct < 0
    if name == "oi_spike_abs":
        return abs(delta_pct) >= spike_abs_pct
    raise ValueError(f"unsupported OI filter: {name}")


def side_for_family(family: str, state: str) -> str | None:
    if state == "positive_hot":
        if family == "funding_contrarian":
            return "SHORT"
        if family == "funding_follow":
            return "LONG"
        if family == "positive_funding_short":
            return "SHORT"
        return None
    if state == "negative_hot":
        if family == "funding_contrarian":
            return "LONG"
        if family == "funding_follow":
            return "SHORT"
        if family == "negative_funding_long":
            return "LONG"
        return None
    return None


def generate_signals(
    *,
    dataset_id: str,
    bars: list[Any],
    derivative_rows: list[dict[str, Any]],
    atr_values: list[float | None],
    family: str,
    oi_filter: str,
    funding_hot_abs: float,
    funding_neutral_abs: float,
    oi_lag: int,
    oi_spike_abs_pct: float,
    funding_change_only: bool,
) -> list[RegimeSignal]:
    signals: list[RegimeSignal] = []
    count = min(len(bars), len(derivative_rows))
    previous_funding: float | None = None
    for index in range(count):
        funding = safe_float(derivative_rows[index].get("funding"))
        if funding is None:
            continue
        if funding_change_only and previous_funding is not None and abs(funding - previous_funding) <= 1e-12:
            previous_funding = funding
            continue
        previous_funding = funding
        atr = atr_values[index]
        if atr is None or atr <= 0:
            continue
        state = funding_state(funding, funding_hot_abs, funding_neutral_abs)
        side = side_for_family(family, state)
        if side is None:
            continue
        delta_pct = oi_delta(derivative_rows, index, oi_lag)
        if not oi_filter_ok(oi_filter, delta_pct, oi_spike_abs_pct):
            continue
        signals.append(
            RegimeSignal(
                dataset_id=dataset_id,
                family=family,
                oi_filter=oi_filter,
                ts=bars[index].ts,
                bar_index=index,
                side_hint=side,
                atr=atr,
                funding=funding,
                oi_delta_pct=None if delta_pct is None else round(delta_pct, 6),
                funding_state=state,
            )
        )
    return signals


def simulate_signals(
    *,
    dataset_id: str,
    strategy_id: str,
    bars: list[Any],
    signals: list[RegimeSignal],
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
            signal={
                "bar_index": signal.bar_index,
                "side_hint": signal.side_hint,
                "atr": signal.atr,
            },
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
        "# Funding/OI Regime Hardening",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research-only hardening backtest.",
        "- Uses public Binance futures cache only.",
        "- Signals are generated on funding changes, not every aligned bar.",
        "- OI filters are evaluated only where OI exists; coverage is reported explicitly.",
        "- Fees/slippage are included.",
        "- No orders are sent and no live permission is granted.",
        "",
        "## Data Coverage",
        "",
    ]
    for dataset in report["datasets"]:
        lines.append(
            f"- `{dataset['dataset_id']}`: rows=`{dataset['rows']}`, "
            f"OI coverage=`{dataset['coverage']['oi_coverage_pct']}`%, "
            f"funding coverage=`{dataset['coverage']['funding_coverage_pct']}`%, "
            f"signals=`{dataset['signals']}`."
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
            "- If `passed_count=0`, keep funding/OI regime as context only.",
            "- If a strategy passes, it still requires out-of-sample/paper validation before any live use.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardening backtest for funding/OI regime signals")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--families", default="funding_contrarian,funding_follow,positive_funding_short,negative_funding_long")
    parser.add_argument("--oi-filters", default="none,oi_expansion,oi_contraction,oi_spike_abs")
    parser.add_argument("--funding-hot-abs", type=float, default=0.00005)
    parser.add_argument("--funding-neutral-abs", type=float, default=0.00002)
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--oi-spike-abs-pct", type=float, default=None)
    parser.add_argument("--stop-grid", default="1.0,1.5")
    parser.add_argument("--take-grid", default="1.0,1.5,2.0")
    parser.add_argument("--hold-grid", default="6,12,18")
    parser.add_argument("--atr-window", type=int, default=14)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-trades", type=int, default=80)
    parser.add_argument("--min-expectancy-r", type=float, default=0.03)
    parser.add_argument("--min-winrate-pct", type=float, default=51.0)
    parser.add_argument("--min-stable-folds", type=int, default=3)
    parser.add_argument("--max-drawdown-r", type=float, default=15.0)
    parser.add_argument("--funding-change-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-overlap", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--out-prefix", default="docs/FUNDING_OI_REGIME_HARDENING_2026-06-03")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    intervals = parse_list(args.intervals, str)
    families = parse_list(args.families, str)
    oi_filters = parse_list(args.oi_filters, str)
    stop_grid = parse_list(args.stop_grid, float)
    take_grid = parse_list(args.take_grid, float)
    hold_grid = parse_list(args.hold_grid, int)
    cost_bps_per_side = args.fee_bps + args.slippage_bps

    dataset_infos: list[dict[str, Any]] = []
    bars_by_dataset: dict[str, list[Any]] = {}
    signals_by_key: dict[tuple[str, str, str], list[RegimeSignal]] = {}
    for interval in intervals:
        dataset_id = f"funding_oi_BTCUSDT_{interval}"
        kline_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv"
        derivative_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_oi_aligned.csv"
        bars = load_ohlcv(kline_path)
        derivative_rows = load_derivative_rows(derivative_path)
        atr_values = compute_atr(bars, args.atr_window)
        spike_abs = args.oi_spike_abs_pct
        if spike_abs is None:
            values = []
            for index in range(args.oi_lag, len(derivative_rows)):
                value = oi_delta(derivative_rows, index, args.oi_lag)
                if value is not None:
                    values.append(abs(value))
            spike_abs = max(0.05, float(statistics.median(values)) if values else 0.05)
        dataset_signal_count = 0
        bars_by_dataset[dataset_id] = bars
        for family in families:
            for oi_filter in oi_filters:
                signals = generate_signals(
                    dataset_id=dataset_id,
                    bars=bars,
                    derivative_rows=derivative_rows,
                    atr_values=atr_values,
                    family=family,
                    oi_filter=oi_filter,
                    funding_hot_abs=args.funding_hot_abs,
                    funding_neutral_abs=args.funding_neutral_abs,
                    oi_lag=args.oi_lag,
                    oi_spike_abs_pct=spike_abs,
                    funding_change_only=args.funding_change_only,
                )
                signals_by_key[(dataset_id, family, oi_filter)] = signals
                dataset_signal_count += len(signals)
        dataset_infos.append(
            {
                "dataset_id": dataset_id,
                "interval": interval,
                "rows": len(bars),
                "coverage": coverage(derivative_rows),
                "oi_spike_abs_pct_used": round(spike_abs, 6),
                "signals": dataset_signal_count,
                "paths": {"klines": str(kline_path), "derivatives": str(derivative_path)},
            }
        )

    results: list[dict[str, Any]] = []
    for family in families:
        for oi_filter in oi_filters:
            for stop_atr in stop_grid:
                for take_atr in take_grid:
                    for max_hold_bars in hold_grid:
                        strategy_id = f"{family}_{oi_filter}_s{stop_atr:g}_t{take_atr:g}_h{max_hold_bars}"
                        trades = []
                        for dataset_id, bars in bars_by_dataset.items():
                            trades.extend(
                                simulate_signals(
                                    dataset_id=dataset_id,
                                    strategy_id=strategy_id,
                                    bars=bars,
                                    signals=signals_by_key.get((dataset_id, family, oi_filter), []),
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
                                    "oi_filter": oi_filter,
                                    "stop_atr": stop_atr,
                                    "take_atr": take_atr,
                                    "max_hold_bars": max_hold_bars,
                                    "funding_hot_abs": args.funding_hot_abs,
                                    "funding_change_only": args.funding_change_only,
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
