#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_hardening import fold_summaries, simulate_trade, summarize_trades  # noqa: E402
from tools.range_family_validator import load_interval_payload  # noqa: E402


@dataclass(frozen=True)
class SessionConfig:
    strategy_id: str
    side: str
    opening_hours: int
    breakout_buffer_atr: float
    trend_filter: str
    min_volume_z: float
    min_close_location: float
    stop_atr: float
    take_atr: float
    max_hold_bars: int
    trade_window_end_hour: int = 15


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ema(values: list[float], length: int) -> list[float]:
    alpha = 2.0 / (length + 1.0)
    output: list[float] = []
    current = values[0]
    for value in values:
        current = alpha * value + (1.0 - alpha) * current
        output.append(current)
    return output


def build_configs() -> list[SessionConfig]:
    configs: list[SessionConfig] = []
    for opening_hours in (4, 6):
        for buffer_atr in (0.0, 0.15):
            for side in ("LONG", "SHORT"):
                for trend_filter in ("none", "ema24_aligned"):
                    for min_volume_z in (0.0, 0.5):
                        for stop_atr in (1.0, 1.5):
                            for take_atr in (1.5, 2.0, 3.0):
                                for hold in (6, 12):
                                    strategy_id = (
                                        f"session_orb_1h_{side.lower()}_or{opening_hours}_buf{buffer_atr:g}_"
                                        f"{trend_filter}_vz{min_volume_z:g}_sl{stop_atr:g}_tp{take_atr:g}_h{hold}"
                                    )
                                    configs.append(
                                        SessionConfig(
                                            strategy_id=strategy_id,
                                            side=side,
                                            opening_hours=opening_hours,
                                            breakout_buffer_atr=buffer_atr,
                                            trend_filter=trend_filter,
                                            min_volume_z=min_volume_z,
                                            min_close_location=0.65,
                                            stop_atr=stop_atr,
                                            take_atr=take_atr,
                                            max_hold_bars=hold,
                                        )
                                    )
    return configs


def group_day_indices(bars: list[Any]) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for index, bar in enumerate(bars):
        day = parse_ts(str(bar.ts)).date().isoformat()
        grouped.setdefault(day, []).append(index)
    return grouped


