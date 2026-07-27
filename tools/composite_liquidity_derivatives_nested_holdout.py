#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import OhlcvBar, load_ohlcv  # noqa: E402
from tools.liquidity_sweep_forward_eval import compute_atr  # noqa: E402
from tools.liquidity_sweep_hardening import Trade, fold_summaries, simulate_trade, summarize_trades  # noqa: E402


@dataclass(frozen=True)
class CompositeConfig:
    strategy_id: str
    lookback: int
    div_window: int
    min_spot_perp_div_bps: float
    oi_window: int
    min_oi_change_pct: float
    funding_mode: str
    funding_abs_max_bps: float
    funding_compression_ratio: float
    stop_atr: float
    take_atr: float
    max_hold_bars: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_csv_dict(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_float(value: Any, default: float = math.nan) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed


def pct_change(values: list[float], index: int, window: int) -> float | None:
    if index < window:
        return None
    base = values[index - window]
    if not math.isfinite(base) or base == 0:
        return None
    current = values[index]
    if not math.isfinite(current):
        return None
    return (current / base - 1.0) * 100.0


def bps_change(values: list[float], index: int, window: int) -> float | None:
    if index < window:
        return None
    base = values[index - window]
    current = values[index]
    if not math.isfinite(base) or base == 0 or not math.isfinite(current):
        return None
    return (current / base - 1.0) * 10_000.0


def rolling_mean_abs(values: list[float], index: int, window: int) -> float | None:
    if index + 1 < window:
        return None
    chunk = [abs(value) for value in values[index + 1 - window : index + 1] if math.isfinite(value)]
    if not chunk:
        return None
    return sum(chunk) / len(chunk)


def previous_range(bars: list[OhlcvBar], index: int, lookback: int) -> tuple[float | None, float | None]:
    if index < lookback:
        return None, None
    chunk = bars[index - lookback : index]
    return max(bar.high for bar in chunk), min(bar.low for bar in chunk)


def align_inputs(
    futures_bars: list[OhlcvBar],
    spot_bars: list[OhlcvBar],
    oi_rows: list[dict[str, str]],
) -> dict[str, Any]:
    spot_by_time = {bar.ts: bar for bar in spot_bars}
    oi_by_time = {str(row.get("time") or "").strip(): row for row in oi_rows}
    aligned_futures: list[OhlcvBar] = []
    aligned_spot: list[OhlcvBar] = []
    open_interest: list[float] = []
    funding_bps: list[float] = []
    for bar in futures_bars:
        spot = spot_by_time.get(bar.ts)
        oi = oi_by_time.get(bar.ts)
        if spot is None or oi is None:
            continue
        oi_value = parse_float(oi.get("open_interest"))
        funding_value = parse_float(oi.get("funding"), 0.0)
        if not math.isfinite(oi_value):
            continue
        aligned_futures.append(bar)
        aligned_spot.append(spot)
        open_interest.append(oi_value)
        funding_bps.append(funding_value * 10_000.0)
    return {
        "futures": aligned_futures,
        "spot": aligned_spot,
        "open_interest": open_interest,
        "funding_bps": funding_bps,
    }


def build_configs(args: argparse.Namespace) -> list[CompositeConfig]:
    configs: list[CompositeConfig] = []
    for lookback in [int(item) for item in args.lookbacks.split(",") if item.strip()]:
        for div_window in [int(item) for item in args.div_windows.split(",") if item.strip()]:
            for div_bps in [float(item) for item in args.min_spot_perp_div_bps.split(",") if item.strip()]:
                for oi_window in [int(item) for item in args.oi_windows.split(",") if item.strip()]:
                    for oi_change in [float(item) for item in args.min_oi_change_pct.split(",") if item.strip()]:
                        for funding_mode in [item.strip() for item in args.funding_modes.split(",") if item.strip()]:
                            for take_atr in [float(item) for item in args.take_atr.split(",") if item.strip()]:
                                for hold in [int(item) for item in args.max_hold_bars.split(",") if item.strip()]:
                                    strategy_id = (
                                        "composite_sweep"
                                        f"_lb{lookback}_div{div_window}x{div_bps:g}"
                                        f"_oi{oi_window}x{oi_change:g}_{funding_mode}"
                                        f"_s{args.stop_atr:g}_t{take_atr:g}_h{hold}"
                                    )
                                    configs.append(
                                        CompositeConfig(
                                            strategy_id=strategy_id,
                                            lookback=lookback,
                                            div_window=div_window,
                                            min_spot_perp_div_bps=div_bps,
                                            oi_window=oi_window,
                                            min_oi_change_pct=oi_change,
                                            funding_mode=funding_mode,
                                            funding_abs_max_bps=args.funding_abs_max_bps,
                                            funding_compression_ratio=args.funding_compression_ratio,
                                            stop_atr=args.stop_atr,
                                            take_atr=take_atr,
                                            max_hold_bars=hold,
                                        )
                                    )
    return configs


def funding_ok(config: CompositeConfig, side: str, funding_values: list[float], index: int) -> bool:
    current = funding_values[index]
    if not math.isfinite(current):
        return False
    if config.funding_mode == "none":
        return True
    if config.funding_mode == "contrarian":
        if side == "LONG":
            return current <= 0.0
        return current >= 0.0
    if config.funding_mode == "compressed":
        mean_abs = rolling_mean_abs(funding_values, index, 72)
        if mean_abs is None or mean_abs <= 0:
            return False
        return abs(current) <= config.funding_abs_max_bps and abs(current) <= mean_abs * config.funding_compression_ratio
    raise ValueError(f"unsupported funding mode: {config.funding_mode}")


def generate_signals(
    config: CompositeConfig,
    futures_bars: list[OhlcvBar],
    spot_bars: list[OhlcvBar],
    open_interest: list[float],
    funding_values: list[float],
    atr_values: list[float | None],
    *,
    start_index: int,
    end_index: int,
) -> list[dict[str, Any]]:
    spot_closes = [bar.close for bar in spot_bars]
    futures_closes = [bar.close for bar in futures_bars]
    signals: list[dict[str, Any]] = []
    start = max(start_index, config.lookback, config.div_window, config.oi_window, 72 if config.funding_mode == "compressed" else 0)
    stop = min(end_index, len(futures_bars) - 1)
    for index in range(start, stop):
        atr = atr_values[index]
        if atr is None or atr <= 0:
            continue
        prev_high, prev_low = previous_range(futures_bars, index, config.lookback)
        if prev_high is None or prev_low is None:
            continue
        spot_ret_bps = bps_change(spot_closes, index, config.div_window)
        futures_ret_bps = bps_change(futures_closes, index, config.div_window)
        oi_change_pct = pct_change(open_interest, index, config.oi_window)
        if spot_ret_bps is None or futures_ret_bps is None or oi_change_pct is None:
            continue
        spot_perp_div_bps = spot_ret_bps - futures_ret_bps
        if oi_change_pct < config.min_oi_change_pct:
            continue
        bar = futures_bars[index]
        if (
            bar.low < prev_low
            and bar.close > prev_low
            and spot_perp_div_bps >= config.min_spot_perp_div_bps
            and funding_ok(config, "LONG", funding_values, index)
        ):
            signals.append(
                {
                    "bar_index": index,
                    "side_hint": "LONG",
                    "atr": atr,
                    "reason": "downside_sweep_spot_stronger_oi_expansion",
                    "spot_perp_div_bps": round(spot_perp_div_bps, 6),
                    "oi_change_pct": round(oi_change_pct, 6),
                    "funding_bps": round(funding_values[index], 6),
                }
            )
        elif (
            bar.high > prev_high
            and bar.close < prev_high
            and spot_perp_div_bps <= -config.min_spot_perp_div_bps
            and funding_ok(config, "SHORT", funding_values, index)
        ):
            signals.append(
                {
                    "bar_index": index,
                    "side_hint": "SHORT",
                    "atr": atr,
                    "reason": "upside_sweep_spot_weaker_oi_expansion",
                    "spot_perp_div_bps": round(spot_perp_div_bps, 6),
                    "oi_change_pct": round(oi_change_pct, 6),
                    "funding_bps": round(funding_values[index], 6),
                }
            )
    return signals


def simulate_window(
    config: CompositeConfig,
    futures_bars: list[OhlcvBar],
    spot_bars: list[OhlcvBar],
    open_interest: list[float],
    funding_values: list[float],
    atr_values: list[float | None],
    *,
    start_index: int,
    end_index: int,
    cost_bps_per_side: float,
    no_overlap: bool,
) -> list[Trade]:
    signals = generate_signals(
        config,
        futures_bars,
        spot_bars,
        open_interest,
        funding_values,
        atr_values,
        start_index=start_index,
        end_index=end_index,
    )
    trades: list[Trade] = []
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: int(item["bar_index"])):
        signal_index = int(signal["bar_index"])
        if no_overlap and signal_index <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id="binance_spot_perp_extended_futures_BTCUSDT_1h",
            strategy_id=config.strategy_id,
            bars=futures_bars,
            signal=signal,
            stop_atr=config.stop_atr,
            take_atr=config.take_atr,
            max_hold_bars=config.max_hold_bars,
            cost_bps_per_side=cost_bps_per_side,
        )
        if trade is None:
            continue
        trades.append(trade)
        if no_overlap:
            for exit_index in range(signal_index + 1, min(len(futures_bars), signal_index + config.max_hold_bars + 2)):
                if futures_bars[exit_index].ts == trade.exit_ts:
                    last_exit_bar = exit_index
                    break
    return trades


