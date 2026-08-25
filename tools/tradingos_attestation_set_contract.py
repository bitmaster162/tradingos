"""TradingOS R83 deterministic frozen-attestation evidence-set contract."""
from __future__ import annotations

import hashlib
from typing import Any

from tools import tradingos_human_review_contract as r82
from tools.tradingos_attestation_set_common import *
from tools.tradingos_attestation_set_common import _ID24_RE, _SHA64_RE

EVIDENCE_ITEM_KEYS = {"attestation", "report", "review_policy"}
BINDING_KEYS = {
    "attestation_id", "attestation_sha256", "shadow_report_id",
    "shadow_report_sha256", "review_policy_sha256",
}
INTEGRITY_KEYS = {
    "all_attestations_valid", "duplicate_attestation_ids_absent",
    "duplicate_attestation_payloads_absent", "review_policy_homogeneous", "bindings_exact",
}
MANIFEST_KEYS = {
    "schema", "evidence_set_id", "review_policy_sha256", "item_count", "bindings", "integrity",
    "shadow_only", "human_review_only", "review_identity_verified",
    "consensus_inference_allowed", "approval_state_allowed",
    "attestation_set_consumption_authority", "memory_write_authority", "policy_update_allowed",
    "live_decision_feedback_allowed", "live_decision_use_allowed", "model_selection_use_allowed",
    "execution_authority", "can_trade", "capital_permission", "confers_authority",
}


def _validate_evidence_item(item: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not isinstance(item, dict) or set(item) != EVIDENCE_ITEM_KEYS:
        raise ValueError("evidence item key set mismatch")
    attestation = item.get("attestation")
    report = item.get("report")
    review_policy = item.get("review_policy")
    r82.validate_human_review_attestation(attestation, report, review_policy)
    return attestation, report, review_policy


def _binding_for(attestation: dict[str, Any]) -> dict[str, Any]:
    return {
        "attestation_id": attestation["attestation_id"],
        "attestation_sha256": stable_sha256(attestation),
        "shadow_report_id": attestation["shadow_report_id"],
        "shadow_report_sha256": attestation["shadow_report_sha256"],
        "review_policy_sha256": attestation["review_policy_sha256"],
    }


def _manifest_identity_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: manifest[key] for key in MANIFEST_KEYS if key != "evidence_set_id"}


def _expected_evidence_set_id(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{EVIDENCE_SET_SCHEMA}:{VERSION}:".encode("utf-8")
        + stable_json_bytes(_manifest_identity_payload(manifest))
    ).hexdigest()[:24]


def _validate_bindings_shape(bindings: Any, item_count: int) -> None:
    if not isinstance(bindings, list) or len(bindings) != item_count or not bindings:
        raise ValueError("bindings length mismatch")
    ids = []
    payload_shas = []
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
            raise ValueError("binding key set mismatch")
        aid = binding.get("attestation_id")
        rid = binding.get("shadow_report_id")
        if not isinstance(aid, str) or _ID24_RE.fullmatch(aid) is None:
            raise ValueError("binding attestation_id invalid")
        if not isinstance(rid, str) or _ID24_RE.fullmatch(rid) is None:
            raise ValueError("binding shadow_report_id invalid")
        for field in ("attestation_sha256", "shadow_report_sha256", "review_policy_sha256"):
            value = binding.get(field)
            if not isinstance(value, str) or _SHA64_RE.fullmatch(value) is None:
                raise ValueError(f"binding {field} invalid")
        ids.append(aid)
        payload_shas.append(binding["attestation_sha256"])
    if ids != sorted(ids):
        raise ValueError("bindings must be canonical attestation_id order")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate attestation id")
    if len(payload_shas) != len(set(payload_shas)):
        raise ValueError("duplicate attestation payload")


