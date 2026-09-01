"""TradingOS R86 deterministic external-assertion replay-guard candidate contract."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from tools import tradingos_external_verifier_provenance_contract as r85
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes, stable_sha256

BINDING_SCHEMA = "tradingos.external_assertion_replay_guard_binding.v1"
REPLAY_REGISTRY_SCHEMA = "control_center.external_assertion_replay_registry_snapshot.v1"
NEXT_CANDIDATE_SCHEMA = "control_center.external_assertion_replay_registry_candidate.v1"
POLICY_ID = "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_GUARD_POLICY_V1"
VERSION = "1.0.0"

_ID24_RE = re.compile(r"^[0-9a-f]{24}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")

OUTPUT_PERMISSIONS = {
    "execution_authority": "NONE",
    "signals_allowed": False,
    "orders_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
    "confers_authority": False,
}

POLICY_KEYS = {
    "schema_version", "policy_id", "mode", "input_r85_binding_schema",
    "replay_registry_schema", "require_full_r85_validation",
    "require_expected_replay_registry_digest", "require_assertion_absent",
    "require_challenge_absent", "require_sorted_unique_digest_sets",
    "require_exact_generation_increment", "network_access_in_core_allowed",
    "credential_access_in_core_allowed", "registry_write_allowed",
    "durable_single_use_inference_allowed", "freshness_inference_allowed",
    "liveness_inference_allowed", "verifier_trust_inference_allowed",
    "reviewer_identity_inference_allowed", "physical_human_presence_inference_allowed",
    "distinct_reviewer_count_allowed", "consensus_inference_allowed",
    "approval_state_allowed", "recommendations_allowed", "policy_update_allowed",
    "live_decision_feedback_allowed", "live_decision_use_allowed",
    "model_selection_use_allowed", "persistence_in_core_allowed",
    "human_review_only", "shadow_only", "attestation_set_consumption_authority",
    "memory_write_authority", "output_permissions",
}

REGISTRY_KEYS = {
    "schema", "registry_id", "generation", "previous_registry_sha256",
    "used_external_assertion_sha256s", "used_challenge_sha256s",
    "registry_scope", "durable_commit_proven", "write_allowed",
    "apply_allowed", "confers_authority",
}

NEXT_CANDIDATE_KEYS = {
    "schema", "registry_id", "prior_registry_sha256", "prior_generation",
    "next_generation", "append_external_assertion_sha256",
    "append_challenge_sha256", "used_external_assertion_sha256s",
    "used_challenge_sha256s", "candidate_status", "durable_commit_proven",
    "write_performed", "apply_allowed", "confers_authority",
}

BINDING_KEYS = {
    "schema", "binding_id", "r85_binding_id", "r85_binding_sha256",
    "r84_binding_id", "external_assertion_sha256", "challenge_sha256",
    "replay_policy_sha256", "replay_registry_sha256",
    "replay_registry_digest_consumed", "registry_id", "prior_generation",
    "assertion_absent_in_expected_registry", "challenge_absent_in_expected_registry",
    "next_registry_candidate_sha256", "next_generation",
    "replay_guard_candidate_bound", "durable_single_use_enforced",
    "registry_write_performed", "assertion_freshness_verified",
    "liveness_verified", "verifier_trust_root_verified",
    "review_identity_verified", "physical_human_presence_proven",
    "distinct_reviewer_count_allowed", "consensus_inference_allowed",
    "approval_state_allowed", "shadow_only", "human_review_only",
    "attestation_set_consumption_authority", "memory_write_authority",
    "policy_update_allowed", "live_decision_feedback_allowed",
    "live_decision_use_allowed", "model_selection_use_allowed",
    "execution_authority", "can_trade", "capital_permission",
    "confers_authority",
}


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA64_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be lowercase sha256")
    return value


def _id24(value: Any, field: str) -> str:
    if not isinstance(value, str) or _ID24_RE.fullmatch(value) is None:
        raise ValueError(f"{field} invalid")
    return value


def _token(value: Any, field: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= 128:
        raise ValueError(f"{field} invalid")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
        raise ValueError(f"{field} invalid")
    return value


def _generation(value: Any, field: str, *, allow_max: bool = False) -> int:
    maximum = 2147483647 if allow_max else 2147483646
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{field} invalid")
    return value


def validate_replay_guard_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("replay policy key set mismatch")
    if policy.get("schema_version") != 1 or policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported replay policy")
    if policy.get("mode") != "OFFLINE_EXTERNAL_ASSERTION_REPLAY_GUARD_CANDIDATE_ONLY":
        raise ValueError("replay policy mode drift")
    if policy.get("input_r85_binding_schema") != r85.BINDING_SCHEMA:
        raise ValueError("input R85 binding schema drift")
    if policy.get("replay_registry_schema") != REPLAY_REGISTRY_SCHEMA:
        raise ValueError("replay registry schema drift")
    for field in (
        "require_full_r85_validation", "require_expected_replay_registry_digest",
        "require_assertion_absent", "require_challenge_absent",
        "require_sorted_unique_digest_sets", "require_exact_generation_increment",
        "human_review_only", "shadow_only",
    ):
        if policy.get(field) is not True:
            raise ValueError(f"required replay guard disabled: {field}")
    for field in (
        "network_access_in_core_allowed", "credential_access_in_core_allowed",
        "registry_write_allowed", "durable_single_use_inference_allowed",
        "freshness_inference_allowed", "liveness_inference_allowed",
        "verifier_trust_inference_allowed", "reviewer_identity_inference_allowed",
        "physical_human_presence_inference_allowed", "distinct_reviewer_count_allowed",
        "consensus_inference_allowed", "approval_state_allowed", "recommendations_allowed",
        "policy_update_allowed", "live_decision_feedback_allowed",
        "live_decision_use_allowed", "model_selection_use_allowed",
        "persistence_in_core_allowed",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"unsafe replay policy: {field}")
    if policy.get("attestation_set_consumption_authority") != "NONE":
        raise ValueError("attestation-set consumption authority must remain NONE")
    if policy.get("memory_write_authority") != "NONE":
        raise ValueError("memory write authority must remain NONE")
    if policy.get("output_permissions") != OUTPUT_PERMISSIONS:
        raise ValueError("unsafe replay output permissions")


def _validated_digest_set(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 4096:
        raise ValueError(f"{field} invalid")
    normalized = [_sha(item, field) for item in value]
    if normalized != sorted(normalized) or len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} must be sorted and unique")
    return normalized


def _validate_replay_registry(
    replay_registry_snapshot: Any,
    *,
    expected_replay_registry_sha256: str,
    external_assertion_sha256: str,
    challenge_sha256: str,
) -> tuple[str, int, list[str], list[str]]:
    if not isinstance(replay_registry_snapshot, dict) or set(replay_registry_snapshot) != REGISTRY_KEYS:
        raise ValueError("replay registry key set mismatch")
    if replay_registry_snapshot.get("schema") != REPLAY_REGISTRY_SCHEMA:
        raise ValueError("unsupported replay registry schema")
    _token(replay_registry_snapshot.get("registry_id"), "registry_id")
    generation = _generation(replay_registry_snapshot.get("generation"), "generation")
    _sha(replay_registry_snapshot.get("previous_registry_sha256"), "previous_registry_sha256")
    if replay_registry_snapshot.get("registry_scope") != "EXTERNAL_ASSERTION_AND_CHALLENGE_REPLAY_GUARD_ONLY":
        raise ValueError("replay registry scope invalid")
    if replay_registry_snapshot.get("durable_commit_proven") is not False:
        raise ValueError("replay registry durability overclaim")
    if replay_registry_snapshot.get("write_allowed") is not False:
        raise ValueError("replay registry write overclaim")
    if replay_registry_snapshot.get("apply_allowed") is not False:
        raise ValueError("replay registry apply overclaim")
    if replay_registry_snapshot.get("confers_authority") is not False:
        raise ValueError("replay registry authority overclaim")

    expected_digest = _sha(expected_replay_registry_sha256, "expected_replay_registry_sha256")
    computed_digest = stable_sha256(replay_registry_snapshot)
    if computed_digest != expected_digest:
        raise ValueError("replay registry digest mismatch")

    used_assertions = _validated_digest_set(
        replay_registry_snapshot.get("used_external_assertion_sha256s"),
        "used_external_assertion_sha256s",
    )
    used_challenges = _validated_digest_set(
        replay_registry_snapshot.get("used_challenge_sha256s"),
        "used_challenge_sha256s",
    )
    assertion_sha = _sha(external_assertion_sha256, "external_assertion_sha256")
    challenge_sha = _sha(challenge_sha256, "challenge_sha256")
    if assertion_sha in used_assertions:
        raise ValueError("external assertion replay detected")
    if challenge_sha in used_challenges:
        raise ValueError("challenge replay detected")
    return computed_digest, generation, used_assertions, used_challenges


def _build_next_registry_candidate(
    replay_registry_snapshot: dict[str, Any],
    replay_registry_sha256: str,
    generation: int,
    used_assertions: list[str],
    used_challenges: list[str],
    external_assertion_sha256: str,
    challenge_sha256: str,
) -> dict[str, Any]:
    assertion_sha = _sha(external_assertion_sha256, "external_assertion_sha256")
    challenge_sha = _sha(challenge_sha256, "challenge_sha256")
    candidate = {
        "schema": NEXT_CANDIDATE_SCHEMA,
        "registry_id": replay_registry_snapshot["registry_id"],
        "prior_registry_sha256": replay_registry_sha256,
        "prior_generation": generation,
        "next_generation": generation + 1,
        "append_external_assertion_sha256": assertion_sha,
        "append_challenge_sha256": challenge_sha,
        "used_external_assertion_sha256s": sorted([*used_assertions, assertion_sha]),
        "used_challenge_sha256s": sorted([*used_challenges, challenge_sha]),
        "candidate_status": "REPLAY_GUARD_CANDIDATE_ONLY_NOT_DURABLY_ENFORCED",
        "durable_commit_proven": False,
        "write_performed": False,
        "apply_allowed": False,
        "confers_authority": False,
    }
    if set(candidate) != NEXT_CANDIDATE_KEYS:
        raise ValueError("next replay candidate key set mismatch")
    return candidate


def _validate_inputs(
    r85_binding: dict[str, Any],
    r84_binding: dict[str, Any],
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any],
    replay_registry_snapshot: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any],
    expected_replay_registry_sha256: str,
    replay_guard_policy: dict[str, Any],
) -> tuple[str, int, dict[str, Any]]:
    validate_replay_guard_policy(replay_guard_policy)
    r85.validate_external_verifier_provenance_binding(
        r85_binding,
        r84_binding,
        evidence_set,
        evidence_items,
        set_policy,
        attestation_id,
        external_assertion,
        verifier_registry_snapshot,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
    )
    registry_sha, generation, used_assertions, used_challenges = _validate_replay_registry(
        replay_registry_snapshot,
        expected_replay_registry_sha256=expected_replay_registry_sha256,
        external_assertion_sha256=r84_binding["external_assertion_sha256"],
        challenge_sha256=r84_binding["challenge_sha256"],
    )
    candidate = _build_next_registry_candidate(
        replay_registry_snapshot,
        registry_sha,
        generation,
        used_assertions,
        used_challenges,
        r84_binding["external_assertion_sha256"],
        r84_binding["challenge_sha256"],
    )
    return registry_sha, generation, candidate


def _binding_payload(
    r85_binding: dict[str, Any],
    r84_binding: dict[str, Any],
    replay_guard_policy: dict[str, Any],
    replay_registry_snapshot: dict[str, Any],
    replay_registry_sha256: str,
    generation: int,
    next_candidate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": BINDING_SCHEMA,
        "r85_binding_id": r85_binding["binding_id"],
        "r85_binding_sha256": stable_sha256(r85_binding),
        "r84_binding_id": r84_binding["binding_id"],
        "external_assertion_sha256": r84_binding["external_assertion_sha256"],
        "challenge_sha256": r84_binding["challenge_sha256"],
        "replay_policy_sha256": stable_sha256(replay_guard_policy),
        "replay_registry_sha256": replay_registry_sha256,
        "replay_registry_digest_consumed": True,
        "registry_id": replay_registry_snapshot["registry_id"],
        "prior_generation": generation,
        "assertion_absent_in_expected_registry": True,
        "challenge_absent_in_expected_registry": True,
        "next_registry_candidate_sha256": stable_sha256(next_candidate),
        "next_generation": next_candidate["next_generation"],
        "replay_guard_candidate_bound": True,
        "durable_single_use_enforced": False,
        "registry_write_performed": False,
        "assertion_freshness_verified": False,
        "liveness_verified": False,
        "verifier_trust_root_verified": False,
        "review_identity_verified": False,
        "physical_human_presence_proven": False,
        "distinct_reviewer_count_allowed": False,
        "consensus_inference_allowed": False,
        "approval_state_allowed": False,
        "shadow_only": True,
        "human_review_only": True,
        "attestation_set_consumption_authority": "NONE",
        "memory_write_authority": "NONE",
        "policy_update_allowed": False,
        "live_decision_feedback_allowed": False,
        "live_decision_use_allowed": False,
        "model_selection_use_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }


def _expected_binding_id(binding: dict[str, Any]) -> str:
    payload = {key: binding[key] for key in BINDING_KEYS if key != "binding_id"}
    return hashlib.sha256(
        f"{BINDING_SCHEMA}:{VERSION}:".encode("utf-8") + stable_json_bytes(payload)
    ).hexdigest()[:24]


def build_external_assertion_replay_guard_binding(
    r85_binding: dict[str, Any],
    r84_binding: dict[str, Any],
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any],
    replay_registry_snapshot: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any],
    expected_replay_registry_sha256: str,
    replay_guard_policy: dict[str, Any],
) -> dict[str, Any]:
    replay_registry_sha, generation, candidate = _validate_inputs(
        r85_binding,
        r84_binding,
        evidence_set,
        evidence_items,
        set_policy,
        attestation_id,
        external_assertion,
        verifier_registry_snapshot,
        replay_registry_snapshot,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
        expected_replay_registry_sha256=expected_replay_registry_sha256,
        replay_guard_policy=replay_guard_policy,
    )
    binding = _binding_payload(
        r85_binding,
        r84_binding,
        replay_guard_policy,
        replay_registry_snapshot,
        replay_registry_sha,
        generation,
        candidate,
    )
    binding["binding_id"] = _expected_binding_id(binding)
    validate_external_assertion_replay_guard_binding(
        binding,
        r85_binding,
        r84_binding,
        evidence_set,
        evidence_items,
        set_policy,
        attestation_id,
        external_assertion,
        verifier_registry_snapshot,
        replay_registry_snapshot,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
        expected_replay_registry_sha256=expected_replay_registry_sha256,
        replay_guard_policy=replay_guard_policy,
    )
    return binding


def validate_external_assertion_replay_guard_binding(
    binding: Any,
    r85_binding: dict[str, Any],
    r84_binding: dict[str, Any],
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any],
    replay_registry_snapshot: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any],
    expected_replay_registry_sha256: str,
    replay_guard_policy: dict[str, Any],
) -> None:
    replay_registry_sha, generation, candidate = _validate_inputs(
        r85_binding,
        r84_binding,
        evidence_set,
        evidence_items,
        set_policy,
        attestation_id,
        external_assertion,
        verifier_registry_snapshot,
        replay_registry_snapshot,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
        expected_replay_registry_sha256=expected_replay_registry_sha256,
        replay_guard_policy=replay_guard_policy,
    )
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise ValueError("replay binding key set mismatch")
    if binding.get("schema") != BINDING_SCHEMA:
        raise ValueError("unsupported replay binding schema")
    _id24(binding.get("binding_id"), "binding_id")
    expected = _binding_payload(
        r85_binding,
        r84_binding,
        replay_guard_policy,
        replay_registry_snapshot,
        replay_registry_sha,
        generation,
        candidate,
    )
    for key, value in expected.items():
        if binding.get(key) != value or type(binding.get(key)) is not type(value):
            raise ValueError(f"replay binding mismatch: {key}")
    if binding["binding_id"] != _expected_binding_id(binding):
        raise ValueError("binding_id binding mismatch")
