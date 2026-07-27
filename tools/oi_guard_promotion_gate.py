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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"_read_error": "invalid_json", "_path": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": str(path)}


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def safe_int(value: Any) -> int | None:
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return parsed


def pick_candidate(validation: dict[str, Any], name: str) -> dict[str, Any]:
    candidates = validation.get("candidates")
    if not isinstance(candidates, list):
        return {}
    if name:
        for item in candidates:
            if isinstance(item, dict) and item.get("candidate") == name:
                return item
        return {}
    for item in candidates:
        if isinstance(item, dict) and item.get("verdict") == "candidate_for_forward_guard_observation":
            return item
    return candidates[0] if candidates and isinstance(candidates[0], dict) else {}


def gate(name: str, passed: bool, actual: Any, required: Any, severity: str = "hard") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "required": required,
        "severity": severity,
    }


def bool_passes(gates: list[dict[str, Any]], *, severity: str = "hard") -> bool:
    return all(item.get("passed") for item in gates if item.get("severity") == severity)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    validation_path = resolve_path(args.validation)
    scoreboard_path = resolve_path(args.forward_scoreboard)
    data_quality_path = resolve_path(args.data_quality)

    validation = read_json(validation_path)
    scoreboard = read_json(scoreboard_path)
    data_quality = read_json(data_quality_path)

    candidate = pick_candidate(validation, args.candidate)
    selected = candidate.get("selected_stats") if isinstance(candidate.get("selected_stats"), dict) else {}
    stress = candidate.get("cost_stress_stats") if isinstance(candidate.get("cost_stress_stats"), dict) else {}
    summary = scoreboard.get("summary") if isinstance(scoreboard.get("summary"), dict) else {}
    dq_summary = data_quality.get("summary") if isinstance(data_quality.get("summary"), dict) else {}
    replay = data_quality.get("replay_trade_coverage") if isinstance(data_quality.get("replay_trade_coverage"), dict) else {}
    latest_guard = scoreboard.get("latest_oi_guard_candidate") if isinstance(scoreboard.get("latest_oi_guard_candidate"), dict) else {}

    selected_trades = safe_int(selected.get("trades")) or 0
    selected_expectancy = safe_float(selected.get("expectancy_r"))
    lift = safe_float(candidate.get("expectancy_lift_r"))
    bootstrap = safe_float(candidate.get("bootstrap_prob_positive"))
    cost_stress_expectancy = safe_float(stress.get("expectancy_r"))
    positive_folds = safe_int(candidate.get("positive_folds")) or 0
    folds = safe_int(candidate.get("folds")) or 0
    replay_full_context = safe_int(replay.get("full_context_available")) or 0
    replay_trades = safe_int(replay.get("trades")) or 0
    replay_context_pct = safe_float(replay.get("full_context_coverage_pct"))

    guard_entry_contexts = safe_int(summary.get("oi_guard_entry_contexts")) or 0
    guard_resolved_contexts = safe_int(summary.get("oi_guard_resolved_contexts")) or 0
    guard_expectancy = safe_float(summary.get("oi_guard_expectancy_r"))
    general_entry_contexts = safe_int(summary.get("entry_contexts")) or 0
    general_resolved = safe_int(summary.get("resolved")) or 0

    historical_gates = [
        gate("validation_report_exists", bool(validation) and not validation.get("_read_error"), str(validation_path), "readable JSON"),
        gate("candidate_found", bool(candidate), candidate.get("candidate"), args.candidate or "first forward-observation candidate"),
        gate("candidate_verdict", candidate.get("verdict") == "candidate_for_forward_guard_observation", candidate.get("verdict"), "candidate_for_forward_guard_observation"),
        gate("history_min_trades", selected_trades >= args.min_history_trades, selected_trades, args.min_history_trades),
        gate("history_positive_expectancy", selected_expectancy is not None and selected_expectancy > 0, selected_expectancy, "> 0R"),
        gate("history_min_lift", lift is not None and lift >= args.min_history_lift_r, lift, args.min_history_lift_r),
        gate("history_min_bootstrap_positive", bootstrap is not None and bootstrap >= args.min_bootstrap_positive, bootstrap, args.min_bootstrap_positive),
        gate("history_min_positive_folds", positive_folds >= args.min_positive_folds, f"{positive_folds}/{folds}", args.min_positive_folds),
        gate("history_cost_stress_positive", cost_stress_expectancy is not None and cost_stress_expectancy > 0, cost_stress_expectancy, "> 0R"),
    ]

    data_quality_gates = [
        gate("data_quality_report_exists", bool(data_quality) and not data_quality.get("_read_error"), str(data_quality_path), "readable JSON"),
        gate("oi_guard_data_ready", dq_summary.get("classification") == "oi_guard_data_ready", dq_summary.get("classification"), "oi_guard_data_ready"),
        gate("replay_full_context_min_trades", replay_full_context >= args.min_replay_full_context_trades, replay_full_context, args.min_replay_full_context_trades),
        gate("replay_full_context_min_pct", replay_context_pct is not None and replay_context_pct >= args.min_replay_context_pct, replay_context_pct, args.min_replay_context_pct),
    ]

    forward_gates = [
        gate("forward_scoreboard_exists", bool(scoreboard) and not scoreboard.get("_read_error"), str(scoreboard_path), "readable JSON"),
        gate("forward_guard_entry_contexts", guard_entry_contexts >= args.min_forward_guard_entries, guard_entry_contexts, args.min_forward_guard_entries),
        gate("forward_guard_resolved_contexts", guard_resolved_contexts >= args.min_forward_guard_resolved, guard_resolved_contexts, args.min_forward_guard_resolved),
        gate("forward_guard_positive_expectancy", guard_expectancy is not None and guard_expectancy >= args.min_forward_guard_expectancy_r, guard_expectancy, args.min_forward_guard_expectancy_r),
    ]

    historical_ok = bool_passes(historical_gates)
    data_quality_ok = bool_passes(data_quality_gates)
    forward_ok = bool_passes(forward_gates)

    if not historical_ok or not data_quality_ok:
        decision = "blocked_historical_or_data_quality_gate_failed"
        next_action = "fix historical validation/data quality before observing promotion"
    elif not forward_ok:
        decision = "blocked_waiting_forward_guard_outcomes"
        next_action = "keep shadow guard observation running until enough resolved paper-entry outcomes exist"
    else:
        decision = "candidate_for_execution_design_review_only"
        next_action = "design a separate execution gate review; do not send live orders from this report"

    promotion = {
        "shadow_guard_allowed": historical_ok and data_quality_ok,
        "active_filter_allowed": forward_ok and historical_ok and data_quality_ok,
        "paper_execution_allowed": False,
        "live_execution_allowed": False,
        "manual_review_required": True,
        "can_trade": False,
    }

    return {
        "generated_at": now_iso(),
        "boundary": {
            "classification": "oi_guard_promotion_gate_local_evidence_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "inputs": {
            "validation": str(validation_path),
            "forward_scoreboard": str(scoreboard_path),
            "data_quality": str(data_quality_path),
            "candidate_requested": args.candidate,
        },
        "thresholds": {
            "min_history_trades": args.min_history_trades,
            "min_history_lift_r": args.min_history_lift_r,
            "min_bootstrap_positive": args.min_bootstrap_positive,
            "min_positive_folds": args.min_positive_folds,
            "min_replay_full_context_trades": args.min_replay_full_context_trades,
            "min_replay_context_pct": args.min_replay_context_pct,
            "min_forward_guard_entries": args.min_forward_guard_entries,
            "min_forward_guard_resolved": args.min_forward_guard_resolved,
            "min_forward_guard_expectancy_r": args.min_forward_guard_expectancy_r,
        },
        "candidate": {
            "name": candidate.get("candidate"),
            "verdict": candidate.get("verdict"),
            "selected_trades": selected_trades,
            "selected_expectancy_r": selected_expectancy,
            "selected_winrate_pct": selected.get("winrate_pct"),
            "expectancy_lift_r": lift,
            "bootstrap_prob_positive": bootstrap,
            "positive_folds": positive_folds,
            "folds": folds,
            "cost_stress_expectancy_r": cost_stress_expectancy,
        },
        "data_quality": {
            "classification": dq_summary.get("classification"),
            "aligned_oi_coverage_pct": dq_summary.get("aligned_oi_coverage_pct"),
            "aligned_funding_coverage_pct": dq_summary.get("aligned_funding_coverage_pct"),
            "replay_trades": replay_trades,
            "replay_full_context_available": replay_full_context,
            "replay_full_context_coverage_pct": replay_context_pct,
        },
        "forward": {
            "classification": summary.get("classification"),
            "entry_contexts": general_entry_contexts,
            "resolved": general_resolved,
            "oi_guard_entry_contexts": guard_entry_contexts,
            "oi_guard_resolved_contexts": guard_resolved_contexts,
            "oi_guard_expectancy_r": guard_expectancy,
            "latest_oi_guard_state": latest_guard.get("state"),
            "latest_oi_guard_would_keep_long_signal": latest_guard.get("would_keep_long_signal"),
            "latest_oi_guard_can_filter_now": latest_guard.get("can_filter_now"),
        },
        "gates": {
            "historical": historical_gates,
            "data_quality": data_quality_gates,
            "forward": forward_gates,
        },
        "promotion": promotion,
        "decision": decision,
        "next_action": next_action,
    }


