"""TradingOS R81 deterministic frozen-record shadow evaluation contract."""
from __future__ import annotations

import hashlib
from typing import Any

from tools import tradingos_retrospective_memory_contract as r80
from tools.tradingos_retrospective_memory_common import validate_memory_policy
from tools.tradingos_shadow_evaluation_common import *
from tools.tradingos_shadow_evaluation_common import _ID24_RE, _SHA64_RE

DECLARATION_KEYS = {
    "schema",
    "declaration_id",
    "memory_policy_sha256",
    "records",
    "record_count",
    "records_digest",
    "shadow_only",
    "memory_write_authority",
    "live_decision_use_allowed",
    "model_selection_use_allowed",
    "execution_authority",
    "can_trade",
    "capital_permission",
    "confers_authority",
}
DECLARATION_RECORD_KEYS = {"record_id", "record_sha256"}

REPORT_KEYS = {
    "schema",
    "report_id",
    "frozen_set_declaration_id",
    "frozen_set_declaration_sha256",
    "memory_policy_sha256",
    "shadow_policy_sha256",
    "record_count",
    "claim_count",
    "counts_by_outcome",
    "counts_by_claim_kind",
    "integrity",
    "report_mode",
    "shadow_only",
    "memory_write_authority",
    "auto_learning_allowed",
    "live_decision_feedback_allowed",
    "live_decision_use_allowed",
    "model_selection_use_allowed",
    "execution_authority",
    "can_trade",
    "capital_permission",
    "confers_authority",
}
INTEGRITY_KEYS = {
    "all_records_valid",
    "duplicate_record_ids_absent",
    "frozen_set_exact",
    "mixed_memory_policy_absent",
    "record_payloads_bound",
}


def _declaration_identity_payload(declaration: dict[str, Any]) -> dict[str, Any]:
    return {key: declaration[key] for key in DECLARATION_KEYS if key != "declaration_id"}


def _expected_declaration_id(declaration: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{DECLARATION_SCHEMA}:{VERSION}:".encode("utf-8")
        + stable_json_bytes(_declaration_identity_payload(declaration))
    ).hexdigest()[:24]


def _report_identity_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in REPORT_KEYS if key != "report_id"}


def _expected_report_id(report: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{REPORT_SCHEMA}:{VERSION}:".encode("utf-8")
        + stable_json_bytes(_report_identity_payload(report))
    ).hexdigest()[:24]


def _validated_bindings(records: list[dict[str, Any]], memory_policy: dict[str, Any]) -> list[dict[str, str]]:
    if not isinstance(records, list):
        raise ValueError("records must be list")
    seen: set[str] = set()
    bindings: list[dict[str, str]] = []
    for record in records:
        r80.validate_retrospective_record(record, memory_policy)
        rid = record["record_id"]
        if rid in seen:
            raise ValueError("duplicate retrospective record_id")
        seen.add(rid)
        bindings.append({"record_id": rid, "record_sha256": stable_sha256(record)})
    return sorted(bindings, key=lambda row: row["record_id"])


