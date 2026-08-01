"""Deterministic preregistration validation and compilation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    ContractError,
    atomic_write_bytes,
    atomic_write_json,
    canonical_bytes,
    parse_utc,
    require_fields,
    sha256_bytes,
    sha256_file,
)


COMPILER_VERSION = "TRADING_EDGE_PREREG_COMPILER_M2A_V1"
MUTABLE_MARKERS = (
    "REQUIRED",
    "TBD",
    "TO_BE_DECIDED",
    "CHOOSE_LATER",
    "OPTIMIZE_ON_FINAL",
    "LATEST_AVAILABLE",
    "DYNAMIC_THRESHOLD",
    "AFTER_FINAL_TEST",
    "POST_FINAL_TEST",
    "FINAL_TEST_READ",
    "SELECTED_AFTER_FINAL",
)


def _reject_mutable_markers(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_mutable_markers(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_mutable_markers(child, f"{path}[{index}]")
    elif isinstance(value, str):
        upper = "_".join(value.upper().replace("-", " ").split())
        marker = next(
            (
                item
                for item in MUTABLE_MARKERS
                if (
                    upper == item
                    or upper.startswith(f"{item}_")
                    or (item not in ("REQUIRED", "TBD") and item in upper)
                )
            ),
            None,
        )
        if marker:
            raise ContractError(
                "MUTABLE_OR_PLACEHOLDER_RULE",
                "preregistration contains a mutable or placeholder rule",
                {"path": path, "marker": marker},
            )


def _interval(value: Any, name: str) -> tuple[Any, Any]:
    if not isinstance(value, dict):
        raise ContractError("INVALID_DATA_SPLIT", f"{name} must be an object")
    require_fields(value, ["start", "end"], f"data_split.{name}")
    start = parse_utc(value["start"], f"data_split.{name}.start")
    end = parse_utc(value["end"], f"data_split.{name}.end")
    if start >= end:
        raise ContractError("INVALID_DATA_SPLIT", f"{name} start must precede end")
    return start, end


def validate_preregistration(document: dict[str, Any]) -> dict[str, Any]:
    require_fields(
        document,
        [
            "schema",
            "hypothesis_id",
            "version",
            "status",
            "frozen_at_utc",
            "claim",
            "material_difference_from_prior_work",
            "source_contract",
            "event_contract",
            "data_split",
            "cost_model",
            "statistical_plan",
            "decision_rule",
            "effect_ceiling",
        ],
        "preregistration",
    )
    if document["status"] != "PREREGISTERED_BEFORE_OUTCOMES":
        raise ContractError("INVALID_PREREG_STATUS", "status must freeze before outcomes")
    parse_utc(document["frozen_at_utc"], "frozen_at_utc")
    _reject_mutable_markers(document)

    source = document["source_contract"]
    require_fields(
        source,
        [
            "required_channels",
            "source_ids",
            "timestamp_semantics",
            "timezone",
            "join_keys",
            "minimum_join_coverage_rule",
            "missingness_policy",
        ],
        "source_contract",
    )
    for field in ("required_channels", "source_ids", "join_keys"):
        if not isinstance(source[field], list) or not source[field]:
            raise ContractError("INCOMPLETE_SOURCE_CONTRACT", f"source_contract.{field} must be non-empty")
    if source["timezone"] != "UTC" or not isinstance(source["timestamp_semantics"], dict):
        raise ContractError("AMBIGUOUS_TIMESTAMP_CONTRACT", "source timestamps must be explicit UTC semantics")
    coverage = source["minimum_join_coverage_rule"]
    require_fields(coverage, ["metric", "operator", "value"], "minimum_join_coverage_rule")
    if coverage["operator"] not in (">=", ">") or not 0 <= float(coverage["value"]) <= 1:
        raise ContractError("INVALID_JOIN_COVERAGE_RULE", "join coverage rule is invalid")

    event = document["event_contract"]
    require_fields(
        event,
        [
            "event_definition",
            "event_independence_rule",
            "cluster_dedupe_rule",
            "entry_time",
            "invalidation",
            "holding_horizons_seconds",
            "exclusions",
        ],
        "event_contract",
    )
    horizons = event["holding_horizons_seconds"]
    if not isinstance(horizons, list) or not horizons or any(int(value) <= 0 for value in horizons):
        raise ContractError("INVALID_HOLDING_HORIZON", "holding horizons must be positive seconds")

    split = document["data_split"]
    require_fields(split, ["train", "validation", "final_test", "purge_embargo", "walk_forward"], "data_split")
    train = _interval(split["train"], "train")
    validation = _interval(split["validation"], "validation")
    final_test = _interval(split["final_test"], "final_test")
    if not (train[1] < validation[0] and validation[1] < final_test[0]):
        raise ContractError("CONTAMINATED_OOS_INTERVAL", "train, validation, and final test must be strictly ordered and disjoint")
    purge = split["purge_embargo"]
    require_fields(purge, ["purge_seconds", "embargo_seconds"], "purge_embargo")
    if int(purge["purge_seconds"]) < max(int(value) for value in horizons) or int(purge["embargo_seconds"]) < 0:
        raise ContractError("INSUFFICIENT_PURGE_EMBARGO", "purge must cover the longest holding horizon")
    walk = split["walk_forward"]
    require_fields(walk, ["mode", "minimum_train_seconds", "step_seconds"], "walk_forward")
    if walk["mode"] not in ("ANCHORED", "ROLLING") or int(walk["minimum_train_seconds"]) <= 0 or int(walk["step_seconds"]) <= 0:
        raise ContractError("INVALID_WALK_FORWARD", "walk-forward contract is invalid")

    costs = document["cost_model"]
    require_fields(
        costs,
        ["fees_bps", "spread_bps", "slippage_bps", "funding_bps", "latency_ms", "adverse_multiplier"],
        "cost_model",
    )
    if any(float(costs[field]) < 0 for field in ("fees_bps", "spread_bps", "slippage_bps", "funding_bps", "latency_ms")):
        raise ContractError("MISSING_OR_NEGATIVE_COST", "all cost and latency values must be non-negative")
    if float(costs["fees_bps"]) + float(costs["spread_bps"]) + float(costs["slippage_bps"]) <= 0:
        raise ContractError("MISSING_ECONOMIC_COSTS", "fees, spread, and slippage cannot all be zero")
    if float(costs["adverse_multiplier"]) < 1:
        raise ContractError("INVALID_ADVERSE_COST", "adverse cost multiplier must be at least one")

    stats = document["statistical_plan"]
    require_fields(
        stats,
        [
            "primary_metric",
            "secondary_metrics",
            "power_or_sequential_evidence_plan",
            "bootstrap_or_resampling_plan",
            "multiple_testing_family",
            "correction",
            "placebo_tests",
            "ablation_tests",
        ],
        "statistical_plan",
    )
    if stats["primary_metric"] != "POST_COST_OOS_EXPECTANCY":
        raise ContractError("INVALID_PRIMARY_METRIC", "primary metric must be post-cost OOS expectancy")
    if str(stats["correction"]).upper() not in ("HOLM", "BONFERRONI"):
        raise ContractError("INVALID_MULTIPLE_TESTING_CORRECTION", "Holm or a more conservative correction is required")
    if not str(stats["multiple_testing_family"]).strip():
        raise ContractError("MISSING_MULTIPLE_TESTING_FAMILY", "testing family must be frozen")
    for field in ("placebo_tests", "ablation_tests", "secondary_metrics"):
        if not isinstance(stats[field], list):
            raise ContractError("INVALID_STATISTICAL_PLAN", f"{field} must be a list")

    decision = document["decision_rule"]
    for terminal in ("KEEP_FOR_FORWARD_PAPER", "KILL", "INSUFFICIENT_DATA", "INVALID_RESEARCH_RETURN"):
        if terminal not in decision or not isinstance(decision[terminal], list) or not decision[terminal]:
            raise ContractError("INCOMPLETE_DECISION_RULE", f"decision rule lacks {terminal}")

    ceiling = document["effect_ceiling"]
    if ceiling.get("can_trade") is not False or ceiling.get("capital_permission") != "DENY" or ceiling.get("deploy_permission") != "DENY":
        raise ContractError("UNSAFE_EFFECT_CEILING", "effect ceiling must deny trading, capital, and deployment")

    return {
        "status": "VALID",
        "hypothesis_id": document["hypothesis_id"],
        "train": [train[0].isoformat(), train[1].isoformat()],
        "validation": [validation[0].isoformat(), validation[1].isoformat()],
        "final_test": [final_test[0].isoformat(), final_test[1].isoformat()],
    }


def compile_preregistration(source_path: Path, output_dir: Path) -> dict[str, Any]:
    raw = source_path.read_bytes()
    import json

    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("INVALID_JSON_INPUT", "preregistration is not valid UTF-8 JSON") from exc
    validation = validate_preregistration(document)
    canonical = canonical_bytes(document)
    canonical_sha = sha256_bytes(canonical)
    signature = sha256_bytes(b"TRADING_EDGE_PREREG_V1\x00" + canonical)
    receipt = {
        "schema": "trading_edge.preregistration_receipt.v1",
        "compiler_version": COMPILER_VERSION,
        "hypothesis_id": document["hypothesis_id"],
        "version": document["version"],
        "frozen_at_utc": document["frozen_at_utc"],
        "input_sha256": sha256_bytes(raw),
        "canonical_sha256": canonical_sha,
        "content_signature_scheme": "SHA256_DOMAIN_SEPARATED_INTEGRITY_V1",
        "content_signature": signature,
        "validation": validation,
        "outcome_commands_locked_until_controller_data_ready": True,
        "can_trade": False,
        "capital_permission": "DENY",
        "deploy_permission": "DENY",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = output_dir / "PREREGISTRATION_CANONICAL.json"
    receipt_path = output_dir / "PREREGISTRATION_RECEIPT.json"
    atomic_write_bytes(canonical_path, canonical)
    atomic_write_json(receipt_path, receipt)
    return {
        "status": "PREREGISTRATION_FROZEN",
        "canonical_path": str(canonical_path),
        "receipt_path": str(receipt_path),
        "canonical_sha256": canonical_sha,
        "receipt_sha256": sha256_file(receipt_path),
    }
