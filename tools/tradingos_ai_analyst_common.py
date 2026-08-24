"""Shared constants and deterministic helpers for TradingOS R78 AI Analyst."""
from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCHEMA = "tradingos.ai_analyst_request.v1"
VERSION = "1.0.0"
RESPONSE_SCHEMA = "tradingos.ai_analyst_response.v1"
POLICY_ID = "TRADINGOS_AI_ANALYST_POLICY_V1"
EXPECTED_INPUT_PRODUCER = "tools/tradingos_market_decision_snapshot_seal.py"
EXPECTED_BRIEF_GENERATOR = "tools/tradingos_decision_brief_v2.py"
EXPECTED_BRIEF_GENERATOR_VERSION = "2.0.0"
EXPECTED_BRIEF_POLICY_ID = "TRADINGOS_DECISION_BRIEF_POLICY_V1"

ALLOWED_BRIEF_STATUS = {"READY", "BLOCKED"}
ALLOWED_STANCES = {"WATCH_LONG", "WATCH_SHORT", "NO_ACTION"}
ALLOWED_DIRECTIONS = {"LONG", "SHORT", "NEUTRAL"}
EXPECTED_BRIEF_PERMISSIONS = {
    "read_only_analysis": True,
    "signals_allowed": False,
    "orders_allowed": False,
    "uses_credentials": False,
    "can_trade": False,
    "capital_permission": "DENY",
}
REQUEST_SAFETY = {
    "model_transport": "NOT_INCLUDED",
    "external_sources_allowed": False,
    "new_market_facts_allowed": False,
    "new_numeric_literals_allowed": False,
    "probability_claims_allowed": False,
    "signals_allowed": False,
    "orders_allowed": False,
    "execution_authority": "NONE",
    "can_trade": False,
    "capital_permission": "DENY",
    "confers_authority": False,
}

POLICY_KEYS = {
    "schema_version", "policy_id", "input_contract", "model_transport_in_core",
    "external_sources_allowed", "new_market_facts_allowed",
    "new_numeric_literals_allowed", "probability_claims_allowed",
    "max_claims", "max_questions", "max_text_chars",
    "allowed_claim_kinds", "blocked_brief_allowed_claim_kinds",
    "allowed_operator_dispositions", "blocked_brief_allowed_dispositions",
    "output_permissions",
}
EXACT_ALLOWED_CLAIM_KINDS = [
    "THESIS", "COUNTERTHESIS", "BLIND_SPOT", "PREMORTEM",
    "SCENARIO_READ", "INVALIDATION_READ", "OPERATOR_QUESTION",
]
EXACT_BLOCKED_CLAIM_KINDS = [
    "BLIND_SPOT", "PREMORTEM", "INVALIDATION_READ", "OPERATOR_QUESTION",
]
EXACT_DISPOSITIONS = [
    "REVIEW_BRIEF", "WAIT_FOR_CONFIRMATION", "REFRESH_DATA", "NO_ACTION",
]
EXACT_BLOCKED_DISPOSITIONS = ["REFRESH_DATA", "NO_ACTION"]

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?(?![A-Za-z0-9_])"
)
_FORBIDDEN_EXECUTION_RE = re.compile(
    r"\b(?:buy|sell|entry|enter\s+(?:a\s+)?(?:long|short)|exit|execute|"
    r"place\s+(?:an?\s+)?order|position\s+size|size\s+the\s+position|"
    r"leverage|stop[- ]?loss|take[- ]?profit|allocate\s+capital|"
    r"send\s+order|open\s+(?:a\s+)?position|close\s+(?:a\s+)?position)\b",
    re.IGNORECASE,
)
_PROBABILITY_RE = re.compile(
    r"\b(?:probability|probabilities|chance\s+of|odds\s+of|confidence\s+of)\b",
    re.IGNORECASE,
)

ROOT_KEYS = {
    "schema",
    "request_id",
    "brief_sha256",
    "analysis_mode",
    "claims",
    "operator_disposition",
    "questions",
    "external_sources_used",
    "probability_claimed",
    "signals_allowed",
    "orders_allowed",
    "execution_authority",
    "can_trade",
    "capital_permission",
    "confers_authority",
}
CLAIM_KEYS = {
    "claim_id",
    "kind",
    "text",
    "evidence_refs",
    "claim_scope",
    "novel_market_fact",
}
QUESTION_KEYS = {"question_id", "text", "evidence_refs"}


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


def finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}: finite number required")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field}: finite number required")
    return number


def _require_text(value: Any, field: str, *, max_chars: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field}: non-empty trimmed string required")
    if max_chars is not None and len(value) > max_chars:
        raise ValueError(f"{field}: exceeds max chars")
    return value

__all__ = [name for name in globals() if not name.startswith("__")]
