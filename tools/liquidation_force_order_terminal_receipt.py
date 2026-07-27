#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TERMINAL_DECISIONS = {
    "force_order_pipeline_pass_for_manual_forward_review": "pass_for_manual_forward_review",
    "force_order_pipeline_tombstone_review_required": "tombstone_review_required",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def actual_artifact(value: str | Path) -> dict[str, Any]:
    path = resolve_path(value)
    return {
        "path": portable(path),
        "sha256": sha256_file(path),
        "size": path.stat().st_size if path.is_file() else None,
    }


def descriptor_errors(name: str, descriptor: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(descriptor, dict) or not descriptor.get("path"):
        return None, [f"pipeline_artifact_missing:{name}"]
    actual = actual_artifact(str(descriptor["path"]))
    errors: list[str] = []
    if actual["sha256"] is None:
        errors.append(f"pipeline_artifact_file_missing:{name}")
    if descriptor.get("exists") is not True:
        errors.append(f"pipeline_artifact_not_marked_present:{name}")
    if str(descriptor.get("sha256") or "").lower() != str(actual.get("sha256") or "").lower():
        errors.append(f"pipeline_artifact_hash_mismatch:{name}")
    if descriptor.get("size") != actual.get("size"):
        errors.append(f"pipeline_artifact_size_mismatch:{name}")
    return actual, errors


def same_path(left: Any, right: Any) -> bool:
    if not left or not right:
        return False
    return resolve_path(str(left)).resolve() == resolve_path(str(right)).resolve()


def build_candidate(lock_path: Path, pipeline_path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    lock = read_json(lock_path)
    pipeline = read_json(pipeline_path)
    errors: list[str] = []
    lock_hash = sha256_file(lock_path)
    pipeline_hash = sha256_file(pipeline_path)
    pipeline_decision = str(pipeline.get("decision") or "")
    expected_evaluation_decision = TERMINAL_DECISIONS.get(pipeline_decision)
    if not lock or not lock_hash:
        errors.append("lock_missing_or_invalid")
    if not pipeline or not pipeline_hash:
        errors.append("pipeline_report_missing_or_invalid")
    if expected_evaluation_decision is None:
        errors.append("pipeline_not_terminal")
    if pipeline.get("can_trade") is not False:
        errors.append("pipeline_trade_boundary_invalid")
    preregistration = pipeline.get("preregistration") if isinstance(pipeline.get("preregistration"), dict) else {}
    if preregistration.get("lock_id") != lock.get("lock_id"):
        errors.append("pipeline_lock_id_mismatch")
    if str(preregistration.get("sha256") or "").lower() != str(lock_hash or "").lower():
        errors.append("pipeline_lock_hash_mismatch")

    pipeline_artifacts = pipeline.get("artifacts") if isinstance(pipeline.get("artifacts"), dict) else {}
    artifacts: dict[str, dict[str, Any]] = {
        "pipeline_report": actual_artifact(pipeline_path),
        "preregistration_lock": actual_artifact(lock_path),
    }
    for name in ("intake_report", "event_study_report", "event_records_csv", "evaluation_report"):
        actual, item_errors = descriptor_errors(name, pipeline_artifacts.get(name))
        errors.extend(item_errors)
        if actual:
            artifacts[name] = actual

    event_path = resolve_path(artifacts.get("event_study_report", {}).get("path", ""))
    evaluation_path = resolve_path(artifacts.get("evaluation_report", {}).get("path", ""))
    records_path = resolve_path(artifacts.get("event_records_csv", {}).get("path", ""))
    event = read_json(event_path)
    evaluation = read_json(evaluation_path)
    if event.get("decision") != "force_order_event_study_ready_for_review":
        errors.append("event_study_not_terminal_ready")
    if event.get("can_trade") is not False:
        errors.append("event_study_trade_boundary_invalid")
    event_artifacts = event.get("artifacts") if isinstance(event.get("artifacts"), dict) else {}
    if not same_path(event_artifacts.get("records_csv"), records_path):
        errors.append("event_records_path_chain_mismatch")
    if str(event_artifacts.get("records_csv_sha256") or "").lower() != str(sha256_file(records_path) or "").lower():
        errors.append("event_records_hash_chain_mismatch")

    if evaluation.get("decision") != expected_evaluation_decision:
        errors.append("evaluation_terminal_decision_mismatch")
    if evaluation.get("can_trade") is not False:
        errors.append("evaluation_trade_boundary_invalid")
    if evaluation.get("integrity_errors") not in ([], None):
        errors.append("evaluation_contains_integrity_errors")
    evaluation_prereg = evaluation.get("preregistration") if isinstance(evaluation.get("preregistration"), dict) else {}
    if evaluation_prereg.get("lock_id") != lock.get("lock_id"):
        errors.append("evaluation_lock_id_mismatch")
    if str(evaluation_prereg.get("sha256") or "").lower() != str(lock_hash or "").lower():
        errors.append("evaluation_lock_hash_mismatch")
    evaluation_source = evaluation.get("source") if isinstance(evaluation.get("source"), dict) else {}
    if not same_path(evaluation_source.get("event_study_report"), event_path):
        errors.append("evaluation_event_report_path_chain_mismatch")
    if not same_path(evaluation_source.get("records_csv"), records_path):
        errors.append("evaluation_records_path_chain_mismatch")
    if str(evaluation_source.get("records_csv_sha256") or "").lower() != str(sha256_file(records_path) or "").lower():
        errors.append("evaluation_records_hash_chain_mismatch")
    evaluated = evaluation.get("evaluation") if isinstance(evaluation.get("evaluation"), dict) else {}
    if evaluated.get("sample_ready") is not True:
        errors.append("evaluation_sample_not_ready")
    checks = evaluated.get("economic_checks") if isinstance(evaluated.get("economic_checks"), dict) else {}
    if expected_evaluation_decision == "pass_for_manual_forward_review" and (not checks or not all(checks.values())):
        errors.append("pass_decision_without_all_economic_checks")
    if expected_evaluation_decision == "tombstone_review_required" and (not checks or all(checks.values())):
        errors.append("tombstone_decision_without_failed_economic_check")
    if errors:
        return None, sorted(set(errors))

    core = {
        "receipt_id": f"{lock_hash}:{pipeline_decision}",
        "lock_id": lock.get("lock_id"),
        "lock_sha256": lock_hash,
        "terminal_pipeline_decision": pipeline_decision,
        "terminal_evaluation_decision": expected_evaluation_decision,
        "artifacts": artifacts,
        "boundary": {
            "evidence_receipt_only": True,
            "automatic_promotion": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    return {
        "schema_version": 1,
        "created_at": now_iso(),
        **core,
        "evidence_chain_sha256": canonical_sha256(core),
    }, []


def read_ledger(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.is_file():
        return [], []
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"ledger_line_{line_number}:invalid_json")
                continue
            if not isinstance(payload, dict):
                errors.append(f"ledger_line_{line_number}:not_object")
                continue
            rows.append(payload)
    return rows, errors


def append_ledger(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "recorded_at": now_iso(),
        "receipt_id": receipt["receipt_id"],
        "evidence_chain_sha256": receipt["evidence_chain_sha256"],
        "lock_id": receipt["lock_id"],
        "lock_sha256": receipt["lock_sha256"],
        "terminal_pipeline_decision": receipt["terminal_pipeline_decision"],
        "terminal_evaluation_decision": receipt["terminal_evaluation_decision"],
        "can_trade": False,
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def create_or_verify_terminal_receipt(
    lock_path: Path,
    pipeline_path: Path,
    receipt_path: Path,
    ledger_path: Path,
) -> dict[str, Any]:
    candidate, errors = build_candidate(lock_path, pipeline_path)
    existing = read_json(receipt_path)
    ledger, ledger_errors = read_ledger(ledger_path)
    errors.extend(ledger_errors)
    action = None
    if candidate and existing:
        if existing.get("receipt_id") != candidate.get("receipt_id") or existing.get("evidence_chain_sha256") != candidate.get("evidence_chain_sha256"):
            errors.append("existing_receipt_mismatch")
        else:
            action = "verified"
    elif candidate and not errors:
        atomic_write_json(receipt_path, candidate)
        existing = candidate
        action = "created"

    if candidate and not errors:
        matching = [row for row in ledger if row.get("receipt_id") == candidate["receipt_id"]]
        if any(row.get("evidence_chain_sha256") != candidate["evidence_chain_sha256"] for row in matching):
            errors.append("ledger_receipt_mismatch")
        elif len(matching) > 1:
            errors.append("ledger_duplicate_receipt")
        elif not matching:
            append_ledger(ledger_path, candidate)
            action = f"{action}_ledger_recorded"

    if errors or not candidate:
        decision = "terminal_receipt_integrity_blocked"
        next_action = "repair the immutable evidence chain before accepting terminal completion"
    elif action and action.startswith("created"):
        decision = "terminal_receipt_created"
        next_action = "preserve receipt and require manual pass/tombstone review"
    else:
        decision = "terminal_receipt_verified"
        next_action = "terminal evidence chain remains unchanged"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": "tools/liquidation_force_order_terminal_receipt.py",
        "decision": decision,
        "action": action,
        "integrity_errors": sorted(set(errors)),
        "receipt_path": portable(receipt_path),
        "receipt_sha256": sha256_file(receipt_path),
        "ledger_path": portable(ledger_path),
        "receipt": existing if not errors else None,
        "boundary": {
            "evidence_receipt_only": True,
            "automatic_promotion": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "next_action": next_action,
        "can_trade": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or verify immutable evidence receipt for terminal forceOrder research")
    parser.add_argument("--prereg-lock", default="configs/BINANCE_FORCE_ORDER_EVENT_STUDY_PREREG_2026-07-12.json")
    parser.add_argument("--pipeline-report", required=True)
    parser.add_argument("--receipt", default="logs/liquidation_force_order/preregistered_terminal_receipt.json")
    parser.add_argument("--ledger", default="logs/liquidation_force_order/preregistered_terminal_receipts.jsonl")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_TERMINAL_RECEIPT_2026-07-12")
    args = parser.parse_args()
    report = create_or_verify_terminal_receipt(
        resolve_path(args.prereg_lock),
        resolve_path(args.pipeline_report),
        resolve_path(args.receipt),
        resolve_path(args.ledger),
    )
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out.with_suffix(".json"), report)
    print(json.dumps({"decision": report["decision"], "receipt": report["receipt_path"], "can_trade": False}, indent=2))
    return 2 if report["decision"] == "terminal_receipt_integrity_blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
