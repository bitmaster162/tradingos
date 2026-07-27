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
class Signal:
    dataset_id: str
    strategy_family: str
    ts: str
    bar_index: int
    side_hint: str
    atr: float
    divergence_pct: float
    divergence_z: float
    spot_ret_pct: float
    perp_ret_pct: float
    funding: float | None
    filter_id: str


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


def load_derivatives_by_time(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {str(row.get("time", "")).strip(): row for row in csv.DictReader(handle)}


def build_dataset(cache_dir: Path, interval: str) -> dict[str, Any]:
    return {
        "dataset_id": f"spot_perp_BTCUSDT_{interval}",
        "interval": interval,
        "futures": cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv",
        "spot": cache_dir / "spot" / "BTCUSDT" / f"{interval}_klines.csv",
        "derivatives": cache_dir / "futures" / "BTCUSDT" / f"{interval}_oi_aligned.csv",
    }


def align_spot_perp(futures_path: Path, spot_path: Path) -> list[dict[str, Any]]:
    futures = load_ohlcv(futures_path)
    spot_by_time = {bar.ts: bar for bar in load_ohlcv(spot_path)}
    rows: list[dict[str, Any]] = []
    for index, perp in enumerate(futures):
        spot = spot_by_time.get(perp.ts)
        if spot is None:
            continue
        rows.append({"index": index, "perp": perp, "spot": spot})
    return rows


def rolling_mean_std(values: list[float], end_index: int, window: int) -> tuple[float | None, float | None]:
    start = max(0, end_index - window)
    sample = values[start:end_index]
    if len(sample) < max(20, min(window, 20)):
        return None, None
    mean = sum(sample) / len(sample)
    if len(sample) < 2:
        return mean, None
    std = statistics.pstdev(sample)
    if std <= 0:
        return mean, None
    return mean, std


def funding_filter_ok(side: str, funding: float | None, filter_id: str, funding_hot_abs: float) -> bool:
    if filter_id == "none":
        return True
    if funding is None:
        return False
    if filter_id == "avoid_crowded":
        return bool((side == "LONG" and funding <= funding_hot_abs) or (side == "SHORT" and funding >= -funding_hot_abs))
    if filter_id == "contrarian_funding":
        return bool((side == "LONG" and funding <= -funding_hot_abs) or (side == "SHORT" and funding >= funding_hot_abs))
    raise ValueError(f"unsupported funding filter: {filter_id}")


def generate_signals(
    *,
    dataset_id: str,
    aligned_rows: list[dict[str, Any]],
    atr_values: list[float | None],
    derivatives_by_time: dict[str, dict[str, str]],
    lookback: int,
    z_threshold: float,
    rolling_window: int,
    family: str,
    funding_filter: str,
    funding_hot_abs: float,
) -> list[Signal]:
    divergence_values: list[float] = []
    signal_candidates: list[dict[str, Any] | None] = []
    for offset, row in enumerate(aligned_rows):
        if offset < lookback:
            divergence_values.append(0.0)
            signal_candidates.append(None)
            continue
        perp_now = row["perp"].close
        spot_now = row["spot"].close
        perp_prev = aligned_rows[offset - lookback]["perp"].close
        spot_prev = aligned_rows[offset - lookback]["spot"].close
        if perp_prev <= 0 or spot_prev <= 0:
            divergence_values.append(0.0)
            signal_candidates.append(None)
            continue
        perp_ret_pct = (perp_now - perp_prev) / perp_prev * 100.0
        spot_ret_pct = (spot_now - spot_prev) / spot_prev * 100.0
        divergence_pct = spot_ret_pct - perp_ret_pct
        mean, std = rolling_mean_std(divergence_values, len(divergence_values), rolling_window)
        divergence_values.append(divergence_pct)
        if mean is None or std is None:
            signal_candidates.append(None)
            continue
        divergence_z = (divergence_pct - mean) / std
        if abs(divergence_z) < z_threshold:
            signal_candidates.append(None)
            continue
        if family == "spot_lead_momentum":
            side = "LONG" if divergence_z > 0 else "SHORT"
        elif family == "perp_overextension_reversion":
            side = "SHORT" if divergence_z > 0 else "LONG"
        else:
            raise ValueError(f"unsupported family: {family}")
        derivative_row = derivatives_by_time.get(row["perp"].ts, {})
        funding = safe_float(derivative_row.get("funding"))
        if not funding_filter_ok(side, funding, funding_filter, funding_hot_abs):
            signal_candidates.append(None)
            continue
        atr = atr_values[row["index"]] if row["index"] < len(atr_values) else None
        if atr is None or atr <= 0:
            signal_candidates.append(None)
            continue
        signal_candidates.append(
            {
                "dataset_id": dataset_id,
                "strategy_family": family,
                "ts": row["perp"].ts,
                "bar_index": row["index"],
                "side_hint": side,
                "atr": atr,
                "divergence_pct": divergence_pct,
                "divergence_z": divergence_z,
                "spot_ret_pct": spot_ret_pct,
                "perp_ret_pct": perp_ret_pct,
                "funding": funding,
                "filter_id": funding_filter,
            }
        )
    return [Signal(**item) for item in signal_candidates if item is not None]


def simulate_signals(
    *,
    dataset_id: str,
    strategy_id: str,
    bars: list[Any],
    signals: list[Signal],
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
        "# Spot/Perp Divergence Hardening",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research-only hardening backtest.",
        "- Uses public Binance spot/futures cache only.",
        "- Entry is delayed to next bar open after signal close.",
        "- Fees/slippage are included.",
        "- No orders are sent and no live permission is granted.",
        "",
        "## Data Coverage",
        "",
    ]
    for dataset in report["datasets"]:
        lines.append(
            f"- `{dataset['dataset_id']}`: aligned_rows=`{dataset['aligned_rows']}`, "
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
            "- If `passed_count=0`, keep spot/perp divergence as research context only.",
            "- If a strategy passes, it still needs a separate out-of-sample/paper validation step.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardening backtest for BTC spot/perp divergence signals")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--families", default="spot_lead_momentum,perp_overextension_reversion")
    parser.add_argument("--funding-filters", default="none,avoid_crowded,contrarian_funding")
    parser.add_argument("--lookbacks", default="4,12,24")
    parser.add_argument("--z-thresholds", default="1.0,1.5,2.0")
    parser.add_argument("--rolling-window", type=int, default=200)
    parser.add_argument("--stop-grid", default="1.0,1.5")
    parser.add_argument("--take-grid", default="1.0,1.5,2.0")
    parser.add_argument("--hold-grid", default="6,12,18")
    parser.add_argument("--atr-window", type=int, default=14)
    parser.add_argument("--funding-hot-abs", type=float, default=0.00005)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-expectancy-r", type=float, default=0.03)
    parser.add_argument("--min-winrate-pct", type=float, default=51.0)
    parser.add_argument("--min-stable-folds", type=int, default=3)
    parser.add_argument("--max-drawdown-r", type=float, default=15.0)
    parser.add_argument("--no-overlap", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--out-prefix", default="docs/SPOT_PERP_DIVERGENCE_HARDENING_2026-06-03")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    intervals = parse_list(args.intervals, str)
    families = parse_list(args.families, str)
    funding_filters = parse_list(args.funding_filters, str)
    lookbacks = parse_list(args.lookbacks, int)
    z_thresholds = parse_list(args.z_thresholds, float)
    stop_grid = parse_list(args.stop_grid, float)
    take_grid = parse_list(args.take_grid, float)
    hold_grid = parse_list(args.hold_grid, int)
    cost_bps_per_side = args.fee_bps + args.slippage_bps

    dataset_infos: list[dict[str, Any]] = []
    bars_by_dataset: dict[str, list[Any]] = {}
    signals_by_key: dict[tuple[str, str, str, int, float], list[Signal]] = {}
    for interval in intervals:
        dataset = build_dataset(cache_dir, interval)
        aligned = align_spot_perp(dataset["futures"], dataset["spot"])
        futures_bars = load_ohlcv(dataset["futures"])
        bars_by_dataset[dataset["dataset_id"]] = futures_bars
        atr_values = compute_atr(futures_bars, args.atr_window)
        derivatives = load_derivatives_by_time(dataset["derivatives"])
        dataset_signal_count = 0
        for family in families:
            for funding_filter in funding_filters:
                for lookback in lookbacks:
                    for z_threshold in z_thresholds:
                        key = (dataset["dataset_id"], family, funding_filter, lookback, z_threshold)
                        signals = generate_signals(
                            dataset_id=dataset["dataset_id"],
                            aligned_rows=aligned,
                            atr_values=atr_values,
                            derivatives_by_time=derivatives,
                            lookback=lookback,
                            z_threshold=z_threshold,
                            rolling_window=args.rolling_window,
                            family=family,
                            funding_filter=funding_filter,
                            funding_hot_abs=args.funding_hot_abs,
                        )
                        dataset_signal_count += len(signals)
                        signals_by_key[key] = signals
        dataset_infos.append(
            {
                "dataset_id": dataset["dataset_id"],
                "interval": interval,
                "aligned_rows": len(aligned),
                "signals": dataset_signal_count,
                "paths": {name: str(path) for name, path in dataset.items() if name != "dataset_id"},
            }
        )

    results: list[dict[str, Any]] = []
    for family in families:
        for funding_filter in funding_filters:
            for lookback in lookbacks:
                for z_threshold in z_thresholds:
                    base_strategy = f"{family}_{funding_filter}_lb{lookback}_z{z_threshold:g}"
                    combined_signals: dict[str, list[Signal]] = {}
                    for dataset_id in bars_by_dataset:
                        combined_signals[dataset_id] = signals_by_key.get((dataset_id, family, funding_filter, lookback, z_threshold), [])
                    for stop_atr in stop_grid:
                        for take_atr in take_grid:
                            for max_hold_bars in hold_grid:
                                strategy_id = f"{base_strategy}_s{stop_atr:g}_t{take_atr:g}_h{max_hold_bars}"
                                trades = []
                                for dataset_id, signals in combined_signals.items():
                                    trades.extend(
                                        simulate_signals(
                                            dataset_id=dataset_id,
                                            strategy_id=strategy_id,
                                            bars=bars_by_dataset[dataset_id],
                                            signals=signals,
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
                                            "funding_filter": funding_filter,
                                            "lookback": lookback,
                                            "z_threshold": z_threshold,
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
