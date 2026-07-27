from __future__ import annotations

import json
from pathlib import Path

from tools.liquidation_force_order_terminal_receipt import (
    create_or_verify_terminal_receipt,
    sha256_file,
)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def descriptor(path: Path) -> dict:
    return {
        "path": str(path),
        "exists": True,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def terminal_chain(tmp_path: Path, evaluation_decision: str = "pass_for_manual_forward_review") -> tuple[Path, Path, Path]:
    lock_path = tmp_path / "lock.json"
    intake_path = tmp_path / "intake.json"
    records_path = tmp_path / "records.csv"
    event_path = tmp_path / "event.json"
    evaluation_path = tmp_path / "evaluation.json"
    pipeline_path = tmp_path / "pipeline.json"
    lock = {"lock_id": "locked-force-order-v3", "can_trade": False}
    write_json(lock_path, lock)
    write_json(intake_path, {"decision": "force_order_context_ready_for_preregistered_research"})
    records_path.write_text("symbol,reversal_return_bps\nBTCUSDT,10\n", encoding="utf-8")
    write_json(
        event_path,
        {
            "decision": "force_order_event_study_ready_for_review",
            "can_trade": False,
            "artifacts": {
                "records_csv": str(records_path),
                "records_csv_sha256": sha256_file(records_path),
                "records": 1,
            },
        },
    )
    checks = {"mean": True, "winrate": True, "ci": True, "horizons": True}
    if evaluation_decision == "tombstone_review_required":
        checks["ci"] = False
    write_json(
        evaluation_path,
        {
            "decision": evaluation_decision,
            "can_trade": False,
            "integrity_errors": [],
            "preregistration": {
                "lock_id": lock["lock_id"],
                "sha256": sha256_file(lock_path),
            },
            "source": {
                "event_study_report": str(event_path),
                "records_csv": str(records_path),
                "records_csv_sha256": sha256_file(records_path),
            },
            "evaluation": {"sample_ready": True, "economic_checks": checks},
        },
    )
    pipeline_decision = (
        "force_order_pipeline_pass_for_manual_forward_review"
        if evaluation_decision == "pass_for_manual_forward_review"
        else "force_order_pipeline_tombstone_review_required"
    )
    write_json(
        pipeline_path,
        {
            "decision": pipeline_decision,
            "can_trade": False,
            "preregistration": {
                "lock_id": lock["lock_id"],
                "sha256": sha256_file(lock_path),
            },
            "artifacts": {
                "intake_report": descriptor(intake_path),
                "event_study_report": descriptor(event_path),
                "event_records_csv": descriptor(records_path),
                "evaluation_report": descriptor(evaluation_path),
            },
        },
    )
    return lock_path, pipeline_path, records_path


def test_terminal_receipt_is_created_once_and_then_verified(tmp_path) -> None:
    lock_path, pipeline_path, _records_path = terminal_chain(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    ledger_path = tmp_path / "ledger.jsonl"

    created = create_or_verify_terminal_receipt(lock_path, pipeline_path, receipt_path, ledger_path)
    verified = create_or_verify_terminal_receipt(lock_path, pipeline_path, receipt_path, ledger_path)

    assert created["decision"] == "terminal_receipt_created"
    assert verified["decision"] == "terminal_receipt_verified"
    assert created["receipt"]["evidence_chain_sha256"] == verified["receipt"]["evidence_chain_sha256"]
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 1
    assert created["can_trade"] is False


def test_terminal_receipt_blocks_artifact_tampering(tmp_path) -> None:
    lock_path, pipeline_path, records_path = terminal_chain(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    ledger_path = tmp_path / "ledger.jsonl"
    first = create_or_verify_terminal_receipt(lock_path, pipeline_path, receipt_path, ledger_path)
    with records_path.open("a", encoding="utf-8") as handle:
        handle.write("ETHUSDT,99\n")

    tampered = create_or_verify_terminal_receipt(lock_path, pipeline_path, receipt_path, ledger_path)

    assert first["decision"] == "terminal_receipt_created"
    assert tampered["decision"] == "terminal_receipt_integrity_blocked"
    assert "pipeline_artifact_hash_mismatch:event_records_csv" in tampered["integrity_errors"]


def test_terminal_receipt_accepts_consistent_tombstone_chain(tmp_path) -> None:
    lock_path, pipeline_path, _records_path = terminal_chain(tmp_path, "tombstone_review_required")

    report = create_or_verify_terminal_receipt(
        lock_path,
        pipeline_path,
        tmp_path / "receipt.json",
        tmp_path / "ledger.jsonl",
    )

    assert report["decision"] == "terminal_receipt_created"
    assert report["receipt"]["terminal_evaluation_decision"] == "tombstone_review_required"
