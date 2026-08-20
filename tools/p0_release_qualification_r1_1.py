#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.p0_release_qualification import (
    EXPECTED_SCHEMA_MATRIX,
    EXPECTED_SURFACE_IDS,
    REQUIRED_GLOBAL_INVARIANTS,
    sha256_obj,
)

RELEASE_MANIFEST_SCHEMA = "bitevo.p0_release_candidate_manifest.v1_1"
RELEASE_QUALIFICATION_SCHEMA = "bitevo.p0_release_qualification_receipt.v1_1"
LIVE_REVIEW_SCHEMA = "triaxis.p0_live_crossrepo_snapshot.v1"

REQUIRED_CONDITIONS = {
    "CONTROL_CENTER_PROVIDER_CAPTURE_STALE",
    "ARCHIVEOS_BLOCKED_REVERIFY_STALE",
    "CI_PREJOB_BLOCKED_ON_SEVEN_SURFACES",
    "NO_LIVE_WRITER_BACKEND_PROOF",
    "NO_DURABLE_COMMIT_PROOF",
    "NO_CRASH_SAFE_PERSISTENCE_PROOF",
    "NO_RUNTIME_DEPLOYMENT_PROOF",
    "NO_MERGE_AUTHORIZATION",
    "INDEPENDENT_LIVE_CROSSREPO_SNAPSHOT_BOUND",
}

ALLOWED_CI = {"SUCCESS_EXACT_HEAD", "CI_BLOCKED_PRE_JOB"}
EXPECTED_GREEN = {"sct_p0", "continuityos_history_p0"}
EXPECTED_BLOCKED = set(EXPECTED_SURFACE_IDS) - EXPECTED_GREEN


class P0ReleaseQualificationR11Error(ValueError):
    pass


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise P0ReleaseQualificationR11Error(f"{field}_required")
    return value.strip()


def _hex(value: Any, field: str, lengths: tuple[int, ...] = (40, 64)) -> str:
    text = _text(value, field).lower()
    if len(text) not in lengths or any(ch not in "0123456789abcdef" for ch in text):
        raise P0ReleaseQualificationR11Error(f"{field}_invalid_hex")
    return text