def stable_folds(folds: list[dict[str, Any]]) -> int:
    return sum(1 for item in folds if item.get("stable"))


def evaluate_stage(
    config: CompositeConfig,
    futures_bars: list[OhlcvBar],
    spot_bars: list[OhlcvBar],
    open_interest: list[float],
    funding_values: list[float],
    atr_values: list[float | None],
    *,
    start_index: int,
    end_index: int,
    cost_bps_per_side: float,
    no_overlap: bool,
    folds: int,
) -> dict[str, Any]:
    trades = simulate_window(
        config,
        futures_bars,
        spot_bars,
        open_interest,
        funding_values,
        atr_values,
        start_index=start_index,
        end_index=end_index,
        cost_bps_per_side=cost_bps_per_side,
        no_overlap=no_overlap,
    )
    summary = summarize_trades(trades)
    fold_rows = fold_summaries(trades, folds)
    return {
        "summary": summary,
        "folds": fold_rows,
        "stable_folds": stable_folds(fold_rows),
        "trades": trades,
    }


def gate(stage: dict[str, Any], args: argparse.Namespace, *, stage_name: str) -> dict[str, Any]:
    summary = stage["summary"]
    required_stable = args.min_train_stable_folds if stage_name == "train" else args.min_validation_stable_folds
    checks = {
        "min_trades": (summary["trades"] or 0) >= args.min_trades,
        "min_expectancy_r": (summary["expectancy_r"] or -999.0) >= args.min_expectancy_r,
        "min_winrate_pct": (summary["winrate_pct"] or 0.0) >= args.min_winrate_pct,
        "min_stable_folds": int(stage["stable_folds"]) >= required_stable,
        "max_drawdown_r": (summary["max_drawdown_r"] or 0.0) >= -abs(args.max_drawdown_r),
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "stable_folds": stage["stable_folds"],
        "required": {
            "min_trades": args.min_trades,
            "min_expectancy_r": args.min_expectancy_r,
            "min_winrate_pct": args.min_winrate_pct,
            "min_stable_folds": required_stable,
            "max_drawdown_r": -abs(args.max_drawdown_r),
        },
    }