def generate_signals(
    config: SessionConfig,
    bars: list[Any],
    features: list[dict[str, Any]],
    ema24: list[float],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for indices in group_day_indices(bars).values():
        by_hour = {parse_ts(str(bars[index].ts)).hour: index for index in indices}
        opening = [by_hour.get(hour) for hour in range(config.opening_hours)]
        if any(index is None for index in opening):
            continue
        opening_indices = [int(index) for index in opening if index is not None]
        opening_high = max(bars[index].high for index in opening_indices)
        opening_low = min(bars[index].low for index in opening_indices)
        candidates = [
            index
            for hour, index in sorted(by_hour.items())
            if config.opening_hours <= hour <= config.trade_window_end_hour
        ]
        for index in candidates:
            if index + config.max_hold_bars + 1 >= len(bars):
                continue
            if config.trend_filter == "ema24_aligned" and index < 6:
                continue
            atr = features[index].get("atr")
            volume_z = features[index].get("volume_z")
            if not isinstance(atr, (int, float)) or atr <= 0 or not isinstance(volume_z, (int, float)):
                continue
            if float(volume_z) < config.min_volume_z:
                continue
            bar = bars[index]
            location = (bar.close - bar.low) / max(bar.high - bar.low, 1e-12)
            if config.side == "LONG":
                breakout = bar.close > opening_high + config.breakout_buffer_atr * float(atr)
                accepted = location >= config.min_close_location
                trend_ok = config.trend_filter == "none" or (bar.close > ema24[index] and ema24[index] > ema24[index - 6])
            else:
                breakout = bar.close < opening_low - config.breakout_buffer_atr * float(atr)
                accepted = location <= 1.0 - config.min_close_location
                trend_ok = config.trend_filter == "none" or (bar.close < ema24[index] and ema24[index] < ema24[index - 6])
            if breakout and accepted and trend_ok:
                signals.append(
                    {
                        "bar_index": index,
                        "side_hint": config.side,
                        "atr": float(atr),
                        "reason": "utc_opening_range_breakout",
                    }
                )
                break
    return signals


def replay(config: SessionConfig, bars: list[Any], signals: list[dict[str, Any]], cost_bps: float) -> list[Any]:
    trades = []
    last_exit_index = -1
    bar_index_by_ts = {str(bar.ts): index for index, bar in enumerate(bars)}
    for signal in sorted(signals, key=lambda row: int(row["bar_index"])):
        signal_index = int(signal["bar_index"])
        if signal_index <= last_exit_index:
            continue
        trade = simulate_trade(
            dataset_id="session_opening_range_BTCUSDT_1h",
            strategy_id=config.strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=config.stop_atr,
            take_atr=config.take_atr,
            max_hold_bars=config.max_hold_bars,
            cost_bps_per_side=cost_bps,
        )
        if trade is None:
            continue
        trades.append(trade)
        last_exit_index = bar_index_by_ts.get(str(trade.exit_ts), signal_index)
    return trades


def signal_key(config: SessionConfig) -> tuple[Any, ...]:
    return (
        config.side,
        config.opening_hours,
        config.breakout_buffer_atr,
        config.trend_filter,
        config.min_volume_z,
        config.min_close_location,
        config.trade_window_end_hour,
    )


def bootstrap_positive_probability(values: list[float], iterations: int = 1_000, seed: int = 20260623) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(iterations):
        mean = statistics.mean(rng.choice(values) for _ in values)
        positive += int(mean > 0.0)
    return round(positive / iterations, 6)


def window_summary(config: SessionConfig, bars: list[Any], signals: list[dict[str, Any]], cost_bps: float, stress_extra: float) -> dict[str, Any]:
    base_trades = replay(config, bars, signals, cost_bps)
    stress_trades = replay(config, bars, signals, cost_bps + stress_extra)
    summary = summarize_trades(base_trades)
    folds = fold_summaries(base_trades, 4)
    stable_folds = sum(1 for row in folds if row.get("stable"))
    stress_summary = summarize_trades(stress_trades)
    bootstrap_eligible = bool(
        int(summary.get("trades") or 0) >= 100
        and float(summary.get("expectancy_r") or -999.0) >= 0.08
        and stable_folds >= 3
        and float(summary.get("max_drawdown_r") or -999.0) >= -15.0
        and float(stress_summary.get("expectancy_r") or -999.0) > 0.0
    )
    return {
        "signals": len(signals),
        "summary": summary,
        "stable_folds": stable_folds,
        "folds": folds,
        "bootstrap_probability_expectancy_gt_0": (
            bootstrap_positive_probability([trade.r_net for trade in base_trades]) if bootstrap_eligible else None
        ),
        "cost_stress": {"extra_bps_per_side": stress_extra, "summary": stress_summary},
        "trades": [asdict(trade) for trade in base_trades],
    }


def train_gate(window: dict[str, Any]) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= 100,
        "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= 0.08,
        "min_stable_folds": int(window.get("stable_folds") or 0) >= 3,
        "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -15.0,
        "bootstrap_probability": float(window.get("bootstrap_probability_expectancy_gt_0") or 0.0) >= 0.90,
        "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def validation_gate(window: dict[str, Any]) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= 30,
        "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= 0.05,
        "min_stable_folds": int(window.get("stable_folds") or 0) >= 2,
        "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -8.0,
        "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def rank_candidate(row: dict[str, Any]) -> tuple[float, int, float, int]:
    summary = row["train"]["summary"]
    stress = row["train"]["cost_stress"]["summary"]
    trades = int(summary.get("trades") or 0)
    return (
        float(stress.get("expectancy_r") or -999.0) * math.sqrt(max(1, trades)),
        int(row["train"].get("stable_folds") or 0),
        float(summary.get("expectancy_r") or -999.0),
        trades,
    )


def render_markdown(report: dict[str, Any]) -> str:
    selected = report.get("selected_on_train")
    lines = [
        "# Session Opening Range Nested Holdout",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "- New independent BTCUSDT 1H UTC opening-range family.",
        "- Train ends at 2024-01-01; validation is calendar 2024.",
        "- OOS from 2025 is opened only after train and validation gates pass.",
        f"- Configs tested on train: `{report['search']['configs_tested']}`.",
        f"- Train-qualified: `{report['search']['train_qualified']}`.",
        f"- Decision: `{report['decision']}`.",
    ]
    if selected:
        lines.extend(
            [
                f"- Frozen train candidate: `{selected['config']['strategy_id']}`.",
                f"- Train: `{selected['train']['summary']['trades']}` trades, `{selected['train']['summary']['expectancy_r']}`R.",
                f"- Validation: `{report['validation']['summary']['trades']}` trades, `{report['validation']['summary']['expectancy_r']}`R.",
            ]
        )
    elif report.get("top_train_results_regardless_of_gate"):
        best = report["top_train_results_regardless_of_gate"][0]
        lines.extend(
            [
                f"- Best rejected train result: `{best['config']['strategy_id']}`.",
                f"- Best rejected train metrics: `{best['train']['summary']['trades']}` trades, `{best['train']['summary']['expectancy_r']}`R; stress `{best['train']['cost_stress']['summary']['expectancy_r']}`R.",
                f"- Failed checks: `{[name for name, passed in best['train_gate']['checks'].items() if not passed]}`.",
            ]
        )
    lines.extend(["- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train/validation/untouched-OOS research for BTC 1H UTC opening-range breakout")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--cost-bps-per-side", type=float, default=7.0)
    parser.add_argument("--cost-stress-extra-bps", type=float, default=10.0)
    parser.add_argument("--out-prefix", default="docs/SESSION_OPENING_RANGE_NESTED_HOLDOUT_2026-06-23")
    parser.add_argument("--lock-path", default="configs/SESSION_OPENING_RANGE_RESEARCH_LOCK.json")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    bars, features, _ = load_interval_payload(cache, "1h", 12, 12)
    ema24 = ema([bar.close for bar in bars], 24)
    train_end = parse_ts(args.train_end)
    validation_end = parse_ts(args.validation_end)
    configs = build_configs()
    candidates: list[dict[str, Any]] = []
    signal_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for config in configs:
        key = signal_key(config)
        if key not in signal_cache:
            signal_cache[key] = generate_signals(config, bars, features, ema24)
        signals = signal_cache[key]
        train_signals = [row for row in signals if parse_ts(str(bars[int(row["bar_index"])].ts)) < train_end]
        train = window_summary(config, bars, train_signals, args.cost_bps_per_side, args.cost_stress_extra_bps)
        gate = train_gate(train)
        candidates.append({"config": asdict(config), "train": train, "train_gate": gate, "_signals": signals})
    qualified = [row for row in candidates if row["train_gate"]["pass"]]
    qualified.sort(key=rank_candidate, reverse=True)
    ranked_all = sorted(candidates, key=rank_candidate, reverse=True)
    failed_checks = Counter(
        name
        for row in candidates
        for name, passed in row["train_gate"]["checks"].items()
        if not passed
    )
    selected = qualified[0] if qualified else None
    validation = None
    validation_result = None
    oos = None
    oos_gate = None
    oos_opened = False
    if selected:
        config = SessionConfig(**selected["config"])
        validation_signals = [
            row
            for row in selected["_signals"]
            if train_end <= parse_ts(str(bars[int(row["bar_index"])].ts)) < validation_end
        ]
        validation = window_summary(config, bars, validation_signals, args.cost_bps_per_side, args.cost_stress_extra_bps)
        validation_result = validation_gate(validation)
        if validation_result["pass"]:
            oos_signals = [
                row for row in selected["_signals"] if parse_ts(str(bars[int(row["bar_index"])].ts)) >= validation_end
            ]
            oos = window_summary(config, bars, oos_signals, args.cost_bps_per_side, args.cost_stress_extra_bps)
            oos_gate = validation_gate(oos)
            oos_opened = True
    if selected is None:
        decision = "reject_no_train_qualified_session_candidate"
    elif not validation_result or not validation_result["pass"]:
        decision = "reject_validation_gate_failed_oos_unopened"
    elif oos_gate and oos_gate["pass"]:
        decision = "session_candidate_requires_forward_observer_review"
    else:
        decision = "reject_oos_gate_failed"

    def compact_window(window: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(window, dict):
            return None
        compact = dict(window)
        trades = compact.pop("trades", [])
        compact["sample_trades"] = trades[:3] if isinstance(trades, list) else []
        return compact

    def public(row: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in row.items() if not key.startswith("_")}
        result["train"] = compact_window(row.get("train"))
        return result

    report = {
        "generated_at": now_iso(),
        "family": "SESSION_OPENING_RANGE_BREAKOUT_1H",
        "method": "train_search_then_calendar_validation_then_conditionally_open_untouched_oos",
        "data": {
            "cache_dir": rel(cache),
            "bars": len(bars),
            "first": bars[0].ts,
            "last": bars[-1].ts,
            "train_end": args.train_end,
            "validation_end": args.validation_end,
        },
        "search": {
            "configs_tested": len(configs),
            "unique_signal_sets": len(signal_cache),
            "train_qualified": len(qualified),
            "failed_check_counts": dict(sorted(failed_checks.items())),
            "oos_used_for_selection": False,
        },
        "gates": {
            "train": {"min_trades": 100, "min_expectancy_r": 0.08, "stable_folds": 3, "bootstrap_probability": 0.90, "cost_stress_positive": True},
            "validation": {"min_trades": 30, "min_expectancy_r": 0.05, "stable_folds": 2, "cost_stress_positive": True},
        },
        "top_train_candidates": [public(row) for row in qualified[:10]],
        "top_train_results_regardless_of_gate": [public(row) for row in ranked_all[:10]],
        "selected_on_train": public(selected) if selected else None,
        "validation": compact_window(validation),
        "validation_gate": validation_result,
        "oos_opened": oos_opened,
        "oos": compact_window(oos),
        "oos_gate": oos_gate,
        "runtime_boundary": {"research_only": True, "changes_active_families": False, "sends_orders": False, "can_trade": False},
        "decision": decision,
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    lock = {
        "schema_version": 1,
        "family": report["family"],
        "enabled": False,
        "status": decision,
        "selected_on_train": selected["config"] if selected else None,
        "validation_gate": validation_result,
        "oos_opened": oos_opened,
        "source_report": rel(out.with_suffix(".json")),
        "boundaries": {"observer_allowed": False, "paper_execution_allowed": False, "live_execution_allowed": False, "allow_orders": False, "can_trade": False},
        "can_trade": False,
    }
    lock_path = resolve_path(args.lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"decision": decision, "tested": len(configs), "train_qualified": len(qualified), "selected": selected["config"]["strategy_id"] if selected else None, "validation_pass": validation_result["pass"] if validation_result else False, "oos_opened": oos_opened, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
