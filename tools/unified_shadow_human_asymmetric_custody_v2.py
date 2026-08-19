#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import ShadowIntegrationError, sha256_obj
from tools.unified_shadow_human_asymmetric_custody import (
    ASYMMETRIC_APPROVAL_SCHEMA,
    ASYMMETRIC_REVEAL_CLOSURE_SCHEMA,
    build_asymmetric_reveal_closure,
)

ASYMMETRIC_APPROVAL_SCHEMA_V2 = "control_center.shadow_asymmetric_human_approval_verification.v2"
ASYMMETRIC_REVEAL_CLOSURE_SCHEMA_V2 = "bitevo.shadow_asymmetric_reveal_closure.v2"


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise ShadowIntegrationError(f"human_asym_v2_{field}_must_be_sha256")
    return value.lower()


def _verify_v2_approval(
    approval: Mapping[str, Any],
    *,
    expected_approval_sha256: str,
    expected_assertion_sha256: str,
) -> tuple[str, dict[str, Any], str]:
    if not isinstance(approval, Mapping) or approval.get("schema") != ASYMMETRIC_APPROVAL_SCHEMA_V2:
        raise ShadowIntegrationError("human_asym_v2_wrong_approval_schema")
    supplied = _sha(approval.get("asymmetric_approval_verification_sha256"), "approval_sha256")
    computed = sha256_obj({k: v for k, v in approval.items() if k != "asymmetric_approval_verification_sha256"})
    if supplied != computed:
        raise ShadowIntegrationError("human_asym_v2_approval_hash_mismatch")
    if supplied != _sha(expected_approval_sha256, "expected_approval_sha256"):
        raise ShadowIntegrationError("human_asym_v2_approval_external_digest_mismatch")
    assertion_sha = _sha(approval.get("external_assertion_sha256"), "external_assertion_sha256")
    if assertion_sha != _sha(expected_assertion_sha256, "expected_assertion_sha256"):
        raise ShadowIntegrationError("human_asym_v2_assertion_external_digest_mismatch")
    if approval.get("external_assertion_digest_consumed") is not True:
        raise ShadowIntegrationError("human_asym_v2_assertion_digest_guard_missing")
    if approval.get("external_asymmetric_verifier_evidence") != "EXPECTED_DIGEST_BOUND":
        raise ShadowIntegrationError("human_asym_v2_external_verifier_evidence_invalid")
    if approval.get("trust_upgrade") != "SELF_HASH_TO_INDEPENDENT_ASSERTION_DIGEST":
        raise ShadowIntegrationError("human_asym_v2_trust_upgrade_invalid")

    prior_sha = _sha(
        approval.get("prior_asymmetric_approval_verification_sha256"),
        "prior_asymmetric_approval_verification_sha256",
    )
    legacy = {
        k: v
        for k, v in approval.items()
        if k
        not in {
            "schema",
            "asymmetric_approval_verification_sha256",
            "prior_asymmetric_approval_verification_sha256",
            "external_assertion_sha256",
            "external_assertion_digest_consumed",
            "external_asymmetric_verifier_evidence",
            "trust_upgrade",
        }
    }
    legacy["schema"] = ASYMMETRIC_APPROVAL_SCHEMA
    legacy["asymmetric_approval_verification_sha256"] = sha256_obj(legacy)
    if legacy["asymmetric_approval_verification_sha256"] != prior_sha:
        raise ShadowIntegrationError("human_asym_v2_prior_approval_reconstruction_mismatch")
    return supplied, legacy, assertion_sha


def build_asymmetric_reveal_closure_v2(
    trade_case: Mapping[str, Any],
    human_reveal_receipt: Mapping[str, Any],
    subject_manifest: Mapping[str, Any],
    domain_history_closure: Mapping[str, Any],
    asymmetric_approval_verification_v2: Mapping[str, Any],
    *,
    expected_asymmetric_approval_verification_sha256: str,
    expected_external_assertion_sha256: str,
    expected_credential_registry_sha256: str,
    expected_nonce_registry_sha256: str,
    expected_human_subject_id: str,
    expected_custody_provider_id: str,
    expected_verifier_id: str,
    expected_verifier_key_id: str,
    expected_credential_id_sha256: str,
    expected_public_key_sha256: str,
    expected_algorithm: str,
    expected_key_epoch: int,
    expected_origin: str,
    expected_rp_id: str,
    generated_at: str,
) -> dict[str, Any]:
    """R6.1 closure: bind externally retained assertion and approval digests before R6 semantics."""
    approval_sha, legacy_approval, assertion_sha = _verify_v2_approval(
        asymmetric_approval_verification_v2,
        expected_approval_sha256=expected_asymmetric_approval_verification_sha256,
        expected_assertion_sha256=expected_external_assertion_sha256,
    )

    v1 = build_asymmetric_reveal_closure(
        trade_case,
        human_reveal_receipt,
        subject_manifest,
        domain_history_closure,
        legacy_approval,
        expected_asymmetric_approval_verification_sha256=legacy_approval["asymmetric_approval_verification_sha256"],
        expected_credential_registry_sha256=expected_credential_registry_sha256,
        expected_nonce_registry_sha256=expected_nonce_registry_sha256,
        expected_human_subject_id=expected_human_subject_id,
        expected_custody_provider_id=expected_custody_provider_id,
        expected_verifier_id=expected_verifier_id,
        expected_verifier_key_id=expected_verifier_key_id,
        expected_credential_id_sha256=expected_credential_id_sha256,
        expected_public_key_sha256=expected_public_key_sha256,
        expected_algorithm=expected_algorithm,
        expected_key_epoch=expected_key_epoch,
        expected_origin=expected_origin,
        expected_rp_id=expected_rp_id,
        generated_at=generated_at,
    )
    if v1.get("schema") != ASYMMETRIC_REVEAL_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("human_asym_v2_prior_closure_schema_mismatch")
    prior_closure_sha = _sha(v1.get("asymmetric_reveal_closure_sha256"), "prior_closure_sha256")
    if prior_closure_sha != sha256_obj({k: v for k, v in v1.items() if k != "asymmetric_reveal_closure_sha256"}):
        raise ShadowIntegrationError("human_asym_v2_prior_closure_hash_mismatch")

    body = {k: v for k, v in v1.items() if k not in {"schema", "asymmetric_reveal_closure_sha256"}}
    body.update(
        {
            "schema": ASYMMETRIC_REVEAL_CLOSURE_SCHEMA_V2,
            "prior_asymmetric_reveal_closure_sha256": prior_closure_sha,
            "asymmetric_approval_verification_sha256": approval_sha,
            "external_assertion_sha256": assertion_sha,
            "external_assertion_digest_consumed": True,
            "trust_upgrade": "INDEPENDENT_ASSERTION_AND_APPROVAL_DIGESTS_BOUND",
        }
    )
    body["asymmetric_reveal_closure_sha256"] = sha256_obj(body)
    return body
