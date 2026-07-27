#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DispersionConfig:
    lookback_hours: int
    entry_z: float
    exit_z: float
    min_abs_basis_bps: float
    max_hold_hours: int

    @property
    def config_id(self) -> str:
        return (
            f"disp_z{self.lookback_hours}_e{self.entry_z:g}_x{self.exit_z:g}_"
            f"basis{self.min_abs_basis_bps:g}_h{self.max_hold_hours}"
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_float(value: str | None, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


def parse_list(value: str, caster: Any) -> list[Any]:
    return [caster(item.strip()) for item in value.split(",") if item.strip()]


def load_panel(path: Path, symbols: list[str]) -> list[dict[str, Any]]:
    required = {
        "symbol",
        "time",
        "time_ms",
        "spot_open",
        "spot_close",
        "perp_open",
        "perp_close",
        "basis_close_bps",
        "funding_event_bps",
    }
    grouped: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"basis panel missing columns: {sorted(missing)}")
        for raw in reader:
            symbol = raw["symbol"].upper()
            if symbol not in symbols:
                continue
            time_ms = int(raw["time_ms"])
            bucket = grouped.setdefault(time_ms, {"time": raw["time"], "time_ms": time_ms, "symbols": {}})
            bucket["symbols"][symbol] = {
                "spot_open": parse_float(raw["spot_open"]),
                "spot_close": parse_float(raw["spot_close"]),
                "perp_open": parse_float(raw["perp_open"]),
                "perp_close": parse_float(raw["perp_close"]),
                "basis_close_bps": parse_float(raw["basis_close_bps"]),
                "funding_event_bps": parse_float(raw["funding_event_bps"], 0.0),
            }
    rows: list[dict[str, Any]] = []
    wanted = set(symbols)
    for time_ms in sorted(grouped):
        bucket = grouped[time_ms]
        if set(bucket["symbols"]) != wanted:
            continue
        basis_values = [bucket["symbols"][symbol]["basis_close_bps"] for symbol in symbols]
        median_basis = statistics.median(basis_values)
        bucket["median_basis_close_bps"] = median_basis
        bucket["time_dt"] = parse_time(bucket["time"])
        for symbol in symbols:
            bucket["symbols"][symbol]["relative_basis_bps"] = (
                bucket["symbols"][symbol]["basis_close_bps"] - median_basis
            )
        rows.append(bucket)
    if not rows:
        raise ValueError("basis panel produced zero complete aligned rows")
    return rows


def split_index(rows: list[dict[str, Any]], cutoff: str) -> int:
    cutoff_dt = parse_time(cutoff)
    for index, row in enumerate(rows):
        if row["time_dt"] >= cutoff_dt:
            return index
    return len(rows)


def build_configs(args: argparse.Namespace) -> list[DispersionConfig]:
    configs: list[DispersionConfig] = []
    for lookback in parse_list(args.lookback_hours, int):
        for entry_z in parse_list(args.entry_z, float):
            for exit_z in parse_list(args.exit_z, float):
                for min_basis in parse_list(args.min_abs_basis_bps, float):
                    for max_hold in parse_list(args.max_hold_hours, int):
                        if exit_z >= entry_z:
                            continue
                        configs.append(DispersionConfig(lookback, entry_z, exit_z, min_basis, max_hold))
    return configs


def rolling_relative_z(
    rows: list[dict[str, Any]],
    symbols: list[str],
    window: int,
) -> dict[str, list[float | None]]:
    min_history = max(48, window // 2)
    output = {symbol: [None] * len(rows) for symbol in symbols}
    for symbol in symbols:
        rel = [float(row["symbols"][symbol]["relative_basis_bps"]) for row in rows]
        prefix_sum = [0.0]
        prefix_sum_sq = [0.0]
        for value in rel:
            prefix_sum.append(prefix_sum[-1] + value)
            prefix_sum_sq.append(prefix_sum_sq[-1] + value * value)
        for index, value in enumerate(rel):
            start = max(0, index - window)
            count = index - start
            if count < min_history:
                continue
            total = prefix_sum[index] - prefix_sum[start]
            total_sq = prefix_sum_sq[index] - prefix_sum_sq[start]
            avg = total / count
            variance = max(0.0, (total_sq / count) - avg * avg)
            sigma = math.sqrt(variance)
            if sigma <= 1e-12:
                continue
            output[symbol][index] = (value - avg) / sigma
    return output


def funding_sum(rows: list[dict[str, Any]], symbol: str, start: int, end: int) -> float:
    # Positive Binance perp funding means longs pay shorts, so a short-perp hedge receives it.
    return sum(float(rows[index]["symbols"][symbol]["funding_event_bps"]) for index in range(start, max(start, end)))


def simulate_stage(
    config: DispersionConfig,
    rows: list[dict[str, Any]],
    symbols: list[str],
    z_values: dict[str, list[float | None]],
    *,
    start_index: int,
    end_index: int,
    fee_bps: float,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    index = max(0, start_index)
    end = min(end_index, len(rows))
    while index < end - 2:
        candidates: list[tuple[float, str]] = []
        for symbol in symbols:
            z = z_values[symbol][index]
            basis = float(rows[index]["symbols"][symbol]["basis_close_bps"])
            if z is None:
                continue
            if z >= config.entry_z and basis >= config.min_abs_basis_bps:
                candidates.append((float(z), symbol))
        if not candidates:
            index += 1
            continue
        _, symbol = max(candidates, key=lambda item: item[0])
        signal_index = index
        entry_index = signal_index + 1
        exit_signal_index = min(entry_index + config.max_hold_hours - 1, end - 2)
        exit_reason = "max_hold"
        for probe in range(entry_index, min(entry_index + config.max_hold_hours, end - 1)):
            z = z_values[symbol][probe]
            if z is not None and z <= config.exit_z:
                exit_signal_index = probe
                exit_reason = "relative_basis_mean_reverted"
                break
        exit_index = min(exit_signal_index + 1, end - 1)
        entry = rows[entry_index]["symbols"][symbol]
        exit_bar = rows[exit_index]["symbols"][symbol]
        spot_return_bps = (float(exit_bar["spot_open"]) / float(entry["spot_open"]) - 1.0) * 10_000.0
        perp_short_return_bps = (float(entry["perp_open"]) - float(exit_bar["perp_open"])) / float(entry["perp_open"]) * 10_000.0
        funding_bps = funding_sum(rows, symbol, entry_index, exit_index)
        round_trip_cost_bps = (fee_bps + slippage_bps) * 4.0
        net_bps = spot_return_bps + perp_short_return_bps + funding_bps - round_trip_cost_bps
        trades.append(
            {
                "symbol": symbol,
                "signal_time": rows[signal_index]["time"],
                "entry_time": rows[entry_index]["time"],
                "exit_time": rows[exit_index]["time"],
                "exit_reason": exit_reason,
                "hold_hours": exit_index - entry_index,
                "entry_relative_z": round(float(z_values[symbol][signal_index] or 0.0), 6),
                "entry_basis_bps": round(float(rows[signal_index]["symbols"][symbol]["basis_close_bps"]), 6),
                "entry_relative_basis_bps": round(float(rows[signal_index]["symbols"][symbol]["relative_basis_bps"]), 6),
                "spot_return_bps": round(spot_return_bps, 6),
                "perp_short_return_bps": round(perp_short_return_bps, 6),
                "funding_bps": round(funding_bps, 6),
                "round_trip_cost_bps": round(round_trip_cost_bps, 6),
                "net_return_bps": round(net_bps, 6),
            }
        )
        index = exit_index + 1
    return trades


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(trade["net_return_bps"]) for trade in trades]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    if not values:
        return {
            "trades": 0,
            "mean_net_bps": None,
            "median_net_bps": None,
            "positive_pct": None,
            "total_net_bps": 0.0,
            "max_drawdown_bps": 0.0,
        }
    return {
        "trades": len(values),
        "mean_net_bps": round(statistics.mean(values), 6),
        "median_net_bps": round(statistics.median(values), 6),
        "positive_pct": round(sum(1 for value in values if value > 0.0) / len(values) * 100.0, 6),
        "total_net_bps": round(sum(values), 6),
        "max_drawdown_bps": round(max_drawdown, 6),
    }


def positive_folds(trades: list[dict[str, Any]], folds: int) -> int:
    if not trades:
        return 0
    ordered = sorted(trades, key=lambda item: (item["entry_time"], item["symbol"]))
    count = 0
    for fold in range(folds):
        start = round(len(ordered) * fold / folds)
        end = round(len(ordered) * (fold + 1) / folds)
        chunk = ordered[start:end]
        if len(chunk) >= 3 and statistics.mean(float(item["net_return_bps"]) for item in chunk) > 0.0:
            count += 1
    return count


def bootstrap_positive_probability(values: list[float], iterations: int, seed: int) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(iterations):
        positive += int(statistics.mean(rng.choice(values) for _ in values) > 0.0)
    return round(positive / iterations, 6)


def evaluate_stage(
    config: DispersionConfig,
    rows: list[dict[str, Any]],
    symbols: list[str],
    z_cache: dict[int, dict[str, list[float | None]]],
    *,
    start_index: int,
    end_index: int,
    fee_bps: float,
    slippage_bps: float,
    stress_extra_bps: float,
    folds: int,
    bootstrap_iterations: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    z_values = z_cache[config.lookback_hours]
    trades = simulate_stage(
        config,
        rows,
        symbols,
        z_values,
        start_index=start_index,
        end_index=end_index,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
    stress_trades = simulate_stage(
        config,
        rows,
        symbols,
        z_values,
        start_index=start_index,
        end_index=end_index,
        fee_bps=fee_bps + stress_extra_bps,
        slippage_bps=slippage_bps,
    )
    summary = summarize(trades)
    stress_summary = summarize(stress_trades)
    cheap_for_bootstrap = int(summary["trades"]) >= 10
    return {
        "summary": summary,
        "positive_folds": positive_folds(trades, folds),
        "bootstrap_probability_mean_gt_0": (
            bootstrap_positive_probability(
                [float(item["net_return_bps"]) for item in trades],
                iterations=bootstrap_iterations,
                seed=bootstrap_seed,
            )
            if cheap_for_bootstrap
            else None
        ),
        "cost_stress": {
            "extra_fee_bps_per_leg_side": stress_extra_bps,
            "summary": stress_summary,
        },
        "by_symbol": {
            symbol: summarize([trade for trade in trades if trade["symbol"] == symbol]) for symbol in symbols
        },
        "sample_trades": sorted(trades, key=lambda item: (item["entry_time"], item["symbol"]))[:5],
    }


def gate_failures(stage: str, evaluation: dict[str, Any], args: argparse.Namespace) -> list[str]:
    summary = evaluation["summary"]
    stress = evaluation["cost_stress"]["summary"]
    failures: list[str] = []
    if stage == "train":
        if int(summary["trades"]) < args.min_train_trades:
            failures.append("min_trades")
        if (summary["mean_net_bps"] is None) or float(summary["mean_net_bps"]) < args.min_train_mean_bps:
            failures.append("min_mean_net_bps")
        if (summary["positive_pct"] is None) or float(summary["positive_pct"]) < args.min_train_positive_pct:
            failures.append("min_positive_pct")
        if float(summary["max_drawdown_bps"]) < args.max_train_drawdown_bps:
            failures.append("max_drawdown_bps")
        if int(evaluation["positive_folds"]) < args.min_train_positive_folds:
            failures.append("min_positive_folds")
        prob = evaluation["bootstrap_probability_mean_gt_0"]
        if prob is None or float(prob) < args.min_train_bootstrap_prob:
            failures.append("min_bootstrap_probability_mean_gt_0")
        if (stress["mean_net_bps"] is None) or float(stress["mean_net_bps"]) <= 0.0:
            failures.append("cost_stress_positive")
        return failures
    if stage == "validation":
        if int(summary["trades"]) < args.min_validation_trades:
            failures.append("min_trades")
        if (summary["mean_net_bps"] is None) or float(summary["mean_net_bps"]) < args.min_validation_mean_bps:
            failures.append("min_mean_net_bps")
        if (summary["positive_pct"] is None) or float(summary["positive_pct"]) < args.min_validation_positive_pct:
            failures.append("min_positive_pct")
        if float(summary["max_drawdown_bps"]) < args.max_validation_drawdown_bps:
            failures.append("max_drawdown_bps")
        if int(evaluation["positive_folds"]) < args.min_validation_positive_folds:
            failures.append("min_positive_folds")
        if (stress["mean_net_bps"] is None) or float(stress["mean_net_bps"]) <= 0.0:
            failures.append("cost_stress_positive")
        return failures
    if stage == "oos":
        if int(summary["trades"]) < args.min_oos_trades:
            failures.append("min_trades")
        if (summary["mean_net_bps"] is None) or float(summary["mean_net_bps"]) < args.min_oos_mean_bps:
            failures.append("min_mean_net_bps")
        if (summary["positive_pct"] is None) or float(summary["positive_pct"]) < args.min_oos_positive_pct:
            failures.append("min_positive_pct")
        if float(summary["max_drawdown_bps"]) < args.max_oos_drawdown_bps:
            failures.append("max_drawdown_bps")
        if int(evaluation["positive_folds"]) < args.min_oos_positive_folds:
            failures.append("min_positive_folds")
    return failures


def train_rank(evaluation: dict[str, Any]) -> tuple[float, float, int]:
    summary = evaluation["summary"]
    return (
        float(summary["mean_net_bps"] or -999_999.0),
        float(summary["positive_pct"] or 0.0),
        int(summary["trades"]),
    )


def render_markdown(report: dict[str, Any]) -> str:
    selected = report.get("selected_config") or {}
    train = report["stages"].get("train", {})
    validation = report["stages"].get("validation", {})
    oos = report["stages"].get("oos", {})
    lines = [
        "# Basis Dispersion Reversion Multi-Symbol Nested Holdout",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Prereg lock: `{report['pre_registration']['lock_file']}`",
        f"- Selected config: `{selected.get('config_id')}`",
        "",
        "## Data",
        "",
        f"- Panel: `{report['data']['panel']}`",
        f"- Symbols: `{', '.join(report['data']['symbols'])}`",
        f"- Complete aligned rows: `{report['data']['complete_aligned_rows']}`",
        f"- First/last: `{report['data']['first']}` / `{report['data']['last']}`",
        "",
        "## Stage Results",
        "",
        "| Stage | Status | Trades | Mean bps | Positive % | Drawdown bps | Failures |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for name, stage in (("train", train), ("validation", validation), ("oos", oos)):
        if not stage or "evaluation" not in stage:
            lines.append(f"| {name} | `not_run` |  |  |  |  |  |")
            continue
        summary = stage["evaluation"]["summary"]
        lines.append(
            f"| {name} | `{stage['status']}` | `{summary['trades']}` | `{summary['mean_net_bps']}` | "
            f"`{summary['positive_pct']}` | `{summary['max_drawdown_bps']}` | `{', '.join(stage['failures']) or 'none'}` |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is a preregistered research-only test.",
            "- OOS is intentionally unopened when validation fails.",
            "- Direction is positive relative basis only: long spot, short perp.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Preregistered multi-symbol basis dispersion reversion nested holdout")
    parser.add_argument("--panel", default="data/research/basis_multi_symbol/1h_basis_panel.csv")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--lock-file", default="configs/BASIS_DISPERSION_REVERSION_RESEARCH_LOCK.json")
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--lookback-hours", default="168,336,720")
    parser.add_argument("--entry-z", default="1.5,2,2.5")
    parser.add_argument("--exit-z", default="0.25,0.5,1")
    parser.add_argument("--min-abs-basis-bps", default="5,10,20")
    parser.add_argument("--max-hold-hours", default="12,24,48,72")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-extra-bps", type=float, default=10.0)
    parser.add_argument("--bootstrap-iterations", type=int, default=500)
    parser.add_argument("--bootstrap-seed", type=int, default=20260630)
    parser.add_argument("--min-train-trades", type=int, default=40)
    parser.add_argument("--min-train-mean-bps", type=float, default=5.0)
    parser.add_argument("--min-train-positive-pct", type=float, default=55.0)
    parser.add_argument("--max-train-drawdown-bps", type=float, default=-200.0)
    parser.add_argument("--min-train-positive-folds", type=int, default=3)
    parser.add_argument("--min-train-bootstrap-prob", type=float, default=0.8)
    parser.add_argument("--min-validation-trades", type=int, default=15)
    parser.add_argument("--min-validation-mean-bps", type=float, default=0.0)
    parser.add_argument("--min-validation-positive-pct", type=float, default=50.0)
    parser.add_argument("--max-validation-drawdown-bps", type=float, default=-200.0)
    parser.add_argument("--min-validation-positive-folds", type=int, default=2)
    parser.add_argument("--min-oos-trades", type=int, default=20)
    parser.add_argument("--min-oos-mean-bps", type=float, default=0.0)
    parser.add_argument("--min-oos-positive-pct", type=float, default=50.0)
    parser.add_argument("--max-oos-drawdown-bps", type=float, default=-200.0)
    parser.add_argument("--min-oos-positive-folds", type=int, default=2)
    parser.add_argument("--out-prefix", default="docs/BASIS_DISPERSION_REVERSION_MULTI_SYMBOL_NESTED_HOLDOUT_2026-06-30")
    args = parser.parse_args()

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    panel = resolve_path(args.panel)
    lock_file = resolve_path(args.lock_file)
    rows = load_panel(panel, symbols)
    train_end = split_index(rows, args.train_end)
    validation_end = split_index(rows, args.validation_end)
    configs = build_configs(args)
    lookbacks = sorted({config.lookback_hours for config in configs})
    z_cache = {lookback: rolling_relative_z(rows, symbols, lookback) for lookback in lookbacks}

    train_results: list[dict[str, Any]] = []
    train_qualified: list[dict[str, Any]] = []
    for config in configs:
        evaluation = evaluate_stage(
            config,
            rows,
            symbols,
            z_cache,
            start_index=0,
            end_index=train_end,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps,
            folds=4,
            bootstrap_iterations=args.bootstrap_iterations,
            bootstrap_seed=args.bootstrap_seed,
        )
        failures = gate_failures("train", evaluation, args)
        item = {
            "config": asdict(config) | {"config_id": config.config_id},
            "evaluation": evaluation,
            "failures": failures,
            "status": "pass" if not failures else "fail",
        }
        train_results.append(item)
        if not failures:
            train_qualified.append(item)

    stages: dict[str, Any] = {}
    selected_config: DispersionConfig | None = None
    if train_qualified:
        selected_train = max(train_qualified, key=lambda item: train_rank(item["evaluation"]))
        selected_config = DispersionConfig(**{k: selected_train["config"][k] for k in asdict(DispersionConfig(1, 1, 0, 0, 1))})
        stages["train"] = selected_train
        validation_eval = evaluate_stage(
            selected_config,
            rows,
            symbols,
            z_cache,
            start_index=train_end,
            end_index=validation_end,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps,
            folds=3,
            bootstrap_iterations=args.bootstrap_iterations,
            bootstrap_seed=args.bootstrap_seed + 1,
        )
        validation_failures = gate_failures("validation", validation_eval, args)
        stages["validation"] = {
            "config": asdict(selected_config) | {"config_id": selected_config.config_id},
            "evaluation": validation_eval,
            "failures": validation_failures,
            "status": "pass" if not validation_failures else "fail",
        }
        if not validation_failures:
            oos_eval = evaluate_stage(
                selected_config,
                rows,
                symbols,
                z_cache,
                start_index=validation_end,
                end_index=len(rows),
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                stress_extra_bps=args.stress_extra_bps,
                folds=3,
                bootstrap_iterations=args.bootstrap_iterations,
                bootstrap_seed=args.bootstrap_seed + 2,
            )
            oos_failures = gate_failures("oos", oos_eval, args)
            stages["oos"] = {
                "config": asdict(selected_config) | {"config_id": selected_config.config_id},
                "evaluation": oos_eval,
                "failures": oos_failures,
                "status": "pass" if not oos_failures else "fail",
            }
            decision = "research_candidate_oos_passed_forward_shadow_required" if not oos_failures else "reject_oos_gate_failed"
        else:
            stages["oos"] = {"status": "not_run_validation_failed", "failures": ["validation_gate_failed"]}
            decision = "reject_validation_gate_failed_oos_unopened"
    else:
        decision = "reject_train_gate_failed_validation_and_oos_unopened"
        if train_results:
            best_failed_train = max(train_results, key=lambda item: train_rank(item["evaluation"]))
            best_failed_train["failures"] = ["no_train_qualified_config"] + best_failed_train["failures"]
            best_failed_train["status"] = "fail"
            stages["train"] = best_failed_train
        else:
            stages["train"] = {
                "status": "fail",
                "failures": ["no_train_configs_tested"],
                "evaluation": {"summary": summarize([])},
            }
        stages["validation"] = {"status": "not_run_train_failed", "failures": ["train_gate_failed"]}
        stages["oos"] = {"status": "not_run_train_failed", "failures": ["train_gate_failed"]}

    report = {
        "generated_at": now_iso(),
        "tool": "tools/basis_dispersion_reversion_multi_symbol_nested_holdout.py",
        "decision": decision,
        "can_trade": False,
        "pre_registration": {
            "lock_file": portable_path(lock_file),
            "status": "required_before_run",
            "lock_exists": lock_file.exists(),
        },
        "data": {
            "panel": portable_path(panel),
            "symbols": symbols,
            "complete_aligned_rows": len(rows),
            "first": rows[0]["time"],
            "last": rows[-1]["time"],
            "train_rows": train_end,
            "validation_rows": validation_end - train_end,
            "oos_rows": len(rows) - validation_end,
        },
        "search": {
            "configs_tested": len(configs),
            "train_qualified_configs": len(train_qualified),
            "top_train_configs": sorted(train_results, key=lambda item: train_rank(item["evaluation"]), reverse=True)[:10],
        },
        "selected_config": (asdict(selected_config) | {"config_id": selected_config.config_id}) if selected_config else None,
        "stages": stages,
        "notes": [
            "Research-only. No live trading permission.",
            "OOS remains unopened unless validation passes.",
            "Signal is positive relative basis only: long spot, short perp.",
        ],
    }

    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "out": portable_path(out.with_suffix(".json"))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
