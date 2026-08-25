from __future__ import annotations

import copy
import math

import pytest

from tools.visionassist_consumer_contract import (
    FRESHNESS_POLICY_ID,
    PRODUCER_GOLDEN_EVIDENCE_SHA256,
    VisionAssistConsumerContractError,
    sha256_canonical_object,
    validate_visionassist_consumer_evidence,
)

EXPECTED_SOURCE_ID = "market:consumer-contract-001"
AS_OF = "2026-08-25T15:04:59Z"


def golden_evidence():
    digest = "\n".join([
        "[CONTEXT] symbol=BTCUSDT(PROVIDED_CONTEXT) venue=Binance(PROVIDED_CONTEXT) timeframe=1h(PROVIDED_CONTEXT)",
        "[HYPOTHESIS] hyp-1 conf=0.6",
        "[SCENE_GRAPH] nodes=3 edges=2",
        "[DETECTORS] SFP=UNKNOWN CHOCH=UNKNOWN BOS=UNKNOWN SWEEP_RECLAIM=UNKNOWN",
        "[EVIDENCE] observations=1 counterevidence=0",
        "[UNCERTAINTY] count=1",
        "[QUALITY] PASS",
        "[SAFETY] DIAGNOSTIC_ONLY NO_ACTION HOLD DENY can_trade=false",
    ])
    body = {
        "schema": "tradingos.visual_market_evidence.v1",
        "source_id": EXPECTED_SOURCE_ID,
        "source_schema": "visionassist.market_observation.v1",
        "source_sha256": "e884589312e921944fa810fba87c3e0cda9bb878d035f9a72fa8ad87d4dd7ca0",
        "image_sha256": "a" * 64,
        "captured_at": "2026-08-25T15:00:00Z",
        "symbol": "BTCUSDT",
        "venue": "Binance",
        "timeframe": "1h",
        "quality": {"status": "PASS", "reasons": [], "abstention_reason": None},
        "visible_observations": [{
            "id": "obs-1",
            "description": "Price tests a marked resistance region.",
            "evidence_refs": ["region:resistance"],
        }],
        "detector_summary": [
            {"detector_type": detector_type, "status": "UNKNOWN", "orientation": "UNKNOWN", "confidence": None,
             "evidence_refs": [], "counterevidence_refs": [], "invalidation_conditions": []}
            for detector_type in ("SFP", "CHOCH", "BOS", "SWEEP_RECLAIM")
        ],
        "counterevidence": [],
        "uncertainties": ["Lower timeframe confirmation is not visible."],
        "alternative_explanations": ["The marked region may be stale."],
        "compact_digest": digest,
        "safety": {
            "mode": "SHADOW", "execution_authority": "NONE", "can_trade": False,
            "capital_permission": "DENY", "orders_allowed": False, "signals_allowed": False,
        },
    }
    evidence_sha256 = sha256_canonical_object(body)
    return {
        **body,
        "evidence_sha256": evidence_sha256,
        "trade_case_ref": {"source_id": EXPECTED_SOURCE_ID, "sha256": evidence_sha256, "schema": body["schema"]},
    }


def rehash(record):
    record = copy.deepcopy(record)
    body = {k: v for k, v in record.items() if k not in {"evidence_sha256", "trade_case_ref"}}
    record["evidence_sha256"] = sha256_canonical_object(body)
    record["trade_case_ref"] = {
        "source_id": record["source_id"], "sha256": record["evidence_sha256"], "schema": record["schema"]
    }
    return record


def deny(fn, code=None):
    with pytest.raises(VisionAssistConsumerContractError) as caught:
        fn()
    if code is not None:
        assert caught.value.code == code


def validate(record, **kwargs):
    return validate_visionassist_consumer_evidence(
        record, as_of=kwargs.pop("as_of", AS_OF), expected_source_id=kwargs.pop("expected_source_id", EXPECTED_SOURCE_ID), **kwargs
    )


def test_exact_producer_golden_vector_parity_and_safety_receipt():
    evidence = golden_evidence()
    assert evidence["evidence_sha256"] == PRODUCER_GOLDEN_EVIDENCE_SHA256
    receipt = validate(evidence)
    assert receipt["observed_at"] == evidence["captured_at"]
    assert receipt["freshness_policy_id"] == FRESHNESS_POLICY_ID
    assert receipt["detector_confidence_as_trading_probability"] is False
    assert receipt["execution_authority"] == "NONE"
    assert receipt["can_trade"] is False
    assert receipt["capital_permission"] == "DENY"
    assert receipt["orders_allowed"] is False
    assert receipt["signals_allowed"] is False


