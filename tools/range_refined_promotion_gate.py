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
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0
    return parsed


def nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def gate(name: str, passed: bool, actual: Any, required: Any, severity: str = "hard") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "required": required,
        "severity": severity,
    }


def gates_pass(items: list[dict[str, Any]], *, severity: str = "hard") -> bool:
    return all(item.get("passed") for item in items if item.get("severity") == severity)


def selected_cost_expectancy(selected: dict[str, Any], extra_bps: float) -> float | None:
    rows = selected.get("cost_stress")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if safe_float(row.get("extra_bps_per_side")) == extra_bps:
            return safe_float(nested(row, "summary", "expectancy_r"))
    return None


def render_gate_table(items: list[dict[str, Any]]) -> list[str]:
    lines = ["| gate | pass | actual | required |", "|---|---:|---|---|"]
    for item in items:
        lines.append(f"| {item.get('name')} | `{item.get('passed')}` | `{item.get('actual')}` | `{item.get('required')}` |")
    return lines


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    refiner_path = resolve_path(args.refiner)
    observer_path = resolve_path(args.observer)
    scoreboard_path = resolve_path(args.scoreboard)
    alert_drill_path = resolve_path(args.alert_drill)

    refiner = read_json(refiner_path)
    observer = read_json(observer_path)
    scoreboard = read_json(scoreboard_path)
    alert_drill = read_json(alert_drill_path)

    selected = refiner.get("selected_candidate") if isinstance(refiner.get("selected_candidate"), dict) else {}
    latest = observer.get("latest_result") if isinstance(observer.get("latest_result"), dict) else {}
    summary = scoreboard.get("summary") if isinstance(scoreboard.get("summary"), dict) else {}

    full_trades = safe_int(nested(selected, "full", "summary", "trades"))
    full_expectancy = safe_float(nested(selected, "full", "summary", "expectancy_r"))
    holdout_trades = safe_int(nested(selected, "holdout", "summary", "trades"))
    holdout_expectancy = safe_float(nested(selected, "holdout", "summary", "expectancy_r"))
    stable_folds = safe_int(nested(selected, "full", "stable_folds"))
    segment_positive_ratio = safe_float(selected.get("segment_positive_ratio"))
    worst_segment_expectancy = safe_float(selected.get("worst_segment_expectancy_r"))
    cost10_expectancy = selected_cost_expectancy(selected, 10.0)

    observer_signal_events = safe_int(summary.get("observer_signal_events"))
    resolved = safe_int(summary.get("resolved"))
    expectancy = safe_float(summary.get("expectancy_r"))
    winrate = safe_float(summary.get("winrate_pct"))
    breakeven = safe_float(summary.get("breakeven_winrate_pct"))
    max_drawdown = safe_float(summary.get("max_drawdown_r"))
    data_degraded = latest.get("data_degraded")
    missing_inputs = latest.get("missing_filter_inputs")
    if not isinstance(missing_inputs, list):
        missing_inputs = []

    research_gates = [
        gate("refiner_report_exists", bool(refiner) and not refiner.get("_read_error"), rel_path(refiner_path), "readable JSON"),
        gate("selected_candidate_exists", bool(selected), selected.get("strategy_id"), "selected_candidate"),
        gate("selected_candidate_verdict", selected.get("verdict") == "range_refined_candidate_for_forward_observation", selected.get("verdict"), "range_refined_candidate_for_forward_observation"),
        gate("history_min_full_trades", full_trades >= args.min_full_trades, full_trades, args.min_full_trades),
        gate("history_full_expectancy", full_expectancy is not None and full_expectancy >= args.min_history_expectancy_r, full_expectancy, args.min_history_expectancy_r),
        gate("history_min_holdout_trades", holdout_trades >= args.min_holdout_trades, holdout_trades, args.min_holdout_trades),
        gate("history_holdout_expectancy", holdout_expectancy is not None and holdout_expectancy >= args.min_holdout_expectancy_r, holdout_expectancy, args.min_holdout_expectancy_r),
        gate("history_stable_folds", stable_folds >= args.min_stable_folds, stable_folds, args.min_stable_folds),
        gate("history_segment_positive_ratio", segment_positive_ratio is not None and segment_positive_ratio >= args.min_segment_positive_ratio, segment_positive_ratio, args.min_segment_positive_ratio),
        gate("history_worst_segment_floor", worst_segment_expectancy is not None and worst_segment_expectancy >= args.min_worst_segment_expectancy_r, worst_segment_expectancy, args.min_worst_segment_expectancy_r),
        gate("history_cost10_expectancy", cost10_expectancy is not None and cost10_expectancy >= args.min_cost10_expectancy_r, cost10_expectancy, args.min_cost10_expectancy_r),
    ]

    observer_gates = [
        gate("observer_report_exists", bool(observer) and not observer.get("_read_error"), rel_path(observer_path), "readable JSON"),
        gate("scoreboard_report_exists", bool(scoreboard) and not scoreboard.get("_read_error"), rel_path(scoreboard_path), "readable JSON"),
        gate("latest_observer_not_degraded", data_degraded is False, data_degraded, False),
        gate("latest_missing_filter_inputs_empty", len(missing_inputs) == 0, missing_inputs, "[]"),
        gate("observer_signal_events", observer_signal_events >= args.min_observer_signals, observer_signal_events, args.min_observer_signals),
        gate("observer_resolved_outcomes", resolved >= args.min_resolved, resolved, args.min_resolved),
        gate("observer_expectancy", expectancy is not None and expectancy >= args.min_forward_expectancy_r, expectancy, args.min_forward_expectancy_r),
        gate("observer_winrate_vs_breakeven", winrate is not None and breakeven is not None and winrate >= breakeven, f"{winrate} vs {breakeven}", "winrate >= breakeven"),
        gate("observer_drawdown_cap", max_drawdown is not None and max_drawdown >= -abs(args.max_drawdown_r), max_drawdown, f">= -{abs(args.max_drawdown_r)}R"),
    ]

    operational_gates = [
        gate("alert_drill_report_exists", bool(alert_drill) and not alert_drill.get("_read_error"), rel_path(alert_drill_path), "readable JSON"),
        gate("alert_drill_passed", alert_drill.get("decision") == "range_signal_alert_drill_passed", alert_drill.get("decision"), "range_signal_alert_drill_passed"),
        gate("alert_drill_first_ready", nested(alert_drill, "first_guard", "guard_report", "decision") == "dry_run_ready", nested(alert_drill, "first_guard", "guard_report", "decision"), "dry_run_ready"),
        gate("alert_drill_duplicate_skip", nested(alert_drill, "duplicate_guard", "guard_report", "decision") == "skipped_duplicate", nested(alert_drill, "duplicate_guard", "guard_report", "decision"), "skipped_duplicate"),
    ]

    research_ok = gates_pass(research_gates)
    observer_ok = gates_pass(observer_gates)
    operational_ok = gates_pass(operational_gates)

    if not research_ok:
        decision = "blocked_range_research_gate_failed"
        next_action = "fix/refine historical RANGE candidate before forward promotion review"
    elif not operational_ok:
        decision = "blocked_range_operational_alert_gate_failed"
        next_action = "fix alert/card duplicate-suppression path before promotion review"
    elif not observer_ok:
        decision = "blocked_waiting_range_observer_outcomes"
        next_action = "keep RANGE observer running until enough real resolved observer outcomes exist"
    else:
        decision = "candidate_for_range_paper_design_review_only"
        next_action = "design a separate paper-entry gate; do not enable orders from this report"

    promotion = {
        "observer_allowed": research_ok and operational_ok,
        "paper_design_review_allowed": research_ok and operational_ok and observer_ok,
        "paper_execution_allowed": False,
        "live_execution_allowed": False,
        "manual_review_required": True,
        "can_trade": False,
    }

    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_promotion_gate_evidence_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "inputs": {
            "refiner": rel_path(refiner_path),
            "observer": rel_path(observer_path),
            "scoreboard": rel_path(scoreboard_path),
            "alert_drill": rel_path(alert_drill_path),
        },
        "thresholds": {
            "min_full_trades": args.min_full_trades,
            "min_history_expectancy_r": args.min_history_expectancy_r,
            "min_holdout_trades": args.min_holdout_trades,
            "min_holdout_expectancy_r": args.min_holdout_expectancy_r,
            "min_stable_folds": args.min_stable_folds,
            "min_segment_positive_ratio": args.min_segment_positive_ratio,
            "min_worst_segment_expectancy_r": args.min_worst_segment_expectancy_r,
            "min_cost10_expectancy_r": args.min_cost10_expectancy_r,
            "min_observer_signals": args.min_observer_signals,
            "min_resolved": args.min_resolved,
            "min_forward_expectancy_r": args.min_forward_expectancy_r,
            "max_drawdown_r": args.max_drawdown_r,
        },
        "candidate": {
            "base_strategy_id": selected.get("base_strategy_id"),
            "strategy_id": selected.get("strategy_id"),
            "filter_mode": selected.get("filter_mode"),
            "verdict": selected.get("verdict"),
            "side": selected.get("side"),
            "interval": selected.get("interval"),
            "rr": selected.get("rr"),
            "full_trades": full_trades,
            "full_expectancy_r": full_expectancy,
            "holdout_trades": holdout_trades,
            "holdout_expectancy_r": holdout_expectancy,
            "stable_folds": stable_folds,
            "segment_positive_ratio": segment_positive_ratio,
            "worst_segment_expectancy_r": worst_segment_expectancy,
            "cost10_expectancy_r": cost10_expectancy,
        },
        "observer": {
            "latest_status": latest.get("status"),
            "latest_closed_bar_ts": latest.get("latest_closed_bar_ts"),
            "latest_closed_close": latest.get("latest_closed_close"),
            "data_degraded": data_degraded,
            "missing_filter_inputs": missing_inputs,
        },
        "scoreboard": {
            "classification": summary.get("classification"),
            "observer_signal_events": observer_signal_events,
            "resolved": resolved,
            "winrate_pct": winrate,
            "breakeven_winrate_pct": breakeven,
            "expectancy_r": expectancy,
            "max_drawdown_r": max_drawdown,
        },
        "operational": {
            "alert_drill_decision": alert_drill.get("decision") if isinstance(alert_drill, dict) else None,
            "alert_drill_first_decision": nested(alert_drill, "first_guard", "guard_report", "decision") if isinstance(alert_drill, dict) else None,
            "alert_drill_duplicate_decision": nested(alert_drill, "duplicate_guard", "guard_report", "decision") if isinstance(alert_drill, dict) else None,
        },
        "gates": {
            "research": research_gates,
            "observer": observer_gates,
            "operational": operational_gates,
        },
        "promotion": promotion,
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    candidate = report.get("candidate", {})
    scoreboard = report.get("scoreboard", {})
    operational = report.get("operational", {})
    promotion = report.get("promotion", {})
    return "\n".join(
        [
            "# Range Refined Promotion Gate",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Evidence gate only.",
            "- Does not create paper-entry intents.",
            "- Does not send exchange orders.",
            "- A pass here would allow paper-design review only, not live trading.",
            "",
            "## Decision",
            "",
            f"- Decision: `{report.get('decision')}`.",
            f"- Next action: `{report.get('next_action')}`.",
            f"- Observer allowed: `{promotion.get('observer_allowed')}`.",
            f"- Paper-design review allowed: `{promotion.get('paper_design_review_allowed')}`.",
            f"- Paper execution allowed: `{promotion.get('paper_execution_allowed')}`.",
            f"- Live execution allowed: `{promotion.get('live_execution_allowed')}`.",
            "",
            "## Candidate",
            "",
            f"- Strategy: `{candidate.get('strategy_id')}`.",
            f"- Filter: `{candidate.get('filter_mode')}`.",
            f"- Side / TF / RR: `{candidate.get('side')}` / `{candidate.get('interval')}` / `{candidate.get('rr')}`.",
            f"- Full expectancy: `{candidate.get('full_expectancy_r')}` R over `{candidate.get('full_trades')}` trades.",
            f"- Holdout expectancy: `{candidate.get('holdout_expectancy_r')}` R over `{candidate.get('holdout_trades')}` trades.",
            f"- Cost +10bps expectancy: `{candidate.get('cost10_expectancy_r')}` R.",
            "",
            "## Forward Observer Evidence",
            "",
            f"- Classification: `{scoreboard.get('classification')}`.",
            f"- Observer signals: `{scoreboard.get('observer_signal_events')}`.",
            f"- Resolved outcomes: `{scoreboard.get('resolved')}`.",
            f"- Winrate / breakeven: `{scoreboard.get('winrate_pct')}` / `{scoreboard.get('breakeven_winrate_pct')}`.",
            f"- Expectancy: `{scoreboard.get('expectancy_r')}` R.",
            f"- Max drawdown: `{scoreboard.get('max_drawdown_r')}` R.",
            "",
            "## Operational Alert Evidence",
            "",
            f"- Alert drill: `{operational.get('alert_drill_decision')}`.",
            f"- First decision: `{operational.get('alert_drill_first_decision')}`.",
            f"- Duplicate decision: `{operational.get('alert_drill_duplicate_decision')}`.",
            "",
            "## Research Gates",
            "",
            *render_gate_table(report.get("gates", {}).get("research", [])),
            "",
            "## Observer Gates",
            "",
            *render_gate_table(report.get("gates", {}).get("observer", [])),
            "",
            "## Operational Gates",
            "",
            *render_gate_table(report.get("gates", {}).get("operational", [])),
            "",
        ]
    )