def build_attestation_evidence_set(
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
) -> dict[str, Any]:
    validate_evidence_set_policy(set_policy)
    if not isinstance(evidence_items, list):
        raise ValueError("evidence_items must be list")
    if not set_policy["min_items"] <= len(evidence_items) <= set_policy["max_items"]:
        raise ValueError("evidence item count outside policy bounds")

    validated = [_validate_evidence_item(item) for item in evidence_items]
    bindings = [_binding_for(attestation) for attestation, _, _ in validated]
    bindings.sort(key=lambda b: b["attestation_id"])
    _validate_bindings_shape(bindings, len(evidence_items))

    review_policy_shas = {binding["review_policy_sha256"] for binding in bindings}
    if len(review_policy_shas) != 1:
        raise ValueError("mixed R82 review policies")
    review_policy_sha256 = next(iter(review_policy_shas))

    manifest = {
        "schema": EVIDENCE_SET_SCHEMA,
        "review_policy_sha256": review_policy_sha256,
        "item_count": len(bindings),
        "bindings": bindings,
        "integrity": {
            "all_attestations_valid": True,
            "duplicate_attestation_ids_absent": True,
            "duplicate_attestation_payloads_absent": True,
            "review_policy_homogeneous": True,
            "bindings_exact": True,
        },
        "shadow_only": True,
        "human_review_only": True,
        "review_identity_verified": False,
        "consensus_inference_allowed": False,
        "approval_state_allowed": False,
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
    manifest["evidence_set_id"] = _expected_evidence_set_id(manifest)
    validate_attestation_evidence_set(manifest, evidence_items, set_policy)
    return manifest


def validate_attestation_evidence_set(
    manifest: Any,
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
) -> None:
    validate_evidence_set_policy(set_policy)
    if not isinstance(evidence_items, list):
        raise ValueError("evidence_items must be list")
    if not set_policy["min_items"] <= len(evidence_items) <= set_policy["max_items"]:
        raise ValueError("evidence item count outside policy bounds")
    validated = [_validate_evidence_item(item) for item in evidence_items]

    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise ValueError("evidence-set manifest key set mismatch")
    if manifest.get("schema") != EVIDENCE_SET_SCHEMA:
        raise ValueError("unsupported evidence-set schema")
    eid = manifest.get("evidence_set_id")
    if not isinstance(eid, str) or _ID24_RE.fullmatch(eid) is None:
        raise ValueError("evidence_set_id invalid")
    count = manifest.get("item_count")
    if isinstance(count, bool) or not isinstance(count, int):
        raise ValueError("item_count invalid")
    if count != len(evidence_items):
        raise ValueError("item_count mismatch")

    expected_bindings = [_binding_for(attestation) for attestation, _, _ in validated]
    expected_bindings.sort(key=lambda b: b["attestation_id"])
    _validate_bindings_shape(expected_bindings, len(evidence_items))
    _validate_bindings_shape(manifest.get("bindings"), count)
    if manifest["bindings"] != expected_bindings:
        raise ValueError("evidence bindings mismatch")

    review_policy_shas = {b["review_policy_sha256"] for b in expected_bindings}
    if len(review_policy_shas) != 1:
        raise ValueError("mixed R82 review policies")
    expected_policy_sha = next(iter(review_policy_shas))
    if manifest.get("review_policy_sha256") != expected_policy_sha:
        raise ValueError("manifest review policy mismatch")

    integrity = manifest.get("integrity")
    if not isinstance(integrity, dict) or set(integrity) != INTEGRITY_KEYS:
        raise ValueError("integrity key set mismatch")
    if any(value is not True for value in integrity.values()):
        raise ValueError("integrity flags must remain all true")

    ceiling = {
        "shadow_only": True,
        "human_review_only": True,
        "review_identity_verified": False,
        "consensus_inference_allowed": False,
        "approval_state_allowed": False,
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
    for key, expected in ceiling.items():
        if manifest.get(key) != expected:
            raise ValueError(f"unsafe evidence-set manifest: {key}")
    if manifest["evidence_set_id"] != _expected_evidence_set_id(manifest):
        raise ValueError("evidence_set_id binding mismatch")
