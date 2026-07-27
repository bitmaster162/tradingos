#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"_read_error": "invalid_json", "_path": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": str(path)}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def safe_int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def gate(name: str, passed: bool, actual: Any, required: Any) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "required": required,
        "severity": "hard",
    }


def gates_pass(items: list[dict[str, Any]]) -> bool:
    return all(item.get("passed") for item in items)


def render_gate_table(items: list[dict[str, Any]]) -> list[str]:
    lines = ["| gate | pass | actual | required |", "|---|---:|---|---|"]
    for item in items:
        lines.append(f"| {item.get('name')} | `{item.get('passed')}` | `{item.get('actual')}` | `{item.get('required')}` |")
    return lines


def evaluate_variant(row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    variant_id = str(row.get("variant_id") or "unknown")
    evidence = row.get("historical_evidence") if isinstance(row.get("historical_evidence"), dict) else {}
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}

    full_trades = safe_int(evidence.get("full_trades"))
    holdout_trades = safe_int(evidence.get("holdout_trades"))
    full_expectancy = safe_float(evidence.get("full_expectancy_r"))
    holdout_expectancy = safe_float(evidence.get("holdout_expectancy_r"))
    segment_positive_ratio = safe_float(evidence.get("segment_positive_ratio"))
    worst_segment_expectancy = safe_float(evidence.get("worst_segment_expectancy_r"))
    cost10_expectancy = safe_float(evidence.get("cost10_expectancy_r"))

    shadow_signals = safe_int(summary.get("shadow_signal_events"))
    resolved = safe_int(summary.get("resolved"))
    expectancy = safe_float(summary.get("expectancy_r"))
    winrate = safe_float(summary.get("winrate_pct"))
    breakeven = safe_float(summary.get("breakeven_winrate_pct"))
    max_drawdown = safe_float(summary.get("max_drawdown_r"))

    historical_gates = [
        gate("historical_verdict", evidence.get("verdict") == "shadow_research_shape_passed", evidence.get("verdict"), "shadow_research_shape_passed"),
        gate("history_min_full_trades", full_trades >= args.min_full_trades, full_trades, args.min_full_trades),
        gate("history_full_expectancy", full_expectancy is not None and full_expectancy >= args.min_history_expectancy_r, full_expectancy, args.min_history_expectancy_r),
        gate("history_min_holdout_trades", holdout_trades >= args.min_holdout_trades, holdout_trades, args.min_holdout_trades),
        gate("history_holdout_expectancy", holdout_expectancy is not None and holdout_expectancy >= args.min_holdout_expectancy_r, holdout_expectancy, args.min_holdout_expectancy_r),
        gate("history_segment_positive_ratio", segment_positive_ratio is not None and segment_positive_ratio >= args.min_segment_positive_ratio, segment_positive_ratio, args.min_segment_positive_ratio),
        gate("history_worst_segment_floor", worst_segment_expectancy is not None and worst_segment_expectancy >= args.min_worst_segment_expectancy_r, worst_segment_expectancy, args.min_worst_segment_expectancy_r),
        gate("history_cost10_expectancy", cost10_expectancy is not None and cost10_expectancy >= args.min_cost10_expectancy_r, cost10_expectancy, args.min_cost10_expectancy_r),
    ]
    forward_gates = [
        gate("shadow_signal_events", shadow_signals >= args.min_shadow_signals, shadow_signals, args.min_shadow_signals),
        gate("shadow_resolved_outcomes", resolved >= args.min_resolved, resolved, args.min_resolved),
        gate("shadow_expectancy", expectancy is not None and expectancy >= args.min_forward_expectancy_r, expectancy, args.min_forward_expectancy_r),
        gate("shadow_winrate_vs_breakeven", winrate is not None and breakeven is not None and winrate >= breakeven, f"{winrate} vs {breakeven}", "winrate >= breakeven"),
        gate("shadow_drawdown_cap", max_drawdown is not None and max_drawdown >= -abs(args.max_drawdown_r), max_drawdown, f">= -{abs(args.max_drawdown_r)}R"),
    ]
    historical_ok = gates_pass(historical_gates)
    forward_ok = gates_pass(forward_gates)
    if not historical_ok:
        verdict = "shadow_blocked_historical_shape"
    elif not forward_ok:
        verdict = "shadow_waiting_forward_outcomes"
    else:
        verdict = "shadow_candidate_for_paper_design_review"
    return {
        "variant_id": variant_id,
        "strategy_id": row.get("strategy_id"),
        "historical_evidence": evidence,
        "summary": summary,
        "historical_gates": historical_gates,
        "forward_gates": forward_gates,
        "historical_ok": historical_ok,
        "forward_ok": forward_ok,
        "verdict": verdict,
    }


