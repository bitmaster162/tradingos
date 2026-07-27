#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def review_checklist(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    train = candidate.get("train") if isinstance(candidate.get("train"), dict) else {}
    return [
        {
            "check": "train_result_reproducible",
            "status": "required_manual_review",
            "reason": "Re-run the exact report from the sealed snapshot and verify selected strategy/metrics match.",
        },
        {
            "check": "cost_stress_survives",
            "status": "required_manual_review",
            "reported_stress_mean_net_bps": train.get("stress_mean_net_bps"),
            "reason": "Candidate must stay positive after stressed fee/slippage assumptions.",
        },
        {
            "check": "fold_stability",
            "status": "required_manual_review",
            "reported_positive_folds": train.get("positive_folds"),
            "reason": "Reject if edge is concentrated in one time block.",
        },
        {
            "check": "drawdown_not_tail_risk",
            "status": "required_manual_review",
            "reported_max_drawdown_bps": train.get("max_drawdown_bps"),
            "reason": "Review distribution of losses before opening validation.",
        },
        {
            "check": "no_feature_leakage",
            "status": "required_manual_review",
            "reason": "Confirm only completed-minute fields are used and entry occurs on the next completed Binance minute.",
        },
        {
            "check": "multiple_testing_policy",
            "status": "required_manual_review",
            "reason": "Apply queue-level correction before any validation/OOS budget is opened.",
        },
        {
            "check": "validation_protocol_required",
            "status": "blocked_until_written",
            "reason": "A separate validation protocol must be created and accepted before validation data is opened.",
        },
    ]


def build_pack(governance: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    candidates = governance.get("candidates") if isinstance(governance.get("candidates"), list) else []
    decision = str(governance.get("decision") or "")
    if not governance:
        pack_decision = "blocked_missing_candidate_governance"
        next_action = "run_candidate_governance_gate"
    elif decision == "blocked_waiting_for_sealed_snapshot":
        pack_decision = "blocked_waiting_for_sealed_snapshot"
        next_action = "continue_collecting_until_snapshot_gate_seals"
    elif decision == "reject_no_microstructure_candidate":
        pack_decision = "blocked_no_candidate_to_review"
        next_action = "keep_collecting_or_preregister_new_hypotheses"
    elif decision == "microstructure_candidate_review_required_no_promotion" and candidates:
        pack_decision = "microstructure_candidate_review_pack_ready"
        next_action = "manual_review_candidates_before_validation_protocol"
    elif decision.startswith("blocked_"):
        pack_decision = "blocked_governance_not_safe"
        next_action = "fix_governance_blocker_before_review"
    else:
        pack_decision = "blocked_no_reviewable_candidate"
        next_action = "inspect_governance_decision"

    review_candidates = []
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        review_candidates.append(
            {
                "rank": index,
                "experiment": candidate.get("experiment"),
                "hypothesis_id": candidate.get("hypothesis_id"),
                "family": candidate.get("family"),
                "strategy_id": candidate.get("strategy_id"),
                "report_path": candidate.get("report_path"),
                "train": candidate.get("train"),
                "review_checklist": review_checklist(candidate),
                "promotion_boundary": {
                    "validation_opened_by_this_pack": False,
                    "observer_registration_allowed": False,
                    "paper_execution_allowed": False,
                    "live_execution_allowed": False,
                    "signals_allowed": False,
                    "orders_allowed": False,
                    "can_trade": False,
                },
            }
        )

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": pack_decision,
        "governance_decision": governance.get("decision"),
        "runner_decision": runner.get("decision"),
        "snapshot_id": governance.get("snapshot_id") or runner.get("snapshot_id"),
        "run_id": governance.get("run_id") or runner.get("run_id"),
        "candidate_count": len(review_candidates),
        "candidates": review_candidates,
        "next_action": next_action,
        "review_rules": {
            "manual_review_required": True,
            "automatic_validation_opening_allowed": False,
            "automatic_oos_opening_allowed": False,
            "automatic_observer_registration_allowed": False,
            "paper_or_live_execution_allowed": False,
        },
        "runtime_boundary": {
            "review_pack_only": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(pack: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Microstructure Candidate Review Pack",
        "",
        f"- Generated: `{pack['generated_at']}`.",
        f"- Decision: `{pack['decision']}`.",
        f"- Governance decision: `{pack.get('governance_decision')}`.",
        f"- Runner decision: `{pack.get('runner_decision')}`.",
        f"- Snapshot: `{pack.get('snapshot_id')}`.",
        f"- Run ID: `{pack.get('run_id')}`.",
        f"- Candidates: `{pack.get('candidate_count')}`.",
        f"- Next action: `{pack.get('next_action')}`.",
        "- This pack does not open validation/OOS and does not permit observer/paper/live execution.",
        "- `can_trade=false`.",
        "",
    ]
    for candidate in pack.get("candidates", []):
        lines.extend(
            [
                f"## Candidate {candidate.get('rank')}: {candidate.get('strategy_id')}",
                "",
                f"- Experiment: `{candidate.get('experiment')}`.",
                f"- Hypothesis: `{candidate.get('hypothesis_id')}`.",
                f"- Family: `{candidate.get('family')}`.",
                f"- Report: `{candidate.get('report_path')}`.",
                "",
                "### Required Checks",
                "",
            ]
        )
        for item in candidate.get("review_checklist", []):
            lines.append(f"- `{item.get('check')}`: `{item.get('status')}` - {item.get('reason')}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build manual review pack for microstructure research candidates")
    parser.add_argument("--governance", default="docs/CROSS_VENUE_MICROSTRUCTURE_CANDIDATE_GOVERNANCE_2026-06-25.json")
    parser.add_argument("--runner-report", default="docs/CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_2026-06-25.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_CANDIDATE_REVIEW_PACK_2026-06-25")
    args = parser.parse_args()

    pack = build_pack(
        read_json(resolve_path(args.governance)),
        read_json(resolve_path(args.runner_report)),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), pack)
    out_prefix.with_suffix(".md").write_text(render_markdown(pack), encoding="utf-8")
    print(json.dumps({"decision": pack["decision"], "candidate_count": pack["candidate_count"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
