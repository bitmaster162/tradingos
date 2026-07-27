#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tools.basis_shock_reversion_nested_holdout import (
        ReversionConfig,
        aligned_bars,
        bootstrap_positive_probability,
        build_configs,
        funding_events,
        gate,
        generate_signals,
        rank_key,
        rolling_basis_z,
        simulate_window,
        split_index,
        summarize,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution from tools/
    from basis_shock_reversion_nested_holdout import (
        ReversionConfig,
        aligned_bars,
        bootstrap_positive_probability,
        build_configs,
        funding_events,
        gate,
        generate_signals,
        rank_key,
        rolling_basis_z,
        simulate_window,
        split_index,
        summarize,
    )


ROOT = Path(__file__).resolve().parents[1]


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


def with_symbol(symbol: str, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**trade, "symbol": symbol} for trade in trades]


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
        "z_cache": {window: rolling_basis_z(rows, window) for window in (168, 336, 720)},
        "signal_cache": {},
    }


def signal_cache_key(config: ReversionConfig) -> tuple[int, float, float]:
    return (config.z_window_hours, config.entry_z, config.min_basis_bps)


def signals_for(context: dict[str, Any], config: ReversionConfig) -> list[int]:
    key = signal_cache_key(config)
    if key not in context["signal_cache"]:
        context["signal_cache"][key] = generate_signals(config, context["rows"], context["z_cache"][config.z_window_hours])
    return context["signal_cache"][key]


def stage_bounds(context: dict[str, Any], stage: str) -> tuple[int, int]:
    if stage == "train":
        return 0, int(context["train_end"])
    if stage == "validation":
        return int(context["train_end"]), int(context["validation_end"])
    if stage == "oos":
        return int(context["validation_end"]), len(context["rows"])
    raise ValueError(f"unsupported stage: {stage}")


def positive_folds_by_time(trades: list[dict[str, Any]], folds: int, value_key: str = "net_return_bps") -> int:
    if not trades:
        return 0
    ordered = sorted(trades, key=lambda item: (item["entry_time"], item.get("symbol", "")))
    positive = 0
    for fold in range(folds):
        start = round(len(ordered) * fold / folds)
        end = round(len(ordered) * (fold + 1) / folds)
        chunk = ordered[start:end]
        if len(chunk) >= 3 and statistics.mean(float(row[value_key]) for row in chunk) > 0:
            positive += 1
    return positive


def evaluate_pooled(
    config: ReversionConfig,
    contexts: list[dict[str, Any]],
    *,
    stage: str,
    fee_bps: float,
    slippage_bps: float,
    stress_extra_bps: float,
    folds: int,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    stressed: list[dict[str, Any]] = []
    by_symbol: dict[str, dict[str, Any]] = {}
    for context in contexts:
        start_index, end_index = stage_bounds(context, stage)
        z_values = context["z_cache"][config.z_window_hours]
        signals = signals_for(context, config)
        symbol_trades = with_symbol(
            context["symbol"],
            simulate_window(
                config,
                context["rows"],
                z_values,
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
            bootstrap_positive_probability([float(row["net_return_bps"]) for row in ordered])
            if cheap_train_checks
            else None
        ),
        "cost_stress": {"extra_fee_bps_per_leg_side": stress_extra_bps, "summary": stress_summary},
        "by_symbol": by_symbol,
        "sample_trades": ordered[:5],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Multi-Symbol Basis Shock Reversion Nested Holdout",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Symbols: `{', '.join(report['data']['symbols'])}`",
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
        lines.append("- Validation and OOS remained unopened because train produced no qualified candidate.")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Pooled multi-symbol research only.",
            "- Selection is train-only; validation opens only after train gate; OOS opens only after validation gate.",
            "- No paper/live execution permission.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict pooled multi-symbol nested holdout for basis-shock reversion.")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-extra-bps", type=float, default=3.0)
    parser.add_argument("--out-prefix", default="docs/BASIS_SHOCK_REVERSION_MULTI_SYMBOL_NESTED_HOLDOUT_2026-06-30")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    contexts = [load_context(cache, symbol, args.train_end, args.validation_end) for symbol in symbols]
    results: list[dict[str, Any]] = []
    for config in build_configs():
        train = evaluate_pooled(
            config,
            contexts,
            stage="train",
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps,
            folds=4,
        )
        results.append({"strategy_id": config.strategy_id, "config": asdict(config), "train": train, "train_gate": gate(train, "train")})
    results.sort(key=rank_key, reverse=True)
    qualified = [item for item in results if item["train_gate"]["pass"]]
    selected = qualified[0] if qualified else None
    validation = validation_gate = oos = oos_gate = None
    oos_opened = False
    decision = "reject_no_train_qualified_multi_symbol_basis_shock_candidate"
    if selected:
        config = ReversionConfig(**selected["config"])
        validation = evaluate_pooled(
            config,
            contexts,
            stage="validation",
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps,
            folds=3,
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
            )
            oos_gate = gate(oos, "validation")
            decision = "multi_symbol_basis_shock_candidate_requires_execution_review" if oos_gate["pass"] else "reject_oos_gate_failed"
    report = {
        "generated_at": now_iso(),
        "family": "BASIS_SHOCK_REVERSION_MULTI_SYMBOL_1H",
        "method": "pooled_train_search_then_calendar_validation_then_conditionally_open_untouched_oos",
        "selection_frozen_before_validation": True,
        "oos_used_for_selection": False,
        "decision": decision,
        "can_trade": False,
        "data": {
            "cache_dir": portable_path(cache),
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
        },
        "gates": {
            "train": {"min_trades": 40, "min_mean_net_bps": 5.0, "min_positive_pct": 55.0, "positive_folds": 3, "bootstrap_probability": 0.95, "stress_positive": True},
            "validation": {"min_trades": 15, "min_mean_net_bps": 0.0, "min_positive_pct": 50.0, "positive_folds": 2, "stress_positive": True},
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
        "next_action": "manual_execution_model_review_only_no_trade_permission" if decision.endswith("requires_execution_review") else "reject_or_research_new_mechanism_without_reusing_opened_stage",
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
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
