#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

try:
    from tools.basis_funding_carry_nested_holdout import (
        CarryConfig,
        aligned_bars,
        build_configs,
        funding_events,
        rolling_funding_means,
        simulate_window,
        split_index,
        summarize,
    )
except ModuleNotFoundError:  # pragma: no cover - direct execution from tools/
    from basis_funding_carry_nested_holdout import (
        CarryConfig,
        aligned_bars,
        build_configs,
        funding_events,
        rolling_funding_means,
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
    funding_mean = rolling_funding_means(rows, events)
    return {
        "symbol": symbol,
        "spot_path": portable_path(spot_path),
        "futures_path": portable_path(futures_path),
        "funding_path": portable_path(funding_path),
        "rows": rows,
        "events": events,
        "funding_mean": funding_mean,
        "train_end": split_index(rows, train_end),
        "validation_end": split_index(rows, validation_end),
    }


def stage_bounds(context: dict[str, Any], stage: str) -> tuple[int, int]:
    if stage == "train":
        return 0, int(context["train_end"])
    if stage == "validation":
        return int(context["train_end"]), int(context["validation_end"])
    if stage == "oos":
        return int(context["validation_end"]), len(context["rows"])
    raise ValueError(f"unsupported stage: {stage}")


def positive_folds_by_time(
    trades: list[dict[str, Any]],
    folds: int,
    value_key: str = "net_return_bps_on_gross_capital",
) -> int:
    if not trades:
        return 0
    ordered = sorted(trades, key=lambda item: (item["entry_time"], item.get("symbol", "")))
    count = 0
    for fold in range(folds):
        start = round(len(ordered) * fold / folds)
        end = round(len(ordered) * (fold + 1) / folds)
        chunk = ordered[start:end]
        if len(chunk) >= 3 and mean(float(item[value_key]) for item in chunk) > 0:
            count += 1
    return count


def bootstrap_positive_probability(
    values: list[float],
    iterations: int = 2_000,
    seed: int = 20260630,
) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(iterations):
        sample_mean = mean(rng.choice(values) for _ in values)
        positive += int(sample_mean > 0.0)
    return round(positive / iterations, 6)


def evaluate_pooled(
    config: CarryConfig,
    contexts: list[dict[str, Any]],
    *,
    stage: str,
    fee_bps: float,
    slippage_bps: float,
    stress_extra_bps: float,
    folds: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    stressed: list[dict[str, Any]] = []
    by_symbol: dict[str, dict[str, Any]] = {}
    for context in contexts:
        start_index, end_index = stage_bounds(context, stage)
        symbol_trades = with_symbol(
            context["symbol"],
            simulate_window(
                config,
                context["rows"],
                context["funding_mean"],
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
                context["funding_mean"],
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
        stage == "train"
        and int(summary["trades"]) >= args.min_train_trades
        and summary["mean_net_bps"] is not None
        and float(summary["mean_net_bps"]) >= args.min_train_mean_bps
        and summary["positive_pct"] is not None
        and float(summary["positive_pct"]) >= args.min_train_positive_pct
        and float(summary["max_drawdown_bps"]) >= -abs(args.max_train_drawdown_bps)
        and fold_count >= args.min_train_positive_folds
        and stress_summary["mean_net_bps"] is not None
        and float(stress_summary["mean_net_bps"]) > 0.0
    )
    values = [float(row["net_return_bps_on_gross_capital"]) for row in ordered]
    return {
        "summary": summary,
        "positive_folds": fold_count,
        "bootstrap_probability_mean_gt_0": bootstrap_positive_probability(values) if cheap_train_checks else None,
        "cost_stress": {"extra_fee_bps_per_leg_side": stress_extra_bps, "summary": stress_summary},
        "by_symbol": by_symbol,
        "sample_trades": ordered[:5],
    }


def strict_gate(result: dict[str, Any], *, stage: str, args: argparse.Namespace) -> dict[str, Any]:
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
    if stage == "train":
        checks["bootstrap_probability"] = float(result.get("bootstrap_probability_mean_gt_0") or 0.0) >= args.min_train_bootstrap_probability
    return {"pass": all(checks.values()), "checks": checks}


def rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    summary = item["train"]["summary"]
    stress = item["train"]["cost_stress"]["summary"]
    return (
        float(stress["mean_net_bps"] or -999.0) * math.sqrt(max(1, int(summary["trades"]))),
        int(item["train"]["positive_folds"]),
        int(summary["trades"]),
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Multi-Symbol Basis/Funding Carry Nested Holdout",
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
            failed = [name for name, passed in (report.get("validation_gate") or {}).get("checks", {}).items() if not passed]
            if failed:
                lines.append(f"- Validation failed checks: `{', '.join(failed)}`.")
        if report.get("oos"):
            oos = report["oos"]["summary"]
            lines.append(f"- OOS: `{oos['trades']}` trades, mean `{oos['mean_net_bps']}` bps, positive `{oos['positive_pct']}%`.")
            failed = [name for name, passed in (report.get("oos_gate") or {}).get("checks", {}).items() if not passed]
            if failed:
                lines.append(f"- OOS failed checks: `{', '.join(failed)}`.")
    else:
        lines.append("- Validation and OOS remained unopened because train produced no qualified candidate.")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Pooled multi-symbol market-neutral research only.",
            "- Selection is train-only; validation opens only after train gate; OOS opens only after validation gate.",
            "- No paper/live execution permission.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict pooled multi-symbol nested holdout for basis/funding carry.")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--train-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2026-01-01T00:00:00+00:00")
    parser.add_argument("--entry-basis-bps", default="5,10,15")
    parser.add_argument("--min-funding-mean-bps", default="0.5,1,2")
    parser.add_argument("--max-hold-hours", default="168,336")
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-extra-bps", type=float, default=3.0)
    parser.add_argument("--min-train-trades", type=int, default=40)
    parser.add_argument("--min-train-mean-bps", type=float, default=5.0)
    parser.add_argument("--min-train-positive-pct", type=float, default=55.0)
    parser.add_argument("--max-train-drawdown-bps", type=float, default=200.0)
    parser.add_argument("--min-train-positive-folds", type=int, default=3)
    parser.add_argument("--min-train-bootstrap-probability", type=float, default=0.95)
    parser.add_argument("--min-validation-trades", type=int, default=15)
    parser.add_argument("--min-validation-mean-bps", type=float, default=0.0)
    parser.add_argument("--min-validation-positive-pct", type=float, default=50.0)
    parser.add_argument("--max-validation-drawdown-bps", type=float, default=150.0)
    parser.add_argument("--min-validation-positive-folds", type=int, default=2)
    parser.add_argument("--min-oos-trades", type=int, default=15)
    parser.add_argument("--min-oos-mean-bps", type=float, default=0.0)
    parser.add_argument("--min-oos-positive-pct", type=float, default=50.0)
    parser.add_argument("--max-oos-drawdown-bps", type=float, default=150.0)
    parser.add_argument("--min-oos-positive-folds", type=int, default=2)
    parser.add_argument("--out-prefix", default="docs/BASIS_FUNDING_CARRY_MULTI_SYMBOL_NESTED_HOLDOUT_2026-06-30")
    args = parser.parse_args()

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
            args=args,
        )
        results.append({"strategy_id": config.strategy_id, "config": asdict(config), "train": train, "train_gate": strict_gate(train, stage="train", args=args)})
    results.sort(key=rank_key, reverse=True)
    qualified = [item for item in results if item["train_gate"]["pass"]]
    selected = qualified[0] if qualified else None
    validation = validation_gate = oos = oos_gate = None
    oos_opened = False
    decision = "reject_no_train_qualified_multi_symbol_basis_funding_carry_candidate"
    if selected:
        config = CarryConfig(**selected["config"])
        validation = evaluate_pooled(
            config,
            contexts,
            stage="validation",
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps,
            folds=3,
            args=args,
        )
        validation_gate = strict_gate(validation, stage="validation", args=args)
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
                args=args,
            )
            oos_gate = strict_gate(oos, stage="oos", args=args)
            decision = "multi_symbol_basis_funding_carry_candidate_requires_execution_review" if oos_gate["pass"] else "reject_oos_gate_failed"
    report = {
        "generated_at": now_iso(),
        "family": "BASIS_FUNDING_CARRY_MULTI_SYMBOL_1H",
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
            "gross_capital_denominator": "spot_notional_plus_perpetual_notional",
        },
        "gates": {
            "train": {
                "min_trades": args.min_train_trades,
                "min_mean_net_bps": args.min_train_mean_bps,
                "min_positive_pct": args.min_train_positive_pct,
                "positive_folds": args.min_train_positive_folds,
                "bootstrap_probability": args.min_train_bootstrap_probability,
                "stress_positive": True,
            },
            "validation": {
                "min_trades": args.min_validation_trades,
                "min_mean_net_bps": args.min_validation_mean_bps,
                "min_positive_pct": args.min_validation_positive_pct,
                "positive_folds": args.min_validation_positive_folds,
                "stress_positive": True,
            },
        },
        "search": {"tested": len(results), "train_qualified": len(qualified)},
        "top_train_candidates": qualified[:10],
        "top_train_results_regardless_of_gate": results[:18],
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
