#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tools.basis_shock_reversion_nested_holdout import (
        bootstrap_positive_probability,
        funding_events,
        rolling_basis_z,
        split_index,
        summarize,
        trade_pnl,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution from tools/
    from basis_shock_reversion_nested_holdout import (
        bootstrap_positive_probability,
        funding_events,
        rolling_basis_z,
        split_index,
        summarize,
        trade_pnl,
    )


ROOT = Path(__file__).resolve().parents[1]
HOUR_MS = 3_600_000


@dataclass(frozen=True)
class AlignmentConfig:
    strategy_id: str
    z_window_hours: int
    entry_z: float
    exit_z: float
    min_basis_bps: float
    min_funding_mean_bps: float
    funding_window_events: int
    funding_exit_bps: float
    max_hold_hours: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def csv_values(value: str, caster: Any) -> list[Any]:
    return [caster(item.strip()) for item in value.split(",") if item.strip()]


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


def load_lock(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"research lock is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("status") != "pre_registered_research_only":
        raise ValueError(f"lock status must be pre_registered_research_only: {path}")
    if payload.get("can_trade") is not False or payload.get("orders_allowed") is not False:
        raise ValueError("lock must explicitly keep can_trade=false and orders_allowed=false")
    return payload


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


def rolling_funding_means(
    rows: list[dict[str, Any]],
    events: list[dict[str, float]],
    window: int,
) -> list[float | None]:
    result: list[float | None] = []
    observed: list[float] = []
    pointer = 0
    for row in rows:
        bar_close_ms = int(row["time_ms"]) + HOUR_MS - 1
        while pointer < len(events) and int(events[pointer]["timestamp"]) <= bar_close_ms:
            observed.append(float(events[pointer]["rate"]) * 10_000.0)
            pointer += 1
        result.append(statistics.mean(observed[-window:]) if len(observed) >= window else None)
    return result


def build_configs(args: argparse.Namespace) -> list[AlignmentConfig]:
    configs: list[AlignmentConfig] = []
    for window in csv_values(args.z_window_hours, int):
        for entry_z in csv_values(args.entry_z, float):
            for exit_z in csv_values(args.exit_z, float):
                for min_basis in csv_values(args.min_basis_bps, float):
                    for min_funding in csv_values(args.min_funding_mean_bps, float):
                        for funding_window in csv_values(args.funding_window_events, int):
                            for funding_exit in csv_values(args.funding_exit_bps, float):
                                for hold in csv_values(args.max_hold_hours, int):
                                    strategy_id = (
                                        f"basis_shock_funding_align_z{window}_e{entry_z:g}_x{exit_z:g}_"
                                        f"b{min_basis:g}_fund{funding_window}x{min_funding:g}_"
                                        f"fx{funding_exit:g}_h{hold}"
                                    )
                                    configs.append(
                                        AlignmentConfig(
                                            strategy_id=strategy_id,
                                            z_window_hours=window,
                                            entry_z=entry_z,
                                            exit_z=exit_z,
                                            min_basis_bps=min_basis,
                                            min_funding_mean_bps=min_funding,
                                            funding_window_events=funding_window,
                                            funding_exit_bps=funding_exit,
                                            max_hold_hours=hold,
                                        )
                                    )
    return configs


def load_context(cache: Path, symbol: str, train_end: str, validation_end: str) -> dict[str, Any]:
    symbol = symbol.upper()
    spot_path = cache / "spot" / symbol / "1h_klines.csv"
    futures_path = cache / "futures" / symbol / "1h_klines.csv"
    funding_path = cache / "futures" / symbol / "funding_raw.csv"
    rows = aligned_bars(spot_path, futures_path)
    events = funding_events(funding_path)
    return {
        "symbol": symbol,
        "spot_path": portable_path(spot_path),
        "futures_path": portable_path(futures_path),
        "funding_path": portable_path(funding_path),
        "rows": rows,
        "events": events,
        "train_end": split_index(rows, train_end),
        "validation_end": split_index(rows, validation_end),
        "z_cache": {},
        "funding_cache": {},
        "signal_cache": {},
    }


def context_z(context: dict[str, Any], window: int) -> list[float | None]:
    cache = context["z_cache"]
    if window not in cache:
        cache[window] = rolling_basis_z(context["rows"], window)
    return cache[window]


def context_funding(context: dict[str, Any], window: int) -> list[float | None]:
    cache = context["funding_cache"]
    if window not in cache:
        cache[window] = rolling_funding_means(context["rows"], context["events"], window)
    return cache[window]


def generate_signals(
    config: AlignmentConfig,
    rows: list[dict[str, Any]],
    z_values: list[float | None],
    funding_mean: list[float | None],
) -> list[int]:
    signals: list[int] = []
    for index in range(1, len(rows) - 2):
        current_z = z_values[index]
        previous_z = z_values[index - 1]
        funding = funding_mean[index]
        if current_z is None or previous_z is None or funding is None:
            continue
        crossed = previous_z < config.entry_z <= current_z
        basis_ok = float(rows[index]["basis_close_bps"]) >= config.min_basis_bps
        funding_ok = float(funding) >= config.min_funding_mean_bps
        if crossed and basis_ok and funding_ok:
            signals.append(index)
    return signals


def signals_for(context: dict[str, Any], config: AlignmentConfig) -> list[int]:
    key = (
        config.z_window_hours,
        config.entry_z,
        config.min_basis_bps,
        config.min_funding_mean_bps,
        config.funding_window_events,
    )
    if key not in context["signal_cache"]:
        context["signal_cache"][key] = generate_signals(
            config,
            context["rows"],
            context_z(context, config.z_window_hours),
            context_funding(context, config.funding_window_events),
        )
    return context["signal_cache"][key]


def simulate_window(
    config: AlignmentConfig,
    rows: list[dict[str, Any]],
    z_values: list[float | None],
    funding_mean: list[float | None],
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
            funding = funding_mean[check_index]
            if funding is not None and float(funding) <= config.funding_exit_bps:
                exit_index = check_index + 1
                exit_reason = "funding_deteriorated"
                break
            if z_value is not None and float(z_value) <= config.exit_z:
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
                "entry_funding_mean_bps": round(float(funding_mean[signal_index] or 0.0), 6),
                "exit_z": round(float(z_values[exit_index - 1] or 0.0), 6),
                "exit_funding_mean_bps": round(float(funding_mean[exit_index - 1] or 0.0), 6),
                "hours_held": exit_index - entry_index,
                "exit_reason": exit_reason,
                **{key: round(value, 8) for key, value in pnl.items()},
            }
        )
        last_exit = exit_index
    return trades


