"""TradingOS R80 count-only retrospective calibration summary."""
from __future__ import annotations

import hashlib
from typing import Any

from tools.tradingos_retrospective_memory_common import *
from tools.tradingos_retrospective_memory_common import _ID24_RE
from tools.tradingos_retrospective_memory_contract import validate_retrospective_record


SUMMARY_KEYS = {
    "schema",
    "summary_id",
    "memory_policy_sha256",
    "record_count",
    "claim_count",
    "counts_by_kind",
    "total_outcomes",
    "calibration_mode",
    "predictive_probability",
    "auto_learning_allowed",
    "live_decision_feedback_allowed",
    "execution_authority",
    "can_trade",
    "capital_permission",
    "confers_authority",
}


def _summary_identity_payload(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: summary[key] for key in SUMMARY_KEYS if key != "summary_id"}


def _expected_summary_id(summary: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{SUMMARY_SCHEMA}:{VERSION}:".encode("utf-8")
        + stable_json_bytes(_summary_identity_payload(summary))
    ).hexdigest()[:24]


def build_count_summary(
    records: list[dict[str, Any]],
    memory_policy: dict[str, Any],
) -> dict[str, Any]:
    validate_memory_policy(memory_policy)
    if not isinstance(records, list):
        raise ValueError("records must be list")
    seen = set()
    counts = {
        kind: {outcome: 0 for outcome in OUTCOMES}
        for kind in CLAIM_KINDS
    }
    total = {outcome: 0 for outcome in OUTCOMES}
    claim_count = 0

    for record in records:
        validate_retrospective_record(record, memory_policy)
        rid = record["record_id"]
        if rid in seen:
            raise ValueError("duplicate retrospective record_id")
        seen.add(rid)
        for row in record["claim_outcomes"]:
            counts[row["claim_kind"]][row["outcome"]] += 1
            total[row["outcome"]] += 1
            claim_count += 1

    summary = {
        "schema": SUMMARY_SCHEMA,
        "memory_policy_sha256": stable_sha256(memory_policy),
        "record_count": len(records),
        "claim_count": claim_count,
        "counts_by_kind": counts,
        "total_outcomes": total,
        "calibration_mode": "COUNT_ONLY",
        "predictive_probability": None,
        "auto_learning_allowed": False,
        "live_decision_feedback_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    summary["summary_id"] = _expected_summary_id(summary)
    validate_count_summary(summary, memory_policy)
    return summary


def validate_count_summary(summary: Any, memory_policy: dict[str, Any]) -> None:
    validate_memory_policy(memory_policy)
    if not isinstance(summary, dict) or set(summary) != SUMMARY_KEYS:
        raise ValueError("count summary key set mismatch")
    if summary.get("schema") != SUMMARY_SCHEMA:
        raise ValueError("unsupported count summary")
    if not isinstance(summary.get("summary_id"), str) or _ID24_RE.fullmatch(summary["summary_id"]) is None:
        raise ValueError("summary_id invalid")
    if summary.get("memory_policy_sha256") != stable_sha256(memory_policy):
        raise ValueError("summary memory policy binding mismatch")
    for field in ("record_count", "claim_count"):
        value = summary.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} invalid")
    counts = summary.get("counts_by_kind")
    if not isinstance(counts, dict) or set(counts) != set(CLAIM_KINDS):
        raise ValueError("counts_by_kind key set mismatch")
    computed_total = {outcome: 0 for outcome in OUTCOMES}
    computed_claim_count = 0
    for kind in CLAIM_KINDS:
        row = counts[kind]
        if not isinstance(row, dict) or set(row) != set(OUTCOMES):
            raise ValueError(f"count row invalid: {kind}")
        for outcome in OUTCOMES:
            value = row[outcome]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"count invalid: {kind}.{outcome}")
            computed_total[outcome] += value
            computed_claim_count += value
    if summary.get("total_outcomes") != computed_total:
        raise ValueError("total_outcomes mismatch")
    if summary.get("claim_count") != computed_claim_count:
        raise ValueError("claim_count mismatch")
    if summary.get("calibration_mode") != "COUNT_ONLY":
        raise ValueError("summary mode drift")
    if summary.get("predictive_probability") is not None:
        raise ValueError("predictive probability forbidden")
    ceiling = {
        "auto_learning_allowed": False,
        "live_decision_feedback_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    for key, expected in ceiling.items():
        if summary.get(key) != expected:
            raise ValueError(f"unsafe count summary: {key}")
    if summary["summary_id"] != _expected_summary_id(summary):
        raise ValueError("summary_id binding mismatch")
