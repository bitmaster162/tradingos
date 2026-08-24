"""TradingOS R80 immutable retrospective record contract."""
from __future__ import annotations

import hashlib
from typing import Any

from tools import tradingos_ai_analyst_contract as r78
from tools import tradingos_model_transport_contract as r79
from tools.tradingos_retrospective_memory_common import *
from tools.tradingos_retrospective_memory_common import _ID24_RE, _SHA64_RE


ANNOTATION_KEYS = {"schema", "request_id", "brief_sha256", "claim_outcomes"}
ANNOTATION_SCHEMA = "tradingos.retrospective_annotation.v1"
ANNOTATION_ROW_KEYS = {"claim_id", "outcome", "rationale_code"}

RECORD_KEYS = {
    "schema",
    "record_id",
    "request_id",
    "brief_sha256",
    "request_sha256",
    "envelope_sha256",
    "transport_receipt_sha256",
    "response_sha256",
    "memory_policy_sha256",
    "annotation_sha256",
    "claim_outcomes",
    "memory_write_authority",
    "auto_learning_allowed",
    "live_decision_feedback_allowed",
    "execution_authority",
    "can_trade",
    "capital_permission",
    "confers_authority",
}
RECORD_ROW_KEYS = {"claim_id", "claim_kind", "outcome", "rationale_code"}


def _record_identity_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in RECORD_KEYS if key != "record_id"}


def _expected_record_id(record: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{RECORD_SCHEMA}:{VERSION}:".encode("utf-8")
        + stable_json_bytes(_record_identity_payload(record))
    ).hexdigest()[:24]


def validate_annotation(annotation: Any, response: dict[str, Any]) -> None:
    if not isinstance(annotation, dict) or set(annotation) != ANNOTATION_KEYS:
        raise ValueError("annotation key set mismatch")
    if annotation.get("schema") != ANNOTATION_SCHEMA:
        raise ValueError("unsupported annotation schema")
    if annotation.get("request_id") != response.get("request_id"):
        raise ValueError("annotation request_id mismatch")
    if annotation.get("brief_sha256") != response.get("brief_sha256"):
        raise ValueError("annotation brief mismatch")

    claims = response.get("claims")
    if not isinstance(claims, list):
        raise ValueError("validated response claims missing")
    response_ids = [row.get("claim_id") for row in claims if isinstance(row, dict)]
    response_kinds = {
        row.get("claim_id"): row.get("kind") for row in claims if isinstance(row, dict)
    }
    rows = annotation.get("claim_outcomes")
    if not isinstance(rows, list):
        raise ValueError("annotation claim_outcomes must be list")
    seen = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != ANNOTATION_ROW_KEYS:
            raise ValueError(f"annotation row {i} key set mismatch")
        claim_id = row.get("claim_id")
        if not isinstance(claim_id, str) or claim_id not in response_kinds:
            raise ValueError(f"annotation row {i} unknown claim_id")
        if row.get("outcome") not in OUTCOMES:
            raise ValueError(f"annotation row {i} invalid outcome")
        if row.get("rationale_code") not in RATIONALE_CODES:
            raise ValueError(f"annotation row {i} invalid rationale_code")
        seen.append(claim_id)
    if len(seen) != len(set(seen)):
        raise ValueError("duplicate annotation claim_id")
    if seen != response_ids:
        raise ValueError("annotation must cover response claims exactly and in order")


