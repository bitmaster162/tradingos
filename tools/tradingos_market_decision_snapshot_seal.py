#!/usr/bin/env python3
"""TradingOS R77.2 — verified provenance seal for Decision Brief snapshots.

The seal does not trust a supplied R77 result. It reconstructs the exact canonical
R77 Market Decision Bridge result from raw capture + Watchtower + Market Radar,
requires exact equality with the supplied result, and only then emits a provenance-
sealed Decision Brief snapshot.

No network, credentials, AI inference, signals, orders, execution, or capital effects.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

SCHEMA = "tradingos.market_decision_snapshot_seal.v2"
VERSION = "2.0.0"
PRODUCER = "tools/tradingos_market_decision_snapshot_seal.py"
BRIDGE_PRODUCER = "tools/tradingos_market_decision_bridge.py"

BRIDGE_PATH = Path(__file__).with_name("tradingos_market_decision_bridge.py")
_SPEC = importlib.util.spec_from_file_location("_tradingos_r77_bridge_for_verified_seal", BRIDGE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load R77 bridge: {BRIDGE_PATH}")
bridge = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bridge)

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_BINDING_KEYS = {
    "watchtower_capture_sha256",
    "watchtower_report_sha256",
    "watchtower_producer_sha256",
    "radar_report_sha256",
    "liquidity_report_sha256",
    "liquidity_capture_sha256",
    "liquidity_producer_sha256",
}
EXPECTED_SOURCE_KINDS = {"ohlcv", "open_interest", "funding", "spot_flow"}

SEAL_SAFETY = {
    "read_only": True,
    "network_fetch": False,
    "ai_generated_market_facts": False,
    "signals_allowed": False,
    "orders_allowed": False,
    "execution_authority": "NONE",
    "can_trade": False,
    "capital_permission": "DENY",
    "confers_authority": False,
}


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


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field}: invalid sha256")
    return value


def _require_exact_source_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("producer") != BRIDGE_PRODUCER:
        raise ValueError("R77 snapshot producer mismatch")
    sources = provenance.get("sources")
    if not isinstance(sources, list) or len(sources) != len(EXPECTED_SOURCE_KINDS):
        raise ValueError("R77 snapshot source list mismatch")
    kinds = [row.get("kind") for row in sources if isinstance(row, dict)]
    if len(kinds) != len(sources) or set(kinds) != EXPECTED_SOURCE_KINDS:
        raise ValueError("R77 snapshot source kind mismatch")
    source_ids: list[str] = []
    for i, row in enumerate(sources):
        source_id = row.get("source_id")
        observed_at = row.get("observed_at")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError(f"R77 source[{i}] source_id invalid")
        if not isinstance(observed_at, str) or not observed_at:
            raise ValueError(f"R77 source[{i}] observed_at invalid")
        source_ids.append(source_id)
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("R77 source_id reuse")
    return sources


def reconstruct_verified_r77(
    capture: dict[str, Any],
    watchtower: dict[str, Any],
    radar: dict[str, Any],
    supplied_result: dict[str, Any],
) -> dict[str, Any]:
    """Reconstruct R77 from primary upstream artifacts and require exact supplied equality."""
    if not isinstance(supplied_result, dict):
        raise ValueError("supplied R77 result must be object")

    canonical = bridge.build_bridge(capture, watchtower, radar)
    bridge.validate_bridge(canonical)
    bridge.validate_bridge(supplied_result)

    if supplied_result != canonical:
        raise ValueError("supplied R77 result does not match deterministic reconstruction")
    if stable_sha256(supplied_result) != stable_sha256(canonical):
        raise ValueError("supplied R77 result digest mismatch")

    snapshot = canonical.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("can_trade") is not False:
        raise ValueError("unsafe/missing R77 snapshot")
    _require_exact_source_rows(snapshot)

    binding = canonical.get("input_binding")
    if not isinstance(binding, dict) or set(binding) != EXPECTED_BINDING_KEYS:
        raise ValueError("R77 input_binding key set mismatch")
    for key in sorted(EXPECTED_BINDING_KEYS):
        require_sha256(binding.get(key), f"R77.input_binding.{key}")

    digest = require_sha256(canonical.get("snapshot_sha256"), "R77.snapshot_sha256")
    if digest != stable_sha256(snapshot):
        raise ValueError("R77 snapshot digest mismatch")
    return canonical


def seal_snapshot(
    capture: dict[str, Any],
    watchtower: dict[str, Any],
    radar: dict[str, Any],
    supplied_result: dict[str, Any],
) -> dict[str, Any]:
    canonical = reconstruct_verified_r77(capture, watchtower, radar, supplied_result)
    source_snapshot = canonical["snapshot"]

    sealed = deepcopy(source_snapshot)
    sealed["provenance"] = {
        "producer": PRODUCER,
        "producer_sha256": file_sha256(Path(__file__)),
        "verification": {
            "method": "RECONSTRUCT_R77_FROM_CAPTURE_WATCHTOWER_RADAR_AND_REQUIRE_EXACT_EQUALITY",
            "verified": True,
        },
        "source_bridge": {
            "producer": BRIDGE_PRODUCER,
            "producer_sha256": file_sha256(BRIDGE_PATH),
            "source_snapshot_sha256": stable_sha256(source_snapshot),
            "source_bridge_result_sha256": stable_sha256(canonical),
        },
        "upstream_binding": deepcopy(canonical["input_binding"]),
        "sources": deepcopy(source_snapshot["provenance"]["sources"]),
    }
    sealed["can_trade"] = False
    validate_sealed_snapshot(sealed, capture, watchtower, radar, supplied_result)
    return sealed


def validate_sealed_snapshot(
    sealed: Any,
    capture: dict[str, Any],
    watchtower: dict[str, Any],
    radar: dict[str, Any],
    supplied_result: dict[str, Any],
) -> None:
    canonical = reconstruct_verified_r77(capture, watchtower, radar, supplied_result)
    source_snapshot = canonical["snapshot"]

    if not isinstance(sealed, dict):
        raise ValueError("sealed snapshot must be object")
    if sealed.get("can_trade") is not False:
        raise ValueError("sealed snapshot can_trade drift")

    provenance = sealed.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("producer") != PRODUCER:
        raise ValueError("sealed snapshot producer mismatch")
    if require_sha256(
        provenance.get("producer_sha256"), "sealed.provenance.producer_sha256"
    ) != file_sha256(Path(__file__)):
        raise ValueError("sealed producer sha256 mismatch")

    verification = provenance.get("verification")
    if verification != {
        "method": "RECONSTRUCT_R77_FROM_CAPTURE_WATCHTOWER_RADAR_AND_REQUIRE_EXACT_EQUALITY",
        "verified": True,
    }:
        raise ValueError("sealed verification contract mismatch")

    source_bridge = provenance.get("source_bridge")
    expected_source_bridge = {
        "producer": BRIDGE_PRODUCER,
        "producer_sha256": file_sha256(BRIDGE_PATH),
        "source_snapshot_sha256": stable_sha256(source_snapshot),
        "source_bridge_result_sha256": stable_sha256(canonical),
    }
    if source_bridge != expected_source_bridge:
        raise ValueError("sealed source_bridge binding mismatch")

    binding = provenance.get("upstream_binding")
    if binding != canonical["input_binding"]:
        raise ValueError("sealed upstream binding mismatch")
    if set(binding) != EXPECTED_BINDING_KEYS:
        raise ValueError("sealed upstream binding key set mismatch")
    for key in sorted(EXPECTED_BINDING_KEYS):
        require_sha256(binding.get(key), f"sealed.upstream_binding.{key}")

    if provenance.get("sources") != source_snapshot["provenance"]["sources"]:
        raise ValueError("sealed sources drift")

    semantic_copy = deepcopy(sealed)
    semantic_copy["provenance"] = deepcopy(source_snapshot["provenance"])
    if semantic_copy != source_snapshot:
        raise ValueError("seal changed market snapshot semantics")


def build_envelope(
    capture: dict[str, Any],
    watchtower: dict[str, Any],
    radar: dict[str, Any],
    supplied_result: dict[str, Any],
) -> dict[str, Any]:
    canonical = reconstruct_verified_r77(capture, watchtower, radar, supplied_result)
    sealed = seal_snapshot(capture, watchtower, radar, supplied_result)
    envelope = {
        "schema": SCHEMA,
        "version": VERSION,
        "sealed_snapshot": sealed,
        "sealed_snapshot_sha256": stable_sha256(sealed),
        "verified_source_bridge_result_sha256": stable_sha256(canonical),
        "verification_inputs": {
            "capture_sha256": stable_sha256(capture),
            "watchtower_sha256": stable_sha256(watchtower),
            "radar_sha256": stable_sha256(radar),
        },
        "safety": dict(SEAL_SAFETY),
    }
    validate_envelope(envelope, capture, watchtower, radar, supplied_result)
    return envelope


def validate_envelope(
    envelope: Any,
    capture: dict[str, Any],
    watchtower: dict[str, Any],
    radar: dict[str, Any],
    supplied_result: dict[str, Any],
) -> None:
    canonical = reconstruct_verified_r77(capture, watchtower, radar, supplied_result)
    if not isinstance(envelope, dict):
        raise ValueError("seal envelope must be object")
    if envelope.get("schema") != SCHEMA or envelope.get("version") != VERSION:
        raise ValueError("unsupported seal envelope contract")
    if envelope.get("safety") != SEAL_SAFETY:
        raise ValueError("unsafe seal envelope")

    sealed = envelope.get("sealed_snapshot")
    validate_sealed_snapshot(sealed, capture, watchtower, radar, supplied_result)

    if require_sha256(
        envelope.get("sealed_snapshot_sha256"), "sealed_snapshot_sha256"
    ) != stable_sha256(sealed):
        raise ValueError("sealed snapshot envelope digest mismatch")
    if require_sha256(
        envelope.get("verified_source_bridge_result_sha256"),
        "verified_source_bridge_result_sha256",
    ) != stable_sha256(canonical):
        raise ValueError("verified R77 result envelope digest mismatch")

    expected_inputs = {
        "capture_sha256": stable_sha256(capture),
        "watchtower_sha256": stable_sha256(watchtower),
        "radar_sha256": stable_sha256(radar),
    }
    if envelope.get("verification_inputs") != expected_inputs:
        raise ValueError("verification input digest mismatch")