def with_symbol(symbol: str, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**trade, "symbol": symbol} for trade in trades]


def stage_bounds(context: dict[str, Any], stage: str) -> tuple[int, int]:
    if stage == "train":
        return 0, int(context["train_end"])
    if stage == "validation":
        return int(context["train_end"]), int(context["validation_end"])
    if stage == "oos":
        return int(context["validation_end"]), len(context["rows"])
    raise ValueError(f"unsupported stage: {stage}")


def positive_folds_by_time(trades: list[dict[str, Any]], folds: int) -> int:
    if not trades:
        return 0
    ordered = sorted(trades, key=lambda item: (item["entry_time"], item.get("symbol", "")))
    positive = 0
    for fold in range(folds):
        start = round(len(ordered) * fold / folds)
        end = round(len(ordered) * (fold + 1) / folds)
        chunk = ordered[start:end]
        if len(chunk) >= 3 and statistics.mean(float(row["net_return_bps"]) for row in chunk) > 0:
            positive += 1
    return positive


def evaluate_pooled(
    config: AlignmentConfig,
    contexts: list[dict[str, Any]],
    *,
    stage: str,
    fee_bps: float,
    slippage_bps: float,
    stress_extra_bps: float,
    folds: int,
    bootstrap_min_trades: int,
    bootstrap_min_mean_bps: float,
    bootstrap_min_positive_pct: float,
    bootstrap_min_folds: int,
    bootstrap_max_drawdown_bps: float,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    stressed: list[dict[str, Any]] = []
    by_symbol: dict[str, dict[str, Any]] = {}
    for context in contexts:
        start_index, end_index = stage_bounds(context, stage)
        z_values = context_z(context, config.z_window_hours)
        funding_mean = context_funding(context, config.funding_window_events)
        signals = signals_for(context, config)
        symbol_trades = with_symbol(
            context["symbol"],
            simulate_window(
                config,
                context["rows"],
                z_values,
                funding_mean,
                signals,
                context["events"],
                start_index=start_index,
                end_index=end_index,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            ),
        )
        symbol_stress = with_symbol(
            context["symbol"],
            simulate_window(
                config,
                context["rows"],
                z_values,
                funding_mean,
                signals,
                context["events"],
                start_index=start_index,
                end_index=end_index,
                fee_bps=fee_bps + stress_extra_bps,
                slippage_bps=slippage_bps,
            ),
        )
        trades.extend(symbol_trades)
        stressed.extend(symbol_stress)
        by_symbol[context["symbol"]] = {
            "summary": summarize(symbol_trades),
            "cost_stress_summary": summarize(symbol_stress),
        }
    ordered = sorted(trades, key=lambda item: (item["entry_time"], item["symbol"]))
    stressed_ordered = sorted(stressed, key=lambda item: (item["entry_time"], item["symbol"]))
    summary = summarize(ordered)
    stress_summary = summarize(stressed_ordered)
    fold_count = positive_folds_by_time(ordered, folds)
    cheap_checks = (
        int(summary["trades"]) >= bootstrap_min_trades
        and float(summary["mean_net_bps"] or -999.0) >= bootstrap_min_mean_bps
        and float(summary["positive_pct"] or 0.0) >= bootstrap_min_positive_pct
        and float(summary["max_drawdown_bps"]) >= bootstrap_max_drawdown_bps
        and fold_count >= bootstrap_min_folds
        and float(stress_summary["mean_net_bps"] or -999.0) > 0.0
    )
    return {
        "summary": summary,
        "positive_folds": fold_count,
        "bootstrap_probability_mean_gt_0": (
            bootstrap_positive_probability([float(row["net_return_bps"]) for row in ordered])
            if cheap_checks
            else None
        ),
        "cost_stress": {"extra_fee_bps_per_leg_side": stress_extra_bps, "summary": stress_summary},
        "by_symbol": by_symbol,
        "sample_trades": ordered[:5],
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
    elif stage == "validation":
        checks = {
            "min_trades": int(summary["trades"]) >= 15,
            "min_mean_net_bps": float(summary["mean_net_bps"] or -999.0) >= 0.0,
            "min_positive_pct": float(summary["positive_pct"] or 0.0) >= 50.0,
            "max_drawdown_bps": float(summary["max_drawdown_bps"]) >= -100.0,
            "min_positive_folds": int(result["positive_folds"]) >= 2,
            "cost_stress_positive": float(stress["mean_net_bps"] or -999.0) > 0.0,
        }
    elif stage == "oos":
        checks = {
            "min_trades": int(summary["trades"]) >= 20,
            "min_mean_net_bps": float(summary["mean_net_bps"] or -999.0) >= 0.0,
            "min_positive_pct": float(summary["positive_pct"] or 0.0) >= 50.0,
            "max_drawdown_bps": float(summary["max_drawdown_bps"]) >= -150.0,
            "min_positive_folds": int(result["positive_folds"]) >= 2,
            "cost_stress_positive": float(stress["mean_net_bps"] or -999.0) > 0.0,
        }
    else:
        raise ValueError(f"unsupported stage: {stage}")
    return {"pass": all(checks.values()), "checks": checks}


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
        "# Multi-Symbol Basis Shock + Funding Alignment Nested Holdout",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Symbols: `{', '.join(report['data']['symbols'])}`",
        "",
        "## Boundary",
        "",
        "- Pre-registered research only; no credentials, paper entry intents, or orders.",
        "- Trade model: long spot / short equal perpetual quantity.",
        "- Signal requires both a positive basis z-score shock and positive recent funding alignment.",
        "- Selection is train-only; validation opens only after train gate; OOS opens only after validation gate.",
        "",
        "## Data",
        "",
        "| Symbol | Matched rows | Funding events | First | Last |",
        "|---|---:|---:|---|---|",
    ]
    for item in report["data"]["by_symbol"]:
        lines.append(
            f"| {item['symbol']} | `{item['matched_rows']}` | `{item['funding_events']}` | `{item['first']}` | `{item['last']}` |"
        )
    lines.extend(
        [
            "",
            "## Result",
            "",
            f"- Tested / train-qualified: `{report['search']['tested']}` / `{report['search']['train_qualified']}`.",
        ]
    )
    selected = report.get("selected_on_train")
    if selected:
        train = selected["train"]["summary"]
        lines.extend(
            [
                f"- Selected: `{selected['strategy_id']}`.",
                f"- Train: `{train['trades']}` trades, mean `{train['mean_net_bps']}` bps, positive `{train['positive_pct']}%`.",
            ]
        )
        if report.get("validation"):
            validation = report["validation"]["summary"]
            lines.append(
                f"- Validation: `{validation['trades']}` trades, mean `{validation['mean_net_bps']}` bps, positive `{validation['positive_pct']}%`."
            )
            validation_gate = report.get("validation_gate") or {}
            failed = [name for name, passed in validation_gate.get("checks", {}).items() if not passed]
            if failed:
                lines.append(f"- Validation failed checks: `{', '.join(failed)}`.")
        if report.get("oos"):
            oos = report["oos"]["summary"]
            lines.append(f"- OOS: `{oos['trades']}` trades, mean `{oos['mean_net_bps']}` bps, positive `{oos['positive_pct']}%`.")
    else:
        best = report["top_train_results_regardless_of_gate"][0] if report["top_train_results_regardless_of_gate"] else None
        if best:
            train = best["train"]["summary"]
            lines.extend(
                [
                    f"- Best rejected: `{best['strategy_id']}`.",
                    f"- Best rejected train: `{train['trades']}` trades, mean `{train['mean_net_bps']}` bps, positive `{train['positive_pct']}%`.",
                ]
            )
        lines.append("- Validation and OOS remained unopened because train produced no qualified candidate.")
    lines.extend(
        [
            "",
            "## Next Action",
            "",
            f"- `{report['next_action']}`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict pooled multi-symbol nested holdout for basis shock with funding alignment.")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--z-window-hours", default="168,336,720")
    parser.add_argument("--entry-z", default="2.0,2.5")
    parser.add_argument("--exit-z", default="0.0,0.5")
    parser.add_argument("--min-basis-bps", default="10,20")
    parser.add_argument("--min-funding-mean-bps", default="0.5,1,2")
    parser.add_argument("--funding-window-events", default="3,6")
    parser.add_argument("--funding-exit-bps", default="0")
    parser.add_argument("--max-hold-hours", default="24,48,72")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-extra-bps", type=float, default=3.0)
    parser.add_argument("--lock-file", default="configs/BASIS_SHOCK_FUNDING_ALIGNMENT_RESEARCH_LOCK.json")
    parser.add_argument("--out-prefix", default="docs/BASIS_SHOCK_FUNDING_ALIGNMENT_MULTI_SYMBOL_NESTED_HOLDOUT_2026-07-02")
    args = parser.parse_args()

    lock_path = resolve_path(args.lock_file)
    lock = load_lock(lock_path)
    cache = resolve_path(args.cache_dir)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    contexts = [load_context(cache, symbol, args.train_end, args.validation_end) for symbol in symbols]
    configs = build_configs(args)
    results: list[dict[str, Any]] = []
    for config in configs:
        train = evaluate_pooled(
            config,
            contexts,
            stage="train",
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps,
            folds=4,
            bootstrap_min_trades=40,
            bootstrap_min_mean_bps=5.0,
            bootstrap_min_positive_pct=55.0,
            bootstrap_min_folds=3,
            bootstrap_max_drawdown_bps=-200.0,
        )
        results.append({"strategy_id": config.strategy_id, "config": asdict(config), "train": train, "train_gate": gate(train, "train")})
    results.sort(key=rank_key, reverse=True)
    qualified = [item for item in results if item["train_gate"]["pass"]]
    selected = qualified[0] if qualified else None
    validation = validation_gate = oos = oos_gate = None
    oos_opened = False
    decision = "reject_no_train_qualified_basis_shock_funding_alignment_candidate"
    if selected:
        config = AlignmentConfig(**selected["config"])
        validation = evaluate_pooled(
            config,
            contexts,
            stage="validation",
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps,
            folds=3,
            bootstrap_min_trades=15,
            bootstrap_min_mean_bps=0.0,
            bootstrap_min_positive_pct=50.0,
            bootstrap_min_folds=2,
            bootstrap_max_drawdown_bps=-100.0,
        )
        validation_gate = gate(validation, "validation")
        decision = "reject_validation_gate_failed_oos_unopened"
        if validation_gate["pass"]:
            oos_opened = True
            oos = evaluate_pooled(
                config,
                contexts,
                stage="oos",
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                stress_extra_bps=args.stress_extra_bps,
                folds=3,
                bootstrap_min_trades=20,
                bootstrap_min_mean_bps=0.0,
                bootstrap_min_positive_pct=50.0,
                bootstrap_min_folds=2,
                bootstrap_max_drawdown_bps=-150.0,
            )
            oos_gate = gate(oos, "oos")
            decision = (
                "basis_shock_funding_alignment_candidate_requires_execution_review"
                if oos_gate["pass"]
                else "reject_oos_gate_failed"
            )
    report = {
        "generated_at": now_iso(),
        "family": "BASIS_SHOCK_FUNDING_ALIGNMENT_MULTI_SYMBOL_1H",
        "method": "pre_registered_pooled_train_search_then_calendar_validation_then_conditionally_open_untouched_oos",
        "selection_frozen_before_validation": True,
        "oos_used_for_selection": False,
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "lock": {
            "path": portable_path(lock_path),
            "lock_id": lock.get("lock_id"),
            "status": lock.get("status"),
        },
        "data": {
            "cache_dir": portable_path(cache),
            "snapshot_provenance": snapshot_provenance(cache),
            "symbols": symbols,
            "train_end": args.train_end,
            "validation_end": args.validation_end,
            "by_symbol": [
                {
                    "symbol": context["symbol"],
                    "matched_rows": len(context["rows"]),
                    "funding_events": len(context["events"]),
                    "first": context["rows"][0]["time"] if context["rows"] else None,
                    "last": context["rows"][-1]["time"] if context["rows"] else None,
                    "spot_path": context["spot_path"],
                    "futures_path": context["futures_path"],
                    "funding_path": context["funding_path"],
                }
                for context in contexts
            ],
        },
        "cost_model": {
            "fee_bps_per_leg_side": args.fee_bps,
            "slippage_bps_per_leg_side": args.slippage_bps,
            "stress_extra_fee_bps_per_leg_side": args.stress_extra_bps,
            "gross_capital_denominator": "spot_notional_plus_perpetual_notional",
        },
        "gates": {
            "train": lock["gates"]["train"],
            "validation": lock["gates"]["validation"],
            "oos": lock["gates"]["oos"],
        },
        "parameter_grid": {
            "z_window_hours": csv_values(args.z_window_hours, int),
            "entry_z": csv_values(args.entry_z, float),
            "exit_z": csv_values(args.exit_z, float),
            "min_basis_bps": csv_values(args.min_basis_bps, float),
            "min_funding_mean_bps": csv_values(args.min_funding_mean_bps, float),
            "funding_window_events": csv_values(args.funding_window_events, int),
            "funding_exit_bps": csv_values(args.funding_exit_bps, float),
            "max_hold_hours": csv_values(args.max_hold_hours, int),
        },
        "search": {"tested": len(results), "train_qualified": len(qualified)},
        "top_train_candidates": qualified[:10],
        "top_train_results_regardless_of_gate": results[:10],
        "selected_on_train": selected,
        "validation": validation,
        "validation_gate": validation_gate,
        "oos_opened": oos_opened,
        "oos": oos,
        "oos_gate": oos_gate,
        "next_action": (
            "manual_execution_model_review_and_shadow_only_no_trade_permission"
            if decision.endswith("requires_execution_review")
            else "reject_or_research_new_mechanism_without_reusing_opened_stage"
        ),
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "tested": len(results),
                "train_qualified": len(qualified),
                "selected": selected["strategy_id"] if selected else None,
                "validation_pass": validation_gate["pass"] if validation_gate else False,
                "oos_opened": oos_opened,
                "can_trade": False,
                "orders_allowed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
