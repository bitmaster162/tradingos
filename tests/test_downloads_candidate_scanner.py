from __future__ import annotations

import hashlib
from pathlib import Path

from tools import downloads_candidate_scanner as scanner


def test_russian_trading_filename_is_classified_high(tmp_path: Path) -> None:
    candidate = tmp_path / "Криптодеривативы_ Микроструктура и Риск.docx"
    candidate.write_bytes(b"bounded-test")

    item = scanner.classify(candidate)

    assert item["relevance"] == "high"
    assert "крипто" in item["keyword_hits"]["high"]
    assert "микроструктур" in item["keyword_hits"]["high"]
    assert item["processing_status"] == "unprocessed"


def test_exact_hash_is_removed_from_actionable_queue(tmp_path: Path) -> None:
    candidate = tmp_path / "BTC trading strategy.md"
    payload = b"same immutable content"
    candidate.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    index = {
        "hashes": {digest: {"docs/EXISTING_INTAKE.json"}},
        "names": {candidate.name.casefold(): {"docs/EXISTING_INTAKE.json"}},
    }

    item = scanner.classify(candidate, index)

    assert item["processing_status"] == "processed_exact_hash"
    assert item["recommended_action"] == "already_processed_no_repeat"
    assert item["processing_evidence"] == ["docs/EXISTING_INTAKE.json"]


def test_same_name_with_new_hash_requires_review(tmp_path: Path) -> None:
    candidate = tmp_path / "BTC strategy.md"
    candidate.write_bytes(b"materially changed content")
    index = {
        "hashes": {},
        "names": {candidate.name.casefold(): {"docs/OLDER_INTAKE.json"}},
    }

    item = scanner.classify(candidate, index)

    assert item["processing_status"] == "name_seen_hash_changed"
    assert item["recommended_action"] != "already_processed_no_repeat"


def test_sensitive_filename_is_not_hashed(tmp_path: Path, monkeypatch) -> None:
    candidate = tmp_path / "trading_api_key_backup.txt"
    candidate.write_text("do-not-read", encoding="utf-8")

    def fail_hash(_: Path) -> str:
        raise AssertionError("sensitive files must not be opened for hashing")

    monkeypatch.setattr(scanner, "sha256", fail_hash)
    item = scanner.classify(candidate)

    assert item["relevance"] == "excluded_sensitive"
    assert item["sha256"] is None