def main() -> int:
    if hasattr(__import__("sys").stdout, "reconfigure"):
        __import__("sys").stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gate selected refined RANGE candidate before paper-design review")
    parser.add_argument("--refiner", default="docs/RANGE_WATCHLIST_REFINER_2026-06-16.json")
    parser.add_argument("--observer", default="docs/RANGE_REFINED_FORWARD_OBSERVER_2026-06-16.json")
    parser.add_argument("--scoreboard", default="docs/RANGE_REFINED_OBSERVER_SCOREBOARD_2026-06-16.json")
    parser.add_argument("--alert-drill", default="docs/RANGE_REFINED_SIGNAL_ALERT_DRILL_2026-06-17.json")
    parser.add_argument("--min-full-trades", type=int, default=60)
    parser.add_argument("--min-history-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-holdout-trades", type=int, default=15)
    parser.add_argument("--min-holdout-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-stable-folds", type=int, default=3)
    parser.add_argument("--min-segment-positive-ratio", type=float, default=0.8)
    parser.add_argument("--min-worst-segment-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-cost10-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-observer-signals", type=int, default=30)
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--min-forward-expectancy-r", type=float, default=0.05)
    parser.add_argument("--max-drawdown-r", type=float, default=8.0)
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_PROMOTION_GATE_2026-06-17")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report.get("decision"),
                "observer_allowed": report.get("promotion", {}).get("observer_allowed"),
                "paper_design_review_allowed": report.get("promotion", {}).get("paper_design_review_allowed"),
                "observer_signal_events": report.get("scoreboard", {}).get("observer_signal_events"),
                "resolved": report.get("scoreboard", {}).get("resolved"),
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
