#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result):
        return default
    return result


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fold_stats(folds: list[dict[str, Any]]) -> dict[str, Any]:
    if not folds:
        return {
            "folds": 0,
            "stable_folds": 0,
            "positive_folds": 0,
            "negative_folds": 0,
            "min_fold_trades": 0,
            "max_fold_profit_share": None,
        }
    net_values = [as_float(item.get("net_r_total")) for item in folds]
    abs_total = sum(abs(value) for value in net_values)
    return {
        "folds": len(folds),
        "stable_folds": sum(1 for item in folds if item.get("stable")),
        "positive_folds": sum(1 for item in folds if as_float(item.get("expectancy_r")) > 0),
        "negative_folds": sum(1 for item in folds if as_float(item.get("expectancy_r")) < 0),
        "min_fold_trades": min(as_int(item.get("trades")) for item in folds),
        "max_fold_profit_share": round(max(abs(value) for value in net_values) / abs_total, 6) if abs_total else None,
    }


def data_quality_flags(result: dict[str, Any], datasets: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    params = result.get("params", {})
    filter_mode = str(params.get("filter_mode", ""))
    if filter_mode in {"all_filters", "oi_confirmation"}:
        oi_coverages = [as_float(item.get("oi_coverage_pct")) for item in datasets]
        if oi_coverages and min(oi_coverages) < 50.0:
            flags.append("low_oi_coverage_for_oi_filter")
    if not datasets:
        flags.append("dataset_metadata_missing")
    return flags


def classify_result(
    result: dict[str, Any],
    *,
    datasets: list[dict[str, Any]],
    min_trades: int,
    min_winrate_pct: float,
    min_expectancy_r: float,
    min_stable_folds: int,
) -> dict[str, Any]:
    summary = result.get("summary", {})
    folds = result.get("folds", [])
    stats = fold_stats(folds if isinstance(folds, list) else [])
    trades = as_int(summary.get("trades"))
    winrate = as_float(summary.get("winrate_pct"))
    expectancy = as_float(summary.get("expectancy_r"), default=-999.0)
    max_dd = as_float(summary.get("max_drawdown_r"))
    original_gate = result.get("gate", {})
    flags = data_quality_flags(result, datasets)

    if trades < min_trades:
        flags.append("sample_too_small")
    if winrate < min_winrate_pct:
        flags.append("winrate_below_gate")
    if expectancy < min_expectancy_r:
        flags.append("expectancy_below_gate")
    if stats["stable_folds"] < min_stable_folds:
        flags.append("unstable_folds")
    if stats["min_fold_trades"] and stats["min_fold_trades"] < 10:
        flags.append("fold_sample_fragile")
    if stats["max_fold_profit_share"] is not None and stats["max_fold_profit_share"] > 0.60:
        flags.append("profit_concentrated_in_one_fold")
    if trades < 30 and winrate >= 60.0:
        flags.append("small_sample_high_winrate_risk")
    if expectancy > 0 and stats["positive_folds"] < 2:
        flags.append("positive_total_but_not_repeated")
    if max_dd < -20.0:
        flags.append("drawdown_too_deep")

    gate_pass = bool(original_gate.get("pass"))
    critical = {
        "sample_too_small",
        "unstable_folds",
        "expectancy_below_gate",
        "winrate_below_gate",
        "low_oi_coverage_for_oi_filter",
    }
    if gate_pass and not any(flag in critical for flag in flags):
        verdict = "paper_candidate_after_independent_oos"
    elif expectancy > 0 and trades >= 20 and stats["positive_folds"] >= 2:
        verdict = "watchlist_only"
    else:
        verdict = "research_only_or_reject"

    quality_score = (
        expectancy * 100.0
        + min(trades, min_trades) / max(min_trades, 1) * 10.0
        + stats["stable_folds"] * 5.0
        + stats["positive_folds"] * 2.0
        + max(winrate - 50.0, -25.0) * 0.2
        - len(flags) * 2.0
    )
    return {
        "strategy_id": result.get("strategy_id"),
        "summary": summary,
        "fold_stats": stats,
        "original_gate": original_gate,
        "quality_flags": flags,
        "quality_score": round(quality_score, 6),
        "research_verdict": verdict,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Research Gate Audit",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Source: `{report['source']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Audit-only layer over an existing hardening JSON report.",
        "- Does not send orders.",
        "- Does not unlock paper/live trading.",
        "- A successful script run is not a successful strategy.",
        "",
        "## Result",
        "",
        f"- Quality-pass candidates: `{report['quality_pass_count']}`.",
        f"- Watchlist-only candidates: `{report['watchlist_count']}`.",
        f"- Blocked/research-only candidates: `{report['blocked_count']}`.",
        "",
        "## Main Drift Check",
        "",
    ]
    if report["quality_pass_count"] == 0:
        lines.append("- Docs and handoff remain aligned: combined regime is still research-only.")
    else:
        lines.append("- A candidate needs a separate out-of-sample replay before any paper route.")
    lines.extend(
        [
            "",
            "## Top Audited Results",
            "",
            "| Strategy | Trades | Winrate | Exp R | Stable Folds | Positive Folds | Verdict | Flags |",
            "|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in report["top_audited_results"]:
        summary = item["summary"]
        stats = item["fold_stats"]
        flags = ", ".join(item["quality_flags"][:4])
        if len(item["quality_flags"]) > 4:
            flags += ", ..."
        lines.append(
            f"| `{item['strategy_id']}` | `{summary.get('trades')}` | `{summary.get('winrate_pct')}` | "
            f"`{summary.get('expectancy_r')}` | `{stats['stable_folds']}` | `{stats['positive_folds']}` | "
            f"`{item['research_verdict']}` | `{flags or 'none'}` |"
        )
    lines.extend(
        [
            "",
            "## Practical Interpretation",
            "",
            "- If a strategy has high winrate but too few trades, treat it as sample noise.",
            "- If OI filters are used while OI coverage is low, do not trust the filter result.",
            "- If profit is concentrated in one fold, assume regime dependence until proven otherwise.",
            "- The next real step is faster broad re-run plus independent out-of-sample, not live trading.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit hardening JSON results for sample, fold and data-quality risk")
    parser.add_argument("--input", default="docs/COMBINED_REGIME_HARDENING_2026-06-03.json")
    parser.add_argument("--out-prefix", default="docs/RESEARCH_GATE_AUDIT_COMBINED_REGIME_2026-06-03")
    args = parser.parse_args()

    source = Path(args.input)
    payload = load_json(source)
    gate_requirements = payload.get("gate_requirements", {})
    datasets = payload.get("datasets", [])
    all_results = payload.get("all_results", [])
    min_trades = as_int(gate_requirements.get("min_trades"), 100)
    min_expectancy_r = as_float(gate_requirements.get("min_expectancy_r"), 0.03)
    min_winrate_pct = as_float(gate_requirements.get("min_winrate_pct"), 51.0)
    min_stable_folds = as_int(gate_requirements.get("min_stable_folds"), 3)

    audited = [
        classify_result(
            item,
            datasets=datasets if isinstance(datasets, list) else [],
            min_trades=min_trades,
            min_winrate_pct=min_winrate_pct,
            min_expectancy_r=min_expectancy_r,
            min_stable_folds=min_stable_folds,
        )
        for item in all_results
        if isinstance(item, dict)
    ]
    audited_ranked = sorted(
        audited,
        key=lambda item: (
            1 if item["research_verdict"] == "paper_candidate_after_independent_oos" else 0,
            1 if item["research_verdict"] == "watchlist_only" else 0,
            item["quality_score"],
            as_int(item["summary"].get("trades")),
        ),
        reverse=True,
    )
    report = {
        "generated_at": now_iso(),
        "source": str(source),
        "runtime_boundary": {
            "classification": "audit_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "gate_requirements": {
            "min_trades": min_trades,
            "min_expectancy_r": min_expectancy_r,
            "min_winrate_pct": min_winrate_pct,
            "min_stable_folds": min_stable_folds,
        },
        "quality_pass_count": sum(1 for item in audited if item["research_verdict"] == "paper_candidate_after_independent_oos"),
        "watchlist_count": sum(1 for item in audited if item["research_verdict"] == "watchlist_only"),
        "blocked_count": sum(1 for item in audited if item["research_verdict"] == "research_only_or_reject"),
        "top_audited_results": audited_ranked[:15],
        "all_audited_results": audited,
    }
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(
        {
            "quality_pass_count": report["quality_pass_count"],
            "watchlist_count": report["watchlist_count"],
            "blocked_count": report["blocked_count"],
            "top": [
                {
                    "strategy_id": item["strategy_id"],
                    "verdict": item["research_verdict"],
                    "quality_flags": item["quality_flags"],
                    "summary": item["summary"],
                    "fold_stats": item["fold_stats"],
                }
                for item in report["top_audited_results"][:5]
            ],
            "json": str(out_prefix.with_suffix(".json")),
            "md": str(out_prefix.with_suffix(".md")),
            "can_trade": False,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