def _iso(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise P0ReleaseQualificationR11Error(f"{field}_must_be_iso8601") from exc
    if parsed.tzinfo is None:
        raise P0ReleaseQualificationR11Error(f"{field}_timezone_required")
    return text


def _verify_manifest_hash(manifest: Mapping[str, Any], expected_manifest_sha256: str) -> str:
    if not isinstance(manifest, Mapping) or manifest.get("schema") != RELEASE_MANIFEST_SCHEMA:
        raise P0ReleaseQualificationR11Error("manifest_schema_mismatch")
    supplied = _hex(manifest.get("manifest_sha256"), "manifest_sha256", (64,))
    computed = sha256_obj({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    if supplied != computed:
        raise P0ReleaseQualificationR11Error("manifest_hash_mismatch")
    expected = _hex(expected_manifest_sha256, "expected_manifest_sha256", (64,))
    if supplied != expected:
        raise P0ReleaseQualificationR11Error("manifest_external_digest_mismatch")
    return supplied


def _verify_live_review_anchor(
    manifest: Mapping[str, Any],
    *,
    expected_live_snapshot_sha256: str,
    expected_live_snapshot_commit_sha: str,
) -> tuple[str, str]:
    review = manifest.get("independent_live_review")
    if not isinstance(review, Mapping) or review.get("schema") != LIVE_REVIEW_SCHEMA:
        raise P0ReleaseQualificationR11Error("live_review_schema_mismatch")
    if review.get("repo") != "bitmaster162/TRIAXIS" or review.get("pr") != 9:
        raise P0ReleaseQualificationR11Error("live_review_identity_mismatch")
    if review.get("branch") != "agent/p0-independent-final-review-r1":
        raise P0ReleaseQualificationR11Error("live_review_branch_mismatch")
    snapshot_sha = _hex(review.get("snapshot_sha256"), "live_review.snapshot_sha256", (64,))
    commit_sha = _hex(review.get("commit_sha"), "live_review.commit_sha", (40,))
    if snapshot_sha != _hex(expected_live_snapshot_sha256, "expected_live_snapshot_sha256", (64,)):
        raise P0ReleaseQualificationR11Error("live_review_external_snapshot_digest_mismatch")
    if commit_sha != _hex(expected_live_snapshot_commit_sha, "expected_live_snapshot_commit_sha", (40,)):
        raise P0ReleaseQualificationR11Error("live_review_external_commit_mismatch")
    if review.get("live_github_reads_performed") is not True:
        raise P0ReleaseQualificationR11Error("live_review_fresh_read_not_asserted")
    if review.get("generated_outside_qualifier") is not True:
        raise P0ReleaseQualificationR11Error("live_review_not_independent")
    _iso(review.get("reviewed_at"), "live_review.reviewed_at")
    return snapshot_sha, commit_sha


def _verify_surfaces(manifest: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    rows = manifest.get("surfaces")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise P0ReleaseQualificationR11Error("surfaces_must_be_sequence")
    if len(rows) != len(EXPECTED_SURFACE_IDS):
        raise P0ReleaseQualificationR11Error("surface_count_mismatch")

    seen: list[str] = []
    blocked: list[str] = []
    green: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise P0ReleaseQualificationR11Error("surface_must_be_object")
        sid = _text(row.get("id"), "surface.id")
        seen.append(sid)
        _text(row.get("repo"), f"{sid}.repo")
        if isinstance(row.get("pr"), bool) or not isinstance(row.get("pr"), int) or row["pr"] < 1:
            raise P0ReleaseQualificationR11Error(f"{sid}_pr_invalid")
        _hex(row.get("base_sha"), f"{sid}.base_sha", (40,))
        _hex(row.get("head_sha"), f"{sid}.head_sha", (40,))
        if row.get("state") != "open" or row.get("draft") is not True or row.get("merged") is not False:
            raise P0ReleaseQualificationR11Error(f"{sid}_must_remain_open_draft_unmerged")
        if row.get("effect_authority") not in {"NONE", "NONE_IN_P0"}:
            raise P0ReleaseQualificationR11Error(f"{sid}_effect_authority_breached")
        ci = _text(row.get("ci_classification"), f"{sid}.ci_classification")
        if ci not in ALLOWED_CI:
            raise P0ReleaseQualificationR11Error(f"{sid}_ci_classification_invalid")
        (green if ci == "SUCCESS_EXACT_HEAD" else blocked).append(sid)

    if tuple(seen) != tuple(EXPECTED_SURFACE_IDS):
        raise P0ReleaseQualificationR11Error("surface_order_or_identity_mismatch")
    if set(green) != EXPECTED_GREEN or set(blocked) != EXPECTED_BLOCKED:
        raise P0ReleaseQualificationR11Error("fresh_ci_partition_mismatch")
    if "control_center_authority" not in blocked:
        raise P0ReleaseQualificationR11Error("control_center_authority_must_be_blocked_prejob")
    return tuple(seen), tuple(blocked), tuple(green)


def _verify_schema_matrix(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    matrix = manifest.get("schema_matrix")
    if not isinstance(matrix, Mapping) or tuple(matrix.keys()) != tuple(EXPECTED_SCHEMA_MATRIX.keys()):
        raise P0ReleaseQualificationR11Error("schema_matrix_generation_mismatch")
    flat: list[str] = []
    for generation, expected in EXPECTED_SCHEMA_MATRIX.items():
        value = matrix.get(generation)
        if not isinstance(value, (list, tuple)) or tuple(value) != tuple(expected):
            raise P0ReleaseQualificationR11Error(f"schema_matrix_mismatch:{generation}")
        flat.extend(value)
    if len(flat) != len(set(flat)):
        raise P0ReleaseQualificationR11Error("schema_matrix_duplicate_contract")
    return tuple(flat)


def _verify_invariants_and_conditions(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    invariants = manifest.get("global_invariants")
    if not isinstance(invariants, Mapping) or dict(invariants) != dict(REQUIRED_GLOBAL_INVARIANTS):
        raise P0ReleaseQualificationR11Error("global_invariants_mismatch")
    conditions = manifest.get("known_conditions")
    if not isinstance(conditions, (list, tuple)) or set(conditions) != REQUIRED_CONDITIONS:
        raise P0ReleaseQualificationR11Error("known_conditions_mismatch")
    if len(conditions) != len(REQUIRED_CONDITIONS):
        raise P0ReleaseQualificationR11Error("known_conditions_duplicate_or_missing")
    return tuple(conditions)


def qualify_p0_release_candidate_r1_1(
    manifest: Mapping[str, Any],
    *,
    expected_manifest_sha256: str,
    expected_live_snapshot_sha256: str,
    expected_live_snapshot_commit_sha: str,
    generated_at: str,
) -> dict[str, Any]:
    manifest_sha = _verify_manifest_hash(manifest, expected_manifest_sha256)
    snapshot_at = _iso(manifest.get("snapshot_at"), "snapshot_at")
    generated = _iso(generated_at, "generated_at")
    if manifest.get("qualification_scope") != "P0_SHADOW_R1_TO_R9_GLOBAL_INVARIANTS_WITH_INDEPENDENT_LIVE_REVIEW":
        raise P0ReleaseQualificationR11Error("qualification_scope_invalid")

    parent = manifest.get("qualified_input_parent")
    if not isinstance(parent, Mapping):
        raise P0ReleaseQualificationR11Error("qualified_input_parent_missing")
    if parent.get("repo") != "bitmaster162/tradingos" or parent.get("branch") != "agent/p0-shadow-integration-r1":
        raise P0ReleaseQualificationR11Error("qualified_input_parent_identity_mismatch")
    parent_sha = _hex(parent.get("head_sha"), "qualified_input_parent.head_sha", (40,))
    if parent_sha != "80d7e24c983529e837daaae49338cf71f9007425":
        raise P0ReleaseQualificationR11Error("frozen_r1_r9_input_changed")

    live_sha, live_commit = _verify_live_review_anchor(
        manifest,
        expected_live_snapshot_sha256=expected_live_snapshot_sha256,
        expected_live_snapshot_commit_sha=expected_live_snapshot_commit_sha,
    )
    surface_ids, blocked, green = _verify_surfaces(manifest)
    contracts = _verify_schema_matrix(manifest)
    conditions = _verify_invariants_and_conditions(manifest)

    semantics = manifest.get("release_semantics")
    expected_semantics = {
        "candidate_only": True,
        "production_qualified": False,
        "current_truth_promotion_allowed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "release_decision": "HOLD",
        "action": "WAIT",
        "final_independent_review_required": True,
    }
    if not isinstance(semantics, Mapping) or dict(semantics) != expected_semantics:
        raise P0ReleaseQualificationR11Error("release_semantics_mismatch")

    body = {
        "schema": RELEASE_QUALIFICATION_SCHEMA,
        "manifest_sha256": manifest_sha,
        "snapshot_at": snapshot_at,
        "generated_at": generated,
        "qualified_input_parent_sha": parent_sha,
        "independent_live_snapshot_sha256": live_sha,
        "independent_live_snapshot_commit_sha": live_commit,
        "surface_ids": surface_ids,
        "surface_count": len(surface_ids),
        "schema_contract_count": len(contracts),
        "verified_generations": tuple(EXPECTED_SCHEMA_MATRIX.keys()),
        "ci_blocked_surfaces": blocked,
        "ci_blocked_surface_count": len(blocked),
        "ci_green_surfaces": green,
        "ci_green_surface_count": len(green),
        "known_conditions": conditions,
        "global_invariants_verified": True,
        "schema_compatibility_verified": True,
        "manifest_snapshot_hash_bound": True,
        "independent_live_review_reference_bound": True,
        "cross_repo_state_live_read_performed_by_qualifier": False,
        "p0_architecture_closed_for_candidate_review": True,
        "final_independent_review_required": True,
        "production_qualified": False,
        "release_ready": False,
        "merge_ready": False,
        "deploy_ready": False,
        "runtime_ready": False,
        "current_truth_promotion_allowed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "execution_authority": "NONE",
        "can_execute": False,
        "can_trade": False,
        "capital_permission": "DENY",
        "decision": "HOLD",
        "action": "WAIT",
        "status": "P0_RELEASE_CANDIDATE_R1_1_QUALIFIED_FOR_INDEPENDENT_FINAL_REVIEW_WITH_CONDITIONS",
    }
    body["release_qualification_sha256"] = sha256_obj(body)
    return body


def load_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise P0ReleaseQualificationR11Error("manifest_root_must_be_object")
    return payload


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Offline P0 release-candidate R1.1 qualification.")
    parser.add_argument("manifest")
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-live-snapshot-sha256", required=True)
    parser.add_argument("--expected-live-snapshot-commit-sha", required=True)
    parser.add_argument("--generated-at", required=True)
    args = parser.parse_args()
    receipt = qualify_p0_release_candidate_r1_1(
        load_manifest(args.manifest),
        expected_manifest_sha256=args.expected_manifest_sha256,
        expected_live_snapshot_sha256=args.expected_live_snapshot_sha256,
        expected_live_snapshot_commit_sha=args.expected_live_snapshot_commit_sha,
        generated_at=args.generated_at,
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
