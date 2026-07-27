#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HOUR_MS = 3_600_000
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_hardening import Trade, fold_summaries, simulate_trade, summarize_trades  # noqa: E402
from tools.range_family_validator import load_interval_payload  # noqa: E402


@dataclass(frozen=True)
class FundingEventConfig:
    strategy_id: str
    funding_z_window_events: int
    entry_abs_z: float
    side: str
    spot_filter: str
    stop_atr: float
    take_atr: float
    max_hold_hours: int = 8
    min_abs_oi_change_pct: float = 0.5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def snapshot_provenance(cache: Path) -> dict[str, Any] | None:
    path = cache / "SNAPSHOT_MANIFEST.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return {
        "snapshot_id": payload.get("snapshot_id"),
        "profile": payload.get("profile"),
        "dataset_sha256": payload.get("dataset_sha256"),
        "manifest_path": portable_path(path),
    }


def read_funding_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                timestamp = int(row["timestamp"])
                rate = float(row["funding"])
            except (KeyError, ValueError):
                continue
            if math.isfinite(rate):
                events.append({"timestamp": timestamp, "hour_ms": timestamp - timestamp % HOUR_MS, "rate": rate})
    return sorted(events, key=lambda item: int(item["timestamp"]))


def rolling_event_z(events: list[dict[str, Any]], window: int) -> list[float | None]:
    rates = [float(event["rate"]) for event in events]
    output: list[float | None] = [None] * len(rates)
    rolling_sum = 0.0
    rolling_sq = 0.0
    for index, value in enumerate(rates):
        if index >= window:
            mean = rolling_sum / window
            variance = max(0.0, rolling_sq / window - mean * mean)
            std = math.sqrt(variance)
            if std > 1e-15:
                output[index] = (value - mean) / std
            old = rates[index - window]
            rolling_sum -= old
            rolling_sq -= old * old
        rolling_sum += value
        rolling_sq += value * value
    return output


def build_configs() -> list[FundingEventConfig]:
    configs: list[FundingEventConfig] = []
    for window in (90, 180):
        for entry_z in (1.5, 2.0, 2.5):
            for side in ("LONG_AFTER_NEGATIVE", "SHORT_AFTER_POSITIVE"):
                for spot_filter in ("none", "perp_excess_move"):
                    for stop_atr in (1.0, 1.5):
                        for take_atr in (1.5, 2.5):
                            strategy_id = (
                                f"funding_event_z{window}_e{entry_z:g}_{side.lower()}_"
                                f"{spot_filter}_sl{stop_atr:g}_tp{take_atr:g}_h8"
                            )
                            configs.append(
                                FundingEventConfig(
                                    strategy_id=strategy_id,
                                    funding_z_window_events=window,
                                    entry_abs_z=entry_z,
                                    side=side,
                                    spot_filter=spot_filter,
                                    stop_atr=stop_atr,
                                    take_atr=take_atr,
                                )
                            )
    return configs


def signal_matches(config: FundingEventConfig, rate: float, z_value: float, feature: dict[str, Any]) -> bool:
    oi_delta = feature.get("oi_delta_pct")
    if not isinstance(oi_delta, (int, float)) or abs(float(oi_delta)) < config.min_abs_oi_change_pct:
        return False
    divergence = feature.get("spot_perp_divergence_pct")
    if config.side == "SHORT_AFTER_POSITIVE":
        direction_ok = rate > 0 and z_value >= config.entry_abs_z
        spot_ok = config.spot_filter == "none" or (isinstance(divergence, (int, float)) and float(divergence) < 0)
    else:
        direction_ok = rate < 0 and z_value <= -config.entry_abs_z
        spot_ok = config.spot_filter == "none" or (isinstance(divergence, (int, float)) and float(divergence) > 0)
    return bool(direction_ok and spot_ok)


