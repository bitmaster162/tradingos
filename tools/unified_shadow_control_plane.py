#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from tools.tradingos_shadow_integration import (
    DECISION_PACKET_SCHEMA,
    SHADOW_SAFETY,
    ShadowIntegrationError,
    sha256_obj,
    validate_trade_case,
)

CONTROL_PLANE_SCHEMA = "bitevo.shadow_control_plane_receipt.v2"

_EXPECTED_CONTROL_REPO = "bitmaster162/control-center"
_EXPECTED_CONTINUITY_REPO = "bitmaster162/continuityos"
_EXPECTED_CONTINUITY_BRANCH = "master"
_EXPECTED_CONTINUITY_HEAD = "9dfb9e5b847a27113ca7c709a0adee900e3ff63f"
_EXPECTED_SCT_ADAPTER_HEAD = "a0a244d40f0a2aa500df45b1f846f0d863a77749"

_ZERO_EFFECT_COUNTERS = {
    "human_now": 0,
    "effect_candidates": 0,
    "effects_authorized": 0,
    "executions_authorized": 0,
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowIntegrationError(f"control_plane_{field}_required")
    return value.strip()


def _hex(value: Any, length: int, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != length or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"control_plane_{field}_must_be_hex{length}")
    return text


def _dt(value: Any, field: str) -> datetime:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ShadowIntegrationError(f"control_plane_{field}_invalid_datetime") from exc
    if parsed.tzinfo is None:
        raise ShadowIntegrationError(f"control_plane_{field}_timezone_required")
    return parsed


def _verify_safety(record: Mapping[str, Any], field: str) -> None:
    safety = record.get("safety") if isinstance(record, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"control_plane_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"control_plane_unsafe_{field}:{key}")


def _verify_packet(case: Mapping[str, Any], packet: Mapping[str, Any]) -> str:
    if not isinstance(packet, Mapping) or packet.get("schema") != DECISION_PACKET_SCHEMA:
        raise ShadowIntegrationError("control_plane_wrong_decision_packet_schema")
    if packet.get("case_id") != case["case_id"] or packet.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("control_plane_decision_packet_case_mismatch")
    _verify_safety(packet, "packet")
    expected = sha256_obj({k: v for k, v in packet.items() if k != "packet_sha256"})
    if packet.get("packet_sha256") != expected:
        raise ShadowIntegrationError("control_plane_packet_hash_mismatch")
    return str(packet["packet_sha256"])


def _draft_pr_ref(value: Mapping[str, Any], *, expected_repo: str, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowIntegrationError(f"control_plane_{field}_must_be_object")
    repo = _text(value.get("repo"), f"{field}.repo")
    if repo != expected_repo:
        raise ShadowIntegrationError(f"control_plane_{field}_repo_mismatch")
    pr_number = value.get("pr_number")
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0:
        raise ShadowIntegrationError(f"control_plane_{field}_pr_number_invalid")
    result = {
        "repo": repo,
        "pr_number": pr_number,
        "head_sha": _hex(value.get("head_sha"), 40, f"{field}.head_sha"),
        "state": _text(value.get("state"), f"{field}.state"),
        "draft": value.get("draft") is True,
        "merged": value.get("merged") is True,
    }
    if result["merged"] or not result["draft"]:
        raise ShadowIntegrationError(f"control_plane_{field}_must_remain_open_draft_unmerged")
    return result


def _continuity_source_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowIntegrationError("control_plane_continuityos_source_ref_must_be_object")
    repo = _text(value.get("repo"), "continuityos_source_ref.repo")
    branch = _text(value.get("branch"), "continuityos_source_ref.branch")
    head = _hex(value.get("head_sha"), 40, "continuityos_source_ref.head_sha")
    if repo != _EXPECTED_CONTINUITY_REPO:
        raise ShadowIntegrationError("control_plane_continuityos_source_ref_repo_mismatch")
    if branch != _EXPECTED_CONTINUITY_BRANCH:
        raise ShadowIntegrationError("control_plane_continuityos_source_ref_branch_mismatch")
    if head != _EXPECTED_CONTINUITY_HEAD:
        raise ShadowIntegrationError("control_plane_continuityos_source_ref_head_mismatch")
    return {
        "repo": repo,
        "branch": branch,
        "head_sha": head,
        "claim_dimension": "SOURCE_IDENTITY",
        "claim_ceiling": "MODERN_GITHUB_SOURCE_ONLY",
        "proves_live_runtime": False,
        "proves_current_host_state": False,
    }


def _sct_adapter_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _draft_pr_ref(value, expected_repo=_EXPECTED_CONTINUITY_REPO, field="sct_adapter_ref")
    if result["head_sha"] != _EXPECTED_SCT_ADAPTER_HEAD:
        raise ShadowIntegrationError("control_plane_sct_adapter_ref_head_mismatch")
    result["role"] = "SCT_R13_TRADER_TWIN_ADAPTER_ONLY"
    result["is_continuityos_source_authority"] = False
    return result


def _effect_counters(value: Mapping[str, Any] | None) -> dict[str, int]:
    counters = dict(_ZERO_EFFECT_COUNTERS if value is None else value)
    if set(counters) != set(_ZERO_EFFECT_COUNTERS):
        raise ShadowIntegrationError("control_plane_effect_counter_keys_mismatch")
    for key, expected in _ZERO_EFFECT_COUNTERS.items():
        current = counters.get(key)
        if isinstance(current, bool) or not isinstance(current, int):
            raise ShadowIntegrationError(f"control_plane_effect_counter_not_int:{key}")
        if current != expected:
            raise ShadowIntegrationError(f"control_plane_effect_ceiling_breached:{key}")
    return counters


def build_shadow_control_plane_receipt(
    trade_case: Mapping[str, Any],
    decision_packet: Mapping[str, Any],
    *,
    control_center_ref: Mapping[str, Any],
    continuityos_source_ref: Mapping[str, Any],
    sct_adapter_ref: Mapping[str, Any],
    provider_capture_at: str,
    lease_expires_at: str,
    evaluated_at: str,
    generated_at: str,
    anti_amnesia_context_sha256: str,
    conflicts: Sequence[str] = (),
    effect_counters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic P0 control receipt without mutating control, continuity or runtime state."""
    case = validate_trade_case(trade_case)
    packet_sha = _verify_packet(case, decision_packet)

    control_ref = _draft_pr_ref(
        control_center_ref,
        expected_repo=_EXPECTED_CONTROL_REPO,
        field="control_center_ref",
    )
    continuity_ref = _continuity_source_ref(continuityos_source_ref)
    sct_ref = _sct_adapter_ref(sct_adapter_ref)

    capture_dt = _dt(provider_capture_at, "provider_capture_at")
    lease_dt = _dt(lease_expires_at, "lease_expires_at")
    evaluated_dt = _dt(evaluated_at, "evaluated_at")
    _dt(generated_at, "generated_at")
    if lease_dt < capture_dt:
        raise ShadowIntegrationError("control_plane_lease_precedes_capture")
    if evaluated_dt < capture_dt:
        raise ShadowIntegrationError("control_plane_evaluation_precedes_capture")

    clean_conflicts = tuple(dict.fromkeys(_text(item, "conflict") for item in conflicts))
    stale = evaluated_dt > lease_dt
    attention_required = stale or bool(clean_conflicts)
    control_gate = "HOLD" if attention_required else "PASS_SHADOW"
    control_action = "WAIT" if control_gate == "HOLD" else str(decision_packet["system_recommendation"])
    counters = _effect_counters(effect_counters)
    context_sha = _hex(anti_amnesia_context_sha256, 64, "anti_amnesia_context_sha256")

    body = {
        "schema": CONTROL_PLANE_SCHEMA,
        "generated_at": _text(generated_at, "generated_at"),
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "decision_packet_sha256": packet_sha,
        "human_sovereign": True,
        "source_refs": {
            "control_center": control_ref,
            "continuityos_modern_source": continuity_ref,
            "sct_trader_twin_adapter": sct_ref,
        },
        "hanri": {
            "provider_capture_at": _text(provider_capture_at, "provider_capture_at"),
            "lease_expires_at": _text(lease_expires_at, "lease_expires_at"),
            "evaluated_at": _text(evaluated_at, "evaluated_at"),
            "freshness": "STALE" if stale else "FRESH",
            "conflicts": clean_conflicts,
            "attention_required": attention_required,
        },
        "anti_amnesia": {
            "context_sha256": context_sha,
            "case_sha256": case["case_sha256"],
            "decision_packet_sha256": packet_sha,
            "binding_exact": True,
            "apply_allowed": False,
        },
        "control_center_projection": {
            "status": "UNAPPLIED_SHADOW_PROJECTION",
            "system_recommendation": decision_packet["system_recommendation"],
            "control_plane_action": control_action,
            "current_truth_mutation": False,
            "command_queue_mutation": False,
            "decision_ledger_mutation": False,
            "human_gate_created": False,
            "apply": False,
        },
        "continuity_and_return": {
            "checkpoint_mode": "DRY_RUN_ONLY",
            "event_append": False,
            "checkpoint_write": False,
            "replay_write": False,
            "return_packet_write": False,
            "archive_write": False,
            "runtime_activation": False,
        },
        "effect_counters": counters,
        "executor_boundary": {
            "enabled": False,
            "execution_authority": "NONE",
            "reason": "P0_SHADOW_NO_EFFECT",
        },
        "control_gate": control_gate,
        "control_plane_action": control_action,
        "semantics": {
            "stale_authority_evidence_forces_hold": True,
            "conflict_forces_attention_and_hold": True,
            "shadow_projection_is_not_current_truth": True,
            "memory_or_prediction_does_not_create_permission": True,
            "continuity_dry_run_does_not_write_state": True,
            "human_sovereign_remains_authority_root": True,
            "sct_adapter_is_not_continuityos_source_authority": True,
            "modern_continuity_source_is_not_live_runtime": True,
            "repo_identity_does_not_prove_host_state": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["control_plane_sha256"] = sha256_obj(body)
    return body