def rank_key(stage: dict[str, Any]) -> tuple[float, float, int, float]:
    summary = stage["summary"]
    return (
        float(summary.get("expectancy_r") or -999.0),
        float(summary.get("winrate_pct") or 0.0),
        int(stage.get("stable_folds") or 0),
        float(summary.get("net_r_total") or -999.0),
    )


def trade_to_dict(trade: Trade) -> dict[str, Any]:
    return asdict(trade)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row}) or ["dataset_id", "strategy_id", "entry_ts", "r_net"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_stage(name: str, stage: dict[str, Any] | None, gate_payload: dict[str, Any] | None = None) -> list[str]:
    if not stage:
        return [f"### {name}", "", "- Not opened.", ""]
    summary = stage["summary"]
    lines = [
        f"### {name}",
        "",
        f"- Trades: `{summary.get('trades')}`",
        f"- Winrate: `{summary.get('winrate_pct')}`%",
        f"- Expectancy: `{summary.get('expectancy_r')}` R",
        f"- Net R: `{summary.get('net_r_total')}`",
        f"- Max DD: `{summary.get('max_drawdown_r')}` R",
        f"- Stable folds: `{stage.get('stable_folds')}`",
    ]
    if gate_payload is not None:
        lines.append(f"- Gate pass: `{gate_payload.get('pass')}`")
        lines.append(f"- Checks: `{gate_payload.get('checks')}`")
    lines.append("")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    selected = report.get("selected") or {}
    lines = [
        "# Composite Liquidity + Derivatives Nested Holdout",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Research-only nested holdout.",
        "- No private credentials, no orders, no paper/live permission.",
        "- OOS is opened only if train and validation gates pass.",
        "",
        "## Decision",
        "",
        f"- `{report.get('decision')}`",
        f"- Can trade: `{report.get('can_trade')}`",
        "",
        "## Data",
        "",
        f"- Futures bars: `{report.get('data', {}).get('futures_bars')}`",
        f"- Matched spot/OI rows: `{report.get('data', {}).get('matched_rows')}`",
        f"- First / last: `{report.get('data', {}).get('first')}` / `{report.get('data', {}).get('last')}`",
        "",
        "## Search",
        "",
        f"- Configs tested: `{report.get('search', {}).get('configs_tested')}`",
        f"- Train-qualified: `{report.get('search', {}).get('train_qualified')}`",
        f"- Validation-qualified: `{report.get('search', {}).get('validation_qualified')}`",
        "",
    ]
    if selected:
        lines.extend(
            [
                "## Selected Candidate",
                "",
                f"- Strategy: `{selected.get('strategy_id')}`",
                f"- Config: `{selected.get('config')}`",
                "",
            ]
        )
    lines.extend(render_stage("Train", report.get("train"), report.get("train_gate")))
    lines.extend(render_stage("Validation", report.get("validation"), report.get("validation_gate")))
    lines.extend(render_stage("OOS", report.get("oos"), report.get("oos_gate")))
    lines.extend(
        [
            "## Notes",
            "",
            "- This test checks a predeclared composite shape: liquidity sweep reclaim/reject + spot/perp divergence + OI expansion + optional funding constraint.",
            "- A pass here would still require observer-only forward proof before any paper/live trading.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only nested holdout for composite liquidity + derivatives confluence")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--out-prefix", default="docs/COMPOSITE_LIQUIDITY_DERIVATIVES_NESTED_HOLDOUT_2026-06-30")
    parser.add_argument("--lookbacks", default="24,48,96")
    parser.add_argument("--div-windows", default="3,6,12")
    parser.add_argument("--min-spot-perp-div-bps", default="2,5,10,20")
    parser.add_argument("--oi-windows", default="3,6,12")
    parser.add_argument("--min-oi-change-pct", default="0,0.25,0.5,1")
    parser.add_argument("--funding-modes", default="none,contrarian,compressed")
    parser.add_argument("--funding-abs-max-bps", type=float, default=1.5)
    parser.add_argument("--funding-compression-ratio", type=float, default=0.6)
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", default="2,3")
    parser.add_argument("--max-hold-bars", default="12,24,48")
    parser.add_argument("--cost-bps-per-side", type=float, default=6.0)
    parser.add_argument("--train-frac", type=float, default=0.6)
    parser.add_argument("--validation-frac", type=float, default=0.2)
    parser.add_argument("--min-trades", type=int, default=30)
    parser.add_argument("--min-expectancy-r", type=float, default=0.1)
    parser.add_argument("--min-winrate-pct", type=float, default=35.0)
    parser.add_argument("--min-train-stable-folds", type=int, default=3)
    parser.add_argument("--min-validation-stable-folds", type=int, default=2)
    parser.add_argument("--max-drawdown-r", type=float, default=12.0)
    parser.add_argument("--no-overlap", action="store_true", default=True)
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    futures_path = cache / "futures" / args.symbol / f"{args.interval}_klines.csv"
    spot_path = cache / "spot" / args.symbol / f"{args.interval}_klines.csv"
    oi_path = cache / "futures" / args.symbol / f"{args.interval}_oi_aligned.csv"

    futures_raw = load_ohlcv(futures_path)
    spot_raw = load_ohlcv(spot_path)
    oi_rows = read_csv_dict(oi_path)
    aligned = align_inputs(futures_raw, spot_raw, oi_rows)
    futures_bars = aligned["futures"]
    spot_bars = aligned["spot"]
    open_interest = aligned["open_interest"]
    funding_values = aligned["funding_bps"]
    if len(futures_bars) < 1000:
        raise SystemExit(f"not enough matched rows: {len(futures_bars)}")

    atr_values = compute_atr(futures_bars, 14)
    train_end = int(len(futures_bars) * args.train_frac)
    validation_end = int(len(futures_bars) * (args.train_frac + args.validation_frac))
    configs = build_configs(args)

    train_candidates: list[tuple[CompositeConfig, dict[str, Any], dict[str, Any]]] = []
    for config in configs:
        stage = evaluate_stage(
            config,
            futures_bars,
            spot_bars,
            open_interest,
            funding_values,
            atr_values,
            start_index=0,
            end_index=train_end,
            cost_bps_per_side=args.cost_bps_per_side,
            no_overlap=args.no_overlap,
            folds=4,
        )
        stage_gate = gate(stage, args, stage_name="train")
        if stage_gate["pass"]:
            train_candidates.append((config, stage, stage_gate))

    train_candidates.sort(key=lambda item: rank_key(item[1]), reverse=True)
    selected_config: CompositeConfig | None = None
    train_stage: dict[str, Any] | None = None
    train_gate: dict[str, Any] | None = None
    validation_stage: dict[str, Any] | None = None
    validation_gate: dict[str, Any] | None = None
    oos_stage: dict[str, Any] | None = None
    oos_gate: dict[str, Any] | None = None
    validation_qualified = 0
    decision = "reject_no_train_qualified_composite_candidate"

    if train_candidates:
        selected_config, train_stage, train_gate = train_candidates[0]
        validation_stage = evaluate_stage(
            selected_config,
            futures_bars,
            spot_bars,
            open_interest,
            funding_values,
            atr_values,
            start_index=train_end,
            end_index=validation_end,
            cost_bps_per_side=args.cost_bps_per_side,
            no_overlap=args.no_overlap,
            folds=3,
        )
        validation_gate = gate(validation_stage, args, stage_name="validation")
        validation_qualified = int(validation_gate["pass"])
        decision = "reject_validation_gate_failed"
        if validation_gate["pass"]:
            oos_stage = evaluate_stage(
                selected_config,
                futures_bars,
                spot_bars,
                open_interest,
                funding_values,
                atr_values,
                start_index=validation_end,
                end_index=len(futures_bars),
                cost_bps_per_side=args.cost_bps_per_side,
                no_overlap=args.no_overlap,
                folds=2,
            )
            oos_gate = gate(oos_stage, args, stage_name="validation")
            decision = "composite_candidate_requires_forward_observer" if oos_gate["pass"] else "reject_oos_gate_failed"

    out_prefix = resolve_path(args.out_prefix)
    trades: list[dict[str, Any]] = []
    for stage_name, stage in (("train", train_stage), ("validation", validation_stage), ("oos", oos_stage)):
        if not stage:
            continue
        for trade in stage["trades"]:
            row = trade_to_dict(trade)
            row["stage"] = stage_name
            trades.append(row)

    report = {
        "generated_at": now_iso(),
        "tool": "tools/composite_liquidity_derivatives_nested_holdout.py",
        "boundary": {
            "research_only": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "opens_oos_only_after_train_validation_pass": True,
        },
        "data": {
            "cache_dir": portable(cache),
            "futures_path": portable(futures_path),
            "spot_path": portable(spot_path),
            "oi_path": portable(oi_path),
            "futures_bars": len(futures_raw),
            "matched_rows": len(futures_bars),
            "first": futures_bars[0].ts,
            "last": futures_bars[-1].ts,
        },
        "search": {
            "configs_tested": len(configs),
            "train_qualified": len(train_candidates),
            "validation_qualified": validation_qualified,
        },
        "selected": {
            "strategy_id": selected_config.strategy_id,
            "config": asdict(selected_config),
        } if selected_config else None,
        "train": {k: v for k, v in (train_stage or {}).items() if k != "trades"} if train_stage else None,
        "train_gate": train_gate,
        "validation": {k: v for k, v in (validation_stage or {}).items() if k != "trades"} if validation_stage else None,
        "validation_gate": validation_gate,
        "oos": {k: v for k, v in (oos_stage or {}).items() if k != "trades"} if oos_stage else None,
        "oos_gate": oos_gate,
        "decision": decision,
        "can_trade": False,
    }

    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_csv(Path(str(out_prefix) + "_trades.csv"), trades)
    print(
        json.dumps(
            {
                "decision": decision,
                "configs_tested": len(configs),
                "train_qualified": len(train_candidates),
                "validation_qualified": validation_qualified,
                "selected": selected_config.strategy_id if selected_config else None,
                "can_trade": False,
                "json": portable(out_prefix.with_suffix(".json")),
                "md": portable(out_prefix.with_suffix(".md")),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
