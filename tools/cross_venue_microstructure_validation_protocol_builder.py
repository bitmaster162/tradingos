#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def candidate_protocol(candidate: dict[str, Any], *, train_snapshot_id: str | None, run_id: str | None) -> dict[str, Any]:
    train = candidate.get("train") if isinstance(candidate.get("train"), dict) else {}
    return {
        "candidate_rank": candidate.get("rank"),
        "experiment": candidate.get("experiment"),
        "hypothesis_id": candidate.get("hypothesis_id"),
        "family": candidate.get("family"),
        "strategy_id": candidate.get("strategy_id"),
        "source_train_snapshot_id": train_snapshot_id,
        "source_run_id": run_id,
        "source_report_path": candidate.get("report_path"),
        "reported_train_metrics": train,
        "validation_contract": {
            "status": "draft_requires_manual_approval",
            "validation_opened": False,
            "oos_opened": False,
            "exact_validation_snapshot_id_required": True,
            "validation_snapshot_must_be_newer_than_train_snapshot": True,
            "train_snapshot_reuse_for_validation_forbidden": True,
            "parameter_search_allowed": False,
            "reoptimization_allowed": False,
            "candidate_parameters_locked": True,
            "feature_contract_must_match_train": True,
            "cost_model_must_be_same_or_stricter": True,
            "shell_allowed": False,
            "arbitrary_extra_args_allowed": False,
            "credentials_allowed": False,
            "observer_registration_allowed": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "minimum_validation_gates": {
            "min_validation_trades": 30,
            "mean_net_bps_gt_zero": True,
            "stress_mean_net_bps_gt_zero": True,
            "positive_folds_min": 2,
            "max_drawdown_bps_floor": -300.0,
            "bootstrap_probability_mean_gt_zero_min": 0.80,
            "manual_failure_mode_review_required": True,
        },
        "promotion_after_validation": {
            "automatic_oos_opening_allowed": False,
            "automatic_observer_registration_allowed": False,
            "automatic_paper_execution_allowed": False,
            "automatic_live_execution_allowed": False,
            "requires_new_governance_amendment": True,
            "can_trade": False,
        },
    }


def build_protocol(review_pack: dict[str, Any], governance: dict[str, Any]) -> dict[str, Any]:
    candidates = review_pack.get("candidates") if isinstance(review_pack.get("candidates"), list) else []
    review_decision = str(review_pack.get("decision") or "")
    if not review_pack:
        decision = "blocked_missing_candidate_review_pack"
        next_action = "run_candidate_review_pack"
    elif review_decision == "blocked_waiting_for_sealed_snapshot":
        decision = "blocked_waiting_for_sealed_snapshot"
        next_action = "continue_collecting_until_snapshot_gate_seals"
    elif review_decision == "blocked_no_candidate_to_review":
        decision = "blocked_no_candidate_to_validate"
        next_action = "no_validation_protocol_until_candidate_exists"
    elif review_decision != "microstructure_candidate_review_pack_ready":
        decision = "blocked_review_pack_not_ready"
        next_action = "inspect_review_pack_decision"
    elif not candidates:
        decision = "blocked_review_pack_has_no_candidates"
        next_action = "inspect_candidate_review_pack"
    else:
        decision = "microstructure_validation_protocol_draft_ready"
        next_action = "manual_approval_required_before_validation_snapshot_can_be_used"

    protocols = [
        candidate_protocol(
            candidate,
            train_snapshot_id=review_pack.get("snapshot_id") or governance.get("snapshot_id"),
            run_id=review_pack.get("run_id") or governance.get("run_id"),
        )
        for candidate in candidates
        if isinstance(candidate, dict)
    ]
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "review_pack_decision": review_pack.get("decision"),
        "governance_decision": governance.get("decision"),
        "source_train_snapshot_id": review_pack.get("snapshot_id") or governance.get("snapshot_id"),
        "source_run_id": review_pack.get("run_id") or governance.get("run_id"),
        "candidate_count": len(protocols),
        "protocols": protocols,
        "next_action": next_action,
        "global_validation_rules": {
            "manual_approval_required": True,
            "validation_data_opened_by_this_builder": False,
            "validation_runner_created_by_this_builder": False,
            "same_snapshot_validation_forbidden": True,
            "new_exact_snapshot_id_required": True,
            "only_one_validation_opening_per_candidate_without_amendment": True,
            "all_outputs_must_keep_can_trade_false": True,
        },
        "runtime_boundary": {
            "protocol_draft_only": True,
            "opens_validation": False,
            "opens_oos": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(protocol: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Microstructure Validation Protocol Draft",
        "",
        f"- Generated: `{protocol['generated_at']}`.",
        f"- Decision: `{protocol['decision']}`.",
        f"- Review pack decision: `{protocol.get('review_pack_decision')}`.",
        f"- Governance decision: `{protocol.get('governance_decision')}`.",
        f"- Source train snapshot: `{protocol.get('source_train_snapshot_id')}`.",
        f"- Source run ID: `{protocol.get('source_run_id')}`.",
        f"- Candidates: `{protocol.get('candidate_count')}`.",
        f"- Next action: `{protocol.get('next_action')}`.",
        "- This is a draft only. It does not open validation/OOS and does not permit observer/paper/live execution.",
        "- `can_trade=false`.",
        "",
    ]
    for item in protocol.get("protocols", []):
        lines.extend(
            [
                f"## Candidate {item.get('candidate_rank')}: {item.get('strategy_id')}",
                "",
                f"- Experiment: `{item.get('experiment')}`.",
                f"- Hypothesis: `{item.get('hypothesis_id')}`.",
                f"- Family: `{item.get('family')}`.",
                f"- Source report: `{item.get('source_report_path')}`.",
                "- Required validation snapshot: exact new sealed snapshot, newer than train snapshot.",
                "- Parameter search: forbidden.",
                "- Reoptimization: forbidden.",
                "- Automatic promotion: forbidden.",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build draft validation protocol for reviewed microstructure candidates")
    parser.add_argument("--review-pack", default="docs/CROSS_VENUE_MICROSTRUCTURE_CANDIDATE_REVIEW_PACK_2026-06-25.json")
    parser.add_argument("--governance", default="docs/CROSS_VENUE_MICROSTRUCTURE_CANDIDATE_GOVERNANCE_2026-06-25.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_VALIDATION_PROTOCOL_DRAFT_2026-06-25")
    args = parser.parse_args()

    protocol = build_protocol(
        read_json(resolve_path(args.review_pack)),
        read_json(resolve_path(args.governance)),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), protocol)
    out_prefix.with_suffix(".md").write_text(render_markdown(protocol), encoding="utf-8")
    print(json.dumps({"decision": protocol["decision"], "candidate_count": protocol["candidate_count"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