def generate_signals(
    config: FundingEventConfig,
    bars: list[Any],
    features: list[dict[str, Any]],
    events: list[dict[str, Any]],
    z_values: list[float | None],
) -> list[dict[str, Any]]:
    bar_by_hour = {int(parse_ts(str(bar.ts)).timestamp() * 1000): index for index, bar in enumerate(bars)}
    signals: list[dict[str, Any]] = []
    for event, z_value in zip(events, z_values):
        if z_value is None:
            continue
        bar_index = bar_by_hour.get(int(event["hour_ms"]))
        if bar_index is None or bar_index + config.max_hold_hours + 2 >= len(bars):
            continue
        feature = features[bar_index]
        atr = feature.get("atr")
        if not isinstance(atr, (int, float)) or float(atr) <= 0:
            continue
        if not signal_matches(config, float(event["rate"]), float(z_value), feature):
            continue
        signals.append(
            {
                "bar_index": bar_index,
                "side_hint": "SHORT" if config.side == "SHORT_AFTER_POSITIVE" else "LONG",
                "atr": float(atr),
                "funding_rate": float(event["rate"]),
                "funding_z": float(z_value),
                "oi_delta_pct": float(feature["oi_delta_pct"]),
                "spot_perp_divergence_pct": feature.get("spot_perp_divergence_pct"),
                "reason": "actual_funding_settlement_extreme",
            }
        )
    return signals


def funding_adjustment_r(
    trade: Trade,
    side: str,
    stop_atr: float,
    events: list[dict[str, Any]],
    prices_by_hour: dict[int, float],
) -> float:
    entry_ms = int(parse_ts(trade.entry_ts).timestamp() * 1000)
    exit_ms = int(parse_ts(trade.exit_ts).timestamp() * 1000)
    funding_quote = 0.0
    for event in events:
        timestamp = int(event["timestamp"])
        if timestamp <= entry_ms:
            continue
        if timestamp > exit_ms:
            break
        price = prices_by_hour.get(int(event["hour_ms"]))
        if price is None:
            continue
        payment = price * float(event["rate"])
        funding_quote += payment if side == "SHORT" else -payment
    risk_quote = trade.atr * stop_atr
    return funding_quote / risk_quote if risk_quote > 0 else 0.0


def replay(
    config: FundingEventConfig,
    bars: list[Any],
    signals: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    start_index: int,
    end_index: int,
    cost_bps: float,
) -> list[Trade]:
    prices = {int(parse_ts(str(bar.ts)).timestamp() * 1000): float(bar.close) for bar in bars}
    bar_index = {str(bar.ts): index for index, bar in enumerate(bars)}
    trades: list[Trade] = []
    last_exit = start_index - 1
    for signal in sorted(signals, key=lambda item: int(item["bar_index"])):
        index = int(signal["bar_index"])
        if index < start_index or index <= last_exit:
            continue
        if index + config.max_hold_hours + 1 >= end_index:
            continue
        trade = simulate_trade(
            dataset_id="funding_settlement_BTCUSDT_1h",
            strategy_id=config.strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=config.stop_atr,
            take_atr=config.take_atr,
            max_hold_bars=config.max_hold_hours,
            cost_bps_per_side=cost_bps,
        )
        if trade is None:
            continue
        adjustment = funding_adjustment_r(trade, trade.side, config.stop_atr, events, prices)
        trade = replace(trade, r_net=round(trade.r_net + adjustment, 6))
        trades.append(trade)
        last_exit = bar_index.get(trade.exit_ts, index)
    return trades


def bootstrap_probability(values: list[float], iterations: int = 2_000, seed: int = 20260624) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(iterations):
        positive += int(statistics.mean(rng.choice(values) for _ in values) > 0)
    return round(positive / iterations, 6)


def evaluate(
    config: FundingEventConfig,
    bars: list[Any],
    signals: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    start_index: int,
    end_index: int,
    cost_bps: float,
    stress_extra_bps: float,
    folds: int,
) -> dict[str, Any]:
    trades = replay(config, bars, signals, events, start_index=start_index, end_index=end_index, cost_bps=cost_bps)
    stressed = replay(config, bars, signals, events, start_index=start_index, end_index=end_index, cost_bps=cost_bps + stress_extra_bps)
    summary = summarize_trades(trades)
    fold_rows = fold_summaries(trades, folds)
    stable_folds = sum(1 for row in fold_rows if row.get("stable"))
    stress_summary = summarize_trades(stressed)
    bootstrap_eligible = bool(
        int(summary.get("trades") or 0) >= 80
        and float(summary.get("expectancy_r") or -999.0) >= 0.08
        and stable_folds >= 3
        and float(summary.get("max_drawdown_r") or -999.0) >= -15.0
        and float(stress_summary.get("expectancy_r") or -999.0) > 0.0
    )
    return {
        "signals": len(signals),
        "summary": summary,
        "stable_folds": stable_folds,
        "folds": fold_rows,
        "bootstrap_probability_expectancy_gt_0": (
            bootstrap_probability([trade.r_net for trade in trades]) if bootstrap_eligible else None
        ),
        "cost_stress": {"extra_bps_per_side": stress_extra_bps, "summary": stress_summary},
        "sample_trades": [asdict(trade) for trade in trades[:3]],
    }


