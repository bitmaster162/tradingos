from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any

AGENT_CONSUMER_CONTRACT_VERSION = "visionassist.agent_consumer_contract.v1"
VISUAL_EVIDENCE_SCHEMA = "tradingos.visual_market_evidence.v1"
VISUAL_EVIDENCE_SOURCE_SCHEMA = "visionassist.market_observation.v1"
FRESHNESS_POLICY_ID = "VISIONASSIST_SHADOW_CAPTURE_AGE_300S_V1"
FRESHNESS_MAX_AGE_SECONDS = 300
FUTURE_EVIDENCE_TOLERANCE_SECONDS = 0
PRODUCER_GOLDEN_EVIDENCE_SHA256 = "4ae5446d6faa50760a04d14c665174ca9ce59e2ca91ed7d36fe8c36fba30486f"

TOP_LEVEL_KEYS = (
    "schema", "source_id", "source_schema", "source_sha256", "image_sha256",
    "captured_at", "symbol", "venue", "timeframe", "quality",
    "visible_observations", "detector_summary", "counterevidence", "uncertainties",
    "alternative_explanations", "compact_digest", "safety", "evidence_sha256",
    "trade_case_ref",
)
EVIDENCE_BODY_KEYS = tuple(k for k in TOP_LEVEL_KEYS if k not in {"evidence_sha256", "trade_case_ref"})
SAFETY_KEYS = ("mode", "execution_authority", "can_trade", "capital_permission", "orders_allowed", "signals_allowed")
TRADE_CASE_REF_KEYS = ("source_id", "sha256", "schema")
QUALITY_KEYS = ("status", "reasons", "abstention_reason")
OBSERVATION_KEYS = ("id", "description", "evidence_refs")
DETECTOR_KEYS = (
    "detector_type", "status", "orientation", "confidence", "evidence_refs",
    "counterevidence_refs", "invalidation_conditions",
)
DETECTOR_TYPES = ("SFP", "CHOCH", "BOS", "SWEEP_RECLAIM")
DETECTOR_TYPE_SET = frozenset(DETECTOR_TYPES)
DETECTOR_STATUSES = frozenset({"SUPPORTED", "CANDIDATE", "UNKNOWN", "REJECTED"})
DETECTOR_ORIENTATIONS = frozenset({"UPSIDE", "DOWNSIDE", "NEUTRAL", "UNKNOWN"})
QUALITY_STATUSES = frozenset({"PASS", "REVISE", "REJECT", "ABSTAIN"})
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
RFC3339_TZ_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|([+-])(\d{2}):(\d{2}))$"
)
JS_SAFE_INTEGER = (1 << 53) - 1


class VisionAssistConsumerContractError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _deny(condition: bool, code: str) -> None:
    if not condition:
        raise VisionAssistConsumerContractError(code)


def _exact_dict(value: Any, keys: tuple[str, ...], path: str) -> dict[str, Any]:
    _deny(type(value) is dict, f"{path}_not_object")
    _deny(set(value) == set(keys), f"{path}_keys_mismatch")
    return value


def _nonempty(value: Any, path: str) -> str:
    _deny(type(value) is str and bool(value.strip()), f"{path}_invalid")
    return value


def _string_array(value: Any, path: str, *, min_items: int = 0) -> list[str]:
    _deny(type(value) is list and len(value) >= min_items, f"{path}_invalid")
    for item in value:
        _nonempty(item, path)
    return value


def _json_string(value: str) -> str:
    _deny(not any(0xD800 <= ord(ch) <= 0xDFFF for ch in value), "unsupported_unicode_surrogate")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _canonical_number(value: int | float) -> str:
    _deny(type(value) is not bool, "unsupported_numeric_boolean")
    if type(value) is int:
        _deny(abs(value) <= JS_SAFE_INTEGER, "unsupported_numeric_safe_integer")
        return str(value)
    _deny(type(value) is float and math.isfinite(value), "unsupported_numeric_nonfinite")
    _deny(not (value == 0.0 and math.copysign(1.0, value) < 0), "unsupported_numeric_negative_zero")
    if value.is_integer():
        integer = int(value)
        _deny(abs(integer) <= JS_SAFE_INTEGER, "unsupported_numeric_safe_integer")
        return str(integer)
    text = format(value, ".15g")
    _deny("e" not in text.lower(), "unsupported_numeric_exponent")
    _deny(float(text) == value, "unsupported_numeric_precision")
    return text


