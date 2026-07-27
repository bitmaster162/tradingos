#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CarryConfig:
    strategy_id: str
    entry_basis_bps: float
    min_funding_mean_bps: float
    max_hold_hours: int
    exit_basis_bps: float = 0.0
    exit_funding_mean_bps: float = 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


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
        rows.append(
            {
                "time": timestamp,
                "time_ms": int(parse_ts(timestamp).timestamp() * 1000),
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


def rolling_funding_means(rows: list[dict[str, Any]], events: list[dict[str, float]], window: int = 3) -> list[float | None]:
    result: list[float | None] = []
    observed: list[float] = []
    pointer = 0
    for row in rows:
        bar_close_ms = int(row["time_ms"]) + 3_600_000 - 1
        while pointer < len(events) and events[pointer]["timestamp"] <= bar_close_ms:
            observed.append(events[pointer]["rate"] * 10_000.0)
            pointer += 1
        result.append(mean(observed[-window:]) if len(observed) >= window else None)
    return result


def build_configs(args: argparse.Namespace) -> list[CarryConfig]:
    configs: list[CarryConfig] = []
    for basis in [float(item) for item in args.entry_basis_bps.split(",") if item.strip()]:
        for funding in [float(item) for item in args.min_funding_mean_bps.split(",") if item.strip()]:
            for hold in [int(item) for item in args.max_hold_hours.split(",") if item.strip()]:
                configs.append(
                    CarryConfig(
                        strategy_id=f"carry_basis{basis:g}_fund3x{funding:g}_h{hold}",
                        entry_basis_bps=basis,
                        min_funding_mean_bps=funding,
                        max_hold_hours=hold,
                    )
                )
    return configs


def funding_pnl_quote(events: list[dict[str, float]], entry_ms: int, exit_ms: int, futures_prices: dict[int, float]) -> float:
    pnl = 0.0
    for event in events:
        timestamp = int(event["timestamp"])
        if timestamp <= entry_ms:
            continue
        if timestamp > exit_ms:
            break
        hour_ms = timestamp - timestamp % 3_600_000
        price = futures_prices.get(hour_ms)
        if price is not None:
            pnl += price * float(event["rate"])
    return pnl


def trade_pnl(
    *,
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
    funding_pnl = funding_pnl_quote(events, int(entry["time_ms"]), int(exit_row["time_ms"]), futures_prices)
    fees = fee * (spot_entry + spot_exit + futures_entry + futures_exit)
    gross_capital = spot_entry + futures_entry
    net_quote = price_pnl + funding_pnl - fees
    return {
        "price_pnl_quote": price_pnl,
        "funding_pnl_quote": funding_pnl,
        "fees_quote": fees,
        "net_quote": net_quote,
        "net_return_bps_on_gross_capital": net_quote / gross_capital * 10_000.0,
    }


def simulate_window(
    config: CarryConfig,
    rows: list[dict[str, Any]],
    funding_mean: list[float | None],
    events: list[dict[str, float]],
    *,
    start_index: int,
    end_index: int,
    fee_bps: float,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    futures_prices = {int(row["time_ms"]): float(row["futures_close"]) for row in rows}
    trades: list[dict[str, Any]] = []
    index = max(1, start_index)
    while index < end_index - 2:
        basis = float(rows[index]["basis_close_bps"])
        funding = funding_mean[index]
        if funding is None or basis < config.entry_basis_bps or funding < config.min_funding_mean_bps:
            index += 1
            continue
        entry_index = index + 1
        max_exit = min(end_index - 1, entry_index + config.max_hold_hours)
        exit_index = max_exit
        exit_reason = "max_hold"
        for check_index in range(entry_index, max_exit):
            if funding_mean[check_index] is not None and funding_mean[check_index] <= config.exit_funding_mean_bps:
                exit_index = check_index + 1
                exit_reason = "funding_deteriorated"
                break
            if float(rows[check_index]["basis_close_bps"]) <= config.exit_basis_bps:
                exit_index = check_index + 1
                exit_reason = "basis_converged"
                break
        pnl = trade_pnl(
            entry=rows[entry_index],
            exit_row=rows[exit_index],
            events=events,
            futures_prices=futures_prices,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        trades.append(
            {
                "strategy_id": config.strategy_id,
                "signal_time": rows[index]["time"],
                "entry_time": rows[entry_index]["time"],
                "exit_time": rows[exit_index]["time"],
                "entry_basis_bps": round(basis, 6),
                "entry_funding_mean_bps": round(float(funding), 6),
                "exit_reason": exit_reason,
                "hours_held": exit_index - entry_index,
                **{key: round(value, 8) for key, value in pnl.items()},
            }
        )
        index = exit_index + 1
    return trades


def max_drawdown(values: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return round(drawdown, 6)


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(trade["net_return_bps_on_gross_capital"]) for trade in trades]
    return {
        "trades": len(values),
        "positive": sum(value > 0 for value in values),
        "positive_pct": round(sum(value > 0 for value in values) / len(values) * 100.0, 3) if values else None,
        "mean_net_bps": round(mean(values), 6) if values else None,
        "median_net_bps": round(sorted(values)[len(values) // 2], 6) if values else None,
        "net_bps_total": round(sum(values), 6),
        "max_drawdown_bps": max_drawdown(values),
        "mean_hours_held": round(mean([trade["hours_held"] for trade in trades]), 3) if trades else None,
        "funding_quote_total": round(sum(float(trade["funding_pnl_quote"]) for trade in trades), 6),
        "price_quote_total": round(sum(float(trade["price_pnl_quote"]) for trade in trades), 6),
        "fees_quote_total": round(sum(float(trade["fees_quote"]) for trade in trades), 6),
    }


def fold_positive_count(trades: list[dict[str, Any]], folds: int) -> int:
    if not trades:
        return 0
    count = 0
    for fold in range(folds):
        start = round(len(trades) * fold / folds)
        end = round(len(trades) * (fold + 1) / folds)
        chunk = trades[start:end]
        if len(chunk) >= 3 and mean(float(item["net_return_bps_on_gross_capital"]) for item in chunk) > 0:
            count += 1
    return count


def evaluate(
    config: CarryConfig,
    rows: list[dict[str, Any]],
    funding_mean: list[float | None],
    events: list[dict[str, float]],
    *,
    start_index: int,
    end_index: int,
    fee_bps: float,
    slippage_bps: float,
    stress_extra_bps: float,
    folds: int,
) -> dict[str, Any]:
    trades = simulate_window(config, rows, funding_mean, events, start_index=start_index, end_index=end_index, fee_bps=fee_bps, slippage_bps=slippage_bps)
    stressed = simulate_window(config, rows, funding_mean, events, start_index=start_index, end_index=end_index, fee_bps=fee_bps + stress_extra_bps, slippage_bps=slippage_bps,)
    return {
        "summary": summarize(trades),
        "positive_folds": fold_positive_count(trades, folds),
        "cost_stress": {"extra_fee_bps_per_leg_side": stress_extra_bps, "summary": summarize(stressed)},
        "sample_trades": trades[:3],
    }


def gate(result: dict[str, Any], *, stage: str, args: argparse.Namespace) -> dict[str, Any]:
    summary = result["summary"]
    stress = result["cost_stress"]["summary"]
    checks = {
        "min_trades": int(summary["trades"]) >= getattr(args, f"min_{stage}_trades"),
        "mean_net_positive": summary["mean_net_bps"] is not None and float(summary["mean_net_bps"]) >= getattr(args, f"min_{stage}_mean_bps"),
        "positive_rate": summary["positive_pct"] is not None and float(summary["positive_pct"]) >= getattr(args, f"min_{stage}_positive_pct"),
        "max_drawdown": float(summary["max_drawdown_bps"]) >= -abs(getattr(args, f"max_{stage}_drawdown_bps")),
        "positive_folds": int(result["positive_folds"]) >= getattr(args, f"min_{stage}_positive_folds"),
        "stress_positive": stress["mean_net_bps"] is not None and float(stress["mean_net_bps"]) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def split_index(rows: list[dict[str, Any]], timestamp: str) -> int:
    boundary = parse_ts(timestamp)
    for index, row in enumerate(rows):
        if parse_ts(row["time"]) >= boundary:
            return index
    raise ValueError(f"split after data: {timestamp}")


def rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    summary = item["train"]["summary"]
    stress = item["train"]["cost_stress"]["summary"]
    return (float(stress["mean_net_bps"] or -999.0) * math.sqrt(max(1, summary["trades"])), item["train"]["positive_folds"], summary["trades"])


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Basis/Funding Carry Nested Holdout",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Market-neutral research: long BTC spot and short equal BTC perpetual quantity.",
        "- Entry is next-hour open after the signal; both legs include fees and slippage.",
        "- Funding uses actual historical settlements; return is measured on gross two-leg capital.",
        "- Train selects one candidate, 2025 validates it, and 2026 OOS opens only after validation passes.",
        "- No credentials, orders, paper/live permission, leverage assumption, or liquidation model.",
        "",
        "## Result",
        "",
        f"- Matched hourly rows: `{report['data']['matched_rows']}` (`{report['data']['coverage_pct']}%`).",
        f"- Tested / train-qualified: `{report['search']['tested']}` / `{report['search']['train_qualified']}`.",
        f"- Decision: `{report['decision']}`.",
    ]
    selected = report.get("selected_on_train")
    if selected:
        train = selected["train"]["summary"]
        validation = report["validation"]["summary"]
        lines.extend([
            f"- Selected: `{selected['strategy_id']}`.",
            f"- Train: `{train['trades']}` trades, mean `{train['mean_net_bps']}` bps, positive `{train['positive_pct']}%`.",
            f"- Validation: `{validation['trades']}` trades, mean `{validation['mean_net_bps']}` bps, positive `{validation['positive_pct']}%`.",
        ])
    if report.get("oos"):
        oos = report["oos"]["summary"]
        lines.append(f"- OOS: `{oos['trades']}` trades, mean `{oos['mean_net_bps']}` bps, positive `{oos['positive_pct']}%`.")
    elif selected:
        lines.append("- Final OOS remained unopened because validation failed.")
    else:
        lines.append("- Validation and OOS remained unopened because train produced no qualified candidate.")
    lines.extend(["", "- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nested validation for market-neutral BTC spot/perpetual basis and funding carry")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--train-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2026-01-01T00:00:00+00:00")
    parser.add_argument("--entry-basis-bps", default="5,10,15")
    parser.add_argument("--min-funding-mean-bps", default="0.5,1,2")
    parser.add_argument("--max-hold-hours", default="168,336")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-extra-bps", type=float, default=3.0)
    parser.add_argument("--min-train-trades", type=int, default=20)
    parser.add_argument("--min-train-mean-bps", type=float, default=5.0)
    parser.add_argument("--min-train-positive-pct", type=float, default=55.0)
    parser.add_argument("--max-train-drawdown-bps", type=float, default=150.0)
    parser.add_argument("--min-train-positive-folds", type=int, default=3)
    parser.add_argument("--min-validation-trades", type=int, default=5)
    parser.add_argument("--min-validation-mean-bps", type=float, default=0.0)
    parser.add_argument("--min-validation-positive-pct", type=float, default=50.0)
    parser.add_argument("--max-validation-drawdown-bps", type=float, default=100.0)
    parser.add_argument("--min-validation-positive-folds", type=int, default=2)
    parser.add_argument("--min-oos-trades", type=int, default=3)
    parser.add_argument("--min-oos-mean-bps", type=float, default=0.0)
    parser.add_argument("--min-oos-positive-pct", type=float, default=50.0)
    parser.add_argument("--max-oos-drawdown-bps", type=float, default=100.0)
    parser.add_argument("--min-oos-positive-folds", type=int, default=1)
    parser.add_argument("--out-prefix", default="docs/BASIS_FUNDING_CARRY_NESTED_HOLDOUT_2026-06-23")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    spot_path = cache / "spot" / "BTCUSDT" / "1h_klines.csv"
    futures_path = cache / "futures" / "BTCUSDT" / "1h_klines.csv"
    funding_path = cache / "futures" / "BTCUSDT" / "funding_raw.csv"
    rows = aligned_bars(spot_path, futures_path)
    events = funding_events(funding_path)
    funding_mean = rolling_funding_means(rows, events)
    train_end = split_index(rows, args.train_end)
    validation_end = split_index(rows, args.validation_end)
    configs = build_configs(args)
    results: list[dict[str, Any]] = []
    for config in configs:
        train = evaluate(config, rows, funding_mean, events, start_index=0, end_index=train_end, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, stress_extra_bps=args.stress_extra_bps, folds=4)
        results.append({"strategy_id": config.strategy_id, "config": asdict(config), "train": train, "train_gate": gate(train, stage="train", args=args)})
    qualified = sorted([item for item in results if item["train_gate"]["pass"]], key=rank_key, reverse=True)
    selected = qualified[0] if qualified else None
    validation = validation_gate = oos = oos_gate = None
    decision = "reject_no_train_candidate"
    if selected:
        config = CarryConfig(**selected["config"])
        validation = evaluate(config, rows, funding_mean, events, start_index=train_end, end_index=validation_end, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, stress_extra_bps=args.stress_extra_bps, folds=3)
        validation_gate = gate(validation, stage="validation", args=args)
        decision = (
            "insufficient_validation_no_events_research_only"
            if int(validation["summary"]["trades"]) == 0
            else "reject_validation_gate_failed"
        )
        if validation_gate["pass"]:
            oos = evaluate(config, rows, funding_mean, events, start_index=validation_end, end_index=len(rows), fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, stress_extra_bps=args.stress_extra_bps, folds=2)
            oos_gate = gate(oos, stage="oos", args=args)
            decision = "pass_oos_research_candidate_not_trade_permission" if oos_gate["pass"] else "reject_oos_gate_failed"
    results.sort(key=rank_key, reverse=True)
    futures_count = len(read_csv(futures_path))
    report = {
        "generated_at": now_iso(),
        "method": "train_selection_then_validation_gate_then_final_calendar_oos",
        "selection_frozen_before_validation": True,
        "validation_required_before_oos": True,
        "runtime_boundary": {"research_only": True, "market_neutral_model": True, "sends_orders": False, "can_trade": False},
        "data": {
            "matched_rows": len(rows),
            "futures_rows": futures_count,
            "coverage_pct": round(len(rows) / futures_count * 100.0, 4),
            "first_time": rows[0]["time"],
            "last_time": rows[-1]["time"],
            "funding_events": len(events),
            "train_end": args.train_end,
            "validation_end": args.validation_end,
        },
        "cost_model": {"fee_bps_per_leg_side": args.fee_bps, "slippage_bps_per_leg_side": args.slippage_bps, "gross_capital_denominator": "spot_notional_plus_perpetual_notional"},
        "search": {"tested": len(results), "train_qualified": len(qualified)},
        "selected_on_train": selected,
        "validation": validation,
        "validation_gate": validation_gate,
        "oos": oos,
        "oos_gate": oos_gate,
        "top_train_results": results[:18],
        "decision": decision,
        "next_action": "execution_and_margin_model_before_observer" if decision.startswith("pass_oos") else "reject_without_reusing_opened_stage_for_retuning",
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
