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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def classify_family(row: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    decisions = policy.get("decisions") if isinstance(policy.get("decisions"), dict) else {}
    early = policy.get("early_pause") if isinstance(policy.get("early_pause"), dict) else {}
    family = str(row.get("family") or "UNKNOWN")
    try:
        resolved = int(row.get("resolved") or 0)
        required = int(row.get("resolved_required") or 0)
    except (TypeError, ValueError):
        resolved, required = -1, -1
    expectancy = safe_float(row.get("expectancy_r"))
    drawdown = safe_float(row.get("max_drawdown_r"))
    winrate = safe_float(row.get("winrate_pct"))
    breakeven = safe_float(row.get("breakeven_winrate_pct"))
    min_expectancy = float(policy.get("minimum_expectancy_r", 0.1))
    max_drawdown = float(policy.get("maximum_drawdown_r", -6.0))
    early_min = int(early.get("minimum_resolved", 10))
    early_drawdown = float(early.get("maximum_drawdown_r", -8.0))

    invalid = resolved < 0 or required <= 0 or resolved > 0 and (expectancy is None or drawdown is None)
    if invalid:
        state = decisions.get("invalid", "blocked_invalid_evidence")
        reason = "missing_or_invalid_required_metrics"
    elif resolved < required:
        if resolved >= early_min and drawdown is not None and drawdown <= early_drawdown:
            state = decisions.get("paused", "paused_early_risk_breach")
            reason = "precommitted_early_drawdown_limit_reached"
        else:
            state = decisions.get("collecting", "collecting_independent_evidence")
            reason = "minimum_independent_sample_not_reached"
    elif drawdown is None or drawdown < max_drawdown:
        state = decisions.get("rejected_drawdown", "rejected_drawdown_breach")
        reason = "drawdown_gate_failed_at_checkpoint"
    elif expectancy is None or expectancy < min_expectancy:
        state = decisions.get("rejected_expectancy", "rejected_no_positive_edge")
        reason = "expectancy_gate_failed_at_checkpoint"
    elif winrate is not None and breakeven is not None and winrate < breakeven:
        state = decisions.get("rejected_winrate", "rejected_below_breakeven")
        reason = "winrate_below_strategy_breakeven_at_checkpoint"
    elif row.get("eligible_for_paper_design") is True:
        state = decisions.get("paper_review", "paper_design_review_only")
        reason = "all_precommitted_evidence_gates_passed"
    else:
        state = decisions.get("invalid", "blocked_invalid_evidence")
        reason = "family_promotion_gate_conflicts_with_normalized_evidence"

    return {
        "family": family,
        "state": state,
        "reason": reason,
        "resolved": resolved,
        "resolved_required": required,
        "progress_pct": round(100.0 * min(max(resolved, 0), max(required, 1)) / max(required, 1), 2),
        "expectancy_r": expectancy,
        "winrate_pct": winrate,
        "breakeven_winrate_pct": breakeven,
        "max_drawdown_r": drawdown,
        "threshold_tuning_allowed": False,
        "paper_execution_allowed": False,
        "live_execution_allowed": False,
        "can_trade": False,
    }


def build_report(
    scoreboard: dict[str, Any],
    policy: dict[str, Any],
    historical_invalidations: dict[str, dict[str, Any]] | None = None,
    historical_evidence: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    families = scoreboard.get("families") if isinstance(scoreboard.get("families"), list) else []
    rows = [classify_family(row, policy) for row in families if isinstance(row, dict)]
    invalidations = historical_invalidations or {}
    rejected_state = policy.get("decisions", {}).get("rejected_historical", "rejected_historical_invalidation")
    for row in rows:
        invalidation = invalidations.get(str(row.get("family")))
        if invalidation:
            row["state"] = rejected_state
            row["reason"] = "broader_historical_test_invalidated_locked_candidate"
            row["historical_invalidation"] = invalidation
    evidence = historical_evidence or {}
    for row in rows:
        family_evidence = evidence.get(str(row.get("family")))
        if family_evidence:
            row["historical_evidence"] = family_evidence
    states = [str(row.get("state")) for row in rows]
    if len(rows) != 4:
        decision = "blocked_invalid_family_inventory"
    elif any(state.startswith("blocked_") for state in states):
        decision = "blocked_invalid_evidence"
    elif any(state.startswith("paper_design_review") for state in states):
        decision = "manual_paper_design_review_available"
    elif any(state.startswith("paused_") for state in states):
        decision = "one_or_more_families_paused_for_risk"
    elif all(state.startswith("rejected_") for state in states):
        decision = "all_families_rejected_at_checkpoint"
    else:
        decision = "collect_independent_forward_evidence"
    return {
        "generated_at": now_iso(),
        "engine": "FORWARD_EVIDENCE_LIFECYCLE_CONTROLLER",
        "engine_version": "1.0.0",
        "policy_version": policy.get("version"),
        "scoreboard_generated_at": scoreboard.get("generated_at"),
        "decision": decision,
        "families": rows,
        "counts": {
            "families": len(rows),
            "collecting": sum(state.startswith("collecting_") for state in states),
            "paused": sum(state.startswith("paused_") for state in states),
            "rejected": sum(state.startswith("rejected_") for state in states),
            "paper_review": sum(state.startswith("paper_design_review") for state in states),
            "invalid": sum(state.startswith("blocked_") for state in states),
        },
        "boundaries": {
            "changes_strategy_parameters": False,
            "creates_entry_intents": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "sends_orders": False,
            "can_trade": False,
        },
        "next_action": "keep collecting independent outcomes; act only at precommitted checkpoints",
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Forward Evidence Lifecycle",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        "- Trading permission: `false`.",
        "",
        "| Family | State | Resolved | Progress | Exp R | DD R |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in report.get("families", []):
        lines.append(
            f"| `{row.get('family')}` | `{row.get('state')}` | {row.get('resolved')}/{row.get('resolved_required')} | "
            f"{row.get('progress_pct')}% | {row.get('expectancy_r')} | {row.get('max_drawdown_r')} |"
        )
    lines.extend(
        [
            "",
            "## Policy",
            "",
            "- Thresholds are precommitted and cannot be tuned before the sample checkpoint.",
            "- A paper-design review is not paper execution permission.",
            "- This controller never creates entry intents or sends orders.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply precommitted lifecycle rules to four-family forward evidence.")
    parser.add_argument("--scoreboard", default="docs/FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_2026-06-22.json")
    parser.add_argument("--policy", default="configs/FORWARD_EVIDENCE_LIFECYCLE.json")
    parser.add_argument("--crowd-lock", default="configs/CROWD_FADE_FORWARD_LOCK.json")
    parser.add_argument("--range-edge-holdout", default="docs/RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json")
    parser.add_argument("--out-prefix", default="docs/FORWARD_EVIDENCE_LIFECYCLE_2026-06-23")
    args = parser.parse_args()
    scoreboard_path = Path(args.scoreboard)
    policy_path = Path(args.policy)
    if not scoreboard_path.is_absolute():
        scoreboard_path = ROOT / scoreboard_path
    if not policy_path.is_absolute():
        policy_path = ROOT / policy_path
    crowd_lock_path = Path(args.crowd_lock)
    if not crowd_lock_path.is_absolute():
        crowd_lock_path = ROOT / crowd_lock_path
    crowd_lock = read_json(crowd_lock_path)
    invalidations: dict[str, dict[str, Any]] = {}
    if crowd_lock.get("enabled") is False and str(crowd_lock.get("status") or "").startswith("historically_rejected"):
        invalidation = crowd_lock.get("invalidation") if isinstance(crowd_lock.get("invalidation"), dict) else {}
        invalidations["CROWD_FADE_1H"] = invalidation
    range_edge_path = Path(args.range_edge_holdout)
    if not range_edge_path.is_absolute():
        range_edge_path = ROOT / range_edge_path
    range_edge = read_json(range_edge_path)
    historical_evidence: dict[str, dict[str, Any]] = {}
    for family_row in range_edge.get("families", []):
        if not isinstance(family_row, dict):
            continue
        family = str(family_row.get("family") or "")
        if not family:
            continue
        oos = family_row.get("oos") if isinstance(family_row.get("oos"), dict) else {}
        summary = oos.get("summary") if isinstance(oos.get("summary"), dict) else {}
        evidence_row = {
            "source": str(range_edge_path),
            "decision": family_row.get("decision"),
            "selection_method": range_edge.get("method"),
            "split_ts": range_edge.get("data", {}).get("split_ts"),
            "oos_trades": summary.get("trades"),
            "oos_expectancy_r": summary.get("expectancy_r"),
            "oos_max_drawdown_r": summary.get("max_drawdown_r"),
            "oos_gate": family_row.get("oos_gate"),
        }
        historical_evidence[family] = evidence_row
        if str(family_row.get("decision") or "").startswith("reject_oos"):
            invalidations[family] = evidence_row
    report = build_report(
        read_json(scoreboard_path),
        read_json(policy_path),
        invalidations,
        historical_evidence,
    )
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "counts": report["counts"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 2 if report["decision"] in {"blocked_invalid_family_inventory", "blocked_invalid_evidence"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
