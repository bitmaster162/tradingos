"""Machine-readable duplicate and killed-family detector."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .common import ContractError, require_fields, sha256_bytes


CLASSES = (
    "MATERIAL_DUPLICATE",
    "RENAMED_KILLED_FAMILY",
    "PARTIAL_OVERLAP",
    "MATERIALLY_DISTINCT",
    "INSUFFICIENT_EVIDENCE",
)

_STOP = {
    "after",
    "and",
    "before",
    "combined",
    "event",
    "events",
    "from",
    "market",
    "over",
    "price",
    "subsequent",
    "that",
    "the",
    "then",
    "with",
}


def normalize_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def tokenize(value: Any) -> set[str]:
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[a-z0-9]+", normalized.lower())
    return {word for word in words if len(word) >= 3 and word not in _STOP}


def _set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {normalize_identifier(item) for item in value if normalize_identifier(item)}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def fingerprint(record: dict[str, Any]) -> dict[str, Any]:
    require_fields(record, ["id", "family", "claim", "causal_signature", "required_channels"], "hypothesis")
    signature = _set(record["causal_signature"])
    channels = _set(record["required_channels"])
    if not signature or not channels or not str(record["claim"]).strip():
        raise ContractError("INSUFFICIENT_DUPLICATE_EVIDENCE", "hypothesis fingerprint fields are empty")
    tokens = tokenize(record["claim"]) | tokenize(record["family"]) | signature
    material = "|".join(
        [
            normalize_identifier(record["id"]),
            normalize_identifier(record["family"]),
            ",".join(sorted(signature)),
            ",".join(sorted(channels)),
            ",".join(sorted(tokens)),
        ]
    ).encode("utf-8")
    return {
        "id": normalize_identifier(record["id"]),
        "family": normalize_identifier(record["family"]),
        "signature": signature,
        "channels": channels,
        "tokens": tokens,
        "sha256": sha256_bytes(material),
    }


def compare(candidate: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    try:
        left = fingerprint(candidate)
        right = fingerprint(existing)
    except ContractError as exc:
        return {
            "classification": "INSUFFICIENT_EVIDENCE",
            "reason": exc.code,
            "existing_id": existing.get("id"),
        }

    signature_similarity = jaccard(left["signature"], right["signature"])
    channel_similarity = jaccard(left["channels"], right["channels"])
    token_similarity = jaccard(left["tokens"], right["tokens"])
    same_family = left["family"] == right["family"]
    same_id = left["id"] == right["id"]
    existing_killed = str(existing.get("terminal", "")).upper() == "KILL"

    if existing_killed and not same_id and same_family and signature_similarity >= 0.60:
        classification = "RENAMED_KILLED_FAMILY"
        reason = "killed family has the same causal mechanism under a different identifier"
    elif same_id or signature_similarity >= 0.90 or (
        same_family and signature_similarity >= 0.75 and channel_similarity >= 0.50
    ):
        classification = "MATERIAL_DUPLICATE"
        reason = "identifier or causal signature is materially the same"
    elif same_family or signature_similarity >= 0.35 or (
        channel_similarity >= 0.50 and token_similarity >= 0.25
    ):
        classification = "PARTIAL_OVERLAP"
        reason = "family, mechanism, or required data partially overlaps"
    else:
        classification = "MATERIALLY_DISTINCT"
        reason = "no material mechanism or data-contract overlap"

    return {
        "classification": classification,
        "reason": reason,
        "existing_id": existing.get("id"),
        "existing_terminal": existing.get("terminal"),
        "same_identifier": same_id,
        "same_family": same_family,
        "signature_similarity": signature_similarity,
        "channel_similarity": channel_similarity,
        "token_similarity": token_similarity,
        "candidate_fingerprint": left["sha256"],
        "existing_fingerprint": right["sha256"],
    }


def compare_registry(candidate: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    records = registry.get("hypotheses")
    if not isinstance(records, list) or not records:
        return {
            "classification": "INSUFFICIENT_EVIDENCE",
            "reason": "registry has no structured hypotheses",
            "comparisons": [],
        }
    comparisons = [compare(candidate, record) for record in records]
    priority = {
        "RENAMED_KILLED_FAMILY": 0,
        "MATERIAL_DUPLICATE": 1,
        "PARTIAL_OVERLAP": 2,
        "MATERIALLY_DISTINCT": 3,
        "INSUFFICIENT_EVIDENCE": 4,
    }
    winner = sorted(comparisons, key=lambda item: (priority[item["classification"]], str(item.get("existing_id"))))[0]
    return {
        "classification": winner["classification"],
        "reason": winner["reason"],
        "matched_existing_id": winner.get("existing_id"),
        "comparisons": comparisons,
        "allowed_classes": list(CLASSES),
    }
