from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.strategy_discovery_pipeline import discover_files, processed_hashes


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_processed_intake_receipt_suppresses_duplicate_candidate(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    receipts_dir = tmp_path / "receipts"
    source_dir.mkdir()
    receipts_dir.mkdir()
    source = source_dir / "strategy.md"
    source.write_text("testable strategy", encoding="utf-8")
    write_json(
        receipts_dir / "STRATEGY_DOCX_INTAKE_2026-07-12.json",
        {
            "decision": "processed_no_new_alpha",
            "processing_status": "processed_do_not_repeat",
            "source": {"sha256": digest(source)},
        },
    )

    assert digest(source) in processed_hashes({"processed": []}, receipts_dir)
    assert discover_files([source_dir], {"processed": []}, receipts_dir) == []


def test_unfinished_intake_does_not_suppress_candidate(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    receipts_dir = tmp_path / "receipts"
    source_dir.mkdir()
    receipts_dir.mkdir()
    source = source_dir / "strategy.md"
    source.write_text("testable strategy", encoding="utf-8")
    write_json(
        receipts_dir / "STRATEGY_DOCX_INTAKE_2026-07-12.json",
        {
            "decision": "intake_pending_review",
            "source": {"sha256": digest(source)},
        },
    )

    assert digest(source) not in processed_hashes({"processed": []}, receipts_dir)
    assert discover_files([source_dir], {"processed": []}, receipts_dir) == [source]


def test_registry_and_receipt_hashes_are_combined(tmp_path: Path) -> None:
    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir()
    registry_hash = "a" * 64
    receipt_hash = "b" * 64
    write_json(
        receipts_dir / "DOCX_INTAKE.json",
        {
            "processing_status": "processed_do_not_repeat",
            "source": {"sha256": receipt_hash},
        },
    )

    assert processed_hashes({"processed": [{"sha256": registry_hash}]}, receipts_dir) == {
        registry_hash,
        receipt_hash,
    }
