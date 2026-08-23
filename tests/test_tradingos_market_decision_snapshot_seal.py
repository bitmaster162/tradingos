from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SEAL_PATH = ROOT / "tools" / "tradingos_market_decision_snapshot_seal.py"
BRIDGE_TEST_PATH = ROOT / "tests" / "test_tradingos_market_decision_bridge.py"

SPEC = importlib.util.spec_from_file_location("r77_2_seal", SEAL_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

BT_SPEC = importlib.util.spec_from_file_location("r77_bridge_tests_for_seal", BRIDGE_TEST_PATH)
assert BT_SPEC and BT_SPEC.loader
bt = importlib.util.module_from_spec(BT_SPEC)
BT_SPEC.loader.exec_module(bt)


def bundle_with_result():
    c, w, r = bt.bundle()
    result = m.bridge.build_bridge(c, w, r)
    return c, w, r, result


def test_reconstruct_verified_r77_exact_happy_path():
    c, w, r, supplied = bundle_with_result()
    reconstructed = m.reconstruct_verified_r77(c, w, r, supplied)
    assert reconstructed == supplied
    assert m.stable_sha256(reconstructed) == m.stable_sha256(supplied)


def test_p1_tampered_input_binding_is_rejected():
    c, w, r, supplied = bundle_with_result()
    tampered = copy.deepcopy(supplied)
    tampered["input_binding"]["radar_report_sha256"] = "2" * 64
    with pytest.raises(ValueError, match="does not match deterministic reconstruction"):
        m.seal_snapshot(c, w, r, tampered)


def test_p1_tampered_liquidity_binding_is_rejected():
    c, w, r, supplied = bundle_with_result()
    tampered = copy.deepcopy(supplied)
    tampered["input_binding"]["liquidity_report_sha256"] = "2" * 64
    with pytest.raises(ValueError, match="does not match deterministic reconstruction"):
        m.seal_snapshot(c, w, r, tampered)


def test_tampered_snapshot_with_recomputed_snapshot_digest_is_still_rejected():
    c, w, r, supplied = bundle_with_result()
    tampered = copy.deepcopy(supplied)
    tampered["snapshot"]["price"]["last"] += 1.0
    tampered["snapshot_sha256"] = m.stable_sha256(tampered["snapshot"])
    with pytest.raises(ValueError, match="does not match deterministic reconstruction"):
        m.seal_snapshot(c, w, r, tampered)


def test_tampered_attention_context_is_rejected():
    c, w, r, supplied = bundle_with_result()
    tampered = copy.deepcopy(supplied)
    tampered["attention_context"]["bias"] = "WATCH_SHORT"
    with pytest.raises(ValueError, match="does not match deterministic reconstruction"):
        m.seal_snapshot(c, w, r, tampered)


def test_changed_raw_capture_with_stale_supplied_result_is_rejected():
    c, w, r, supplied = bundle_with_result()
    changed = copy.deepcopy(c)
    changed["assets"]["BTCUSDT"]["open_interest"]["time"] -= 1000
    with pytest.raises(ValueError):
        m.seal_snapshot(changed, w, r, supplied)


def test_changed_watchtower_with_stale_radar_and_result_is_rejected():
    c, w, r, supplied = bundle_with_result()
    changed = copy.deepcopy(w)
    changed["matrix"][0]["timeframes"]["4h"]["last"] += 1.0
    with pytest.raises(ValueError):
        m.seal_snapshot(c, changed, r, supplied)


def test_changed_radar_with_stale_result_is_rejected():
    c, w, r, supplied = bundle_with_result()
    changed = copy.deepcopy(r)
    changed["matrix"][0]["priority_score"] += 1.0
    with pytest.raises(ValueError, match="does not match deterministic reconstruction"):
        m.seal_snapshot(c, w, changed, supplied)


def test_seal_puts_verified_full_upstream_binding_inside_snapshot():
    c, w, r, supplied = bundle_with_result()
    sealed = m.seal_snapshot(c, w, r, supplied)
    provenance = sealed["provenance"]
    assert provenance["producer"] == m.PRODUCER
    assert provenance["producer_sha256"] == m.file_sha256(Path(m.__file__))
    assert provenance["verification"]["verified"] is True
    assert provenance["upstream_binding"] == supplied["input_binding"]
    assert provenance["source_bridge"]["source_bridge_result_sha256"] == m.stable_sha256(supplied)


def test_seal_binds_exact_r77_bridge_source_bytes():
    c, w, r, supplied = bundle_with_result()
    sealed = m.seal_snapshot(c, w, r, supplied)
    assert sealed["provenance"]["source_bridge"]["producer"] == m.BRIDGE_PRODUCER
    assert sealed["provenance"]["source_bridge"]["producer_sha256"] == m.file_sha256(m.BRIDGE_PATH)


def test_seal_preserves_all_market_semantics_exactly():
    c, w, r, supplied = bundle_with_result()
    sealed = m.seal_snapshot(c, w, r, supplied)
    source = supplied["snapshot"]
    semantic = copy.deepcopy(sealed)
    semantic["provenance"] = copy.deepcopy(source["provenance"])
    assert semantic == source


def test_sealed_upstream_binding_mutation_is_rejected():
    c, w, r, supplied = bundle_with_result()
    sealed = m.seal_snapshot(c, w, r, supplied)
    sealed["provenance"]["upstream_binding"]["radar_report_sha256"] = "3" * 64
    with pytest.raises(ValueError, match="upstream binding mismatch"):
        m.validate_sealed_snapshot(sealed, c, w, r, supplied)


def test_sealed_verification_claim_mutation_is_rejected():
    c, w, r, supplied = bundle_with_result()
    sealed = m.seal_snapshot(c, w, r, supplied)
    sealed["provenance"]["verification"]["verified"] = False
    with pytest.raises(ValueError, match="verification contract mismatch"):
        m.validate_sealed_snapshot(sealed, c, w, r, supplied)


def test_sealed_market_semantic_mutation_is_rejected():
    c, w, r, supplied = bundle_with_result()
    sealed = m.seal_snapshot(c, w, r, supplied)
    sealed["price"]["last"] += 1.0
    with pytest.raises(ValueError, match="changed market snapshot semantics"):
        m.validate_sealed_snapshot(sealed, c, w, r, supplied)


def test_envelope_binds_all_verification_inputs_and_is_deny_only():
    c, w, r, supplied = bundle_with_result()
    envelope = m.build_envelope(c, w, r, supplied)
    assert envelope["verification_inputs"] == {
        "capture_sha256": m.stable_sha256(c),
        "watchtower_sha256": m.stable_sha256(w),
        "radar_sha256": m.stable_sha256(r),
    }
    assert envelope["verified_source_bridge_result_sha256"] == m.stable_sha256(supplied)
    assert envelope["safety"] == m.SEAL_SAFETY
    assert envelope["safety"]["execution_authority"] == "NONE"
    assert envelope["safety"]["can_trade"] is False
    assert envelope["safety"]["capital_permission"] == "DENY"


def test_envelope_verification_input_digest_tamper_is_rejected():
    c, w, r, supplied = bundle_with_result()
    envelope = m.build_envelope(c, w, r, supplied)
    envelope["verification_inputs"]["radar_sha256"] = "4" * 64
    with pytest.raises(ValueError, match="verification input digest mismatch"):
        m.validate_envelope(envelope, c, w, r, supplied)


def test_seal_retains_decision_brief_required_source_rows():
    c, w, r, supplied = bundle_with_result()
    sealed = m.seal_snapshot(c, w, r, supplied)
    assert sealed["schema_version"] == 1
    assert sealed["symbol"] == "BTCUSDT"
    assert sealed["timeframe"] == "4h"
    assert sealed["can_trade"] is False
    assert {row["kind"] for row in sealed["provenance"]["sources"]} == {
        "ohlcv", "open_interest", "funding", "spot_flow"
    }
    assert len({row["source_id"] for row in sealed["provenance"]["sources"]}) == 4


def test_seal_has_no_network_model_or_process_transport_imports():
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