def build_retrospective_record(
    *,
    request: dict[str, Any],
    prompt: str,
    r78_policy: dict[str, Any],
    source_brief: dict[str, Any],
    transport_policy: dict[str, Any],
    envelope: dict[str, Any],
    transport_receipt: dict[str, Any],
    response: dict[str, Any],
    annotation: dict[str, Any],
    memory_policy: dict[str, Any],
) -> dict[str, Any]:
    validate_memory_policy(memory_policy)
    r79.validate_transport_envelope(envelope, request, prompt, transport_policy)
    r79.validate_transport_receipt(transport_receipt, envelope, response)
    result = r78.validate_response(request, response, r78_policy, source_brief)
    if not isinstance(result, dict) or result.get("passed") is not True:
        raise ValueError("R78 response validation did not pass")
    validate_annotation(annotation, response)

    claim_kind_by_id = {
        row["claim_id"]: row["kind"] for row in response["claims"]
    }
    claim_outcomes = [
        {
            "claim_id": row["claim_id"],
            "claim_kind": claim_kind_by_id[row["claim_id"]],
            "outcome": row["outcome"],
            "rationale_code": row["rationale_code"],
        }
        for row in annotation["claim_outcomes"]
    ]

    request_sha = stable_sha256(request)
    envelope_sha = stable_sha256(envelope)
    receipt_sha = stable_sha256(transport_receipt)
    response_sha = stable_sha256(response)
    memory_policy_sha = stable_sha256(memory_policy)
    annotation_sha = stable_sha256(annotation)
    brief_sha = response["brief_sha256"]

    record = {
        "schema": RECORD_SCHEMA,
        "request_id": response["request_id"],
        "brief_sha256": brief_sha,
        "request_sha256": request_sha,
        "envelope_sha256": envelope_sha,
        "transport_receipt_sha256": receipt_sha,
        "response_sha256": response_sha,
        "memory_policy_sha256": memory_policy_sha,
        "annotation_sha256": annotation_sha,
        "claim_outcomes": claim_outcomes,
        "memory_write_authority": "NONE",
        "auto_learning_allowed": False,
        "live_decision_feedback_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    record["record_id"] = _expected_record_id(record)
    validate_retrospective_record(record, memory_policy)
    return record


def validate_retrospective_record(record: Any, memory_policy: dict[str, Any]) -> None:
    validate_memory_policy(memory_policy)
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        raise ValueError("retrospective record key set mismatch")
    if record.get("schema") != RECORD_SCHEMA:
        raise ValueError("unsupported retrospective record")
    if not isinstance(record.get("record_id"), str) or _ID24_RE.fullmatch(record["record_id"]) is None:
        raise ValueError("record_id invalid")
    if not isinstance(record.get("request_id"), str) or _ID24_RE.fullmatch(record["request_id"]) is None:
        raise ValueError("request_id invalid")
    for field in (
        "brief_sha256",
        "request_sha256",
        "envelope_sha256",
        "transport_receipt_sha256",
        "response_sha256",
        "memory_policy_sha256",
        "annotation_sha256",
    ):
        value = record.get(field)
        if not isinstance(value, str) or _SHA64_RE.fullmatch(value) is None:
            raise ValueError(f"{field} invalid")
    if record["memory_policy_sha256"] != stable_sha256(memory_policy):
        raise ValueError("memory policy binding mismatch")
    rows = record.get("claim_outcomes")
    if not isinstance(rows, list):
        raise ValueError("record claim_outcomes must be list")
    ids = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != RECORD_ROW_KEYS:
            raise ValueError(f"record row {i} key set mismatch")
        if not isinstance(row.get("claim_id"), str) or not row["claim_id"]:
            raise ValueError(f"record row {i} claim_id invalid")
        if row.get("claim_kind") not in CLAIM_KINDS:
            raise ValueError(f"record row {i} claim_kind invalid")
        if row.get("outcome") not in OUTCOMES:
            raise ValueError(f"record row {i} outcome invalid")
        if row.get("rationale_code") not in RATIONALE_CODES:
            raise ValueError(f"record row {i} rationale invalid")
        ids.append(row["claim_id"])
    if len(ids) != len(set(ids)):
        raise ValueError("record duplicate claim_id")
    ceiling = {
        "memory_write_authority": "NONE",
        "auto_learning_allowed": False,
        "live_decision_feedback_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    for key, expected in ceiling.items():
        if record.get(key) != expected:
            raise ValueError(f"unsafe retrospective record: {key}")
    if record["record_id"] != _expected_record_id(record):
        raise ValueError("record_id binding mismatch")