def test_one_field_tamper_with_stale_hash_fails_closed():
    evidence = golden_evidence()
    evidence["symbol"] = "ETHUSDT"
    deny(lambda: validate(evidence), "evidence_sha256_mismatch")


def test_schema_contract_version_and_source_ref_mismatches_fail_closed():
    wrong_schema = golden_evidence()
    wrong_schema["schema"] = "tradingos.visual_market_evidence.v2"
    deny(lambda: validate(wrong_schema), "wrong_schema")
    deny(lambda: validate(golden_evidence(), producer_contract_version="visionassist.agent_consumer_contract.v2"), "producer_contract_version_mismatch")
    deny(lambda: validate(golden_evidence(), expected_source_id="market:other"), "source_id_mismatch")
    wrong_ref = golden_evidence()
    wrong_ref["trade_case_ref"]["source_id"] = "market:other"
    deny(lambda: validate(wrong_ref), "trade_case_ref_source_mismatch")


def test_strict_keys_reject_top_level_and_nested_extensions():
    extra = golden_evidence()
    extra["trade_probability"] = 0.9
    deny(lambda: validate(extra), "record_keys_mismatch")
    nested = golden_evidence()
    nested["safety"]["approval"] = True
    deny(lambda: validate(nested), "safety_keys_mismatch")


@pytest.mark.parametrize("captured_at", [
    "2026-08-25T15:00:00", "not-a-time", "2026-02-30T15:00:00Z", "2026-08-25T25:00:00Z", "2026-08-25T15:00:60Z",
])
def test_timezone_less_malformed_and_impossible_timestamps_fail_closed(captured_at):
    evidence = golden_evidence()
    evidence["captured_at"] = captured_at
    if "T15:00:00" in captured_at and captured_at.endswith("Z"):
        evidence = rehash(evidence)
    deny(lambda: validate(evidence))


def test_freshness_policy_future_stale_and_exact_boundary():
    validate(golden_evidence(), as_of="2026-08-25T15:05:00Z")
    deny(lambda: validate(golden_evidence(), as_of="2026-08-25T14:59:59Z"), "future_evidence")
    deny(lambda: validate(golden_evidence(), as_of="2026-08-25T15:05:00.000000001Z"), "stale_evidence")
    deny(lambda: validate(golden_evidence(), freshness_policy_id="VISIONASSIST_SHADOW_CAPTURE_AGE_600S_V1"), "freshness_policy_mismatch")


def test_authority_widening_attempts_fail_even_after_rehash():
    mutations = [
        lambda x: x["safety"].__setitem__("can_trade", True),
        lambda x: x["safety"].__setitem__("execution_authority", "LIVE"),
        lambda x: x["safety"].__setitem__("capital_permission", "ALLOW"),
        lambda x: x["safety"].__setitem__("orders_allowed", True),
        lambda x: x["safety"].__setitem__("signals_allowed", True),
    ]
    for mutate in mutations:
        evidence = golden_evidence()
        mutate(evidence)
        deny(lambda evidence=rehash(evidence): validate(evidence))


def test_numeric_canonicalization_supported_and_unsupported_boundaries():
    supported = golden_evidence()
    supported["detector_summary"][0]["confidence"] = 1.0
    validate(rehash(supported))

    negative_zero = golden_evidence()
    negative_zero["detector_summary"][0]["confidence"] = -0.0
    deny(lambda: rehash(negative_zero), "unsupported_numeric_negative_zero")

    exponent = golden_evidence()
    exponent["detector_summary"][0]["confidence"] = 1e-7
    deny(lambda: rehash(exponent), "unsupported_numeric_exponent")

    nonfinite = golden_evidence()
    nonfinite["detector_summary"][0]["confidence"] = math.inf
    deny(lambda: rehash(nonfinite), "unsupported_numeric_nonfinite")


def test_counterevidence_uncertainty_alternatives_and_invalidation_are_preserved():
    evidence = golden_evidence()
    evidence["counterevidence"] = ["visible rejection at resistance"]
    evidence["uncertainties"].append("Order-flow confirmation unavailable.")
    evidence["alternative_explanations"].append("Range rotation remains plausible.")
    evidence["detector_summary"][0]["invalidation_conditions"] = ["SFP premise invalid if close accepts above resistance."]
    evidence = rehash(evidence)
    before = copy.deepcopy(evidence)
    receipt = validate(evidence)
    assert evidence == before
    assert receipt["counterevidence"] == tuple(before["counterevidence"])
    assert receipt["uncertainties"] == tuple(before["uncertainties"])
    assert receipt["alternative_explanations"] == tuple(before["alternative_explanations"])
    assert receipt["detector_invalidation_conditions"][0][1] == tuple(before["detector_summary"][0]["invalidation_conditions"])