def rank_variant(row: dict[str, Any]) -> tuple[Any, ...]:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    evidence = row.get("historical_evidence") if isinstance(row.get("historical_evidence"), dict) else {}
    verdict_rank = {
        "shadow_candidate_for_paper_design_review": 3,
        "shadow_waiting_forward_outcomes": 2,
        "shadow_blocked_historical_shape": 1,
    }
    return (
        verdict_rank.get(str(row.get("verdict")), 0),
        safe_int(summary.get("resolved")),
        safe_int(summary.get("shadow_signal_events")),
        safe_float(summary.get("expectancy_r")) or -999.0,
        safe_float(evidence.get("cost10_expectancy_r")) or -999.0,
        safe_float(evidence.get("full_expectancy_r")) or -999.0,
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    scoreboard_path = resolve_path(args.scoreboard)
    ablation_path = resolve_path(args.ablation)
    scoreboard = read_json(scoreboard_path)
    ablation = read_json(ablation_path)
    variants = scoreboard.get("variants") if isinstance(scoreboard.get("variants"), list) else []
    evaluated = [evaluate_variant(row, args) for row in variants if isinstance(row, dict)]
    evaluated.sort(key=rank_variant, reverse=True)

    historical_ready = [row for row in evaluated if row.get("historical_ok")]
    paper_design_ready = [row for row in evaluated if row.get("historical_ok") and row.get("forward_ok")]
    summary = scoreboard.get("summary") if isinstance(scoreboard.get("summary"), dict) else {}

    input_gates = [
        gate("scoreboard_report_exists", bool(scoreboard) and not scoreboard.get("_read_error"), rel_path(scoreboard_path), "readable JSON"),
        gate("ablation_report_exists", bool(ablation) and not ablation.get("_read_error"), rel_path(ablation_path), "readable JSON"),
        gate("scoreboard_no_trade_permission", scoreboard.get("can_trade") is False, scoreboard.get("can_trade"), False),
        gate("ablation_no_trade_permission", ablation.get("can_trade") is False, ablation.get("can_trade"), False),
        gate("variants_present", len(evaluated) > 0, len(evaluated), "> 0"),
    ]
    inputs_ok = gates_pass(input_gates)

    if not inputs_ok:
        decision = "blocked_shadow_gate_input_error"
        next_action = "fix shadow scoreboard/ablation reports before promotion review"
    elif not historical_ready:
        decision = "blocked_no_shadow_variant_with_historical_shape"
        next_action = "keep shadow variants research-only; no historical candidate is strong enough"
    elif not paper_design_ready:
        decision = "blocked_waiting_shadow_forward_outcomes"
        next_action = "keep shadow observer and scoreboard running until enough real resolved shadow outcomes exist"
    else:
        decision = "shadow_candidate_for_paper_design_review_only"
        next_action = "manual review required; design a separate paper-entry gate before any execution path"

    promotion = {
        "shadow_observer_allowed": inputs_ok and bool(historical_ready),
        "paper_design_review_allowed": inputs_ok and bool(paper_design_ready),
        "paper_execution_allowed": False,
        "live_execution_allowed": False,
        "manual_review_required": True,
        "can_trade": False,
    }
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_filter_shadow_promotion_gate_evidence_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "inputs": {
            "scoreboard": rel_path(scoreboard_path),
            "ablation": rel_path(ablation_path),
        },
        "thresholds": {
            "min_full_trades": args.min_full_trades,
            "min_history_expectancy_r": args.min_history_expectancy_r,
            "min_holdout_trades": args.min_holdout_trades,
            "min_holdout_expectancy_r": args.min_holdout_expectancy_r,
            "min_segment_positive_ratio": args.min_segment_positive_ratio,
            "min_worst_segment_expectancy_r": args.min_worst_segment_expectancy_r,
            "min_cost10_expectancy_r": args.min_cost10_expectancy_r,
            "min_shadow_signals": args.min_shadow_signals,
            "min_resolved": args.min_resolved,
            "min_forward_expectancy_r": args.min_forward_expectancy_r,
            "max_drawdown_r": args.max_drawdown_r,
        },
        "scoreboard_summary": {
            "classification": summary.get("classification"),
            "shadow_signal_events": summary.get("shadow_signal_events"),
            "resolved": summary.get("resolved"),
            "expectancy_r": summary.get("expectancy_r"),
        },
        "ablation": {
            "decision": ablation.get("decision") if isinstance(ablation, dict) else None,
            "tested": ablation.get("tested") if isinstance(ablation, dict) else None,
            "shadow_shape_pass_count": ablation.get("shadow_shape_pass_count") if isinstance(ablation, dict) else None,
        },
        "input_gates": input_gates,
        "variants": evaluated,
        "best_variant": evaluated[0] if evaluated else None,
        "historical_ready_variants": [row.get("variant_id") for row in historical_ready],
        "paper_design_ready_variants": [row.get("variant_id") for row in paper_design_ready],
        "promotion": promotion,
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    promotion = report.get("promotion") if isinstance(report.get("promotion"), dict) else {}
    summary = report.get("scoreboard_summary") if isinstance(report.get("scoreboard_summary"), dict) else {}
    ablation = report.get("ablation") if isinstance(report.get("ablation"), dict) else {}
    best = report.get("best_variant") if isinstance(report.get("best_variant"), dict) else {}
    lines = [
        "# Range Refined Filter Shadow Promotion Gate",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Evidence gate only for RANGE shadow filter variants.",
        "- Does not change the active RANGE observer.",
        "- Does not create paper-entry intents.",
        "- Does not send orders or grant live permission.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report.get('decision')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        f"- Shadow observer allowed: `{promotion.get('shadow_observer_allowed')}`.",
        f"- Paper-design review allowed: `{promotion.get('paper_design_review_allowed')}`.",
        f"- Paper execution allowed: `{promotion.get('paper_execution_allowed')}`.",
        f"- Live execution allowed: `{promotion.get('live_execution_allowed')}`.",
        "",
        "## Current Evidence",
        "",
        f"- Shadow scoreboard: `{summary.get('classification')}`, signals `{summary.get('shadow_signal_events')}`, resolved `{summary.get('resolved')}`, expectancy `{summary.get('expectancy_r')}`.",
        f"- Historical ablation: `{ablation.get('decision')}`, tested `{ablation.get('tested')}`, shape-pass `{ablation.get('shadow_shape_pass_count')}`.",
        f"- Historical-ready variants: `{report.get('historical_ready_variants')}`.",
        f"- Paper-design-ready variants: `{report.get('paper_design_ready_variants')}`.",
        "",
        "## Best Ranked Variant",
        "",
        f"- Variant: `{best.get('variant_id')}`.",
        f"- Verdict: `{best.get('verdict')}`.",
        f"- Historical ok / forward ok: `{best.get('historical_ok')}` / `{best.get('forward_ok')}`.",
        "",
        "## Input Gates",
        "",
        *render_gate_table(report.get("input_gates", [])),
        "",
        "## Variant Summary",
        "",
        "| Variant | Verdict | Hist OK | Fwd OK | Signals | Resolved | Exp R | Cost +10 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("variants", []):
        if not isinstance(row, dict):
            continue
        row_summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        evidence = row.get("historical_evidence") if isinstance(row.get("historical_evidence"), dict) else {}
        lines.append(
            f"| `{row.get('variant_id')}` | `{row.get('verdict')}` | `{row.get('historical_ok')}` | `{row.get('forward_ok')}` | "
            f"`{row_summary.get('shadow_signal_events')}` | `{row_summary.get('resolved')}` | `{row_summary.get('expectancy_r')}` | `{evidence.get('cost10_expectancy_r')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    if hasattr(__import__("sys").stdout, "reconfigure"):
        __import__("sys").stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gate RANGE shadow filter variants before paper-design review")
    parser.add_argument("--scoreboard", default="docs/RANGE_REFINED_FILTER_SHADOW_FORWARD_SCOREBOARD_2026-06-17.json")
    parser.add_argument("--ablation", default="docs/RANGE_REFINED_FILTER_SHADOW_ABLATION_2026-06-17.json")
    parser.add_argument("--min-full-trades", type=int, default=60)
    parser.add_argument("--min-history-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-holdout-trades", type=int, default=15)
    parser.add_argument("--min-holdout-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-segment-positive-ratio", type=float, default=0.8)
    parser.add_argument("--min-worst-segment-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-cost10-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-shadow-signals", type=int, default=30)
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--min-forward-expectancy-r", type=float, default=0.05)
    parser.add_argument("--max-drawdown-r", type=float, default=8.0)
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_FILTER_SHADOW_PROMOTION_GATE_2026-06-17")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report.get("decision"),
                "shadow_observer_allowed": report.get("promotion", {}).get("shadow_observer_allowed"),
                "paper_design_review_allowed": report.get("promotion", {}).get("paper_design_review_allowed"),
                "historical_ready_variants": report.get("historical_ready_variants"),
                "paper_design_ready_variants": report.get("paper_design_ready_variants"),
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
