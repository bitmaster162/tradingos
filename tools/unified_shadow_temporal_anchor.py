#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Mapping

from tools.tradingos_shadow_integration import (
    SHADOW_SAFETY,
    ShadowIntegrationError,
    sha256_obj,
    validate_trade_case,
)

TEMPORAL_EVIDENCE_BUNDLE_SCHEMA = "tradingos.temporal_evidence_bundle.v1"
REPLAY_ANCHOR_SCHEMA = "bitevo.external_replay_anchor.v1"
REPLAY_QUALIFICATION_SCHEMA = "tradingos.shadow_temporal_replay_qualification.v1"
TRUSTED_REPLAY_INPUT_SCHEMA = "tradingos.trusted_replay_input.v1"

_NO_EFFECTS = {
    "current_truth_apply": False,
    "continuity_write": False,
    "return_write": False,
    "archive_write": False,
    "runtime_activation": False,
    "model_call": False,
    "exchange_call": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowIntegrationError(f"temporal_anchor_{field}_required")
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"temporal_anchor_{field}_must_be_sha256")
    return text


def _iso_epoch(value: Any, field: str) -> tuple[str, float]:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ShadowIntegrationError(f"temporal_anchor_{field}_must_be_iso8601") from exc
    if parsed.tzinfo is None:
        raise ShadowIntegrationError(f"temporal_anchor_{field}_timezone_required")
    epoch = parsed.timestamp()
    if not math.isfinite(epoch):
        raise ShadowIntegrationError(f"temporal_anchor_{field}_not_finite")
    return text, epoch


