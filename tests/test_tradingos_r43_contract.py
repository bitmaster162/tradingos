from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools.tradingos_r43_contract import safe_relative, verify_identity


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(os.environ.get("TRADINGOS_SOURCE_ROOT", ROOT))
CONTRACT_PATH = ROOT / "configs" / "TRADINGOS_R43_EVIDENCE_CONTRACT.json"


def contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_contract_is_orderless_and_non_promoting() -> None:
    value = contract()

    assert value["status"] == "DISPOSABLE_OUTCOME_CANDIDATE"
    assert value["accepted_strength_preservation"]["registration_performed"] is False
    assert value["accepted_strength_preservation"]["promotion_performed"] is False
    assert value["self_application"] is False
    assert value["can_trade"] is False
    assert value["capital_permission"] == "DENY"


def test_seven_runtime_snapshots_are_excluded_from_source() -> None:
    rows = contract()["runtime_state_exclusions"]

    assert len(rows) == 7
    assert all(row["classification"] == "RUNTIME_STATE" for row in rows)
    assert all(row["action"] == "EXCLUDE" for row in rows)
    assert all(not (ROOT / row["path"]).exists() for row in rows)


def test_external_evidence_and_fixtures_are_explicitly_separate() -> None:
    value = contract()
    evidence = value["external_evidence_additions"]
    fixtures = value["immutable_test_fixtures"]

    assert len(evidence) == 7
    assert len(fixtures) == 8
    assert all(row["source_relation"] == "UNLOCKED_ACTIVE_OBSERVED" for row in evidence)
    assert sum(row["source_relation"] == "UNLOCKED_ACTIVE_OBSERVED" for row in fixtures) == 6
    assert sum(row["source_relation"] == "R6_EXCLUDED_GENERATED_HASH_BOUND" for row in fixtures) == 2
    assert all(not (SOURCE_ROOT / row["path"]).exists() for row in evidence + fixtures)


def test_each_external_input_has_size_and_sha256() -> None:
    value = contract()
    rows = value["external_evidence_additions"] + value["immutable_test_fixtures"]

    assert all(isinstance(row["size"], int) and row["size"] > 0 for row in rows)
    assert all(len(row["sha256"]) == 64 for row in rows)


@pytest.mark.parametrize("value", ["/absolute", "../escape", "a/../../escape", "C:/absolute"])
def test_safe_relative_rejects_escape(value: str) -> None:
    with pytest.raises(ValueError):
        safe_relative(value)


def test_safe_relative_accepts_portable_path() -> None:
    assert safe_relative("docs/evidence.json") == Path("docs") / "evidence.json"


def test_verify_identity_passes_and_detects_tamper(tmp_path: Path) -> None:
    path = tmp_path / "fixture.bin"
    path.write_bytes(b"bound")
    verify_identity(
        path,
        5,
        "5e1cf42878df58fea7bfa45b715b7832d889092ad23e802e63912b1bfd205630",
    )
    path.write_bytes(b"tampered")
    with pytest.raises(RuntimeError):
        verify_identity(
            path,
            5,
            "5e1cf42878df58fea7bfa45b715b7832d889092ad23e802e63912b1bfd205630",
        )


def test_projection_policy_is_fail_closed() -> None:
    policy = contract()["test_assembly_policy"]

    assert policy["source_is_immutable_input"] is True
    assert policy["evidence_projection_is_test_only"] is True
    assert policy["fixture_projection_is_test_only"] is True
    assert policy["runtime_state_projection_allowed"] is False
    assert policy["collision_policy"] == "FAIL_UNLESS_BYTE_IDENTICAL"
    assert policy["path_traversal_policy"] == "FAIL_CLOSED"
