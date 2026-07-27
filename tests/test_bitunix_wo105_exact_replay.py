from __future__ import annotations

import json
from pathlib import Path

from tools import bitunix_wo105_exact_replay as module


def test_exact_123_frame_sample_passes_with_dual_parser_agreement() -> None:
    report = module.build_report()
    repeated = module.build_report()

    assert report["decision"] == "bitunix_wo105_exact_123_frame_replay_pass"
    assert report["canonical_replay"] == "PASS"
    assert report["public_contract_confirmed"] is True
    assert report["failures"] == []
    assert report["replays"]["reviewed_v2"]["parse_kinds"] == module.EXPECTED_KINDS
    assert report["replays"]["canonical"]["parse_kinds"] == module.EXPECTED_KINDS
    assert report["can_trade"] is False
    assert report == repeated
    assert report["proof_time_basis"] == "source_capture_ended_utc_for_deterministic_replay_receipt"


def test_similar_but_shorter_capture_is_not_accepted(tmp_path: Path) -> None:
    lines = module.DEFAULTS["raw"].read_text(encoding="utf-8").splitlines()
    substitute = tmp_path / "RAW_FRAMES.jsonl"
    substitute.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    report = module.build_report(raw_path=substitute)

    assert report["canonical_replay"] == "HOLD"
    assert "hash_mismatch:raw" in report["failures"]
    assert "raw_frame_count:122!=123" in report["failures"]
    assert report["can_trade"] is False


def test_index_line_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    rows = [json.loads(line) for line in module.DEFAULTS["index"].read_text(encoding="utf-8").splitlines()]
    rows[4]["sha256"] = "0" * 64
    substitute = tmp_path / "RAW_FRAME_INDEX.jsonl"
    substitute.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    report = module.build_report(index_path=substitute)

    assert report["canonical_replay"] == "HOLD"
    assert "hash_mismatch:index" in report["failures"]
    assert "index_raw_line_hash_mismatch" in report["failures"]
    assert report["sample_identity"]["index_raw_hash_mismatch_positions"] == [5]