def build_frozen_set_declaration(
    records: list[dict[str, Any]],
    memory_policy: dict[str, Any],
    shadow_policy: dict[str, Any],
) -> dict[str, Any]:
    validate_memory_policy(memory_policy)
    validate_shadow_policy(shadow_policy)
    bindings = _validated_bindings(records, memory_policy)
    declaration = {
        "schema": DECLARATION_SCHEMA,
        "memory_policy_sha256": stable_sha256(memory_policy),
        "records": bindings,
        "record_count": len(bindings),
        "records_digest": stable_sha256(bindings),
        "shadow_only": True,
        "memory_write_authority": "NONE",
        "live_decision_use_allowed": False,
        "model_selection_use_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    declaration["declaration_id"] = _expected_declaration_id(declaration)
    validate_frozen_set_declaration(declaration, memory_policy, shadow_policy)
    return declaration


def validate_frozen_set_declaration(
    declaration: Any,
    memory_policy: dict[str, Any],
    shadow_policy: dict[str, Any],
) -> None:
    validate_memory_policy(memory_policy)
    validate_shadow_policy(shadow_policy)
    if not isinstance(declaration, dict) or set(declaration) != DECLARATION_KEYS:
        raise ValueError("frozen declaration key set mismatch")
    if declaration.get("schema") != DECLARATION_SCHEMA:
        raise ValueError("unsupported frozen declaration")
    did = declaration.get("declaration_id")
    if not isinstance(did, str) or _ID24_RE.fullmatch(did) is None:
        raise ValueError("declaration_id invalid")
    if declaration.get("memory_policy_sha256") != stable_sha256(memory_policy):
        raise ValueError("declaration memory policy mismatch")
    count = declaration.get("record_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("declaration record_count invalid")
    rows = declaration.get("records")
    if not isinstance(rows, list):
        raise ValueError("declaration records must be list")
    ids: list[str] = []
    normalized: list[dict[str, str]] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != DECLARATION_RECORD_KEYS:
            raise ValueError(f"declaration record {i} key set mismatch")
        rid = row.get("record_id")
        sha = row.get("record_sha256")
        if not isinstance(rid, str) or _ID24_RE.fullmatch(rid) is None:
            raise ValueError(f"declaration record {i} id invalid")
        if not isinstance(sha, str) or _SHA64_RE.fullmatch(sha) is None:
            raise ValueError(f"declaration record {i} sha invalid")
        ids.append(rid)
        normalized.append({"record_id": rid, "record_sha256": sha})
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate declaration record_id")
    if normalized != sorted(normalized, key=lambda row: row["record_id"]):
        raise ValueError("declaration records must be canonical order")
    if count != len(rows):
        raise ValueError("declaration record_count mismatch")
    if declaration.get("records_digest") != stable_sha256(rows):
        raise ValueError("declaration records_digest mismatch")
    ceiling = {
        "shadow_only": True,
        "memory_write_authority": "NONE",
        "live_decision_use_allowed": False,
        "model_selection_use_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    for key, expected in ceiling.items():
        if declaration.get(key) != expected:
            raise ValueError(f"unsafe frozen declaration: {key}")
    if declaration["declaration_id"] != _expected_declaration_id(declaration):
        raise ValueError("declaration_id binding mismatch")


def build_shadow_report(
    records: list[dict[str, Any]],
    declaration: dict[str, Any],
    memory_policy: dict[str, Any],
    shadow_policy: dict[str, Any],
) -> dict[str, Any]:
    validate_memory_policy(memory_policy)
    validate_shadow_policy(shadow_policy)
    validate_frozen_set_declaration(declaration, memory_policy, shadow_policy)
    bindings = _validated_bindings(records, memory_policy)
    if bindings != declaration["records"]:
        raise ValueError("supplied records do not exactly match frozen declaration")
    if stable_sha256(bindings) != declaration["records_digest"]:
        raise ValueError("frozen record binding digest mismatch")

    counts_by_outcome = {outcome: 0 for outcome in OUTCOMES}
    counts_by_claim_kind = {kind: 0 for kind in CLAIM_KINDS}
    claim_count = 0
    records_by_id = {record["record_id"]: record for record in records}
    for binding in bindings:
        record = records_by_id[binding["record_id"]]
        for row in record["claim_outcomes"]:
            counts_by_outcome[row["outcome"]] += 1
            counts_by_claim_kind[row["claim_kind"]] += 1
            claim_count += 1

    report = {
        "schema": REPORT_SCHEMA,
        "frozen_set_declaration_id": declaration["declaration_id"],
        "frozen_set_declaration_sha256": stable_sha256(declaration),
        "memory_policy_sha256": stable_sha256(memory_policy),
        "shadow_policy_sha256": stable_sha256(shadow_policy),
        "record_count": len(bindings),
        "claim_count": claim_count,
        "counts_by_outcome": counts_by_outcome,
        "counts_by_claim_kind": counts_by_claim_kind,
        "integrity": {
            "all_records_valid": True,
            "duplicate_record_ids_absent": True,
            "frozen_set_exact": True,
            "mixed_memory_policy_absent": True,
            "record_payloads_bound": True,
        },
        "report_mode": "COUNT_AND_INTEGRITY_ONLY",
        "shadow_only": True,
        "memory_write_authority": "NONE",
        "auto_learning_allowed": False,
        "live_decision_feedback_allowed": False,
        "live_decision_use_allowed": False,
        "model_selection_use_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    report["report_id"] = _expected_report_id(report)
    validate_shadow_report(report, declaration, memory_policy, shadow_policy)
    return report


def validate_shadow_report(
    report: Any,
    declaration: dict[str, Any],
    memory_policy: dict[str, Any],
    shadow_policy: dict[str, Any],
) -> None:
    validate_frozen_set_declaration(declaration, memory_policy, shadow_policy)
    if not isinstance(report, dict) or set(report) != REPORT_KEYS:
        raise ValueError("shadow report key set mismatch")
    if report.get("schema") != REPORT_SCHEMA:
        raise ValueError("unsupported shadow report")
    rid = report.get("report_id")
    if not isinstance(rid, str) or _ID24_RE.fullmatch(rid) is None:
        raise ValueError("report_id invalid")
    if report.get("frozen_set_declaration_id") != declaration["declaration_id"]:
        raise ValueError("report declaration id mismatch")
    if report.get("frozen_set_declaration_sha256") != stable_sha256(declaration):
        raise ValueError("report declaration sha mismatch")
    if report.get("memory_policy_sha256") != stable_sha256(memory_policy):
        raise ValueError("report memory policy mismatch")
    if report.get("shadow_policy_sha256") != stable_sha256(shadow_policy):
        raise ValueError("report shadow policy mismatch")
    for field in ("record_count", "claim_count"):
        value = report.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} invalid")
    if report["record_count"] != declaration["record_count"]:
        raise ValueError("report record_count mismatch")
    outcome_counts = report.get("counts_by_outcome")
    if not isinstance(outcome_counts, dict) or set(outcome_counts) != set(OUTCOMES):
        raise ValueError("counts_by_outcome key set mismatch")
    kind_counts = report.get("counts_by_claim_kind")
    if not isinstance(kind_counts, dict) or set(kind_counts) != set(CLAIM_KINDS):
        raise ValueError("counts_by_claim_kind key set mismatch")
    for label, counts in (("outcome", outcome_counts), ("claim_kind", kind_counts)):
        for key, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} count invalid: {key}")
    if sum(outcome_counts.values()) != report["claim_count"]:
        raise ValueError("outcome claim_count mismatch")
    if sum(kind_counts.values()) != report["claim_count"]:
        raise ValueError("claim-kind claim_count mismatch")
    integrity = report.get("integrity")
    if not isinstance(integrity, dict) or set(integrity) != INTEGRITY_KEYS:
        raise ValueError("integrity key set mismatch")
    if any(value is not True for value in integrity.values()):
        raise ValueError("shadow integrity must be all true")
    if report.get("report_mode") != "COUNT_AND_INTEGRITY_ONLY":
        raise ValueError("report mode drift")
    ceiling = {
        "shadow_only": True,
        "memory_write_authority": "NONE",
        "auto_learning_allowed": False,
        "live_decision_feedback_allowed": False,
        "live_decision_use_allowed": False,
        "model_selection_use_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    for key, expected in ceiling.items():
        if report.get(key) != expected:
            raise ValueError(f"unsafe shadow report: {key}")
    if report["report_id"] != _expected_report_id(report):
        raise ValueError("report_id binding mismatch")