def canonicalize_ecmascript_compatible(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is str:
        return _json_string(value)
    if type(value) in (int, float):
        return _canonical_number(value)
    if type(value) is list:
        return "[" + ",".join(canonicalize_ecmascript_compatible(item) for item in value) + "]"
    if type(value) is dict:
        _deny(all(type(k) is str and k.isascii() for k in value), "unsupported_object_key")
        return "{" + ",".join(
            f"{_json_string(key)}:{canonicalize_ecmascript_compatible(value[key])}"
            for key in sorted(value)
        ) + "}"
    raise VisionAssistConsumerContractError("unsupported_json_type")


def sha256_canonical_object(value: Any) -> str:
    return hashlib.sha256(canonicalize_ecmascript_compatible(value).encode("utf-8")).hexdigest()


def _timestamp_ns(value: Any, path: str) -> int:
    text = _nonempty(value, path)
    match = RFC3339_TZ_RE.fullmatch(text)
    _deny(match is not None, f"{path}_not_rfc3339_tz")
    year, month, day, hour, minute, second = map(int, match.group(1, 2, 3, 4, 5, 6))
    fraction = match.group(7) or ""
    zone = match.group(8)
    sign = match.group(9)
    offset_hour = int(match.group(10) or 0)
    offset_minute = int(match.group(11) or 0)
    _deny(1 <= year <= 9999, f"{path}_year_invalid")
    _deny(1 <= month <= 12, f"{path}_month_invalid")
    _deny(0 <= hour <= 23, f"{path}_hour_invalid")
    _deny(0 <= minute <= 59, f"{path}_minute_invalid")
    _deny(0 <= second <= 59, f"{path}_second_invalid")
    _deny(offset_hour <= 23 and offset_minute <= 59, f"{path}_offset_invalid")
    try:
        local = datetime(year, month, day, hour, minute, second)
    except ValueError as exc:
        raise VisionAssistConsumerContractError(f"{path}_calendar_invalid") from exc
    if zone == "Z":
        offset_seconds = 0
    else:
        direction = 1 if sign == "+" else -1
        offset_seconds = direction * (offset_hour * 3600 + offset_minute * 60)

    # Compute the UTC instant arithmetically instead of shifting ``datetime``.
    # A valid RFC3339 local timestamp at year 0001/9999 with an explicit offset
    # can map just outside Python datetime's representable UTC year range while
    # remaining a valid ECMAScript Date instant. Integer epoch arithmetic keeps
    # that producer-valid boundary deterministic and avoids raw OverflowError.
    epoch_ordinal = datetime(1970, 1, 1).toordinal()
    local_seconds = hour * 3600 + minute * 60 + second
    whole_seconds = (local.toordinal() - epoch_ordinal) * 86400 + local_seconds - offset_seconds
    fraction_ns = int(fraction.ljust(9, "0")) if fraction else 0
    return whole_seconds * 1_000_000_000 + fraction_ns


def _validate_quality(value: Any) -> None:
    quality = _exact_dict(value, QUALITY_KEYS, "quality")
    _deny(type(quality["status"]) is str and quality["status"] in QUALITY_STATUSES, "quality_status_invalid")
    _string_array(quality["reasons"], "quality_reasons")
    _deny(quality["abstention_reason"] is None or (type(quality["abstention_reason"]) is str and bool(quality["abstention_reason"].strip())), "quality_abstention_reason_invalid")


def _validate_observations(value: Any) -> None:
    _deny(type(value) is list and len(value) > 0, "visible_observations_invalid")
    for index, item in enumerate(value):
        item = _exact_dict(item, OBSERVATION_KEYS, f"visible_observations_{index}")
        _nonempty(item["id"], f"visible_observations_{index}_id")
        _nonempty(item["description"], f"visible_observations_{index}_description")
        _string_array(item["evidence_refs"], f"visible_observations_{index}_evidence_refs", min_items=1)


def _validate_detectors(value: Any) -> None:
    _deny(type(value) is list and len(value) == len(DETECTOR_TYPES), "detector_summary_count_invalid")
    seen: set[str] = set()
    for index, item in enumerate(value):
        item = _exact_dict(item, DETECTOR_KEYS, f"detector_summary_{index}")
        detector_type = item["detector_type"]
        _deny(type(detector_type) is str and detector_type in DETECTOR_TYPE_SET and detector_type not in seen, f"detector_summary_{index}_type_invalid")
        seen.add(detector_type)
        _deny(type(item["status"]) is str and item["status"] in DETECTOR_STATUSES, f"detector_summary_{index}_status_invalid")
        _deny(type(item["orientation"]) is str and item["orientation"] in DETECTOR_ORIENTATIONS, f"detector_summary_{index}_orientation_invalid")
        confidence = item["confidence"]
        if confidence is not None:
            valid_confidence = (
                (type(confidence) is int and 0 <= confidence <= 1)
                or (type(confidence) is float and math.isfinite(confidence) and 0 <= confidence <= 1)
            )
            _deny(valid_confidence, f"detector_summary_{index}_confidence_invalid")
        _string_array(item["evidence_refs"], f"detector_summary_{index}_evidence_refs")
        _string_array(item["counterevidence_refs"], f"detector_summary_{index}_counterevidence_refs")
        _string_array(item["invalidation_conditions"], f"detector_summary_{index}_invalidation_conditions")
    _deny(seen == DETECTOR_TYPE_SET, "detector_summary_types_incomplete")


def _validate_safety(value: Any) -> None:
    safety = _exact_dict(value, SAFETY_KEYS, "safety")
    _deny(safety["mode"] == "SHADOW", "unsafe_mode")
    _deny(safety["execution_authority"] == "NONE", "unsafe_execution_authority")
    _deny(safety["can_trade"] is False, "unsafe_can_trade")
    _deny(safety["capital_permission"] == "DENY", "unsafe_capital_permission")
    _deny(safety["orders_allowed"] is False, "unsafe_orders_allowed")
    _deny(safety["signals_allowed"] is False, "unsafe_signals_allowed")


def _evidence_body(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in EVIDENCE_BODY_KEYS}


def validate_visionassist_consumer_evidence(
    record: Any,
    *,
    as_of: str,
    expected_source_id: str,
    producer_contract_version: str = AGENT_CONSUMER_CONTRACT_VERSION,
    freshness_policy_id: str = FRESHNESS_POLICY_ID,
) -> dict[str, Any]:
    _deny(producer_contract_version == AGENT_CONSUMER_CONTRACT_VERSION, "producer_contract_version_mismatch")
    _deny(freshness_policy_id == FRESHNESS_POLICY_ID, "freshness_policy_mismatch")
    record = _exact_dict(record, TOP_LEVEL_KEYS, "record")
    _deny(record["schema"] == VISUAL_EVIDENCE_SCHEMA, "wrong_schema")
    _deny(record["source_schema"] == VISUAL_EVIDENCE_SOURCE_SCHEMA, "wrong_source_schema")
    source_id = _nonempty(record["source_id"], "source_id")
    _deny(source_id == _nonempty(expected_source_id, "expected_source_id"), "source_id_mismatch")
    _deny(type(record["source_sha256"]) is str and SHA256_RE.fullmatch(record["source_sha256"]) is not None, "source_sha256_invalid")
    _deny(type(record["image_sha256"]) is str and SHA256_RE.fullmatch(record["image_sha256"]) is not None, "image_sha256_invalid")
    captured_ns = _timestamp_ns(record["captured_at"], "captured_at")
    as_of_ns = _timestamp_ns(as_of, "as_of")
    for field in ("symbol", "venue", "timeframe", "compact_digest"):
        _nonempty(record[field], field)
    _validate_quality(record["quality"])
    _validate_observations(record["visible_observations"])
    _validate_detectors(record["detector_summary"])
    _string_array(record["counterevidence"], "counterevidence")
    _string_array(record["uncertainties"], "uncertainties", min_items=1)
    _string_array(record["alternative_explanations"], "alternative_explanations", min_items=1)
    _validate_safety(record["safety"])

    _deny(type(record["evidence_sha256"]) is str and SHA256_RE.fullmatch(record["evidence_sha256"]) is not None, "evidence_sha256_invalid")
    recomputed = sha256_canonical_object(_evidence_body(record))
    _deny(recomputed == record["evidence_sha256"], "evidence_sha256_mismatch")

    ref = _exact_dict(record["trade_case_ref"], TRADE_CASE_REF_KEYS, "trade_case_ref")
    _deny(ref["source_id"] == source_id, "trade_case_ref_source_mismatch")
    _deny(ref["sha256"] == record["evidence_sha256"], "trade_case_ref_sha_mismatch")
    _deny(ref["schema"] == record["schema"], "trade_case_ref_schema_mismatch")

    _deny(captured_ns <= as_of_ns + FUTURE_EVIDENCE_TOLERANCE_SECONDS * 1_000_000_000, "future_evidence")
    age_ns = as_of_ns - captured_ns
    _deny(age_ns <= FRESHNESS_MAX_AGE_SECONDS * 1_000_000_000, "stale_evidence")

    return {
        "valid": True,
        "contract_version": AGENT_CONSUMER_CONTRACT_VERSION,
        "schema": record["schema"],
        "source_id": source_id,
        "evidence_sha256": record["evidence_sha256"],
        "observed_at": record["captured_at"],
        "as_of": as_of,
        "freshness_policy_id": FRESHNESS_POLICY_ID,
        "freshness_status": "FRESH",
        "counterevidence": tuple(record["counterevidence"]),
        "uncertainties": tuple(record["uncertainties"]),
        "alternative_explanations": tuple(record["alternative_explanations"]),
        "detector_invalidation_conditions": tuple(
            (item["detector_type"], tuple(item["invalidation_conditions"]))
            for item in record["detector_summary"]
        ),
        "detector_confidence_as_trading_probability": False,
        "decision_authority": "NONE",
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "orders_allowed": False,
        "signals_allowed": False,
    }
