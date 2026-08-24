#!/usr/bin/env python3
"""TradingOS R78 bounded AI Analyst public contract.

Implementation is split into small transparent modules. This wrapper preserves the
original provider-agnostic API and contains no model/network/process transport.
"""
from tools.tradingos_ai_analyst_common import *
from tools.tradingos_ai_analyst_policy import validate_policy
from tools.tradingos_ai_analyst_brief import validate_brief
from tools.tradingos_ai_analyst_evidence import build_evidence_catalog
from tools.tradingos_ai_analyst_request import build_request, validate_request
from tools.tradingos_ai_analyst_prompt import render_model_prompt, allowed_numeric_literals
from tools.tradingos_ai_analyst_response import validate_response

__all__ = [
    "SCHEMA", "VERSION", "RESPONSE_SCHEMA", "POLICY_ID",
    "EXPECTED_INPUT_PRODUCER", "EXPECTED_BRIEF_GENERATOR",
    "EXPECTED_BRIEF_GENERATOR_VERSION", "EXPECTED_BRIEF_POLICY_ID",
    "EXPECTED_BRIEF_PERMISSIONS", "REQUEST_SAFETY",
    "stable_json_bytes", "stable_sha256", "finite",
    "validate_policy", "validate_brief", "build_evidence_catalog",
    "build_request", "validate_request", "render_model_prompt",
    "allowed_numeric_literals", "validate_response",
]
