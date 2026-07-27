#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import candle_value, find_exit  # noqa: E402
from tools.max_v11_candidate_validator import atr14_at, bootstrap_report, fold_report, load_or_fetch, summarize_trades  # noqa: E402
from tools.max_v15_state_filters import load_or_fetch_derivatives  # noqa: E402


@dataclass(frozen=True)
class ImpulseConfig:
    strategy_id: str
    hypothesis: str
    side: str
    mode: str
    displacement_atr: float
    oi_drop_pct: float
    volume_z: float
    stop_atr: float
    take_atr: float
    max_hold_bars: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def split_index(rows: list[dict[str, str]], timestamp: str) -> int:
    boundary = parse_ts(timestamp)
    for index, row in enumerate(rows):
        if parse_ts(str(row["time"])) >= boundary:
            return index
    raise ValueError(f"split after data: {timestamp}")


def finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def build_features(
    rows: list[dict[str, str]],
    derivatives: list[dict[str, str]],
    *,
    displacement_bars: int = 3,
    oi_lag: int = 3,
    volume_window: int = 100,
) -> dict[int, dict[str, Any]]:
    derivatives_by_time = {str(row.get("time")): row for row in derivatives}
    aligned = [derivatives_by_time.get(str(row.get("time"))) for row in rows]
    features: dict[int, dict[str, Any]] = {}
    warmup = max(220, displacement_bars, oi_lag, volume_window)
    for index in range(warmup, len(rows) - 1):
        current_derivative = aligned[index]
        previous_derivative = aligned[index - oi_lag]
        if not isinstance(current_derivative, dict) or not isinstance(previous_derivative, dict):
            continue
        oi = finite_float(current_derivative.get("open_interest"))
        previous_oi = finite_float(previous_derivative.get("open_interest"))
        if oi is None or previous_oi in {None, 0.0}:
            continue
        atr = atr14_at(rows, index)
        if not math.isfinite(atr) or atr <= 0:
            continue
        close = candle_value(rows[index], "close")
        prior_close = candle_value(rows[index - displacement_bars], "close")
        high = candle_value(rows[index], "high")
        low = candle_value(rows[index], "low")
        volume = candle_value(rows[index], "volume")
        prior_volumes = [candle_value(row, "volume") for row in rows[index - volume_window : index]]
        if any(not math.isfinite(value) for value in (close, prior_close, high, low, volume)):
            continue
        sigma = statistics.pstdev(prior_volumes)
        volume_z = (volume - statistics.mean(prior_volumes)) / sigma if sigma > 0 else 0.0
        candle_range = max(high - low, 1e-12)
        close_location = (close - low) / candle_range
        features[index] = {
            "signal_time": rows[index]["time"],
            "atr14": atr,
            "displacement_atr": (close - prior_close) / atr,
            "oi_delta_pct": (oi - previous_oi) / previous_oi * 100.0,
            "volume_z": volume_z,
            "close_location": close_location,
        }
    return features


def signal_matches(config: ImpulseConfig, feature: dict[str, Any]) -> bool:
    displacement = float(feature["displacement_atr"])
    if float(feature["oi_delta_pct"]) > -abs(config.oi_drop_pct):
        return False
    if float(feature["volume_z"]) < config.volume_z:
        return False
    if config.hypothesis == "reversal" and config.side == "LONG":
        return displacement <= -abs(config.displacement_atr) and (config.mode == "raw" or float(feature["close_location"]) >= 0.60)
    if config.hypothesis == "reversal" and config.side == "SHORT":
        return displacement >= abs(config.displacement_atr) and (config.mode == "raw" or float(feature["close_location"]) <= 0.40)
    if config.hypothesis == "continuation" and config.side == "LONG":
        return displacement >= abs(config.displacement_atr) and (config.mode == "raw" or float(feature["close_location"]) >= 0.60)
    if config.hypothesis == "continuation" and config.side == "SHORT":
        return displacement <= -abs(config.displacement_atr) and (config.mode == "raw" or float(feature["close_location"]) <= 0.40)
    raise ValueError(f"unsupported side: {config.side}")


