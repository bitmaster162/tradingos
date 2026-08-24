#!/usr/bin/env python3
"""TradingOS R79 public model transport boundary.

R1 is local-mock-only. Live provider/network transport remains denied.
"""
from __future__ import annotations

import copy
from typing import Any

from tools.tradingos_model_transport_common import *
from tools.tradingos_model_transport_envelope import build_transport_envelope, validate_transport_envelope
from tools import tradingos_ai_analyst_contract as r78

def invoke_mock_transport(
    envelope: dict[str, Any],
    adapter: Any,
    transport_policy: dict[str, Any],
    *,
    request: dict[str, Any],
    prompt: str,
    r78_policy: dict[str, Any],
    source_brief: dict[str, Any],
) -> dict[str, Any]:
    """Invoke one injected local mock adapter and validate its output via canonical R78."""
    validate_transport_envelope(envelope, request, prompt, transport_policy)

    mode = getattr(adapter, "transport_mode", None)
    if mode != ALLOWED_MODE:
        raise ValueError("adapter mode denied")
    if getattr(adapter, "network_call_performed", False) is not False:
        raise ValueError("adapter declares network activity")
    if getattr(adapter, "credentials_used", False) is not False:
        raise ValueError("adapter declares credential use")
    invoke = getattr(adapter, "invoke", None)
    if not callable(invoke):
        raise ValueError("adapter.invoke callable required")

    original_hash = stable_sha256(envelope)
    response = invoke(copy.deepcopy(envelope))
    if stable_sha256(envelope) != original_hash:
        raise ValueError("transport envelope mutated")
    if not isinstance(response, dict):
        raise ValueError("adapter response must be object")

    validation = r78.validate_response(request, response, r78_policy, source_brief)
    if not isinstance(validation, dict) or validation.get("passed") is not True:
        raise ValueError("R78 response validation did not pass")

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "envelope_id": envelope["envelope_id"],
        "request_id": envelope["request_id"],
        "brief_sha256": envelope["brief_sha256"],
        "request_sha256": envelope["request_sha256"],
        "prompt_sha256": envelope["prompt_sha256"],
        "transport_policy_sha256": envelope["transport_policy_sha256"],
        "provider_id": envelope["provider"]["provider_id"],
        "model_id": envelope["provider"]["model_id"],
        "transport_mode": ALLOWED_MODE,
        "adapter_invoked": True,
        "network_call_performed": False,
        "credentials_used": False,
        "response_sha256": stable_sha256(response),
        "r78_response_validated": True,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    validate_transport_receipt(receipt, envelope, response)
    return receipt


def validate_transport_receipt(
    receipt: Any,
    envelope: dict[str, Any],
    response: dict[str, Any],
) -> None:
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        raise ValueError("transport receipt key set mismatch")
    exact = {
        "schema": RECEIPT_SCHEMA,
        "envelope_id": envelope["envelope_id"],
        "request_id": envelope["request_id"],
        "brief_sha256": envelope["brief_sha256"],
        "request_sha256": envelope["request_sha256"],
        "prompt_sha256": envelope["prompt_sha256"],
        "transport_policy_sha256": envelope["transport_policy_sha256"],
        "provider_id": envelope["provider"]["provider_id"],
        "model_id": envelope["provider"]["model_id"],
        "transport_mode": ALLOWED_MODE,
        "adapter_invoked": True,
        "network_call_performed": False,
        "credentials_used": False,
        "response_sha256": stable_sha256(response),
        "r78_response_validated": True,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    if receipt != exact:
        raise ValueError("transport receipt mismatch")
