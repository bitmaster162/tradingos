from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SEAL_PATH = ROOT / "tools" / "tradingos_market_decision_snapshot_seal.py"
SPEC = importlib.util.spec_from_file_location("r77_1_seal", SEAL_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def source_result():
    snapshot = {
        "schema_version": 1,
        "snapshot_id": "BTCUSDT-2026-08-24T00:00:00Z-R77-deadbeefcafe",
        "as_of": "2026-08-24T00:00:00Z",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "can_trade": False,
        "provenance": {
            "producer": m.BRIDGE_PRODUCER,
            "sources": [
                {"kind": "ohlcv", "source_id": "ohlcv-id", "observed_at": "2026-08-23T23:59:59Z"},
                {"kind": "open_interest", "source_id": "oi-id", "observed_at": "2026-08-23T23:59:57Z"},
                {"kind": "funding", "source_id": "funding-id", "observed_at": "2026-08-23T23:59:56Z"},
                {"kind": "spot_flow", "source_id": "spot-id", "observed_at": "2026-08-23T23:59:57Z"},
            ],
        },
        "price": {"last": 118400.0},
        "market_structure": {"trend": "up"},
        "derivatives": {"open_interest_change_pct": 2.1},
        "flow": {"spot_cvd_direction": "up"},
        "data_quality": {"present_sources": ["ohlcv", "open_interest", "funding", "spot_flow"], "conflicts": []},
        "operator": {"prevented_decision": "execution_not_permitted"},
    }
    binding = {
        "watchtower_capture_sha256": "a" * 64,
        "watchtower_report_sha256": "b" * 64,
        "watchtower_producer_sha256": "c" * 64,
        "radar_report_sha256": "d" * 64,
        "liquidity_report_sha256": "e" * 64,
        "liquidity_capture_sha256": "f" * 64,
        "liquidity_producer_sha256": "1" * 64,
    }
    return {
        "schema": m.bridge.SCHEMA,
        "version": m.bridge.VERSION,
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "snapshot": snapshot,
        "snapshot_sha256": m.stable_sha256(snapshot),
        "input_binding": binding,
        "attention_context": {
            "bias": "WATCH_LONG",
            "confers_authority": False,
        },
        "safety": dict(m.bridge.BRIDGE_SAFETY),
    }


def test_seal_puts_full_upstream_binding_inside_snapshot_bytes():
    source = source_result()
    sealed = m.seal_snapshot(source)
    provenance = sealed["provenance"]
    assert provenance["producer"] == m.PRODUCER
    assert provenance["producer_sha256"] == m.file_sha256(Path(m.__file__))
    assert provenance["upstream_binding"] == source["input_binding"]
    assert set(provenance["upstream_binding"]) == m.EXPECTED_BINDING_KEYS
    assert provenance["source_bridge"]["source_bridge_result_sha256"] == m.stable_sha256(source)


def test_seal_binds_exact_r77_bridge_source_bytes():
    sealed = m.seal_snapshot(source_result())
    assert sealed["provenance"]["source_bridge"]["producer"] == m.BRIDGE_PRODUCER
    assert sealed["provenance"]["source_bridge"]["producer_sha256"] == m.file_sha256(m.BRIDGE_PATH)


def test_seal_preserves_market_semantics_exactly():
    source = source_result()
    sealed = m.seal_snapshot(source)
    for key in ("price", "market_structure", "derivatives", "flow", "data_quality", "operator"):
        assert sealed[key] == source["snapshot"][key]


def test_envelope_is_replayable_and_deny_only():
    source = source_result()
    envelope = m.build_envelope(source)
    assert envelope["sealed_snapshot_sha256"] == m.stable_sha256(envelope["sealed_snapshot"])
    assert envelope["source_bridge_result_sha256"] == m.stable_sha256(source)
    assert envelope["safety"] == m.SEAL_SAFETY
    assert envelope["safety"]["execution_authority"] == "NONE"
    assert envelope["safety"]["can_trade"] is False
    assert envelope["safety"]["capital_permission"] == "DENY"


def test_tampered_r77_snapshot_digest_is_refused():
    source = source_result()
    source["snapshot"]["price"]["last"] += 1.0
    with pytest.raises(ValueError, match="snapshot digest mismatch"):
        m.seal_snapshot(source)


def test_r77_input_binding_missing_key_is_refused():
    source = source_result()
    source["input_binding"].pop("radar_report_sha256")
    with pytest.raises(ValueError, match="input_binding key set mismatch"):
        m.seal_snapshot(source)


def test_r77_input_binding_invalid_sha_is_refused():
    source = source_result()
    source["input_binding"]["radar_report_sha256"] = "not-a-sha"
    with pytest.raises(ValueError, match="invalid sha256"):
        m.seal_snapshot(source)


def test_source_kind_set_must_be_exact():
    source = source_result()
    source["snapshot"]["provenance"]["sources"][-1]["kind"] = "other"
    source["snapshot_sha256"] = m.stable_sha256(source["snapshot"])
    with pytest.raises(ValueError, match="source kind mismatch"):
        m.seal_snapshot(source)


def test_source_ids_must_be_unique():
    source = source_result()
    source["snapshot"]["provenance"]["sources"][1]["source_id"] = "ohlcv-id"
    source["snapshot_sha256"] = m.stable_sha256(source["snapshot"])
    with pytest.raises(ValueError, match="source_id reuse"):
        m.seal_snapshot(source)


def test_sealed_upstream_binding_drift_is_refused():
    source = source_result()
    sealed = m.seal_snapshot(source)
    sealed["provenance"]["upstream_binding"]["radar_report_sha256"] = "2" * 64
    with pytest.raises(ValueError, match="upstream binding mismatch"):
        m.validate_sealed_snapshot(sealed, source)


def test_sealed_source_bridge_digest_drift_is_refused():
    source = source_result()
    sealed = m.seal_snapshot(source)
    sealed["provenance"]["source_bridge"]["source_bridge_result_sha256"] = "2" * 64
    with pytest.raises(ValueError, match="source_bridge binding mismatch"):
        m.validate_sealed_snapshot(sealed, source)


def test_sealed_market_semantic_mutation_is_refused():
    source = source_result()
    sealed = m.seal_snapshot(source)
    sealed["price"]["last"] += 1.0
    with pytest.raises(ValueError, match="changed market snapshot semantics"):
        m.validate_sealed_snapshot(sealed, source)


def test_sealed_snapshot_digest_tamper_is_refused():
    source = source_result()
    envelope = m.build_envelope(source)
    envelope["sealed_snapshot"]["price"]["last"] += 1.0
    with pytest.raises(ValueError):
        m.validate_envelope(envelope, source)


def test_seal_has_no_network_or_model_transport_imports():
    tree = __import__("ast").parse(SEAL_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in __import__("ast").walk(tree):
        if isinstance(node, __import__("ast").Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, __import__("ast").ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {
        "requests", "urllib", "httpx", "aiohttp", "socket", "subprocess",
        "openai", "anthropic", "google", "ccxt", "websockets"
    }
    assert imported.isdisjoint(forbidden)