def build_configs(args: argparse.Namespace) -> list[ImpulseConfig]:
    configs: list[ImpulseConfig] = []
    modes = ("raw", "reclaim") if args.hypothesis == "reversal" else ("raw", "acceptance")
    for side in ("LONG", "SHORT"):
        for mode in modes:
            for displacement in [float(item) for item in args.displacement_atr.split(",") if item.strip()]:
                for oi_drop in [float(item) for item in args.oi_drop_pct.split(",") if item.strip()]:
                    for volume_z in [float(item) for item in args.volume_z.split(",") if item.strip()]:
                        for take in [float(item) for item in args.take_atr.split(",") if item.strip()]:
                            for hold in [int(item) for item in args.max_hold_bars.split(",") if item.strip()]:
                                strategy_id = (
                                    f"liq_impulse_1h_{args.hypothesis}_{side.lower()}_{mode}_d{displacement:g}"
                                    f"_oi{oi_drop:g}_vz{volume_z:g}_sl{args.stop_atr:g}_tp{take:g}_h{hold}"
                                )
                                configs.append(
                                    ImpulseConfig(
                                        strategy_id=strategy_id,
                                        hypothesis=args.hypothesis,
                                        side=side,
                                        mode=mode,
                                        displacement_atr=displacement,
                                        oi_drop_pct=oi_drop,
                                        volume_z=volume_z,
                                        stop_atr=args.stop_atr,
                                        take_atr=take,
                                        max_hold_bars=hold,
                                    )
                                )
    return configs


def simulate_exit(
    config: ImpulseConfig,
    rows: list[dict[str, str]],
    signal_index: int,
    atr: float,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any] | None:
    entry_index = signal_index + 1
    if entry_index >= len(rows):
        return None
    raw_entry = candle_value(rows[entry_index], "open")
    slip = slippage_bps / 10_000.0
    if config.side == "LONG":
        entry = raw_entry * (1.0 + slip)
        stop = entry - config.stop_atr * atr
        take = entry + config.take_atr * atr
    else:
        entry = raw_entry * (1.0 - slip)
        stop = entry + config.stop_atr * atr
        take = entry - config.take_atr * atr
    risk = config.stop_atr * atr
    exit_index, raw_exit, reason = find_exit(
        rows,
        start_index=entry_index,
        side=config.side,
        entry=entry,
        stop=stop,
        take_profit=take,
        max_hold_bars=config.max_hold_bars,
    )
    exit_price = raw_exit * (1.0 - slip if config.side == "LONG" else 1.0 + slip)
    gross_r = (exit_price - entry) / risk if config.side == "LONG" else (entry - exit_price) / risk
    fee_r = ((entry + exit_price) * fee_bps / 10_000.0) / risk
    return {
        "entry_row": entry_index,
        "exit_row": exit_index,
        "entry": entry,
        "exit": exit_price,
        "net_r": gross_r - fee_r,
        "exit_reason": reason,
        "bars_held": max(1, exit_index - entry_index + 1),
    }


