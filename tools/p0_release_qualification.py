#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

RELEASE_MANIFEST_SCHEMA = "bitevo.p0_release_candidate_manifest.v1"
RELEASE_QUALIFICATION_SCHEMA = "bitevo.p0_release_qualification_receipt.v1"

EXPECTED_SURFACE_IDS = (
    "control_center_authority",
    "control_center_p0",
    "hanri_p0",
    "tradingos_p0_input",
    "sct_p0",
    "continuityos_history_p0",
    "triaxis_p0",
    "visionassist_p0",
    "return_broker_p0",
)

EXPECTED_SCHEMA_MATRIX = {
    "R1": (
        "tradingos.shadow_trade_case.v1",
        "tradingos.trade_thesis.v1",
        "tradingos.trade_decision_packet.v1",
        "tradingos.trade_outcome_receipt.v1",
        "triaxis.trade_audit_request.v1",
        "triaxis.trade_adjudication.v1",
        "sct.prediction/v3",
    ),
    "R2": (
        "tradingos.temporal_evidence_bundle.v1",
        "bitevo.external_replay_anchor.v1",
        "tradingos.shadow_temporal_replay_qualification.v1",
        "tradingos.trusted_replay_input.v1",
        "sct.trusted_replay_shadow_preparation.v1",
    ),
    "R3": (
        "continuityos.shadow_replay_admission_candidate.v1",
        "continuityos.shadow_case_append_candidate.v1",
        "continuityos.shadow_case_ledger_snapshot.v1",
        "continuityos.shadow_case_event.v1",
        "control_return_broker.shadow_return_dedup_candidate.v1",
        "bitevo.shadow_history_replay_verification.v1",
    ),
    "R4": (
        "tradingos.shadow_human_reveal_receipt.v1",
        "tradingos.shadow_domain_subject_manifest.v1",
        "bitevo.shadow_domain_history_verification.v1",
        "bitevo.shadow_domain_history_closure.v1",
    ),
    "R5": (
        "control_center.shadow_human_approval_verification.v1",
        "bitevo.shadow_authenticated_reveal_closure.v1",
    ),
    "R6.1": (
        "control_center.shadow_asymmetric_human_approval_verification.v2",
        "bitevo.shadow_asymmetric_reveal_closure.v2",
    ),
    "R7": (
        "control_center.shadow_human_gate_atomic_consume_verification.v1",
        "bitevo.shadow_human_gate_consume_closure.v1",
    ),
    "R8": (
        "control_center.shadow_human_gate_crash_recovery_verification.v1",
        "bitevo.shadow_writer_fencing_recovery_closure.v1",
    ),
    "R8.1": (
        "control_center.shadow_human_gate_commit_receipt_index_snapshot.v2",
        "control_center.shadow_human_gate_writer_authority_anchor.v1",
        "control_center.shadow_human_gate_crash_recovery_verification.v2",
        "bitevo.shadow_writer_fencing_recovery_closure.v2",
    ),
    "R9": (
        "control_center.shadow_human_gate_lease_epoch_lineage.v1",
        "control_center.shadow_human_gate_dual_state_commit_candidate.v1",
        "control_center.shadow_human_gate_dual_state_readback_snapshot.v1",
        "control_center.shadow_human_gate_dual_state_atomicity_verification.v1",
        "bitevo.shadow_dual_state_atomicity_closure.v1",
    ),
}

REQUIRED_GLOBAL_INVARIANTS = {
    "hold_cannot_widen_to_pass": True,
    "wait_mandatory_on_hold": True,
    "all_effects_false": True,
    "external_runtime_invoked": False,
    "current_truth_apply": False,
    "human_gate_write": False,
    "lease_registry_write": False,
    "commit_receipt_registry_write": False,
    "backend_write": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
    "merge_allowed": False,
    "deploy_allowed": False,
    "runtime_activation_allowed": False,
    "execution_authority": "NONE",
    "can_trade": False,
    "capital_permission": "DENY",
}

REQUIRED_CONDITIONS = {
    "CONTROL_CENTER_PROVIDER_CAPTURE_STALE",
    "ARCHIVEOS_BLOCKED_REVERIFY_STALE",
    "CI_PREJOB_BLOCKED_ON_MULTIPLE_SURFACES",
    "NO_LIVE_WRITER_BACKEND_PROOF",
    "NO_DURABLE_COMMIT_PROOF",
    "NO_CRASH_SAFE_PERSISTENCE_PROOF",
    "NO_RUNTIME_DEPLOYMENT_PROOF",
    "NO_MERGE_AUTHORIZATION",
}