def _verify_safety(value: Mapping[str, Any], field: str) -> None:
    safety = value.get("safety") if isinstance(value, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"temporal_anchor_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"temporal_anchor_unsafe_{field}:{key}")


def _verify_no_effects(value: Any, field: str) -> None:
    if not isinstance(value, Mapping):
        raise ShadowIntegrationError(f"temporal_anchor_{field}_effects_missing")
    if set(value) != set(_NO_EFFECTS):
        raise ShadowIntegrationError(f"temporal_anchor_{field}_effect_keys_mismatch")
    for key, expected in _NO_EFFECTS.items():
        if value.get(key) is not expected:
            raise ShadowIntegrationError(f"temporal_anchor_{field}_effect_boundary_breached:{key}")


def _expected_roles(case: Mapping[str, Any]) -> tuple[str, ...]:
    market = case["market_evidence"]
    roles = ["snapshot"]
    if market.get("vision") is not None:
        roles.append("vision")
    return tuple(roles)


def _normalize_temporal_row(
    *,
    role: str,
    source_ref: Mapping[str, Any],
    timing: Mapping[str, Any],
    frozen_at: str,
    frozen_epoch: float,
) -> dict[str, Any]:
    if not isinstance(timing, Mapping):
        raise ShadowIntegrationError(f"temporal_anchor_{role}_timing_must_be_object")
    for field in ("source_id", "sha256", "schema"):
        if timing.get(field) != source_ref.get(field):
            raise ShadowIntegrationError(f"temporal_anchor_{role}_{field}_binding_mismatch")

    observed_text, observed_epoch = _iso_epoch(timing.get("observed_at"), f"{role}.observed_at")
    ingested_text, ingested_epoch = _iso_epoch(timing.get("ingested_at"), f"{role}.ingested_at")
    fresh_text, fresh_epoch = _iso_epoch(timing.get("fresh_until"), f"{role}.fresh_until")

    if observed_epoch > ingested_epoch + 1e-6:
        raise ShadowIntegrationError(f"temporal_anchor_{role}_observed_after_ingest")
    if ingested_epoch > frozen_epoch + 1e-6:
        raise ShadowIntegrationError(f"temporal_anchor_{role}_ingested_after_case_freeze")
    if observed_epoch > frozen_epoch + 1e-6:
        raise ShadowIntegrationError(f"temporal_anchor_{role}_observed_after_case_freeze")
    if fresh_epoch + 1e-6 < frozen_epoch:
        raise ShadowIntegrationError(f"temporal_anchor_{role}_stale_at_case_freeze")
    if fresh_epoch + 1e-6 < ingested_epoch:
        raise ShadowIntegrationError(f"temporal_anchor_{role}_freshness_precedes_ingest")
    if timing.get("clock_verified") is not True:
        raise ShadowIntegrationError(f"temporal_anchor_{role}_clock_unverified")
    if timing.get("provenance_verified") is not True:
        raise ShadowIntegrationError(f"temporal_anchor_{role}_provenance_unverified")

    custody_ref = _text(timing.get("custody_ref"), f"{role}.custody_ref")
    return {
        "role": role,
        "source_id": str(source_ref["source_id"]),
        "sha256": str(source_ref["sha256"]),
        "schema": str(source_ref["schema"]),
        "observed_at": observed_text,
        "ingested_at": ingested_text,
        "fresh_until": fresh_text,
        "frozen_at": frozen_at,
        "clock_verified": True,
        "provenance_verified": True,
        "custody_ref": custody_ref,
        "temporal_admissibility": "PRE_FREEZE_AND_FRESH_AT_FREEZE",
    }


def build_temporal_evidence_bundle(
    trade_case: Mapping[str, Any],
    evidence_timing: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    case = validate_trade_case(trade_case)
    if not isinstance(evidence_timing, Mapping):
        raise ShadowIntegrationError("temporal_anchor_evidence_timing_must_be_object")
    roles = _expected_roles(case)
    if set(evidence_timing) != set(roles):
        raise ShadowIntegrationError("temporal_anchor_evidence_coverage_mismatch")

    frozen_text, frozen_epoch = _iso_epoch(case["frozen_at"], "case.frozen_at")
    rows = []
    for role in roles:
        rows.append(
            _normalize_temporal_row(
                role=role,
                source_ref=case["market_evidence"][role],
                timing=evidence_timing[role],
                frozen_at=frozen_text,
                frozen_epoch=frozen_epoch,
            )
        )
    body = {
        "schema": TEMPORAL_EVIDENCE_BUNDLE_SCHEMA,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "frozen_at": frozen_text,
        "evidence_roles": roles,
        "evidence": tuple(rows),
        "all_evidence_pre_freeze": True,
        "all_evidence_fresh_at_freeze": True,
        "source_authenticity_claimed": False,
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(_NO_EFFECTS),
    }
    body["evidence_bundle_sha256"] = sha256_obj(body)
    return body


def derive_case_binding_sha256(
    *,
    authority_id: str,
    authority_root_sha256: str,
    case_id: str,
    case_sha256: str,
    evidence_bundle_sha256: str,
) -> str:
    return sha256_obj(
        {
            "authority_id": _text(authority_id, "authority_id"),
            "authority_root_sha256": _sha256(authority_root_sha256, "authority_root_sha256"),
            "case_id": _text(case_id, "case_id"),
            "case_sha256": _sha256(case_sha256, "case_sha256"),
            "evidence_bundle_sha256": _sha256(evidence_bundle_sha256, "evidence_bundle_sha256"),
        }
    )


def build_replay_anchor_candidate(
    trade_case: Mapping[str, Any],
    evidence_bundle: Mapping[str, Any],
    *,
    authority_id: str,
    authority_generation: str,
    authority_root_sha256: str,
    root_effective_at: str,
) -> dict[str, Any]:
    case = validate_trade_case(trade_case)
    _verify_temporal_bundle(case, evidence_bundle)
    root_text = _sha256(authority_root_sha256, "authority_root_sha256")
    root_time_text, root_epoch = _iso_epoch(root_effective_at, "root_effective_at")
    _, freeze_epoch = _iso_epoch(case["frozen_at"], "case.frozen_at")
    if root_epoch > freeze_epoch + 1e-6:
        raise ShadowIntegrationError("temporal_anchor_root_effective_after_case_freeze")

    authority = _text(authority_id, "authority_id")
    generation = _text(authority_generation, "authority_generation")
    case_binding = derive_case_binding_sha256(
        authority_id=authority,
        authority_root_sha256=root_text,
        case_id=case["case_id"],
        case_sha256=case["case_sha256"],
        evidence_bundle_sha256=evidence_bundle["evidence_bundle_sha256"],
    )
    body = {
        "schema": REPLAY_ANCHOR_SCHEMA,
        "authority_id": authority,
        "authority_generation": generation,
        "authority_root_sha256": root_text,
        "root_effective_at": root_time_text,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "evidence_bundle_sha256": evidence_bundle["evidence_bundle_sha256"],
        "case_binding_sha256": case_binding,
        "trust_source": "EXTERNAL_EXPECTED_REFERENCE_REQUIRED",
        "self_issued_trust_forbidden": True,
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(_NO_EFFECTS),
    }
    body["anchor_sha256"] = sha256_obj(body)
    return body


def _verify_temporal_bundle(case: Mapping[str, Any], bundle: Mapping[str, Any]) -> str:
    if not isinstance(bundle, Mapping) or bundle.get("schema") != TEMPORAL_EVIDENCE_BUNDLE_SCHEMA:
        raise ShadowIntegrationError("temporal_anchor_wrong_evidence_bundle_schema")
    if bundle.get("case_id") != case["case_id"] or bundle.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("temporal_anchor_evidence_bundle_case_mismatch")
    if bundle.get("frozen_at") != case["frozen_at"]:
        raise ShadowIntegrationError("temporal_anchor_evidence_bundle_freeze_mismatch")
    _verify_safety(bundle, "evidence_bundle")
    _verify_no_effects(bundle.get("effects"), "evidence_bundle")
    if bundle.get("source_authenticity_claimed") is not False:
        raise ShadowIntegrationError("temporal_anchor_evidence_bundle_authenticity_overclaim")
    if bundle.get("all_evidence_pre_freeze") is not True or bundle.get("all_evidence_fresh_at_freeze") is not True:
        raise ShadowIntegrationError("temporal_anchor_evidence_bundle_temporal_status_invalid")
    roles = _expected_roles(case)
    if tuple(bundle.get("evidence_roles") or ()) != roles:
        raise ShadowIntegrationError("temporal_anchor_evidence_bundle_role_mismatch")
    rows = bundle.get("evidence")
    if not isinstance(rows, (list, tuple)) or tuple(row.get("role") for row in rows) != roles:
        raise ShadowIntegrationError("temporal_anchor_evidence_bundle_row_mismatch")

    timing = {row["role"]: row for row in rows}
    rebuilt = build_temporal_evidence_bundle(case, timing)
    if bundle.get("evidence_bundle_sha256") != rebuilt["evidence_bundle_sha256"]:
        raise ShadowIntegrationError("temporal_anchor_evidence_bundle_hash_mismatch")
    if dict(bundle) != rebuilt:
        raise ShadowIntegrationError("temporal_anchor_evidence_bundle_content_mismatch")
    return str(bundle["evidence_bundle_sha256"])


def _verify_anchor(
    case: Mapping[str, Any],
    bundle: Mapping[str, Any],
    anchor: Mapping[str, Any],
    *,
    expected_authority_id: str,
    expected_root_sha256: str,
    expected_case_binding_sha256: str,
) -> str:
    if not isinstance(anchor, Mapping) or anchor.get("schema") != REPLAY_ANCHOR_SCHEMA:
        raise ShadowIntegrationError("temporal_anchor_wrong_anchor_schema")
    _verify_safety(anchor, "anchor")
    _verify_no_effects(anchor.get("effects"), "anchor")
    if anchor.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("temporal_anchor_anchor_authority_breached")
    if anchor.get("self_issued_trust_forbidden") is not True:
        raise ShadowIntegrationError("temporal_anchor_self_issued_trust_flag_missing")
    if anchor.get("trust_source") != "EXTERNAL_EXPECTED_REFERENCE_REQUIRED":
        raise ShadowIntegrationError("temporal_anchor_trust_source_invalid")

    expected_authority = _text(expected_authority_id, "expected_authority_id")
    expected_root = _sha256(expected_root_sha256, "expected_root_sha256")
    expected_binding = _sha256(expected_case_binding_sha256, "expected_case_binding_sha256")
    if anchor.get("authority_id") != expected_authority:
        raise ShadowIntegrationError("temporal_anchor_authority_id_mismatch")
    if anchor.get("authority_root_sha256") != expected_root:
        raise ShadowIntegrationError("temporal_anchor_external_root_mismatch")
    if anchor.get("case_id") != case["case_id"] or anchor.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("temporal_anchor_anchor_case_mismatch")
    if anchor.get("evidence_bundle_sha256") != bundle["evidence_bundle_sha256"]:
        raise ShadowIntegrationError("temporal_anchor_anchor_evidence_mismatch")

    _, root_epoch = _iso_epoch(anchor.get("root_effective_at"), "root_effective_at")
    _, freeze_epoch = _iso_epoch(case["frozen_at"], "case.frozen_at")
    if root_epoch > freeze_epoch + 1e-6:
        raise ShadowIntegrationError("temporal_anchor_root_effective_after_case_freeze")

    derived_binding = derive_case_binding_sha256(
        authority_id=expected_authority,
        authority_root_sha256=expected_root,
        case_id=case["case_id"],
        case_sha256=case["case_sha256"],
        evidence_bundle_sha256=bundle["evidence_bundle_sha256"],
    )
    if anchor.get("case_binding_sha256") != derived_binding:
        raise ShadowIntegrationError("temporal_anchor_case_binding_internal_mismatch")
    if derived_binding != expected_binding:
        raise ShadowIntegrationError("temporal_anchor_external_case_binding_mismatch")

    expected_anchor_sha = sha256_obj({k: v for k, v in anchor.items() if k != "anchor_sha256"})
    if anchor.get("anchor_sha256") != expected_anchor_sha:
        raise ShadowIntegrationError("temporal_anchor_anchor_hash_mismatch")
    return str(anchor["anchor_sha256"])


def build_temporal_replay_qualification(
    trade_case: Mapping[str, Any],
    evidence_timing: Mapping[str, Mapping[str, Any]],
    replay_anchor: Mapping[str, Any],
    *,
    expected_authority_id: str,
    expected_root_sha256: str,
    expected_case_binding_sha256: str,
    generated_at: str,
) -> dict[str, Any]:
    case = validate_trade_case(trade_case)
    generated_text, _ = _iso_epoch(generated_at, "generated_at")
    bundle = build_temporal_evidence_bundle(case, evidence_timing)
    anchor_sha = _verify_anchor(
        case,
        bundle,
        replay_anchor,
        expected_authority_id=expected_authority_id,
        expected_root_sha256=expected_root_sha256,
        expected_case_binding_sha256=expected_case_binding_sha256,
    )
    body = {
        "schema": REPLAY_QUALIFICATION_SCHEMA,
        "generated_at": generated_text,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "frozen_at": case["frozen_at"],
        "evidence_bundle": bundle,
        "evidence_bundle_sha256": bundle["evidence_bundle_sha256"],
        "replay_anchor": dict(replay_anchor),
        "anchor_sha256": anchor_sha,
        "temporal_status": "QUALIFIED_PRE_FREEZE_AND_FRESH_AT_FREEZE",
        "trust_status": "MATCHED_EXPECTED_EXTERNAL_ROOT_AND_CASE_BINDING",
        "qualification_status": "QUALIFIED_FOR_OFFLINE_REPLAY_ONLY",
        "source_authenticity_created_here": False,
        "external_expected_reference_required": True,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(_NO_EFFECTS),
    }
    body["qualification_sha256"] = sha256_obj(body)
    return body


def verify_temporal_replay_qualification(
    trade_case: Mapping[str, Any],
    qualification: Mapping[str, Any],
    *,
    expected_authority_id: str,
    expected_root_sha256: str,
    expected_case_binding_sha256: str,
) -> str:
    case = validate_trade_case(trade_case)
    if not isinstance(qualification, Mapping) or qualification.get("schema") != REPLAY_QUALIFICATION_SCHEMA:
        raise ShadowIntegrationError("temporal_anchor_wrong_qualification_schema")
    if qualification.get("case_id") != case["case_id"] or qualification.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("temporal_anchor_qualification_case_mismatch")
    if qualification.get("frozen_at") != case["frozen_at"]:
        raise ShadowIntegrationError("temporal_anchor_qualification_freeze_mismatch")
    _verify_safety(qualification, "qualification")
    _verify_no_effects(qualification.get("effects"), "qualification")
    if qualification.get("apply_allowed") is not False or qualification.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("temporal_anchor_qualification_authority_breached")
    if qualification.get("source_authenticity_created_here") is not False:
        raise ShadowIntegrationError("temporal_anchor_qualification_authenticity_overclaim")
    if qualification.get("external_expected_reference_required") is not True:
        raise ShadowIntegrationError("temporal_anchor_external_reference_requirement_missing")
    if qualification.get("temporal_status") != "QUALIFIED_PRE_FREEZE_AND_FRESH_AT_FREEZE":
        raise ShadowIntegrationError("temporal_anchor_qualification_temporal_status_invalid")
    if qualification.get("trust_status") != "MATCHED_EXPECTED_EXTERNAL_ROOT_AND_CASE_BINDING":
        raise ShadowIntegrationError("temporal_anchor_qualification_trust_status_invalid")
    if qualification.get("qualification_status") != "QUALIFIED_FOR_OFFLINE_REPLAY_ONLY":
        raise ShadowIntegrationError("temporal_anchor_qualification_status_invalid")

    bundle = qualification.get("evidence_bundle")
    bundle_sha = _verify_temporal_bundle(case, bundle)
    if qualification.get("evidence_bundle_sha256") != bundle_sha:
        raise ShadowIntegrationError("temporal_anchor_qualification_evidence_hash_mismatch")
    anchor = qualification.get("replay_anchor")
    anchor_sha = _verify_anchor(
        case,
        bundle,
        anchor,
        expected_authority_id=expected_authority_id,
        expected_root_sha256=expected_root_sha256,
        expected_case_binding_sha256=expected_case_binding_sha256,
    )
    if qualification.get("anchor_sha256") != anchor_sha:
        raise ShadowIntegrationError("temporal_anchor_qualification_anchor_hash_mismatch")

    expected = sha256_obj({k: v for k, v in qualification.items() if k != "qualification_sha256"})
    if qualification.get("qualification_sha256") != expected:
        raise ShadowIntegrationError("temporal_anchor_qualification_hash_mismatch")
    return str(qualification["qualification_sha256"])


def build_trusted_replay_input(
    trade_case: Mapping[str, Any],
    qualification: Mapping[str, Any],
    *,
    expected_authority_id: str,
    expected_root_sha256: str,
    expected_case_binding_sha256: str,
) -> dict[str, Any]:
    case = validate_trade_case(trade_case)
    qualification_sha = verify_temporal_replay_qualification(
        case,
        qualification,
        expected_authority_id=expected_authority_id,
        expected_root_sha256=expected_root_sha256,
        expected_case_binding_sha256=expected_case_binding_sha256,
    )
    body = {
        "schema": TRUSTED_REPLAY_INPUT_SCHEMA,
        "trade_case": case,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "qualification": dict(qualification),
        "qualification_sha256": qualification_sha,
        "replay_mode": "OFFLINE_TRUSTED_REPLAY_ONLY",
        "external_expected_reference_consumed": True,
        "source_authenticity_created_here": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(_NO_EFFECTS),
    }
    body["replay_input_sha256"] = sha256_obj(body)
    return body
