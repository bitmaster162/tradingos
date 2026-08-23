from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SEAL_PATH = ROOT / "tools" / "tradingos_market_decision_snapshot_seal.py"
BRIDGE_TEST_PATH = ROOT / "tests" / "test_tradingos_market_decision_bridge.py"

SPEC = importlib.util.spec_from_file_location("r77_3_seal", SEAL_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

BT_SPEC = importlib.util.spec_from_file_location("r77_bridge_tests_for_r77_3", BRIDGE_TEST_PATH)
assert BT_SPEC and BT_SPEC.loader
bt = importlib.util.module_from_spec(BT_SPEC)
BT_SPEC.loader.exec_module(bt)


def fixture_bundle():
    capture, watchtower, radar = bt.bundle()

    liquidity_capture = {
        "fixture": "canonical-liquidity-capture",
        "captured_at": radar["liquidity_captured_at"],
        "symbols": list(radar["symbols"]),
    }
    liquidity = {
        "schema": "tradingos.liquidity_lens.v1",
        "version": "1.1.0",
        "captured_at": radar["liquidity_captured_at"],
        "matrix": [],
        "top_attention": radar["symbols"][0],
        "provenance": {
            "producer": m.LIQUIDITY_PRODUCER,
            "producer_sha256": m.EXPECTED_LIQUIDITY_PRODUCER_SHA256,
            "capture_sha256": m.stable_sha256(liquidity_capture),
        },
        "safety": {"can_trade": False},
    }

    radar = copy.deepcopy(radar)
    radar["provenance"]["watchtower_report_sha256"] = m.stable_sha256(watchtower)
    radar["provenance"]["watchtower_capture_sha256"] = m.stable_sha256(capture)
    radar["provenance"]["watchtower_producer_sha256"] = m.EXPECTED_WATCHTOWER_PRODUCER_SHA256
    radar["provenance"]["liquidity_report_sha256"] = m.stable_sha256(liquidity)
    radar["provenance"]["liquidity_capture_sha256"] = m.stable_sha256(liquidity_capture)
    radar["provenance"]["liquidity_producer_sha256"] = m.EXPECTED_LIQUIDITY_PRODUCER_SHA256

    bridge_spec = importlib.util.spec_from_file_location(
        "r77_bridge_actual_for_r77_3", ROOT / "tools" / "tradingos_market_decision_bridge.py"
    )
    assert bridge_spec and bridge_spec.loader
    bridge = importlib.util.module_from_spec(bridge_spec)
    bridge_spec.loader.exec_module(bridge)
    result = bridge.build_bridge(capture, watchtower, radar)
    return capture, watchtower, liquidity_capture, liquidity, radar, result


@pytest.fixture()
def deterministic_modules(monkeypatch):
    capture, watchtower, liquidity_capture, liquidity, radar, result = fixture_bundle()
    bridge_spec = importlib.util.spec_from_file_location(
        "r77_bridge_actual_for_stub", ROOT / "tools" / "tradingos_market_decision_bridge.py"
    )
    assert bridge_spec and bridge_spec.loader
    bridge = importlib.util.module_from_spec(bridge_spec)
    bridge_spec.loader.exec_module(bridge)

    def loader(path, expected_blob, label):
        if label == "watchtower":
            return SimpleNamespace(build_watchtower=lambda c: copy.deepcopy(watchtower))
        if label == "liquidity":
            return SimpleNamespace(build_lens=lambda c: copy.deepcopy(liquidity))
        if label == "radar":
            return SimpleNamespace(build_radar=lambda w, l: copy.deepcopy(radar))
        if label == "bridge":
            return bridge
        raise AssertionError(label)

    monkeypatch.setattr(m, "_load_verified_module", loader)
    return capture, watchtower, liquidity_capture, liquidity, radar, result


def test_full_chain_happy_path(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    chain = m.reconstruct_verified_chain(c, w, lc, l, r, result)
    assert chain["watchtower"] == w
    assert chain["liquidity"] == l
    assert chain["radar"] == r
    assert chain["result"] == result


def test_p1_r77_input_binding_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    bad = copy.deepcopy(result)
    bad["input_binding"]["radar_report_sha256"] = "2" * 64
    with pytest.raises(ValueError):
        m.seal_snapshot(c, w, lc, l, r, bad)


def test_watchtower_report_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    bad = copy.deepcopy(w)
    bad["matrix"][0]["bias"] = "NO_ACTION"
    with pytest.raises(ValueError, match="Watchtower does not match"):
        m.seal_snapshot(c, bad, lc, l, r, result)


def test_watchtower_capture_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    bad = copy.deepcopy(c)
    bad["captured_at"] = "2026-08-24T00:00:01Z"
    with pytest.raises(ValueError):
        m.seal_snapshot(bad, w, lc, l, r, result)


def test_p2_liquidity_capture_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    bad = copy.deepcopy(lc)
    bad["fixture"] = "tampered"
    with pytest.raises(ValueError, match="Liquidity capture binding mismatch"):
        m.seal_snapshot(c, w, bad, l, r, result)


def test_p2_liquidity_report_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    bad = copy.deepcopy(l)
    bad["captured_at"] = "2026-08-24T00:00:01Z"
    with pytest.raises(ValueError, match="Liquidity report does not match"):
        m.seal_snapshot(c, w, lc, bad, r, result)


def test_p2_radar_liquidity_report_hash_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    bad = copy.deepcopy(r)
    bad["provenance"]["liquidity_report_sha256"] = "2" * 64
    with pytest.raises(ValueError, match="Market Radar does not match"):
        m.seal_snapshot(c, w, lc, l, bad, result)


def test_p2_radar_liquidity_capture_hash_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    bad = copy.deepcopy(r)
    bad["provenance"]["liquidity_capture_sha256"] = "3" * 64
    with pytest.raises(ValueError, match="Market Radar does not match"):
        m.seal_snapshot(c, w, lc, l, bad, result)


def test_radar_liquidity_context_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    bad = copy.deepcopy(r)
    bad["matrix"][0]["liquidity"]["spread_bps"] += 1.0
    with pytest.raises(ValueError, match="Market Radar does not match"):
        m.seal_snapshot(c, w, lc, l, bad, result)


def test_radar_report_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    bad = copy.deepcopy(r)
    bad["matrix"][0]["priority_score"] += 1.0
    with pytest.raises(ValueError, match="Market Radar does not match"):
        m.seal_snapshot(c, w, lc, l, bad, result)


def test_rebuilt_r77_mismatch_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    bad = copy.deepcopy(result)
    bad["attention_context"]["priority_score"] += 1.0
    with pytest.raises(ValueError):
        m.seal_snapshot(c, w, lc, l, r, bad)


def test_seal_contains_full_verified_chain(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    sealed = m.seal_snapshot(c, w, lc, l, r, result)
    p = sealed["provenance"]
    assert p["verification"]["verified"] is True
    assert p["verification"]["canonical_git_blobs"] == {
        "watchtower": m.EXPECTED_WATCHTOWER_GIT_BLOB_SHA1,
        "liquidity": m.EXPECTED_LIQUIDITY_GIT_BLOB_SHA1,
        "radar": m.EXPECTED_RADAR_GIT_BLOB_SHA1,
        "bridge": m.EXPECTED_BRIDGE_GIT_BLOB_SHA1,
    }
    assert p["source_chain"]["watchtower_capture_sha256"] == m.stable_sha256(c)
    assert p["source_chain"]["liquidity_capture_sha256"] == m.stable_sha256(lc)
    assert p["source_chain"]["liquidity_report_sha256"] == m.stable_sha256(l)
    assert p["source_chain"]["radar_report_sha256"] == m.stable_sha256(r)
    assert p["source_chain"]["r77_result_sha256"] == m.stable_sha256(result)


def test_seal_preserves_market_semantics(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    sealed = m.seal_snapshot(c, w, lc, l, r, result)
    semantic = copy.deepcopy(sealed)
    semantic["provenance"] = copy.deepcopy(result["snapshot"]["provenance"])
    assert semantic == result["snapshot"]


def test_sealed_source_chain_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    sealed = m.seal_snapshot(c, w, lc, l, r, result)
    sealed["provenance"]["source_chain"]["liquidity_report_sha256"] = "4" * 64
    with pytest.raises(ValueError, match="source-chain binding mismatch"):
        m.validate_sealed_snapshot(sealed, c, w, lc, l, r, result)


def test_sealed_upstream_binding_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    sealed = m.seal_snapshot(c, w, lc, l, r, result)
    sealed["provenance"]["upstream_binding"]["liquidity_capture_sha256"] = "5" * 64
    with pytest.raises(ValueError, match="upstream binding mismatch"):
        m.validate_sealed_snapshot(sealed, c, w, lc, l, r, result)


def test_sealed_verification_claim_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    sealed = m.seal_snapshot(c, w, lc, l, r, result)
    sealed["provenance"]["verification"]["verified"] = False
    with pytest.raises(ValueError, match="verification contract mismatch"):
        m.validate_sealed_snapshot(sealed, c, w, lc, l, r, result)


def test_sealed_market_semantic_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    sealed = m.seal_snapshot(c, w, lc, l, r, result)
    sealed["price"]["last"] += 1.0
    with pytest.raises(ValueError, match="changed market snapshot semantics"):
        m.validate_sealed_snapshot(sealed, c, w, lc, l, r, result)


def test_envelope_binds_all_primary_and_derived_artifacts(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    envelope = m.build_envelope(c, w, lc, l, r, result)
    assert envelope["verified_artifact_sha256"] == {
        "watchtower_capture": m.stable_sha256(c),
        "watchtower": m.stable_sha256(w),
        "liquidity_capture": m.stable_sha256(lc),
        "liquidity": m.stable_sha256(l),
        "radar": m.stable_sha256(r),
        "r77_result": m.stable_sha256(result),
    }
    assert envelope["safety"]["execution_authority"] == "NONE"
    assert envelope["safety"]["can_trade"] is False
    assert envelope["safety"]["capital_permission"] == "DENY"


def test_envelope_digest_tamper_rejected(deterministic_modules):
    c, w, lc, l, r, result = deterministic_modules
    envelope = m.build_envelope(c, w, lc, l, r, result)
    envelope["verified_artifact_sha256"]["liquidity"] = "6" * 64
    with pytest.raises(ValueError, match="verified artifact digest mismatch"):
        m.validate_envelope(envelope, c, w, lc, l, r, result)


def test_git_blob_loader_accepts_exact_and_rejects_mismatch(tmp_path):
    module_path = tmp_path / "fixture_module.py"
    module_path.write_text("def build_watchtower(value):\n    return value\n", encoding="utf-8", newline="\n")
    blob = m.git_blob_sha1(module_path)
    loaded = m._load_verified_module(module_path, blob, "fixture")
    assert loaded.build_watchtower({"x": 1}) == {"x": 1}
    with pytest.raises(ValueError, match="Git blob mismatch"):
        m._load_verified_module(module_path, "0" * 40, "fixture")


def test_default_canonical_blob_pins_are_exact():
    assert m.EXPECTED_WATCHTOWER_GIT_BLOB_SHA1 == "96f00327e5bd8a77612d7b26718d4c9951f2be73"
    assert m.EXPECTED_LIQUIDITY_GIT_BLOB_SHA1 == "193ac1c869dd479dac47c35cede777cc34bce687"
    assert m.EXPECTED_RADAR_GIT_BLOB_SHA1 == "3e4df1d56648483254667b39d16b1879434ca858"
    assert m.EXPECTED_BRIDGE_GIT_BLOB_SHA1 == "e6e0f6ecad22068acd82ca0588ad2dfb5fdd89b4"


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