ALLOWED_CI_CLASSIFICATIONS = {
    "SUCCESS_EXACT_HEAD",
    "SUCCESS_HISTORICAL_EXACT_HEAD",
    "CI_BLOCKED_PRE_JOB",
}


class P0ReleaseQualificationError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_obj(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise P0ReleaseQualificationError(f"{field}_required")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 40 and len(text) != 64:
        raise P0ReleaseQualificationError(f"{field}_must_be_git_or_sha256")
    if any(ch not in "0123456789abcdef" for ch in text):
        raise P0ReleaseQualificationError(f"{field}_must_be_hex")
    return text


def _iso(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise P0ReleaseQualificationError(f"{field}_must_be_iso8601") from exc
    if parsed.tzinfo is None:
        raise P0ReleaseQualificationError(f"{field}_timezone_required")
    return text


def _verify_manifest_hash(manifest: Mapping[str, Any], expected_manifest_sha256: str) -> str:
    if not isinstance(manifest, Mapping) or manifest.get("schema") != RELEASE_MANIFEST_SCHEMA:
        raise P0ReleaseQualificationError("release_manifest_schema_mismatch")
    supplied = _sha(manifest.get("manifest_sha256"), "manifest_sha256")
    if len(supplied) != 64:
        raise P0ReleaseQualificationError("manifest_sha256_must_be_sha256")
    computed = sha256_obj({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    if supplied != computed:
        raise P0ReleaseQualificationError("release_manifest_hash_mismatch")
    expected = _sha(expected_manifest_sha256, "expected_manifest_sha256")
    if len(expected) != 64 or supplied != expected:
        raise P0ReleaseQualificationError("release_manifest_external_digest_mismatch")
    return supplied


def _verify_surfaces(manifest: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    surfaces = manifest.get("surfaces")
    if isinstance(surfaces, (str, bytes)) or not isinstance(surfaces, Sequence):
        raise P0ReleaseQualificationError("release_surfaces_must_be_sequence")
    if len(surfaces) != len(EXPECTED_SURFACE_IDS):
        raise P0ReleaseQualificationError("release_surface_count_mismatch")

    seen: list[str] = []
    blocked: list[str] = []
    for surface in surfaces:
        if not isinstance(surface, Mapping):
            raise P0ReleaseQualificationError("release_surface_must_be_object")
        surface_id = _text(surface.get("id"), "surface.id")
        seen.append(surface_id)
        _text(surface.get("repo"), f"{surface_id}.repo")
        if isinstance(surface.get("pr"), bool) or not isinstance(surface.get("pr"), int) or surface["pr"] < 1:
            raise P0ReleaseQualificationError(f"{surface_id}_pr_invalid")
        _sha(surface.get("base_sha"), f"{surface_id}.base_sha")
        _sha(surface.get("head_sha"), f"{surface_id}.head_sha")
        if surface.get("state") != "open" or surface.get("draft") is not True or surface.get("merged") is not False:
            raise P0ReleaseQualificationError(f"{surface_id}_must_remain_open_draft_unmerged")
        _text(surface.get("role"), f"{surface_id}.role")
        if surface.get("effect_authority") not in {"NONE", "NONE_IN_P0"}:
            raise P0ReleaseQualificationError(f"{surface_id}_effect_authority_breached")
        ci = _text(surface.get("ci_classification"), f"{surface_id}.ci_classification")
        if ci not in ALLOWED_CI_CLASSIFICATIONS:
            raise P0ReleaseQualificationError(f"{surface_id}_ci_classification_invalid")
        if ci == "CI_BLOCKED_PRE_JOB":
            blocked.append(surface_id)
        _text(surface.get("freshness"), f"{surface_id}.freshness")

    if tuple(seen) != EXPECTED_SURFACE_IDS:
        raise P0ReleaseQualificationError("release_surface_order_or_identity_mismatch")
    if "sct_p0" in blocked or "continuityos_history_p0" in blocked:
        raise P0ReleaseQualificationError("release_green_continuity_surface_regressed")
    if not blocked:
        raise P0ReleaseQualificationError("release_conditions_expected_but_no_ci_blockers_recorded")
    return tuple(seen), tuple(blocked)


def _verify_schema_matrix(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    matrix = manifest.get("schema_matrix")
    if not isinstance(matrix, Mapping) or tuple(matrix.keys()) != tuple(EXPECTED_SCHEMA_MATRIX.keys()):
        raise P0ReleaseQualificationError("release_schema_matrix_generation_mismatch")
    flattened: list[str] = []
    for generation, expected in EXPECTED_SCHEMA_MATRIX.items():
        value = matrix.get(generation)
        if not isinstance(value, (list, tuple)) or tuple(value) != expected:
            raise P0ReleaseQualificationError(f"release_schema_matrix_mismatch:{generation}")
        flattened.extend(value)
    if len(flattened) != len(set(flattened)):
        raise P0ReleaseQualificationError("release_schema_matrix_duplicate_contract")
    return tuple(flattened)


def _verify_global_invariants(manifest: Mapping[str, Any]) -> None:
    invariants = manifest.get("global_invariants")
    if not isinstance(invariants, Mapping) or set(invariants) != set(REQUIRED_GLOBAL_INVARIANTS):
        raise P0ReleaseQualificationError("release_global_invariant_keys_mismatch")
    for key, expected in REQUIRED_GLOBAL_INVARIANTS.items():
        if invariants.get(key) != expected or type(invariants.get(key)) is not type(expected):
            raise P0ReleaseQualificationError(f"release_global_invariant_breached:{key}")


def _verify_conditions(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    conditions = manifest.get("known_conditions")
    if not isinstance(conditions, (list, tuple)):
        raise P0ReleaseQualificationError("release_known_conditions_must_be_sequence")
    if set(conditions) != REQUIRED_CONDITIONS or len(conditions) != len(REQUIRED_CONDITIONS):
        raise P0ReleaseQualificationError("release_known_conditions_mismatch")
    return tuple(conditions)


def qualify_p0_release_candidate(
    manifest: Mapping[str, Any],
    *,
    expected_manifest_sha256: str,
    generated_at: str,
) -> dict[str, Any]:
    manifest_sha = _verify_manifest_hash(manifest, expected_manifest_sha256)
    snapshot_at = _iso(manifest.get("snapshot_at"), "snapshot_at")
    generated = _iso(generated_at, "generated_at")
    if manifest.get("qualification_scope") != "P0_SHADOW_R1_TO_R9_GLOBAL_INVARIANTS":
        raise P0ReleaseQualificationError("release_qualification_scope_invalid")

    parent = manifest.get("qualified_input_parent")
    if not isinstance(parent, Mapping):
        raise P0ReleaseQualificationError("release_qualified_input_parent_missing")
    if parent.get("repo") != "bitmaster162/tradingos" or parent.get("branch") != "agent/p0-shadow-integration-r1":
        raise P0ReleaseQualificationError("release_qualified_input_parent_identity_mismatch")
    qualified_parent_sha = _sha(parent.get("head_sha"), "qualified_input_parent.head_sha")
    if len(qualified_parent_sha) != 40:
        raise P0ReleaseQualificationError("release_qualified_input_parent_sha_invalid")

    surface_ids, blocked = _verify_surfaces(manifest)
    contracts = _verify_schema_matrix(manifest)
    _verify_global_invariants(manifest)
    conditions = _verify_conditions(manifest)

    semantics = manifest.get("release_semantics")
    expected_semantics = {
        "candidate_only": True,
        "production_qualified": False,
        "current_truth_promotion_allowed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "release_decision": "HOLD",
        "action": "WAIT",
    }
    if not isinstance(semantics, Mapping) or dict(semantics) != expected_semantics:
        raise P0ReleaseQualificationError("release_semantics_mismatch")

    body = {
        "schema": RELEASE_QUALIFICATION_SCHEMA,
        "manifest_sha256": manifest_sha,
        "snapshot_at": snapshot_at,
        "generated_at": generated,
        "qualified_input_parent_sha": qualified_parent_sha,
        "surface_ids": surface_ids,
        "surface_count": len(surface_ids),
        "schema_contract_count": len(contracts),
        "verified_generations": tuple(EXPECTED_SCHEMA_MATRIX.keys()),
        "ci_blocked_surfaces": blocked,
        "ci_blocked_surface_count": len(blocked),
        "known_conditions": conditions,
        "global_invariants_verified": True,
        "schema_compatibility_verified": True,
        "cross_repo_snapshot_bound": True,
        "p0_architecture_closed_for_candidate_review": True,
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
        "status": "P0_RELEASE_CANDIDATE_QUALIFIED_WITH_CONDITIONS",
    }
    body["release_qualification_sha256"] = sha256_obj(body)
    return body


def load_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise P0ReleaseQualificationError("release_manifest_root_must_be_object")
    return payload


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Offline P0 release-candidate qualification.")
    parser.add_argument("manifest")
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--generated-at", required=True)
    args = parser.parse_args()
    receipt = qualify_p0_release_candidate(
        load_manifest(args.manifest),
        expected_manifest_sha256=args.expected_manifest_sha256,
        generated_at=args.generated_at,
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
