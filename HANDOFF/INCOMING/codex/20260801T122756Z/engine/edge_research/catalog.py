"""Future census catalog validation and pre-outcome authorization gate."""

from __future__ import annotations

import re
from typing import Any

from .common import (
    ContractError,
    canonical_bytes,
    ensure_full_sha256,
    parse_utc,
    require_fields,
    sha256_bytes,
)
from .preregistration import validate_preregistration


ALLOWED_READINESS = ("DATA_READY", "PARTIAL_DATA", "NO_DATA", "PROVENANCE_BLOCKED")
TIMESTAMP_UNITS = ("s", "ms", "us", "ns", "iso8601")


def catalog_sha256(catalog: dict[str, Any]) -> str:
    return sha256_bytes(canonical_bytes(catalog))


def validate_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    require_fields(catalog, ["schema", "catalog_id", "frozen_at_utc", "raw_packets"], "catalog")
    parse_utc(catalog["frozen_at_utc"], "catalog.frozen_at_utc")
    packets = catalog["raw_packets"]
    if not isinstance(packets, list) or not packets:
        raise ContractError("EMPTY_SOURCE_CATALOG", "catalog must contain raw packets")
    source_ids: set[str] = set()
    paths: set[str] = set()
    channels: set[str] = set()
    for index, packet in enumerate(packets):
        scope = f"raw_packets[{index}]"
        require_fields(
            packet,
            [
                "source_id",
                "exchange",
                "vendor",
                "symbol",
                "channel",
                "raw_path",
                "sha256",
                "bytes",
                "timestamp_unit",
                "timezone",
                "min_time",
                "max_time",
                "missingness_rate",
                "duplicate_rows",
                "monotonic",
                "clock_skew_ms",
                "freshness_status",
                "last_observed_at_utc",
                "provenance_status",
                "immutable",
            ],
            scope,
        )
        source_id = str(packet["source_id"])
        if source_id in source_ids:
            raise ContractError("DUPLICATE_SOURCE_ID", "source IDs must be unique", {"source_id": source_id})
        source_ids.add(source_id)
        raw_path = str(packet["raw_path"])
        if raw_path in paths:
            raise ContractError("DUPLICATE_RAW_REFERENCE", "raw references must be unique", {"raw_path": raw_path})
        paths.add(raw_path)
        ensure_full_sha256(packet["sha256"], f"{scope}.sha256")
        if int(packet["bytes"]) <= 0 or packet["immutable"] is not True:
            raise ContractError("MUTABLE_OR_EMPTY_RAW_SOURCE", "raw packet must be non-empty and immutable")
        if packet["timestamp_unit"] not in TIMESTAMP_UNITS or packet["timezone"] != "UTC":
            raise ContractError("AMBIGUOUS_TIMESTAMP_SEMANTICS", "timestamp unit and UTC timezone are required")
        minimum = parse_utc(packet["min_time"], f"{scope}.min_time")
        maximum = parse_utc(packet["max_time"], f"{scope}.max_time")
        last_observed = parse_utc(packet["last_observed_at_utc"], f"{scope}.last_observed_at_utc")
        if minimum >= maximum:
            raise ContractError("INVALID_SOURCE_TIME_RANGE", "source min_time must precede max_time")
        if last_observed < minimum or last_observed > maximum:
            raise ContractError("INVALID_SOURCE_FRESHNESS", "last observation must be inside the frozen source range")
        if packet["freshness_status"] not in ("FROZEN_COMPLETE", "STALE", "UNKNOWN"):
            raise ContractError("INVALID_SOURCE_FRESHNESS", "freshness status is not recognized")
        if not 0 <= float(packet["missingness_rate"]) <= 1 or int(packet["duplicate_rows"]) < 0:
            raise ContractError("INVALID_SOURCE_QUALITY_METRIC", "missingness or duplicate count is invalid")
        channels.add(str(packet["channel"]))
    return {
        "status": "CATALOG_VALID",
        "catalog_id": catalog["catalog_id"],
        "catalog_sha256": catalog_sha256(catalog),
        "packet_count": len(packets),
        "channels": sorted(channels),
    }