def gate(result: dict[str, Any], stage: str) -> dict[str, Any]:
    summary = result["summary"]
    stress = result["cost_stress"]["summary"]
    if stage == "train":
        checks = {
            "min_trades": int(summary.get("trades") or 0) >= 80,
            "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= 0.08,
            "min_positive_folds": int(result.get("stable_folds") or 0) >= 3,
            "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -15.0,
            "screening_bootstrap_probability": float(result.get("bootstrap_probability_expectancy_gt_0") or 0.0) >= 0.95,
            "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0.0,
        }
    else:
        checks = {
            "min_trades": int(summary.get("trades") or 0) >= 20,
            "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= 0.05,
            "min_positive_folds": int(result.get("stable_folds") or 0) >= 2,
            "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -8.0,
            "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0.0,
        }
    return {"pass": all(checks.values()), "checks": checks}


def split_index(bars: list[Any], timestamp: str) -> int:
    boundary = parse_ts(timestamp)
    for index, bar in enumerate(bars):
        if parse_ts(str(bar.ts)) >= boundary:
            return index
    raise ValueError(f"split after data: {timestamp}")


def rank_key(item: dict[str, Any]) -> tuple[float, int, float, int]:
    summary = item["train"]["summary"]
    stress = item["train"]["cost_stress"]["summary"]
    trades = int(summary.get("trades") or 0)
    return (
        float(stress.get("expectancy_r") or -999.0) * math.sqrt(max(1, trades)),
        int(item["train"].get("stable_folds") or 0),
        float(summary.get("expectancy_r") or -999.0),
        trades,
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Funding Settlement Reversion Nested Holdout",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "- Prospective hypothesis `HYP-FUNDING-EVENT-001` was registered before code.",
        "- Signal uses actual funding settlement, next-hour entry, fixed OI confirmation and optional spot/perp non-confirmation.",
        "- Train is 2021-2023; validation is 2024; OOS from 2025 opens only after prior gates pass.",
        f"- Tested / train-qualified: `{report['search']['tested']}` / `{report['search']['train_qualified']}`.",
        f"- Decision: `{report['decision']}`.",
    ]
    selected = report.get("selected_on_train")
    if selected:
        train = selected["train"]["summary"]
        lines.append(f"- Frozen train candidate: `{selected['strategy_id']}`, `{train['trades']}` trades, `{train['expectancy_r']}`R.")
    else:
        best = report["top_train_results_regardless_of_gate"][0]
        train = best["train"]["summary"]
        lines.append(f"- Best rejected: `{best['strategy_id']}`, `{train['trades']}` trades, `{train['expectancy_r']}`R.")
        lines.append("- Validation and OOS remained unopened.")
    lines.extend(["- Registry-level Bonferroni assessment is applied by the verified runner.", "- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nested holdout for BTC reversion after actual funding settlements")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--cost-bps-per-side", type=float, default=7.0)
    parser.add_argument("--stress-extra-bps", type=float, default=10.0)
    parser.add_argument("--out-prefix", default="docs/FUNDING_SETTLEMENT_REVERSION_NESTED_HOLDOUT_2026-06-24")
    parser.add_argument("--lock-path", default="configs/FUNDING_SETTLEMENT_REVERSION_RESEARCH_LOCK.json")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    bars, features, _ = load_interval_payload(cache, "1h", 12, 12)
    events = read_funding_events(cache / "futures" / "BTCUSDT" / "funding_raw.csv")
    train_end = split_index(bars, args.train_end)
    validation_end = split_index(bars, args.validation_end)
    z_cache = {window: rolling_event_z(events, window) for window in (90, 180)}
    signal_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    candidates: list[dict[str, Any]] = []
    for config in build_configs():
        key = (config.funding_z_window_events, config.entry_abs_z, config.side, config.spot_filter)
        if key not in signal_cache:
            signal_cache[key] = generate_signals(config, bars, features, events, z_cache[config.funding_z_window_events])
        train = evaluate(
            config, bars, signal_cache[key], events,
            start_index=0, end_index=train_end,
            cost_bps=args.cost_bps_per_side, stress_extra_bps=args.stress_extra_bps, folds=4,
        )
        candidates.append({"strategy_id": config.strategy_id, "config": asdict(config), "train": train, "train_gate": gate(train, "train"), "_signals": signal_cache[key]})

    candidates.sort(key=rank_key, reverse=True)
    qualified = [item for item in candidates if item["train_gate"]["pass"]]
    selected = qualified[0] if qualified else None
    validation = validation_result = oos = oos_result = None
    oos_opened = False
    decision = "reject_no_train_qualified_funding_event_candidate"
    if selected:
        config = FundingEventConfig(**selected["config"])
        validation = evaluate(
            config, bars, selected["_signals"], events,
            start_index=train_end, end_index=validation_end,
            cost_bps=args.cost_bps_per_side, stress_extra_bps=args.stress_extra_bps, folds=3,
        )
        validation_result = gate(validation, "validation")
        decision = "reject_validation_gate_failed_oos_unopened"
        if validation_result["pass"]:
            oos_opened = True
            oos = evaluate(
                config, bars, selected["_signals"], events,
                start_index=validation_end, end_index=len(bars),
                cost_bps=args.cost_bps_per_side, stress_extra_bps=args.stress_extra_bps, folds=3,
            )
            oos_result = gate(oos, "validation")
            decision = "funding_event_candidate_requires_registry_review" if oos_result["pass"] else "reject_oos_gate_failed"

    failed_checks = Counter(name for item in candidates for name, passed in item["train_gate"]["checks"].items() if not passed)

    def public(item: dict[str, Any] | None) -> dict[str, Any] | None:
        if item is None:
            return None
        return {key: value for key, value in item.items() if not key.startswith("_")}

    report = {
        "generated_at": now_iso(),
        "hypothesis_id": "HYP-FUNDING-EVENT-001",
        "family": "FUNDING_SETTLEMENT_REVERSION_1H",
        "method": "prospective_train_search_then_validation_then_conditionally_open_oos",
        "data": {
            "cache_dir": portable_path(cache),
            "snapshot": snapshot_provenance(cache),
            "bars": len(bars),
            "funding_events": len(events),
            "first": bars[0].ts,
            "last": bars[-1].ts,
            "train_end": args.train_end,
            "validation_end": args.validation_end,
        },
        "search": {
            "tested": len(candidates),
            "unique_signal_sets": len(signal_cache),
            "train_qualified": len(qualified),
            "failed_check_counts": dict(sorted(failed_checks.items())),
            "oos_used_for_selection": False,
        },
        "protocol": {
            "actual_funding_events_only": True,
            "entry_next_hour": True,
            "min_abs_oi_change_pct": 0.5,
            "max_hold_hours": 8,
            "cost_bps_per_side": args.cost_bps_per_side,
            "stress_extra_bps_per_side": args.stress_extra_bps,
            "bonferroni_required_probability": 0.9994791666666667,
        },
        "top_train_candidates": [public(item) for item in qualified[:10]],
        "top_train_results_regardless_of_gate": [public(item) for item in candidates[:10]],
        "selected_on_train": public(selected),
        "validation": validation,
        "validation_gate": validation_result,
        "oos_opened": oos_opened,
        "oos": oos,
        "oos_gate": oos_result,
        "runtime_boundary": {"research_only": True, "observer_allowed": False, "orders_allowed": False, "can_trade": False},
        "decision": decision,
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    lock = {
        "schema_version": 1,
        "hypothesis_id": report["hypothesis_id"],
        "family": report["family"],
        "enabled": False,
        "status": decision,
        "selected_on_train": selected["config"] if selected else None,
        "validation_gate": validation_result,
        "oos_opened": oos_opened,
        "source_report": portable_path(out.with_suffix(".json")),
        "boundaries": {"observer_allowed": False, "paper_execution_allowed": False, "live_execution_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }
    lock_path = resolve_path(args.lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "decision": decision,
        "tested": len(candidates),
        "train_qualified": len(qualified),
        "selected": selected["strategy_id"] if selected else None,
        "validation_pass": validation_result["pass"] if validation_result else False,
        "oos_opened": oos_opened,
        "can_trade": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