def simulate_window(
    config: ImpulseConfig,
    rows: list[dict[str, str]],
    features: dict[int, dict[str, Any]],
    *,
    start_index: int,
    end_index: int,
    fee_bps: float,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    safe_end = min(end_index, len(rows)) - config.max_hold_bars - 1
    index = max(220, start_index)
    while index < safe_end:
        feature = features.get(index)
        if feature is None or not signal_matches(config, feature):
            index += 1
            continue
        outcome = simulate_exit(config, rows[:end_index], index, float(feature["atr14"]), fee_bps, slippage_bps)
        if outcome is None or int(outcome["exit_row"]) >= end_index:
            index += 1
            continue
        trades.append(
            {
                "index": len(trades) + 1,
                "candidate_id": config.strategy_id,
                "side": config.side,
                "setup": config.strategy_id,
                "signal_row": index,
                "entry_row": outcome["entry_row"],
                "exit_row": outcome["exit_row"],
                "signal_time": feature["signal_time"],
                "entry_time": rows[outcome["entry_row"]]["time"],
                "exit_time": rows[outcome["exit_row"]]["time"],
                "entry": round(float(outcome["entry"]), 8),
                "exit": round(float(outcome["exit"]), 8),
                "net_r": round(float(outcome["net_r"]), 6),
                "exit_reason": outcome["exit_reason"],
                "bars_held": outcome["bars_held"],
                "displacement_atr": round(float(feature["displacement_atr"]), 6),
                "oi_delta_pct": round(float(feature["oi_delta_pct"]), 6),
                "volume_z": round(float(feature["volume_z"]), 6),
                "close_location": round(float(feature["close_location"]), 6),
            }
        )
        index = int(outcome["exit_row"]) + 1
    return trades


def stable_fold_count(folds: list[dict[str, Any]]) -> int:
    return sum(
        1
        for fold in folds
        if int(fold.get("trades") or 0) >= 4
        and finite_float(fold.get("expectancy_r")) is not None
        and float(fold["expectancy_r"]) > 0.0
    )


def evaluate(
    config: ImpulseConfig,
    rows: list[dict[str, str]],
    features: dict[int, dict[str, Any]],
    *,
    start_index: int,
    end_index: int,
    folds: int,
    fee_bps: float,
    slippage_bps: float,
    stress_extra_bps: float,
    bootstrap_iterations: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    trades = simulate_window(config, rows, features, start_index=start_index, end_index=end_index, fee_bps=fee_bps, slippage_bps=slippage_bps)
    stressed = simulate_window(config, rows, features, start_index=start_index, end_index=end_index, fee_bps=fee_bps, slippage_bps=slippage_bps + stress_extra_bps)
    fold_rows = fold_report(trades, rows_count=end_index, warmup_bars=start_index, folds=folds)
    return {
        "summary": summarize_trades(trades),
        "stable_folds": stable_fold_count(fold_rows),
        "folds": fold_rows,
        "bootstrap": bootstrap_report(trades, iterations=bootstrap_iterations, seed=bootstrap_seed) if bootstrap_iterations else {},
        "cost_stress": {"extra_slippage_bps_per_side": stress_extra_bps, "summary": summarize_trades(stressed)},
        "sample_trades": trades[:3],
    }


def gate(result: dict[str, Any], *, stage: str, args: argparse.Namespace) -> dict[str, Any]:
    summary = result["summary"]
    stress = result["cost_stress"]["summary"]
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= getattr(args, f"min_{stage}_trades"),
        "min_expectancy_r": finite_float(summary.get("expectancy_r")) is not None
        and float(summary["expectancy_r"]) >= getattr(args, f"min_{stage}_expectancy_r"),
        "min_stable_folds": int(result.get("stable_folds") or 0) >= getattr(args, f"min_{stage}_stable_folds"),
        "max_drawdown_r": float(summary.get("max_drawdown_r") or 0.0) >= -abs(getattr(args, f"max_{stage}_drawdown_r")),
        "stress_positive": finite_float(stress.get("expectancy_r")) is not None and float(stress["expectancy_r"]) > 0.0,
    }
    if stage == "train":
        probability = finite_float((result.get("bootstrap", {}).get("expectancy_r") or {}).get("prob_gt_0"))
        checks["bootstrap_probability"] = probability is not None and probability >= args.min_train_bootstrap_prob
    return {"pass": all(checks.values()), "checks": checks}


def rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    summary = item["train"]["summary"]
    stress = item["train"]["cost_stress"]["summary"]
    trades = max(1, int(summary.get("trades") or 0))
    return (
        float(stress.get("expectancy_r") or -999.0) * math.sqrt(trades),
        int(item["train"].get("stable_folds") or 0),
        float(summary.get("expectancy_r") or -999.0),
        trades,
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Liquidation Impulse {report['hypothesis'].title()} Nested Holdout",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        f"- Event-first `{report['hypothesis']}` after price displacement, OI contraction and abnormal volume.",
        "- Signal is evaluated on a closed 1H bar; entry is the next-hour open.",
        "- Train selects one candidate, 2025 validates it, and 2026 OOS opens only after validation passes.",
        "- Costs, extra-slippage stress and same-bar conservative exit behavior are included.",
        "- No credentials, observer mutation, paper/live permission or orders.",
        "",
        "## Result",
        "",
        f"- Data: `{report['data']['first_time']}` to `{report['data']['last_time']}`.",
        f"- Derivatives timestamp coverage: `{report['data']['derivatives_coverage_pct']}%`.",
        f"- Tested / train-qualified: `{report['search']['tested']}` / `{report['search']['train_qualified']}`.",
        f"- Decision: `{report['decision']}`.",
    ]
    selected = report.get("selected_on_train")
    if selected:
        train = selected["train"]["summary"]
        validation = report["validation"]["summary"]
        lines.extend([
            f"- Selected: `{selected['strategy_id']}`.",
            f"- Train: `{train.get('trades')}` trades, `{train.get('winrate_pct')}%`, `{train.get('expectancy_r')}`R.",
            f"- Validation: `{validation.get('trades')}` trades, `{validation.get('winrate_pct')}%`, `{validation.get('expectancy_r')}`R.",
        ])
    if report.get("oos"):
        oos = report["oos"]["summary"]
        lines.append(f"- OOS: `{oos.get('trades')}` trades, `{oos.get('winrate_pct')}%`, `{oos.get('expectancy_r')}`R.")
    elif selected:
        lines.append("- Final OOS remained unopened because validation failed or was insufficient.")
    else:
        lines.append("- Validation and OOS remained unopened because train produced no qualified candidate.")
    lines.extend(["", "- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nested validation for liquidation-like BTCUSDT 1H impulse reversals")
    parser.add_argument("--hypothesis", choices=["reversal", "continuation"], default="reversal")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--train-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2026-01-01T00:00:00+00:00")
    parser.add_argument("--displacement-atr", default="1.5,2,2.5")
    parser.add_argument("--oi-drop-pct", default="0.5,1,2")
    parser.add_argument("--volume-z", default="1.5,2")
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", default="1.5,2")
    parser.add_argument("--max-hold-bars", default="8,12")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-extra-bps", type=float, default=10.0)
    parser.add_argument("--bootstrap-iterations", type=int, default=500)
    parser.add_argument("--bootstrap-seed", type=int, default=20260623)
    parser.add_argument("--min-train-trades", type=int, default=25)
    parser.add_argument("--min-train-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-train-stable-folds", type=int, default=3)
    parser.add_argument("--max-train-drawdown-r", type=float, default=12.0)
    parser.add_argument("--min-train-bootstrap-prob", type=float, default=0.80)
    parser.add_argument("--min-validation-trades", type=int, default=6)
    parser.add_argument("--min-validation-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-validation-stable-folds", type=int, default=2)
    parser.add_argument("--max-validation-drawdown-r", type=float, default=8.0)
    parser.add_argument("--min-oos-trades", type=int, default=10)
    parser.add_argument("--min-oos-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-oos-stable-folds", type=int, default=2)
    parser.add_argument("--max-oos-drawdown-r", type=float, default=8.0)
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_IMPULSE_REVERSAL_NESTED_HOLDOUT_2026-06-23")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    rows, futures_source = load_or_fetch(use_cache=True, cache_dir=cache, market="futures", symbol="BTCUSDT", interval="1h", limit=1000, pages=100)
    derivatives, derivatives_source = load_or_fetch_derivatives(use_cache=True, cache_dir=cache, symbol="BTCUSDT", interval="1h", rows=rows, limit=500, pages=100)
    features = build_features(rows, derivatives)
    train_end = split_index(rows, args.train_end)
    validation_end = split_index(rows, args.validation_end)
    configs = build_configs(args)
    rng = random.Random(args.bootstrap_seed)
    results: list[dict[str, Any]] = []
    for config in configs:
        train = evaluate(config, rows, features, start_index=220, end_index=train_end, folds=5, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, stress_extra_bps=args.stress_extra_bps, bootstrap_iterations=args.bootstrap_iterations, bootstrap_seed=rng.randrange(1, 10_000_000))
        results.append({"strategy_id": config.strategy_id, "config": asdict(config), "train": train, "train_gate": gate(train, stage="train", args=args)})
    qualified = sorted([item for item in results if item["train_gate"]["pass"]], key=rank_key, reverse=True)
    selected = qualified[0] if qualified else None
    validation = validation_gate = oos = oos_gate = None
    decision = "reject_no_train_candidate"
    if selected:
        config = ImpulseConfig(**selected["config"])
        validation = evaluate(config, rows, features, start_index=train_end, end_index=validation_end, folds=3, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, stress_extra_bps=args.stress_extra_bps, bootstrap_iterations=0, bootstrap_seed=args.bootstrap_seed)
        validation_gate = gate(validation, stage="validation", args=args)
        decision = "insufficient_validation_sample_research_only" if int(validation["summary"]["trades"]) < args.min_validation_trades else "reject_validation_gate_failed"
        if validation_gate["pass"]:
            oos = evaluate(config, rows, features, start_index=validation_end, end_index=len(rows), folds=2, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, stress_extra_bps=args.stress_extra_bps, bootstrap_iterations=0, bootstrap_seed=args.bootstrap_seed)
            oos_gate = gate(oos, stage="oos", args=args)
            decision = "pass_oos_observer_candidate_not_trade_permission" if oos_gate["pass"] else "insufficient_or_failed_oos_research_only"
    results.sort(key=rank_key, reverse=True)
    derivative_times = {str(row.get("time")) for row in derivatives}
    matched = sum(str(row.get("time")) in derivative_times for row in rows)
    report = {
        "generated_at": now_iso(),
        "hypothesis": args.hypothesis,
        "method": "train_selection_then_validation_gate_then_final_calendar_oos",
        "selection_frozen_before_validation": True,
        "validation_required_before_oos": True,
        "runtime_boundary": {"research_only": True, "sends_orders": False, "can_trade": False},
        "data": {"futures_source": futures_source, "derivatives_source": derivatives_source, "rows": len(rows), "first_time": rows[0]["time"], "last_time": rows[-1]["time"], "feature_rows": len(features), "derivatives_coverage_pct": round(matched / len(rows) * 100.0, 4)},
        "search": {"tested": len(results), "train_qualified": len(qualified)},
        "selected_on_train": selected,
        "validation": validation,
        "validation_gate": validation_gate,
        "oos": oos,
        "oos_gate": oos_gate,
        "top_train_results": results[:25],
        "decision": decision,
        "next_action": "observer_only_forward_proof" if decision.startswith("pass_oos") else "reject_or_archive_without_reusing_opened_stage",
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "tested": len(results), "train_qualified": len(qualified), "selected": selected["strategy_id"] if selected else None, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