def derive_readiness(hypothesis: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    validated = validate_catalog(catalog)
    require_fields(hypothesis, ["hypothesis_id", "source_contract"], "hypothesis")
    source = hypothesis["source_contract"]
    require_fields(source, ["required_channels", "source_ids", "minimum_join_coverage_rule"], "hypothesis.source_contract")
    required = {str(item) for item in source["required_channels"]}
    required_sources = {str(item) for item in source["source_ids"]}
    available_sources = {str(packet["source_id"]) for packet in catalog["raw_packets"]}
    source_scoped = [packet for packet in catalog["raw_packets"] if str(packet["source_id"]) in required_sources]
    available = {str(packet["channel"]) for packet in source_scoped}
    matching = [packet for packet in source_scoped if str(packet["channel"]) in required]
    missing = sorted(required - available)
    missing_sources = sorted(required_sources - available_sources)

    provenance_failures = [
        packet["source_id"]
        for packet in matching
        if packet["provenance_status"] != "VERIFIED"
        or packet["timezone"] != "UTC"
        or packet["monotonic"] is not True
        or packet["freshness_status"] != "FROZEN_COMPLETE"
        or float(packet["missingness_rate"]) > float(source.get("maximum_missingness_rate", 0.0))
        or int(packet["duplicate_rows"]) > int(source.get("maximum_duplicate_rows", 0))
        or abs(float(packet["clock_skew_ms"])) > float(packet.get("max_allowed_clock_skew_ms", 1000))
    ]
    if provenance_failures:
        status = "PROVENANCE_BLOCKED"
        reason = "one or more required sources fail provenance, monotonicity, or clock-skew rules"
    elif not matching:
        status = "NO_DATA"
        reason = "all required source/channel bindings are absent"
    elif missing or missing_sources:
        status = "PARTIAL_DATA"
        reason = "one or more required source/channel bindings are absent"
    else:
        rule = source["minimum_join_coverage_rule"]
        coverage = min(float(packet.get("join_coverage", 0.0)) for packet in matching)
        threshold = float(rule["value"])
        passed = coverage >= threshold if rule["operator"] == ">=" else coverage > threshold
        if not passed:
            status = "PARTIAL_DATA"
            reason = "join coverage is below the frozen hypothesis rule"
        else:
            status = "DATA_READY"
            reason = "all required channels and provenance checks pass"
    coverage_values = [float(packet.get("join_coverage", 0.0)) for packet in matching]
    return {
        "schema": "trading_edge.hypothesis_data_readiness.v1",
        "hypothesis_id": hypothesis["hypothesis_id"],
        "status": status,
        "reason": reason,
        "catalog_id": catalog["catalog_id"],
        "catalog_sha256": validated["catalog_sha256"],
        "required_channels": sorted(required),
        "available_channels": sorted(available & required),
        "missing_channels": missing,
        "required_source_ids": sorted(required_sources),
        "available_source_ids": sorted(available_sources & required_sources),
        "missing_source_ids": missing_sources,
        "minimum_join_coverage": min(coverage_values) if coverage_values else 0.0,
        "provenance_failures": sorted(provenance_failures),
        "controller_adjudication": {
            "status": "PENDING",
            "outcome_budget": "DENY",
        },
        "can_trade": False,
        "capital_permission": "DENY",
    }


def verify_preregistration_receipt(
    preregistration: dict[str, Any], receipt: dict[str, Any]
) -> None:
    validate_preregistration(preregistration)
    require_fields(
        receipt,
        ["schema", "hypothesis_id", "canonical_sha256", "content_signature", "content_signature_scheme", "compiler_version"],
        "preregistration_receipt",
    )
    from .preregistration import COMPILER_VERSION

    if receipt["compiler_version"] != COMPILER_VERSION or receipt["content_signature_scheme"] != "SHA256_DOMAIN_SEPARATED_INTEGRITY_V1":
        raise ContractError("INVALID_PREREGISTRATION_RECEIPT_FORMAT", "receipt compiler or integrity scheme is not approved")
    canonical = canonical_bytes(preregistration)
    expected_sha = sha256_bytes(canonical)
    expected_signature = sha256_bytes(b"TRADING_EDGE_PREREG_V1\x00" + canonical)
    if receipt["hypothesis_id"] != preregistration["hypothesis_id"]:
        raise ContractError("PREREGISTRATION_ID_MISMATCH", "receipt hypothesis differs from preregistration")
    if receipt["canonical_sha256"] != expected_sha or receipt["content_signature"] != expected_signature:
        raise ContractError("PREREGISTRATION_SHA_MISMATCH", "preregistration receipt does not bind current bytes")


def authorize_outcome_command(
    catalog: dict[str, Any],
    readiness: dict[str, Any],
    preregistration: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    validated = validate_catalog(catalog)
    verify_preregistration_receipt(preregistration, receipt)
    require_fields(readiness, ["hypothesis_id", "status", "catalog_sha256", "controller_adjudication"], "readiness")
    if readiness["hypothesis_id"] != preregistration["hypothesis_id"]:
        raise ContractError("READINESS_HYPOTHESIS_MISMATCH", "readiness and preregistration IDs differ")
    if readiness["catalog_sha256"] != validated["catalog_sha256"]:
        raise ContractError("SOURCE_CATALOG_MUTATION", "catalog bytes changed after readiness was produced")
    derived = derive_readiness(preregistration, catalog)
    if (
        readiness["status"] != derived["status"]
        or sorted(readiness.get("missing_channels", [])) != derived["missing_channels"]
        or sorted(readiness.get("missing_source_ids", [])) != derived["missing_source_ids"]
    ):
        raise ContractError(
            "READINESS_DERIVATION_MISMATCH",
            "readiness does not match deterministic derivation from the frozen catalog",
        )
    if readiness["status"] not in ALLOWED_READINESS:
        raise ContractError("INVALID_READINESS_STATUS", "unknown readiness status")
    if readiness["status"] != "DATA_READY":
        raise ContractError(
            "OUTCOME_COMMAND_BEFORE_DATA_READY",
            "outcomes are denied until controller-adjudicated DATA_READY",
            {"status": readiness["status"]},
        )
    adjudication = readiness["controller_adjudication"]
    if adjudication.get("status") != "APPROVED" or adjudication.get("outcome_budget") != "ALLOW":
        raise ContractError("CONTROLLER_ADJUDICATION_MISSING", "DATA_READY has not been controller-authorized")
    require_fields(
        adjudication,
        ["status", "controller_id", "generation", "authorized_task_id", "task_sha256", "adjudicated_at_utc", "outcome_budget"],
        "controller_adjudication",
    )
    if adjudication["controller_id"] != "GPT_CONTROLLER" or not re.fullmatch(r"R\d{1,6}", str(adjudication["generation"])):
        raise ContractError("INVALID_CONTROLLER_ADJUDICATION", "controller identity or generation is invalid")
    if not str(adjudication["authorized_task_id"]).strip():
        raise ContractError("INVALID_CONTROLLER_ADJUDICATION", "authorized task ID is empty")
    if adjudication["authorized_task_id"] == "TRADING_EDGE_RESEARCH_ENGINE_M2A":
        raise ContractError("M2A_OUTCOME_FORBIDDEN", "M2A may not authorize its own outcome computation")
    ensure_full_sha256(adjudication["task_sha256"], "controller_adjudication.task_sha256")
    parse_utc(adjudication["adjudicated_at_utc"], "controller_adjudication.adjudicated_at_utc")
    return {
        "status": "OUTCOME_COMMAND_AUTHORIZED",
        "hypothesis_id": preregistration["hypothesis_id"],
        "catalog_sha256": validated["catalog_sha256"],
        "authorized_task_id": adjudication["authorized_task_id"],
        "can_trade": False,
        "capital_permission": "DENY",
    }
