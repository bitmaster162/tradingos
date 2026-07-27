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


def approval_enabled(approval: dict[str, Any]) -> bool:
    payload = approval.get("approval") if isinstance(approval.get("approval"), dict) else approval
    return (
        payload.get("manual_approval_granted") is True
        and payload.get("validation_opening_allowed") is True
        and payload.get("can_trade") is False
    )


def snapshot_decision(snapshot_gate: dict[str, Any]) -> tuple[str | None, bool]:
    decision = str(snapshot_gate.get("decision") or "")
    snapshot_id = snapshot_gate.get("snapshot_id")
    sealed = decision in {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"}
    return (snapshot_id if isinstance(snapshot_id, str) and snapshot_id else None), sealed


def build_runner_status(protocol: dict[str, Any], snapshot_gate: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
    protocols = protocol.get("protocols") if isinstance(protocol.get("protocols"), list) else []
    source_train_snapshot_id = protocol.get("source_train_snapshot_id")
    validation_snapshot_id, validation_snapshot_sealed = snapshot_decision(snapshot_gate)
    checks = {
        "protocol_present": bool(protocol),
        "protocol_decision_ready": protocol.get("decision") == "microstructure_validation_protocol_draft_ready",
        "candidate_protocols_present": bool(protocols),
        "manual_approval_file_present": bool(approval),
        "manual_approval_granted": approval_enabled(approval),
        "validation_snapshot_sealed": validation_snapshot_sealed,
        "validation_snapshot_id_present": validation_snapshot_id is not None,
        "validation_snapshot_differs_from_train": bool(validation_snapshot_id)
        and bool(source_train_snapshot_id)
        and validation_snapshot_id != source_train_snapshot_id,
        "validation_execution_implemented": False,
        "signals_forbidden": True,
        "orders_forbidden": True,
        "can_trade_false": True,
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
    elif not protocols:
        decision = "blocked_no_candidate_protocol"
        next_action = "wait_for_reviewed_candidate_protocol"
    elif not approval:
        decision = "blocked_manual_approval_missing"
        next_action = "create_explicit_validation_approval_after_manual_review"
    elif not approval_enabled(approval):
        decision = "blocked_manual_approval_not_granted"
        next_action = "fix_or_revoke_validation_approval"
    elif not validation_snapshot_sealed or not validation_snapshot_id:
        decision = "blocked_waiting_for_validation_snapshot"
        next_action = "collect_new_exact_sealed_snapshot_for_validation"
    elif validation_snapshot_id == source_train_snapshot_id:
        decision = "blocked_validation_snapshot_same_as_train"
        next_action = "wait_for_newer_snapshot; never_validate_on_train_snapshot"
    else:
        decision = "blocked_validation_runner_skeleton_no_execution"
        next_action = "implement_validation_runner_in_a_separate_reviewed_change"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "protocol_decision": protocol.get("decision"),
        "source_train_snapshot_id": source_train_snapshot_id,
        "validation_snapshot_id": validation_snapshot_id,
        "candidate_count": len(protocols),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "next_action": next_action,
        "runtime_boundary": {
            "skeleton_only": True,
            "opens_validation": False,
            "opens_oos": False,
            "executes_strategy_code": False,
            "parameter_search_allowed": False,
            "reoptimization_allowed": False,
            "observer_registration_allowed": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Microstructure Validation Runner Skeleton",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Protocol decision: `{report.get('protocol_decision')}`.",
        f"- Train snapshot: `{report.get('source_train_snapshot_id')}`.",
        f"- Validation snapshot: `{report.get('validation_snapshot_id')}`.",
        f"- Candidates: `{report.get('candidate_count')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        "- This is a locked skeleton. It does not open validation/OOS and does not execute strategy code.",
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
    parser = argparse.ArgumentParser(description="Fail-closed validation runner skeleton for microstructure candidates")
    parser.add_argument("--protocol", default="docs/CROSS_VENUE_MICROSTRUCTURE_VALIDATION_PROTOCOL_DRAFT_2026-06-25.json")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--approval", default="configs/CROSS_VENUE_MICROSTRUCTURE_VALIDATION_APPROVAL.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_VALIDATION_RUNNER_SKELETON_2026-06-25")
    args = parser.parse_args()

    report = build_runner_status(
        read_json(resolve_path(args.protocol)),
        read_json(resolve_path(args.snapshot_gate)),
        read_json(resolve_path(args.approval)),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "candidate_count": report["candidate_count"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
