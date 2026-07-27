#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_HUMAN_CHECKS = (
    "candidate_report_reviewed",
    "train_result_reproducible",
    "cost_stress_survives_reviewed",
    "fold_stability_reviewed",
    "drawdown_tail_risk_reviewed",
    "no_feature_leakage_reviewed",
    "multiple_testing_policy_reviewed",
    "validation_budget_accepted",
    "no_live_execution_understood",
)
FORBIDDEN_FLAGS = (
    "parameter_search_allowed",
    "reoptimization_allowed",
    "observer_registration_allowed",
    "paper_execution_allowed",
    "live_execution_allowed",
    "signals_allowed",
    "orders_allowed",
)


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


def approval_payload(approval: dict[str, Any]) -> dict[str, Any]:
    payload = approval.get("approval") if isinstance(approval.get("approval"), dict) else approval
    return payload if isinstance(payload, dict) else {}


def protocol_candidates(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = protocol.get("protocols") if isinstance(protocol.get("protocols"), list) else []
    return [item for item in candidates if isinstance(item, dict)]


def candidate_matches(protocol: dict[str, Any], approval_data: dict[str, Any]) -> bool:
    rank = approval_data.get("candidate_rank")
    strategy_id = approval_data.get("strategy_id")
    for candidate in protocol_candidates(protocol):
        rank_matches = rank is not None and candidate.get("candidate_rank") == rank
        strategy_matches = bool(strategy_id) and candidate.get("strategy_id") == strategy_id
        if rank_matches and strategy_matches:
            return True
    return False


def snapshot_id(snapshot_gate: dict[str, Any]) -> str | None:
    value = snapshot_gate.get("snapshot_id")
    return value if isinstance(value, str) and value else None


def snapshot_is_sealed(snapshot_gate: dict[str, Any]) -> bool:
    return str(snapshot_gate.get("decision") or "") in {
        "microstructure_snapshot_sealed",
        "snapshot_already_sealed_for_readiness_epoch",
    }


def build_audit(protocol: dict[str, Any], approval: dict[str, Any], snapshot_gate: dict[str, Any]) -> dict[str, Any]:
    data = approval_payload(approval)
    checked = data.get("checked") if isinstance(data.get("checked"), dict) else {}
    prohibitions = data.get("prohibitions") if isinstance(data.get("prohibitions"), dict) else {}
    current_snapshot_id = snapshot_id(snapshot_gate)
    approved_validation_snapshot_id = data.get("validation_snapshot_id")
    source_train_snapshot_id = protocol.get("source_train_snapshot_id")

    checks = {
        "protocol_present": bool(protocol),
        "protocol_ready": protocol.get("decision") == "microstructure_validation_protocol_draft_ready",
        "candidate_protocols_present": bool(protocol_candidates(protocol)),
        "approval_file_present": bool(approval),
        "approval_scope_valid": data.get("approval_scope") == "microstructure_validation_only",
        "manual_approval_granted": data.get("manual_approval_granted") is True,
        "validation_opening_allowed": data.get("validation_opening_allowed") is True,
        "approval_can_trade_false": data.get("can_trade") is False,
        "source_train_snapshot_matches_protocol": bool(source_train_snapshot_id)
        and data.get("source_train_snapshot_id") == source_train_snapshot_id,
        "candidate_matches_protocol": candidate_matches(protocol, data),
        "validation_snapshot_id_present": isinstance(approved_validation_snapshot_id, str) and bool(approved_validation_snapshot_id),
        "validation_snapshot_differs_from_train": isinstance(approved_validation_snapshot_id, str)
        and bool(source_train_snapshot_id)
        and approved_validation_snapshot_id != source_train_snapshot_id,
        "current_snapshot_sealed": snapshot_is_sealed(snapshot_gate),
        "approval_matches_current_snapshot": bool(current_snapshot_id)
        and approved_validation_snapshot_id == current_snapshot_id,
        "all_human_checks_true": all(checked.get(name) is True for name in REQUIRED_HUMAN_CHECKS),
        "all_execution_prohibitions_false": all(prohibitions.get(name) is False for name in FORBIDDEN_FLAGS),
    }

    if not protocol:
        decision = "blocked_missing_validation_protocol"
        next_action = "run_validation_protocol_builder"
    elif protocol.get("decision") == "blocked_waiting_for_sealed_snapshot":
        decision = "blocked_waiting_for_training_candidate_snapshot"
        next_action = "continue_collecting_until_initial_sealed_snapshot"
    elif protocol.get("decision") != "microstructure_validation_protocol_draft_ready":
        decision = "blocked_validation_protocol_not_ready"
        next_action = "inspect_validation_protocol_decision"
    elif not protocol_candidates(protocol):
        decision = "blocked_no_candidate_protocol"
        next_action = "wait_for_reviewed_candidate_protocol"
    elif not approval:
        decision = "blocked_validation_approval_missing"
        next_action = "copy_template_to_explicit_approval_only_after_manual_review"
    elif data.get("manual_approval_granted") is not True:
        decision = "blocked_validation_approval_not_granted"
        next_action = "manual_review_required_before_approval"
    elif data.get("validation_opening_allowed") is not True:
        decision = "blocked_validation_opening_not_allowed"
        next_action = "set_validation_opening_allowed_only_after_review"
    elif not checks["candidate_matches_protocol"]:
        decision = "blocked_approval_candidate_mismatch"
        next_action = "fix_candidate_rank_and_strategy_id_to_match_protocol"
    elif not checks["source_train_snapshot_matches_protocol"]:
        decision = "blocked_approval_train_snapshot_mismatch"
        next_action = "fix_source_train_snapshot_id_to_match_protocol"
    elif not checks["validation_snapshot_id_present"]:
        decision = "blocked_validation_snapshot_id_missing"
        next_action = "bind_approval_to_exact_future_validation_snapshot"
    elif not checks["validation_snapshot_differs_from_train"]:
        decision = "blocked_validation_snapshot_same_as_train"
        next_action = "never_validate_on_training_snapshot"
    elif not checks["current_snapshot_sealed"]:
        decision = "blocked_waiting_for_validation_snapshot"
        next_action = "collect_until_exact_validation_snapshot_is_sealed"
    elif not checks["approval_matches_current_snapshot"]:
        decision = "blocked_approval_snapshot_not_current"
        next_action = "approval_must_name_current_sealed_validation_snapshot"
    elif not checks["all_human_checks_true"]:
        decision = "blocked_incomplete_manual_review_checks"
        next_action = "complete_required_manual_review_checklist"
    elif not checks["all_execution_prohibitions_false"]:
        decision = "blocked_unsafe_approval_permissions"
        next_action = "keep_all_execution_prohibitions_false"
    elif data.get("can_trade") is not False:
        decision = "blocked_approval_can_trade_not_false"
        next_action = "set_can_trade_false"
    else:
        decision = "validation_approval_structurally_valid_runner_still_skeleton"
        next_action = "validation_runner_still_requires_separate_implementation_review"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "protocol_decision": protocol.get("decision"),
        "source_train_snapshot_id": source_train_snapshot_id,
        "approval_candidate_rank": data.get("candidate_rank"),
        "approval_strategy_id": data.get("strategy_id"),
        "approval_validation_snapshot_id": approved_validation_snapshot_id,
        "current_snapshot_id": current_snapshot_id,
        "candidate_count": len(protocol_candidates(protocol)),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "next_action": next_action,
        "runtime_boundary": {
            "approval_audit_only": True,
            "creates_approval": False,
            "opens_validation": False,
            "opens_oos": False,
            "executes_strategy_code": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Microstructure Validation Approval Audit",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Protocol decision: `{report.get('protocol_decision')}`.",
        f"- Candidate: rank `{report.get('approval_candidate_rank')}`, strategy `{report.get('approval_strategy_id')}`.",
        f"- Train snapshot: `{report.get('source_train_snapshot_id')}`.",
        f"- Approved validation snapshot: `{report.get('approval_validation_snapshot_id')}`.",
        f"- Current snapshot: `{report.get('current_snapshot_id')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        "- Audit-only. It does not create approval, open validation/OOS, execute strategy code, send signals, or place orders.",
        "- `can_trade=false`.",
        "",
        "## Failed Checks",
        "",
    ]
    failed = report.get("failed_checks") if isinstance(report.get("failed_checks"), list) else []
    if failed:
        lines.extend(f"- `{item}`." for item in failed)
    else:
        lines.append("- none.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit explicit validation approval file for microstructure candidates")
    parser.add_argument("--protocol", default="docs/CROSS_VENUE_MICROSTRUCTURE_VALIDATION_PROTOCOL_DRAFT_2026-06-25.json")
    parser.add_argument("--approval", default="configs/CROSS_VENUE_MICROSTRUCTURE_VALIDATION_APPROVAL.json")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_VALIDATION_APPROVAL_AUDIT_2026-06-25")
    args = parser.parse_args()

    report = build_audit(
        read_json(resolve_path(args.protocol)),
        read_json(resolve_path(args.approval)),
        read_json(resolve_path(args.snapshot_gate)),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "candidate_count": report["candidate_count"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
