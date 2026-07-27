#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HOUR_MS = 3_600_000


@dataclass(frozen=True)
class ReversionConfig:
    strategy_id: str
    z_window_hours: int
    entry_z: float
    exit_z: float
    min_basis_bps: float
    max_hold_hours: int


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
    manifest_path = cache / "SNAPSHOT_MANIFEST.json"
    if not manifest_path.is_file():
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        return None
    return {
        "snapshot_id": payload.get("snapshot_id"),
        "profile": payload.get("profile"),
        "dataset_sha256": payload.get("dataset_sha256"),
        "manifest_path": portable_path(manifest_path),
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def aligned_bars(spot_path: Path, futures_path: Path) -> list[dict[str, Any]]:
    spot = {row["time"]: row for row in read_csv(spot_path)}
    futures = {row["time"]: row for row in read_csv(futures_path)}
    rows: list[dict[str, Any]] = []
    for timestamp in sorted(spot.keys() & futures.keys(), key=parse_ts):
        s = spot[timestamp]
        f = futures[timestamp]
        try:
            spot_open = float(s["open"])
            spot_close = float(s["close"])
            futures_open = float(f["open"])
            futures_close = float(f["close"])
        except (KeyError, ValueError):
            continue
        if min(spot_open, spot_close, futures_open, futures_close) <= 0:
            continue
        time_ms = int(s.get("time_ms") or int(parse_ts(timestamp).timestamp() * 1000))
        rows.append(
            {
                "time": timestamp,
                "time_ms": time_ms,
                "spot_open": spot_open,
                "spot_close": spot_close,
                "futures_open": futures_open,
                "futures_close": futures_close,
                "basis_close_bps": (futures_close / spot_close - 1.0) * 10_000.0,
            }
        )
    return rows


def funding_events(path: Path) -> list[dict[str, float]]:
    events: list[dict[str, float]] = []
    for row in read_csv(path):
        try:
            timestamp = int(row["timestamp"])
            rate = float(row["funding"])
        except (KeyError, ValueError):
            continue
        if math.isfinite(rate):
            events.append({"timestamp": float(timestamp), "rate": rate})
    return sorted(events, key=lambda item: item["timestamp"])


def rolling_basis_z(rows: list[dict[str, Any]], window: int) -> list[float | None]:
    """Current basis versus prior observations only; the current bar is never in its own baseline."""
    values = [float(row["basis_close_bps"]) for row in rows]
    output: list[float | None] = [None] * len(values)
    rolling_sum = 0.0
    rolling_sq = 0.0
    for index, value in enumerate(values):
        if index >= window:
            mean = rolling_sum / window
            variance = max(0.0, rolling_sq / window - mean * mean)
            std = math.sqrt(variance)
            if std > 1e-12:
                output[index] = (value - mean) / std
            old = values[index - window]
            rolling_sum -= old
            rolling_sq -= old * old
        rolling_sum += value
        rolling_sq += value * value
    return output


def build_configs() -> list[ReversionConfig]:
    configs: list[ReversionConfig] = []
    for window in (168, 336, 720):
        for entry_z in (1.5, 2.0, 2.5):
            for exit_z in (0.0, 0.5, 1.0):
                for min_basis in (5.0, 10.0, 20.0):
                    for hold in (12, 24, 48, 72):
                        strategy_id = (
                            f"basis_shock_z{window}_e{entry_z:g}_x{exit_z:g}_"
                            f"b{min_basis:g}_h{hold}"
                        )
                        configs.append(
                            ReversionConfig(
                                strategy_id=strategy_id,
                                z_window_hours=window,
                                entry_z=entry_z,
                                exit_z=exit_z,
                                min_basis_bps=min_basis,
                                max_hold_hours=hold,
                            )
                        )
    return configs


def generate_signals(
    config: ReversionConfig,
    rows: list[dict[str, Any]],
    z_values: list[float | None],
) -> list[int]:
    signals: list[int] = []
    for index in range(1, len(rows) - 2):
        current = z_values[index]
        previous = z_values[index - 1]
        if current is None or previous is None:
            continue
        crossed = previous < config.entry_z <= current
        if crossed and float(rows[index]["basis_close_bps"]) >= config.min_basis_bps:
            signals.append(index)
    return signals


def funding_pnl_quote(
    events: list[dict[str, float]],
    entry_ms: int,
    exit_ms: int,
    futures_prices: dict[int, float],
) -> float:
    # A positive settlement is received by the short perpetual leg.
    pnl = 0.0
    for event in events:
        timestamp = int(event["timestamp"])
        if timestamp <= entry_ms:
            continue
        if timestamp > exit_ms:
            break
        hour_ms = timestamp - timestamp % HOUR_MS
        price = futures_prices.get(hour_ms)
        if price is not None:
            pnl += price * float(event["rate"])
    return pnl


def trade_pnl(
    entry: dict[str, Any],
    exit_row: dict[str, Any],
    events: list[dict[str, float]],
    futures_prices: dict[int, float],
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, float]:
    slip = slippage_bps / 10_000.0
    fee = fee_bps / 10_000.0
    spot_entry = float(entry["spot_open"]) * (1.0 + slip)
    futures_entry = float(entry["futures_open"]) * (1.0 - slip)
    spot_exit = float(exit_row["spot_open"]) * (1.0 - slip)
    futures_exit = float(exit_row["futures_open"]) * (1.0 + slip)
    price_pnl = (spot_exit - spot_entry) + (futures_entry - futures_exit)
    funding_pnl = funding_pnl_quote(
        events,
        int(entry["time_ms"]),
        int(exit_row["time_ms"]),
        futures_prices,
    )
    fees = fee * (spot_entry + spot_exit + futures_entry + futures_exit)
    gross_capital = spot_entry + futures_entry
    net_quote = price_pnl + funding_pnl - fees
    return {
        "price_pnl_quote": price_pnl,
        "funding_pnl_quote": funding_pnl,
        "fees_quote": fees,
        "net_quote": net_quote,
        "net_return_bps": net_quote / gross_capital * 10_000.0,
    }


def simulate_window(
    config: ReversionConfig,
    rows: list[dict[str, Any]],
    z_values: list[float | None],
    signals: list[int],
    events: list[dict[str, float]],
    *,
    start_index: int,
    end_index: int,
    fee_bps: float,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    futures_prices = {int(row["time_ms"]): float(row["futures_close"]) for row in rows}
    trades: list[dict[str, Any]] = []
    last_exit = start_index - 1
    for signal_index in signals:
        if signal_index < start_index or signal_index >= end_index - 2 or signal_index <= last_exit:
            continue
        entry_index = signal_index + 1
        max_exit = min(end_index - 1, entry_index + config.max_hold_hours)
        exit_index = max_exit
        exit_reason = "max_hold"
        for check_index in range(entry_index, max_exit):
            z_value = z_values[check_index]
            if z_value is not None and z_value <= config.exit_z:
                exit_index = check_index + 1
                exit_reason = "basis_z_converged"
                break
        pnl = trade_pnl(
            rows[entry_index],
            rows[exit_index],
            events,
            futures_prices,
            fee_bps,
            slippage_bps,
        )
        trades.append(
            {
                "strategy_id": config.strategy_id,
                "signal_time": rows[signal_index]["time"],
                "entry_time": rows[entry_index]["time"],
                "exit_time": rows[exit_index]["time"],
                "entry_basis_bps": round(float(rows[signal_index]["basis_close_bps"]), 6),
                "entry_z": round(float(z_values[signal_index] or 0.0), 6),
                "exit_z": round(float(z_values[exit_index - 1] or 0.0), 6),
                "hours_held": exit_index - entry_index,
                "exit_reason": exit_reason,
                **{key: round(value, 8) for key, value in pnl.items()},
            }
        )
        last_exit = exit_index
    return trades


def max_drawdown(values: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return round(drawdown, 6)


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(trade["net_return_bps"]) for trade in trades]
    return {
        "trades": len(values),
        "positive": sum(value > 0 for value in values),
        "positive_pct": round(sum(value > 0 for value in values) / len(values) * 100.0, 3) if values else None,
        "mean_net_bps": round(statistics.mean(values), 6) if values else None,
        "median_net_bps": round(statistics.median(values), 6) if values else None,
        "net_bps_total": round(sum(values), 6),
        "max_drawdown_bps": max_drawdown(values),
        "mean_hours_held": round(statistics.mean(trade["hours_held"] for trade in trades), 3) if trades else None,
        "funding_quote_total": round(sum(float(trade["funding_pnl_quote"]) for trade in trades), 6),
        "price_quote_total": round(sum(float(trade["price_pnl_quote"]) for trade in trades), 6),
        "fees_quote_total": round(sum(float(trade["fees_quote"]) for trade in trades), 6),
    }


def positive_folds(trades: list[dict[str, Any]], folds: int) -> int:
    if not trades:
        return 0
    positive = 0
    for fold in range(folds):
        start = round(len(trades) * fold / folds)
        end = round(len(trades) * (fold + 1) / folds)
        chunk = trades[start:end]
        if len(chunk) >= 3 and statistics.mean(float(row["net_return_bps"]) for row in chunk) > 0:
            positive += 1
    return positive


def bootstrap_positive_probability(values: list[float], iterations: int = 2_000, seed: int = 20260623) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(iterations):
        sample_mean = statistics.mean(rng.choice(values) for _ in values)
        positive += int(sample_mean > 0.0)
    return round(positive / iterations, 6)


def evaluate(
    config: ReversionConfig,
    rows: list[dict[str, Any]],
    z_values: list[float | None],
    signals: list[int],
    events: list[dict[str, float]],
    *,
    start_index: int,
    end_index: int,
    fee_bps: float,
    slippage_bps: float,
    stress_extra_bps: float,
    folds: int,
) -> dict[str, Any]:
    trades = simulate_window(
        config, rows, z_values, signals, events,
        start_index=start_index, end_index=end_index,
        fee_bps=fee_bps, slippage_bps=slippage_bps,
    )
    stressed = simulate_window(
        config, rows, z_values, signals, events,
        start_index=start_index, end_index=end_index,
        fee_bps=fee_bps + stress_extra_bps, slippage_bps=slippage_bps,
    )
    summary = summarize(trades)
    fold_count = positive_folds(trades, folds)
    stress_summary = summarize(stressed)
    cheap_train_checks = (
        int(summary["trades"]) >= 40
        and float(summary["mean_net_bps"] or -999.0) >= 5.0
        and float(summary["positive_pct"] or 0.0) >= 55.0
        and float(summary["max_drawdown_bps"]) >= -200.0
        and fold_count >= 3
        and float(stress_summary["mean_net_bps"] or -999.0) > 0.0
    )
    return {
        "summary": summary,
        "positive_folds": fold_count,
        "bootstrap_probability_mean_gt_0": (
            bootstrap_positive_probability([float(row["net_return_bps"]) for row in trades])
            if cheap_train_checks else None
        ),
        "cost_stress": {"extra_fee_bps_per_leg_side": stress_extra_bps, "summary": stress_summary},
        "sample_trades": trades[:3],
    }


def gate(result: dict[str, Any], stage: str) -> dict[str, Any]:
    summary = result["summary"]
    stress = result["cost_stress"]["summary"]
    if stage == "train":
        checks = {
            "min_trades": int(summary["trades"]) >= 40,
            "min_mean_net_bps": float(summary["mean_net_bps"] or -999.0) >= 5.0,
            "min_positive_pct": float(summary["positive_pct"] or 0.0) >= 55.0,
            "max_drawdown_bps": float(summary["max_drawdown_bps"]) >= -200.0,
            "min_positive_folds": int(result["positive_folds"]) >= 3,
            "bootstrap_probability": float(result["bootstrap_probability_mean_gt_0"] or 0.0) >= 0.95,
            "cost_stress_positive": float(stress["mean_net_bps"] or -999.0) > 0.0,
        }
    else:
        checks = {
            "min_trades": int(summary["trades"]) >= 15,
            "min_mean_net_bps": float(summary["mean_net_bps"] or -999.0) >= 0.0,
            "min_positive_pct": float(summary["positive_pct"] or 0.0) >= 50.0,
            "max_drawdown_bps": float(summary["max_drawdown_bps"]) >= -100.0,
            "min_positive_folds": int(result["positive_folds"]) >= 2,
            "cost_stress_positive": float(stress["mean_net_bps"] or -999.0) > 0.0,
        }
    return {"pass": all(checks.values()), "checks": checks}


def split_index(rows: list[dict[str, Any]], timestamp: str) -> int:
    boundary = parse_ts(timestamp)
    for index, row in enumerate(rows):
        if parse_ts(row["time"]) >= boundary:
            return index
    raise ValueError(f"split after data: {timestamp}")


def rank_key(item: dict[str, Any]) -> tuple[float, int, float, int]:
    summary = item["train"]["summary"]
    stress = item["train"]["cost_stress"]["summary"]
    trades = int(summary["trades"])
    return (
        float(stress["mean_net_bps"] or -999.0) * math.sqrt(max(1, trades)),
        int(item["train"]["positive_folds"]),
        float(summary["mean_net_bps"] or -999.0),
        trades,
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Basis Shock Reversion Nested Holdout",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "- Event-driven market-neutral research: long BTC spot, short equal BTC perpetual quantity.",
        "- Signal: positive spot/perpetual basis crosses a rolling z-score shock threshold.",
        "- Entry and exit use the next hourly open; fees, slippage and actual funding settlements are included.",
        "- Train is 2021-2023, validation is calendar 2024, and OOS from 2025 opens only after both gates pass.",
        "- This is distinct from the prior long-duration funding-carry family: funding is PnL, not an entry filter.",
        "",
        "## Result",
        "",
        f"- Tested / train-qualified: `{report['search']['tested']}` / `{report['search']['train_qualified']}`.",
        f"- Decision: `{report['decision']}`.",
    ]
    selected = report.get("selected_on_train")
    if selected:
        train = selected["train"]["summary"]
        validation = report["validation"]["summary"]
        lines.extend([
            f"- Frozen train candidate: `{selected['strategy_id']}`.",
            f"- Train: `{train['trades']}` trades, mean `{train['mean_net_bps']}` bps, positive `{train['positive_pct']}%`.",
            f"- Validation: `{validation['trades']}` trades, mean `{validation['mean_net_bps']}` bps, positive `{validation['positive_pct']}%`.",
        ])
    else:
        best = report["top_train_results_regardless_of_gate"][0]
        train = best["train"]["summary"]
        lines.extend([
            f"- Best rejected: `{best['strategy_id']}`.",
            f"- Best rejected train: `{train['trades']}` trades, mean `{train['mean_net_bps']}` bps, positive `{train['positive_pct']}%`.",
            "- Validation and OOS remained unopened.",
        ])
    lines.extend(["- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nested holdout for BTC spot/perpetual basis-shock reversion")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-extra-bps", type=float, default=3.0)
    parser.add_argument("--out-prefix", default="docs/BASIS_SHOCK_REVERSION_NESTED_HOLDOUT_2026-06-23")
    parser.add_argument("--lock-path", default="configs/BASIS_SHOCK_REVERSION_RESEARCH_LOCK.json")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    spot_path = cache / "spot" / "BTCUSDT" / "1h_klines.csv"
    futures_path = cache / "futures" / "BTCUSDT" / "1h_klines.csv"
    funding_path = cache / "futures" / "BTCUSDT" / "funding_raw.csv"
    rows = aligned_bars(spot_path, futures_path)
    events = funding_events(funding_path)
    train_end = split_index(rows, args.train_end)
    validation_end = split_index(rows, args.validation_end)
    z_cache = {window: rolling_basis_z(rows, window) for window in (168, 336, 720)}
    signal_cache: dict[tuple[int, float, float], list[int]] = {}
    results: list[dict[str, Any]] = []
    for config in build_configs():
        signal_key = (config.z_window_hours, config.entry_z, config.min_basis_bps)
        z_values = z_cache[config.z_window_hours]
        if signal_key not in signal_cache:
            signal_cache[signal_key] = generate_signals(config, rows, z_values)
        train = evaluate(
            config, rows, z_values, signal_cache[signal_key], events,
            start_index=0, end_index=train_end,
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps, folds=4,
        )
        results.append({"strategy_id": config.strategy_id, "config": asdict(config), "train": train, "train_gate": gate(train, "train")})

    results.sort(key=rank_key, reverse=True)
    qualified = [item for item in results if item["train_gate"]["pass"]]
    selected = qualified[0] if qualified else None
    validation = validation_result = oos = oos_result = None
    oos_opened = False
    decision = "reject_no_train_qualified_basis_shock_candidate"
    if selected:
        config = ReversionConfig(**selected["config"])
        z_values = z_cache[config.z_window_hours]
        signals = signal_cache[(config.z_window_hours, config.entry_z, config.min_basis_bps)]
        validation = evaluate(
            config, rows, z_values, signals, events,
            start_index=train_end, end_index=validation_end,
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps, folds=3,
        )
        validation_result = gate(validation, "validation")
        decision = "reject_validation_gate_failed_oos_unopened"
        if validation_result["pass"]:
            oos_opened = True
            oos = evaluate(
                config, rows, z_values, signals, events,
                start_index=validation_end, end_index=len(rows),
                fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
                stress_extra_bps=args.stress_extra_bps, folds=3,
            )
            oos_result = gate(oos, "validation")
            decision = "basis_shock_candidate_requires_execution_review" if oos_result["pass"] else "reject_oos_gate_failed"

    failed_checks = Counter(
        name for item in results for name, passed in item["train_gate"]["checks"].items() if not passed
    )
    report = {
        "generated_at": now_iso(),
        "family": "BASIS_SHOCK_REVERSION_1H",
        "method": "train_search_then_calendar_validation_then_conditionally_open_untouched_oos",
        "hypothesis_boundary": {
            "position": "long_spot_short_equal_quantity_perpetual",
            "entry_driver": "positive_basis_z_score_cross",
            "funding_role": "realized_pnl_only_not_signal_filter",
            "distinct_from": "BASIS_FUNDING_CARRY",
        },
        "data": {
            "cache_dir": portable_path(cache),
            "snapshot": snapshot_provenance(cache),
            "matched_rows": len(rows),
            "first": rows[0]["time"],
            "last": rows[-1]["time"],
            "funding_events": len(events),
            "train_end": args.train_end,
            "validation_end": args.validation_end,
        },
        "cost_model": {
            "fee_bps_per_leg_side": args.fee_bps,
            "slippage_bps_per_leg_side": args.slippage_bps,
            "stress_extra_fee_bps_per_leg_side": args.stress_extra_bps,
            "capital_denominator": "spot_notional_plus_perpetual_notional",
            "borrow_cost": "not_applicable_positive_basis_direction",
        },
        "search": {
            "tested": len(results),
            "unique_signal_sets": len(signal_cache),
            "train_qualified": len(qualified),
            "failed_check_counts": dict(sorted(failed_checks.items())),
            "oos_used_for_selection": False,
        },
        "gates": {
            "train": {"min_trades": 40, "min_mean_net_bps": 5.0, "min_positive_pct": 55.0, "positive_folds": 3, "bootstrap_probability": 0.95, "stress_positive": True},
            "validation": {"min_trades": 15, "min_mean_net_bps": 0.0, "min_positive_pct": 50.0, "positive_folds": 2, "stress_positive": True},
        },
        "top_train_candidates": qualified[:10],
        "top_train_results_regardless_of_gate": results[:10],
        "selected_on_train": selected,
        "validation": validation,
        "validation_gate": validation_result,
        "oos_opened": oos_opened,
        "oos": oos,
        "oos_gate": oos_result,
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
        "source_report": portable_path(out.with_suffix(".json")),
        "boundaries": {"observer_allowed": False, "paper_execution_allowed": False, "live_execution_allowed": False, "allow_orders": False, "can_trade": False},
        "can_trade": False,
    }
    lock_path = resolve_path(args.lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "decision": decision,
        "tested": len(results),
        "train_qualified": len(qualified),
        "selected": selected["strategy_id"] if selected else None,
        "validation_pass": validation_result["pass"] if validation_result else False,
        "oos_opened": oos_opened,
        "can_trade": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
