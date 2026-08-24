#!/usr/bin/env python3
"""TradingOS R79 model transport boundary.

R1 is provider-agnostic and local-mock-only. It contains no network client, provider
SDK, credential access, process transport, or runtime/deployment authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from tools import tradingos_ai_analyst_contract as r78

ENVELOPE_SCHEMA = "tradingos.model_transport_envelope.v1"
RECEIPT_SCHEMA = "tradingos.model_transport_receipt.v1"
VERSION = "1.0.0"
POLICY_ID = "TRADINGOS_MODEL_TRANSPORT_POLICY_V1"
ALLOWED_MODE = "MOCK_LOCAL"

_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
_ID24_RE = re.compile(r"^[0-9a-f]{24}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")

POLICY_KEYS = {
    "schema_version",
    "policy_id",
    "transport_boundary_enabled",
    "live_provider_calls_allowed",
    "mock_local_allowed",
    "allowed_adapter_modes",
    "credentials_allowed",
    "environment_secret_reads_allowed",
    "external_sources_allowed",
    "provider_tools_allowed",
    "streaming_allowed",
    "async_dispatch_allowed",
    "retries",
    "timeout_seconds",
    "max_prompt_chars",
    "request_binding_required",
    "prompt_binding_required",
    "post_model_validation_required",
    "output_permissions",
}

OUTPUT_PERMISSIONS = {
    "interpretation_only": True,
    "signals_allowed": False,
    "orders_allowed": False,
    "execution_authority": "NONE",
    "can_trade": False,
    "capital_permission": "DENY",
    "confers_authority": False,
}

ENVELOPE_KEYS = {
    "schema",
    "version",
    "envelope_id",
    "request_id",
    "brief_sha256",
    "request_sha256",
    "prompt_sha256",
    "transport_policy_sha256",
    "provider",
    "prompt",
    "safety",
}

PROVIDER_KEYS = {"provider_id", "model_id", "transport_mode"}
SAFETY = {
    "network_call_authorized": False,
    "credentials_allowed": False,
    "external_sources_allowed": False,
    "provider_tools_allowed": False,
    "execution_authority": "NONE",
    "can_trade": False,
    "capital_permission": "DENY",
    "confers_authority": False,
}

RECEIPT_KEYS = {
    "schema",
    "envelope_id",
    "request_id",
    "brief_sha256",
    "request_sha256",
    "prompt_sha256",
    "transport_policy_sha256",
    "provider_id",
    "model_id",
    "transport_mode",
    "adapter_invoked",
    "network_call_performed",
    "credentials_used",
    "response_sha256",
    "r78_response_validated",
    "execution_authority",
    "can_trade",
    "capital_permission",
    "confers_authority",
}


def stable_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonically serializable") from exc


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(stable_json_bytes(value)).hexdigest()


def text_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("text must be string")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _label(value: Any, field: str, max_chars: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_chars
        or _LABEL_RE.fullmatch(value) is None
    ):
        raise ValueError(f"{field}: invalid label")
    return value


def validate_transport_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("transport policy key set mismatch")
    if policy.get("schema_version") != 1 or policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported transport policy")
    exact_false = (
        "live_provider_calls_allowed",
        "credentials_allowed",
        "environment_secret_reads_allowed",
        "external_sources_allowed",
        "provider_tools_allowed",
        "streaming_allowed",
        "async_dispatch_allowed",
    )
    for field in exact_false:
        if policy.get(field) is not False:
            raise ValueError(f"unsafe transport policy: {field}")
    exact_true = (
        "transport_boundary_enabled",
        "mock_local_allowed",
        "request_binding_required",
        "prompt_binding_required",
        "post_model_validation_required",
    )
    for field in exact_true:
        if policy.get(field) is not True:
            raise ValueError(f"transport policy requires {field}=true")
    if policy.get("allowed_adapter_modes") != [ALLOWED_MODE]:
        raise ValueError("adapter mode contract mismatch")
    if policy.get("retries") != 0 or isinstance(policy.get("retries"), bool):
        raise ValueError("retries must be exactly zero")
    if policy.get("timeout_seconds") != 0 or isinstance(policy.get("timeout_seconds"), bool):
        raise ValueError("timeout_seconds must be exactly zero in R1")
    max_prompt = policy.get("max_prompt_chars")
    if isinstance(max_prompt, bool) or not isinstance(max_prompt, int) or max_prompt != 50000:
        raise ValueError("max_prompt_chars exact R1 limit required")
    if policy.get("output_permissions") != OUTPUT_PERMISSIONS:
        raise ValueError("unsafe transport output permissions")

__all__ = [name for name in globals() if not name.startswith('_')]
