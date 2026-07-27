from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.validate_source_authority_candidate import (
    REQUIRED_DOCUMENTS,
    _paths_overlap,
    load_registry,
    parent_git_roots,
    scan_bytes_for_secrets,
    validate_registry,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(ROOT / "SOURCE_AUTHORITY_REGISTRY.json")


def changed(path: tuple[str, ...], value: object) -> dict:
    registry = copy.deepcopy(REGISTRY)
    target = registry
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return registry


def assert_error(registry: dict, code: str) -> None:
    errors = validate_registry(registry)
    assert any(item.startswith(f"{code}:") for item in errors), errors


def test_registry_is_valid_proposal_only_contract() -> None:
    assert validate_registry(REGISTRY) == []
    assert set(REGISTRY["required_documents"]) == REQUIRED_DOCUMENTS


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("authority", "authority_status"), "REGISTERED", "AUTHORITY_STATUS"),
        (("authority", "human_approval_status"), "APPROVED", "APPROVAL_STATUS"),
        (("authority", "registered_source_root"), "C:\\source", "REGISTERED_ROOT"),
        (("authority", "adoption_permitted"), True, "ADOPTION"),
        (("authority", "self_application"), True, "SELF_APPLICATION"),
        (("active_relationship", "write_policy"), "READ_WRITE", "ACTIVE_WRITE"),
        (("active_relationship", "automatic_sync"), True, "ACTIVE_SYNC"),
        (("active_relationship", "runtime_wiring"), "ENABLED", "RUNTIME_WIRING"),
        (("active_relationship", "deployment_authorized"), True, "DEPLOYMENT"),
        (("candidate_repository", "writable_remotes_allowed"), True, "REMOTE_POLICY"),
        (("remote_policy", "writable_remote"), "origin", "WRITABLE_REMOTE"),
        (("permissions", "can_trade"), True, "CAN_TRADE"),
        (("permissions", "capital_permission"), "ALLOW", "CAPITAL"),
        (("permissions", "orders"), True, "EFFECT_ORDERS"),
    ],
)
def test_adoption_and_effect_escalations_fail_closed(
    path: tuple[str, ...], value: object, code: str
) -> None:
    assert_error(changed(path, value), code)


def test_missing_required_document_fails_closed() -> None:
    registry = copy.deepcopy(REGISTRY)
    registry["required_documents"].remove("ROLLBACK_PLAN.md")
    assert_error(registry, "REQUIRED_DOCUMENTS")


def test_divergent_wo009_cannot_be_promoted_or_merged() -> None:
    registry = copy.deepcopy(REGISTRY)
    registry["divergent_roots"][0]["authority_status"] = "ACCEPTED"
    registry["divergent_roots"][0]["disposition"] = "MERGE"
    errors = validate_registry(registry)
    assert any(item.startswith("WO009_AUTHORITY:") for item in errors)
    assert any(item.startswith("WO009_DISPOSITION:") for item in errors)


def test_r2_lineage_substitution_fails_closed() -> None:
    assert_error(
        changed(("provenance", "r2_proposal_commit"), "0" * 40),
        "R2_COMMIT",
    )
    assert_error(
        changed(("provenance", "r2_proposal_tree"), "0" * 40),
        "R2_TREE",
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"-----BEGIN " + b"PRIVATE KEY-----", "private_key"),
        (b"8772498635:" + b"A" * 35, "telegram_token"),
        (b"sk-" + b"A" * 32, "openai_key"),
        (b"ghp_" + b"A" * 36, "github_token"),
        (b"AKIA" + b"A" * 16, "aws_access_key"),
    ],
)
def test_high_confidence_secret_patterns_are_rejected(
    payload: bytes, expected: str
) -> None:
    assert expected in scan_bytes_for_secrets(payload)


def test_policy_text_without_secret_value_is_allowed() -> None:
    payload = b"No credentials, private API calls, access tokens, or passwords."
    assert scan_bytes_for_secrets(payload) == []


def test_path_overlap_detects_active_inside_candidate(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    active = candidate / "Active"
    assert _paths_overlap(candidate, active)


def test_path_overlap_allows_separate_roots(tmp_path: Path) -> None:
    assert not _paths_overlap(tmp_path / "candidate", tmp_path / "Active")


def test_parent_git_root_detection_fails_nested_layout(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "candidate"
    (outer / ".git").mkdir(parents=True)
    inner.mkdir()
    assert str(outer.resolve()) in parent_git_roots(inner)


def test_registry_round_trip_is_utf8_json() -> None:
    rendered = json.dumps(REGISTRY, sort_keys=True)
    assert json.loads(rendered)["work_order_id"] == REGISTRY["work_order_id"]
