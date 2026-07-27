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


def family_row(
    family: str,
    scoreboard: dict[str, Any],
    promotion: dict[str, Any],
    min_resolved: int,
    min_expectancy_r: float,
) -> dict[str, Any]:
    summary = scoreboard.get("summary") if isinstance(scoreboard.get("summary"), dict) else {}
    promotion_state = promotion.get("promotion") if isinstance(promotion.get("promotion"), dict) else promotion
    signals = int(summary.get("entry_intents") or summary.get("observer_signal_events") or 0)
    resolved = int(summary.get("resolved") or 0)
    family_min = int(summary.get("min_resolved_required") or 0)
    required = max(min_resolved, family_min)
    expectancy = safe_float(summary.get("expectancy_r"))
    winrate = safe_float(summary.get("winrate_pct"))
    breakeven = safe_float(summary.get("breakeven_winrate_pct"))
    drawdown = safe_float(summary.get("max_drawdown_r"))
    sample_ok = resolved >= required
    expectancy_ok = expectancy is not None and expectancy >= min_expectancy_r
    winrate_ok = winrate is not None and (breakeven is None or winrate >= breakeven)
    drawdown_ok = drawdown is not None and drawdown >= -6.0
    evidence_ready = sample_ok and expectancy_ok and winrate_ok and drawdown_ok
    paper_design_allowed = promotion_state.get("paper_design_review_allowed") is True
    outcomes = scoreboard.get("outcomes") if isinstance(scoreboard.get("outcomes"), list) else []
    resolved_outcomes = [
        {"exit_ts": item.get("exit_ts"), "r": safe_float(item.get("r"))}
        for item in outcomes
        if isinstance(item, dict) and safe_float(item.get("r")) is not None
    ]
    return {
        "family": family,
        "classification": summary.get("classification"),
        "raw_signals": scoreboard.get("raw_unique_signal_events", signals),
        "independent_signals": scoreboard.get("independent_signal_events", signals),
        "overlap_suppressed": scoreboard.get("overlap_suppressed_events", 0),
        "resolved": resolved,
        "resolved_required": required,
        "wins": int(summary.get("wins") or 0),
        "winrate_pct": winrate,
        "breakeven_winrate_pct": breakeven,
        "expectancy_r": expectancy,
        "net_r_total": safe_float(summary.get("net_r_total")),
        "max_drawdown_r": drawdown,
        "promotion_decision": promotion.get("decision"),
        "paper_design_review_allowed": paper_design_allowed,
        "evidence_gates": {
            "sample_ok": sample_ok,
            "expectancy_ok": expectancy_ok,
            "winrate_ok": winrate_ok,
            "drawdown_ok": drawdown_ok,
        },
        "evidence_ready": evidence_ready,
        "eligible_for_paper_design": evidence_ready and paper_design_allowed,
        "resolved_outcomes": resolved_outcomes,
        "can_trade": False,
    }


def pairwise_overlap(rows: list[dict[str, Any]], min_joint: int = 5) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for left_index, left in enumerate(rows):
        left_map = {str(item.get("exit_ts")): item.get("r") for item in left.get("resolved_outcomes", []) if item.get("exit_ts")}
        for right in rows[left_index + 1 :]:
            right_map = {str(item.get("exit_ts")): item.get("r") for item in right.get("resolved_outcomes", []) if item.get("exit_ts")}
            common = sorted(set(left_map) & set(right_map))
            result.append(
                {
                    "left": left.get("family"),
                    "right": right.get("family"),
                    "joint_outcomes": len(common),
                    "classification": "ready_for_correlation_review" if len(common) >= min_joint else "insufficient_joint_outcomes",
                }
            )
    return result


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Four-Family Forward Portfolio Scoreboard",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Can trade: `{report.get('can_trade')}`",
        "",
        "| Family | Independent | Resolved/Required | WR/BE | EXP R | DD R | Promotion | Ready |",
        "|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    for row in report.get("families", []):
        lines.append(
            f"| `{row.get('family')}` | {row.get('independent_signals')} | {row.get('resolved')}/{row.get('resolved_required')} | "
            f"{row.get('winrate_pct')}/{row.get('breakeven_winrate_pct')} | {row.get('expectancy_r')} | {row.get('max_drawdown_r')} | "
            f"`{row.get('promotion_decision')}` | `{row.get('eligible_for_paper_design')}` |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Independent non-overlapping evidence only where the family provides it.",
            "- No family is allowed to execute from this scoreboard.",
            "- Correlation is not estimated until enough joint resolved outcomes exist.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize forward evidence across the four active strategy families.")
    parser.add_argument("--min-resolved", type=int, default=20)
    parser.add_argument("--min-expectancy-r", type=float, default=0.10)
    parser.add_argument("--out-prefix", default="docs/FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_2026-06-22")
    args = parser.parse_args()
    specs = [
        ("TREND_MIX_4H", "STRATEGY_MIX_FORWARD_SCOREBOARD_2026-06-08.json", "OI_GUARD_PROMOTION_GATE_2026-06-15.json"),
        ("RANGE_REFINED_4H", "RANGE_REFINED_OBSERVER_SCOREBOARD_2026-06-16.json", "RANGE_REFINED_PROMOTION_GATE_2026-06-17.json"),
        ("EDGE_FORWARD_4H", "EDGE_FORWARD_RANGE_SCOREBOARD_2026-06-18.json", "EDGE_FORWARD_PROMOTION_GATE_2026-06-18.json"),
        ("CROWD_FADE_1H", "CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19.json", "CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19.json"),
    ]
    families = [
        family_row(family, read_json(ROOT / "docs" / score), read_json(ROOT / "docs" / gate), args.min_resolved, args.min_expectancy_r)
        for family, score, gate in specs
    ]
    total_resolved = sum(int(item.get("resolved") or 0) for item in families)
    total_net_r = sum(float(item.get("net_r_total") or 0.0) for item in families)
    ready = [item["family"] for item in families if item.get("eligible_for_paper_design")]
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "four_family_portfolio_evidence_only",
            "can_trade": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
        },
        "thresholds": {"min_resolved": args.min_resolved, "min_expectancy_r": args.min_expectancy_r, "max_drawdown_r": -6.0},
        "families": families,
        "portfolio": {
            "families": len(families),
            "families_ready_for_paper_design": ready,
            "total_resolved": total_resolved,
            "total_net_r": round(total_net_r, 6),
            "correlation_status": "insufficient_joint_outcomes",
        },
        "pairwise": pairwise_overlap(families),
        "decision": "family_candidates_ready_for_manual_paper_design" if ready else "all_families_blocked_waiting_independent_forward_evidence",
        "next_action": "accumulate independent outcomes and reject families that remain negative; do not optimize thresholds on this sample",
        "can_trade": False,
    }
    for row in families:
        row.pop("resolved_outcomes", None)
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "resolved": total_resolved, "net_r": report["portfolio"]["total_net_r"], "ready": ready, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
