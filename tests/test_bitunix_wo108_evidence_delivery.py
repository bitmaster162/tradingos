from __future__ import annotations

import csv
import json
import os
import zipfile
from pathlib import Path

import pytest

from tools.bitunix_wo108_evidence_delivery import (
    EvidenceSpec,
    build_manifest,
    copy_evidence,
    create_zip,
    process_alive,
    secret_scan,
    verify_folder_against_manifest,
    zip_test,
)


def test_exact_copy_and_missing_are_reported_without_fabrication(tmp_path: Path) -> None:
    root = tmp_path / "root"
    package = tmp_path / "package"
    root.mkdir()
    original = root / "proof.bin"
    original.write_bytes(b"exact\x00bytes\n")

    rows, missing = copy_evidence(
        root,
        package,
        (
            EvidenceSpec("proof.bin", "proof", "artifacts/proof.bin"),
            EvidenceSpec("absent.json", "missing_receipt", "artifacts/absent.json"),
        ),
    )

    assert (package / "artifacts/proof.bin").read_bytes() == original.read_bytes()
    assert rows[0]["copy_mode"] == "ORIGINAL_BYTE_COPY"
    assert rows[0]["status"] == "VERIFIED"
    assert missing == [
        {
            "object": "missing_receipt",
            "requested_path": str(root / "absent.json"),
            "status": "MISSING_SOURCE_ORIGINAL",
            "reason": "source file does not exist; no replacement was fabricated",
        }
    ]
    assert not (package / "artifacts/absent.json").exists()


def test_secret_scan_is_high_confidence_and_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "clean.txt").write_text("api_key field is documented but has no value", encoding="utf-8")
    assert secret_scan(tmp_path) == []

    (tmp_path / "bad.txt").write_text("1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcd12345", encoding="utf-8")
    findings = secret_scan(tmp_path)
    assert findings == [{"path": "bad.txt", "kind": "telegram_bot_token"}]


def test_manifest_covers_payload_and_zip_tests(tmp_path: Path) -> None:
    package = tmp_path / "evidence"
    (package / "artifacts").mkdir(parents=True)
    (package / "artifacts/a.txt").write_text("a", encoding="utf-8")
    (package / "RESULT.json").write_text(json.dumps({"can_trade": False}), encoding="utf-8")

    rows, manifest_sha = build_manifest(package)

    assert manifest_sha
    assert {row["relative_path"] for row in rows} == {
        "RESULT.json",
        "artifacts/a.txt",
        "MANIFEST_SHA256.csv",
    }
    assert verify_folder_against_manifest(package, rows) == []
    with (package / "MANIFEST_SHA256.csv").open(encoding="utf-8", newline="") as handle:
        manifest_rows = list(csv.DictReader(handle))
    assert any(row["relative_path"] == "MANIFEST_SHA256.csv" for row in manifest_rows)

    archive = tmp_path / "evidence.zip"
    create_zip(package, archive)
    assert zip_test(archive) is True
    with zipfile.ZipFile(archive) as payload:
        assert "evidence/MANIFEST_SHA256.csv" in payload.namelist()


def test_zip_test_rejects_invalid_archive(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    assert zip_test(bad) is False


def test_process_alive_handles_current_and_invalid_pid() -> None:
    assert process_alive(os.getpid()) is True
    assert process_alive(-1) is False


def test_duplicate_destination_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    (tmp_path / "b").write_text("b", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_destination"):
        copy_evidence(
            tmp_path,
            tmp_path / "out",
            (
                EvidenceSpec("a", "a", "same"),
                EvidenceSpec("b", "b", "same"),
            ),
        )