def render_gate_table(items: list[dict[str, Any]]) -> list[str]:
    lines = ["| gate | pass | actual | required |", "|---|---:|---|---|"]
    for item in items:
        lines.append(
            f"| {item.get('name')} | `{item.get('passed')}` | `{item.get('actual')}` | `{item.get('required')}` |"
        )
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    candidate = report.get("candidate", {})
    dq = report.get("data_quality", {})
    forward = report.get("forward", {})
    promotion = report.get("promotion", {})
    lines = [
        "# OI Guard Promotion Gate",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Local evidence gate only.",
        "- No private credentials, no exchange account, no orders.",
        "- A pass here would allow execution-design review only, not live trading.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report.get('decision')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        f"- Shadow guard allowed: `{promotion.get('shadow_guard_allowed')}`.",
        f"- Active filter allowed: `{promotion.get('active_filter_allowed')}`.",
        f"- Paper execution allowed: `{promotion.get('paper_execution_allowed')}`.",
        f"- Live execution allowed: `{promotion.get('live_execution_allowed')}`.",
        "",
        "## Candidate",
        "",
        f"- Name: `{candidate.get('name')}`.",
        f"- Verdict: `{candidate.get('verdict')}`.",
        f"- Selected trades: `{candidate.get('selected_trades')}`.",
        f"- Expectancy: `{candidate.get('selected_expectancy_r')}` R.",
        f"- Lift: `{candidate.get('expectancy_lift_r')}` R.",
        f"- Bootstrap positive: `{candidate.get('bootstrap_prob_positive')}`.",
        f"- Positive folds: `{candidate.get('positive_folds')}/{candidate.get('folds')}`.",
        "",
        "## Data Quality",
        "",
        f"- Classification: `{dq.get('classification')}`.",
        f"- Aligned OI coverage: `{dq.get('aligned_oi_coverage_pct')}`%.",
        f"- Replay full context: `{dq.get('replay_full_context_available')}/{dq.get('replay_trades')}`.",
        "",
        "## Forward Evidence",
        "",
        f"- Classification: `{forward.get('classification')}`.",
        f"- General entry/resolved contexts: `{forward.get('entry_contexts')}/{forward.get('resolved')}`.",
        f"- OI guard entry/resolved contexts: `{forward.get('oi_guard_entry_contexts')}/{forward.get('oi_guard_resolved_contexts')}`.",
        f"- OI guard expectancy: `{forward.get('oi_guard_expectancy_r')}` R.",
        f"- Latest OI guard state: `{forward.get('latest_oi_guard_state')}`.",
        f"- Latest OI guard can filter now: `{forward.get('latest_oi_guard_can_filter_now')}`.",
        "",
        "## Historical Gates",
        "",
        *render_gate_table(report.get("gates", {}).get("historical", [])),
        "",
        "## Data-Quality Gates",
        "",
        *render_gate_table(report.get("gates", {}).get("data_quality", [])),
        "",
        "## Forward Gates",
        "",
        *render_gate_table(report.get("gates", {}).get("forward", [])),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate OI guard promotion from shadow observation to execution-design review")
    parser.add_argument("--validation", default="docs/STRATEGY_MIX_OI_GUARD_VALIDATION_2026-06-15.json")
    parser.add_argument("--forward-scoreboard", default="docs/OI_FUNDING_FORWARD_CONTEXT_SCOREBOARD_2026-06-15.json")
    parser.add_argument("--data-quality", default="docs/OI_FUNDING_DATA_QUALITY_2026-06-15.json")
    parser.add_argument("--candidate", default="keep_oi_expansion_strong")
    parser.add_argument("--min-history-trades", type=int, default=60)
    parser.add_argument("--min-history-lift-r", type=float, default=0.05)
    parser.add_argument("--min-bootstrap-positive", type=float, default=0.95)
    parser.add_argument("--min-positive-folds", type=int, default=4)
    parser.add_argument("--min-replay-full-context-trades", type=int, default=52)
    parser.add_argument("--min-replay-context-pct", type=float, default=80.0)
    parser.add_argument("--min-forward-guard-entries", type=int, default=30)
    parser.add_argument("--min-forward-guard-resolved", type=int, default=30)
    parser.add_argument("--min-forward-guard-expectancy-r", type=float, default=0.05)
    parser.add_argument("--out-prefix", default="docs/OI_GUARD_PROMOTION_GATE_2026-06-15")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(
        json.dumps(
            {
                "decision": report.get("decision"),
                "shadow_guard_allowed": report.get("promotion", {}).get("shadow_guard_allowed"),
                "active_filter_allowed": report.get("promotion", {}).get("active_filter_allowed"),
                "forward_guard_contexts": [
                    report.get("forward", {}).get("oi_guard_entry_contexts"),
                    report.get("forward", {}).get("oi_guard_resolved_contexts"),
                ],
                "json": str(json_path),
                "md": str(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
