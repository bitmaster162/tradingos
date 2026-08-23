#!/usr/bin/env python3
"""TradingOS R77.1 — additive provenance seal for Decision Brief snapshots.

Consumes an R77 Market Decision Bridge result and returns a snapshot whose own
bytes bind the exact R77 bridge producer, the full upstream evidence chain, the
source R77 result digest, and the pre-seal snapshot digest.

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

SCHEMA = "tradingos.market_decision_snapshot_seal.v1"
VERSION = "1.0.0"
PRODUCER = "tools/tradingos_market_decision_snapshot_seal.py"
BRIDGE_PRODUCER = "tools/tradingos_market_decision_bridge.py"

BRIDGE_PATH = Path(__file__).with_name("tradingos_market_decision_bridge.py")
_SPEC = importlib.util.spec_from_file_location("_tradingos_r77_bridge_for_seal", BRIDGE_PATH)
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


def stable_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field}: invalid sha256")
    return value


def _validate_r77_result(result: Any) -> dict[str, Any]:
    bridge.validate_bridge(result)
    if not isinstance(result, dict):
        raise ValueError("R77 result must be object")
    if result.get("schema") != bridge.SCHEMA or result.get("version") != bridge.VERSION:
        raise ValueError("unsupported R77 result contract")
    if result.get("safety") != bridge.BRIDGE_SAFETY:
        raise ValueError("unsafe R77 result safety")

    snapshot = result.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("can_trade") is not False:
        raise ValueError("unsafe/missing R77 snapshot")
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("producer") != BRIDGE_PRODUCER:
        raise ValueError("R77 snapshot producer mismatch")

    sources = provenance.get("sources")
    if not isinstance(sources, list) or len(sources) != len(EXPECTED_SOURCE_KINDS):
        raise ValueError("R77 snapshot source list mismatch")
    kinds = [row.get("kind") for row in sources if isinstance(row, dict)]
    if len(kinds) != len(sources) or set(kinds) != EXPECTED_SOURCE_KINDS:
        raise ValueError("R77 snapshot source kind mismatch")
    source_ids = []
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

    binding = result.get("input_binding")
    if not isinstance(binding, dict) or set(binding) != EXPECTED_BINDING_KEYS:
        raise ValueError("R77 input_binding key set mismatch")
    for key in sorted(EXPECTED_BINDING_KEYS):
        require_sha256(binding.get(key), f"R77.input_binding.{key}")

    snapshot_sha = require_sha256(result.get("snapshot_sha256"), "R77.snapshot_sha256")
    if snapshot_sha != stable_sha256(snapshot):
        raise ValueError("R77 snapshot digest mismatch")
    return snapshot


def seal_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    source_snapshot = _validate_r77_result(result)
    sealed = deepcopy(source_snapshot)
    sealed["provenance"] = {
        "producer": PRODUCER,
        "producer_sha256": file_sha256(Path(__file__)),
        "source_bridge": {
            "producer": BRIDGE_PRODUCER,
            "producer_sha256": file_sha256(BRIDGE_PATH),
            "source_snapshot_sha256": stable_sha256(source_snapshot),
            "source_bridge_result_sha256": stable_sha256(result),
        },
        "upstream_binding": deepcopy(result["input_binding"]),
        "sources": deepcopy(source_snapshot["provenance"]["sources"]),
    }
    sealed["can_trade"] = False
    validate_sealed_snapshot(sealed, result)
    return sealed


def validate_sealed_snapshot(sealed: Any, source_result: dict[str, Any]) -> None:
    source_snapshot = _validate_r77_result(source_result)
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

    source_bridge = provenance.get("source_bridge")
    expected_source_bridge = {
        "producer": BRIDGE_PRODUCER,
        "producer_sha256": file_sha256(BRIDGE_PATH),
        "source_snapshot_sha256": stable_sha256(source_snapshot),
        "source_bridge_result_sha256": stable_sha256(source_result),
    }
    if source_bridge != expected_source_bridge:
        raise ValueError("sealed source_bridge binding mismatch")

    binding = provenance.get("upstream_binding")
    if binding != source_result["input_binding"]:
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


def build_envelope(result: dict[str, Any]) -> dict[str, Any]:
    sealed = seal_snapshot(result)
    envelope = {
        "schema": SCHEMA,
        "version": VERSION,
        "sealed_snapshot": sealed,
        "sealed_snapshot_sha256": stable_sha256(sealed),
        "source_bridge_result_sha256": stable_sha256(result),
        "safety": dict(SEAL_SAFETY),
    }
    validate_envelope(envelope, result)
    return envelope


def validate_envelope(envelope: Any, source_result: dict[str, Any]) -> None:
    if not isinstance(envelope, dict):
        raise ValueError("seal envelope must be object")
    if envelope.get("schema") != SCHEMA or envelope.get("version") != VERSION:
        raise ValueError("unsupported seal envelope contract")
    if envelope.get("safety") != SEAL_SAFETY:
        raise ValueError("unsafe seal envelope")
    sealed = envelope.get("sealed_snapshot")
    validate_sealed_snapshot(sealed, source_result)
    if require_sha256(
        envelope.get("sealed_snapshot_sha256"), "sealed_snapshot_sha256"
    ) != stable_sha256(sealed):
        raise ValueError("sealed snapshot envelope digest mismatch")
    if require_sha256(
        envelope.get("source_bridge_result_sha256"), "source_bridge_result_sha256"
    ) != stable_sha256(source_result):
        raise ValueError("source R77 result envelope digest mismatch")
