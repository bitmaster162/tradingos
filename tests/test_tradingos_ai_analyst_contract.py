from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODULE_PATH = ROOT / "tools" / "tradingos_ai_analyst_contract.py"
POLICY_PATH = ROOT / "configs" / "TRADINGOS_AI_ANALYST_POLICY_V1.json"

SPEC = importlib.util.spec_from_file_location("r78", MODULE_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def ev(dimension, label, direction, strength, observation):
    return {
        "dimension": dimension,
        "label": label,
        "direction": direction,
        "strength": strength,
        "observation": observation,
    }


def brief():
    long_rows = [
        ev("market_structure", "HTF trend", "LONG", 2.0, "trend=up"),
        ev("price_trend", "EMA alignment", "LONG", 1.0, "ema_fast > ema_slow"),
        ev("open_interest", "Price/OI alignment", "LONG", 1.25, "price=1.8% OI=2.1%"),
        ev("spot_flow", "Spot CVD", "LONG", 1.25, "spot=up, perp=up"),
        ev("volume", "Relative volume confirmation", "LONG", 1.0, "relative_volume=1.35"),
    ]
    neutral = [
        ev("derivatives_crowding", "Crowding balanced", "NEUTRAL", 0.0, "funding_z=0.7, basis_z=0.6")
    ]
    return {
        "schema_version": 1,
        "brief_id": "abc123def456",
        "snapshot_id": "BTCUSDT-2026-08-24T00:00:00Z-R77-deadbeefcafe",
        "generated_at": "2026-08-24T00:01:00Z",
        "as_of": "2026-08-24T00:00:00Z",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "status": "READY",
        "decision": {
            "stance": "WATCH_LONG",
            "reason": "edge_gate_passed",
            "lead_direction": "LONG",
            "score_margin": 6.5,
            "edge_sufficient": True,
        },
        "regime": {
            "label": "TREND_UP",
            "volatility": "NORMAL",
            "basis": ["market_structure.trend=up", "price.atr_pct=1.9"],
        },
        "intent_hypotheses": [
            {
                "intent": "LONG_CONTINUATION_OR_ROTATION",
                "direction": "LONG",
                "support_score": 6.5,
                "counter_score": 0.0,
                "independent_support_dimensions": 5,
                "supporting_evidence": long_rows,
                "contradicting_evidence": neutral,
            },
            {
                "intent": "SHORT_CONTINUATION_OR_ROTATION",
                "direction": "SHORT",
                "support_score": 0.0,
                "counter_score": 6.5,
                "independent_support_dimensions": 0,
                "supporting_evidence": [],
                "contradicting_evidence": long_rows + neutral,
            },
        ],
        "derivatives_context": {
            "open_interest_change_pct": 2.1,
            "open_interest_read": "leverage_building",
            "funding_rate": 0.00008,
            "funding_z": 0.7,
            "funding_read": "balanced_or_unconfirmed",
            "basis_pct": 0.04,
            "basis_z": 0.6,
            "liquidation_bias": "unknown",
        },
        "scenarios": [
            {
                "name": "bull",
                "trigger": "4h close above 119600.0 with spot-flow and OI confirmation",
                "invalidation": "4h close back below 119600.0 or loss of 116800.0",
                "operator_use": "reassess WATCH_LONG; this brief itself is not an entry signal",
            },
            {
                "name": "base",
                "trigger": "price remains between 116800.0 and 119600.0",
                "invalidation": "accepted close outside [116800.0, 119600.0]",
                "operator_use": "NO_ACTION in the middle of the range; wait for new evidence",
            },
            {
                "name": "bear",
                "trigger": "4h close below 116800.0 with spot-flow and OI confirmation",
                "invalidation": "4h close back above 116800.0 or reclaim of 119600.0",
                "operator_use": "reassess WATCH_SHORT; this brief itself is not an entry signal",
            },
        ],
        "invalidation": {
            "global": "Any stale, missing, conflicting, or unsafe input invalidates the brief.",
            "long": "4h close back below 119600.0 or loss of 116800.0",
            "short": "4h close back above 116800.0 or reclaim of 119600.0",
        },
        "uncertainty": {
            "input_gate_passed": True,
            "snapshot_age_minutes": 1.0,
            "missing_data": [],
            "conflicts": [],
            "blockers": [],
            "model_probability_claimed": False,
            "caveats": [
                "Scores are deterministic evidence weights, not calibrated probabilities.",
                "A WATCH stance is an observation priority, not an entry signal.",
                "This generator does not assess execution, account state, fees, or position sizing.",
            ],
        },
        "operator_feedback": {
            "prior_decision": "NO_ACTION",
            "changed_decision": "not_computed_by_bridge",
            "prevented_decision": "execution_not_permitted",
        },
        "operator_next_action": (
            "Wait for a 4h close above 119600.0 with spot-flow and OI confirmation; "
            "do not place an order from this brief."
        ),
        "provenance": {
            "input_path": "snapshot.json",
            "input_sha256": "a" * 64,
            "input_producer": "tools/tradingos_market_decision_snapshot_seal.py",
            "input_sources": [
                {"kind": "ohlcv", "source_id": "binance-public-futures-klines-4h", "observed_at": "2026-08-23T23:59:59Z"},
                {"kind": "open_interest", "source_id": "binance-public-futures-open-interest", "observed_at": "2026-08-23T23:59:57Z"},
                {"kind": "funding", "source_id": "binance-public-futures-mark-price", "observed_at": "2026-08-23T23:59:56Z"},
                {"kind": "spot_flow", "source_id": "binance-public-spot-klines-4h", "observed_at": "2026-08-23T23:59:57Z"},
            ],
            "policy_path": "configs/TRADINGOS_DECISION_BRIEF_POLICY_V1.json",
            "policy_id": "TRADINGOS_DECISION_BRIEF_POLICY_V1",
            "policy_sha256": "b" * 64,
            "generator": "tools/tradingos_decision_brief_v2.py",
            "generator_version": "2.0.0",
            "generator_sha256": "c" * 64,
            "base_generator": "tools/tradingos_decision_brief.py",
            "base_generator_sha256": "d" * 64,
        },
        "permissions": dict(m.EXPECTED_BRIEF_PERMISSIONS),
        "can_trade": False,
    }


def blocked_brief():
    b = brief()
    b["status"] = "BLOCKED"
    b["decision"] = {
        "stance": "NO_ACTION",
        "reason": "input_gate_failed",
        "lead_direction": None,
        "score_margin": 0.0,
        "edge_sufficient": False,
    }
    b["uncertainty"]["input_gate_passed"] = False
    b["uncertainty"]["blockers"] = ["stale_snapshot"]
    b["operator_next_action"] = "Do not trade; repair `stale_snapshot` and generate a fresh brief."
    return b


def valid_response(req, b):
    ids = [row["evidence_id"] for row in req["evidence_catalog"]]
    return {
        "schema": m.RESPONSE_SCHEMA,
        "request_id": req["request_id"],
        "brief_sha256": req["brief"]["brief_sha256"],
        "analysis_mode": req["analysis_mode"],
        "claims": [
            {
                "claim_id": "C1",
                "kind": "THESIS" if b["status"] == "READY" else "BLIND_SPOT",
                "text": (
                    "The watched case is supported by the cited deterministic evidence."
                    if b["status"] == "READY"
                    else "The brief is blocked and should remain diagnostic."
                ),
                "evidence_refs": [ids[0]],
                "claim_scope": "INTERPRETATION_OF_REFERENCED_EVIDENCE",
                "novel_market_fact": False,
            }
        ],
        "operator_disposition": "WAIT_FOR_CONFIRMATION" if b["status"] == "READY" else "REFRESH_DATA",
        "questions": [
            {
                "question_id": "Q1",
                "text": "Which cited invalidation should the operator review first?",
                "evidence_refs": [ids[-1]],
            }
        ],
        "external_sources_used": False,
        "probability_claimed": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }


def test_policy_is_deny_only_and_valid():
    p = policy()
    m.validate_policy(p)
    assert p["model_transport_in_core"] is False
    assert p["output_permissions"]["execution_authority"] == "NONE"
    assert p["output_permissions"]["can_trade"] is False
    assert p["output_permissions"]["capital_permission"] == "DENY"


def test_build_request_binds_exact_brief_and_catalog():
    b = brief()
    req = m.build_request(b, policy())
    assert req["brief"]["brief_sha256"] == m.stable_sha256(b)
    assert req["analysis_mode"] == "INTERPRETATION_ONLY"
    assert req["safety"] == m.REQUEST_SAFETY
    assert req["evidence_catalog"]
    assert len({row["evidence_id"] for row in req["evidence_catalog"]}) == len(req["evidence_catalog"])


def test_request_is_deterministic():
    b = brief()
    a = m.build_request(copy.deepcopy(b), policy())
    c = m.build_request(copy.deepcopy(b), policy())
    assert a == c
    assert m.stable_sha256(a) == m.stable_sha256(c)


def test_catalog_deduplicates_same_evidence_payload_across_hypotheses():
    req = m.build_request(brief(), policy())
    payload_hashes = [m.stable_sha256(row["payload"]) for row in req["evidence_catalog"]]
    assert len(payload_hashes) == len(set(payload_hashes))


def test_blocked_brief_switches_to_diagnostic_mode_and_filters_tasks():
    b = blocked_brief()
    req = m.build_request(b, policy())
    assert req["analysis_mode"] == "DIAGNOSTIC_ONLY"
    kinds = {task["kind"] for task in req["tasks"]}
    assert "THESIS" not in kinds
    assert "COUNTERTHESIS" not in kinds
    assert "SCENARIO_READ" not in kinds
    assert kinds <= set(policy()["blocked_brief_allowed_claim_kinds"])


def test_unsafe_brief_permission_drift_is_refused():
    b = brief()
    b["permissions"]["orders_allowed"] = True
    with pytest.raises(ValueError, match="unsafe brief permissions"):
        m.build_request(b, policy())


def test_brief_status_uncertainty_mismatch_is_refused():
    b = brief()
    b["uncertainty"]["input_gate_passed"] = False
    with pytest.raises(ValueError, match="status/input gate mismatch"):
        m.build_request(b, policy())


def test_model_prompt_is_provider_agnostic_and_explicitly_bounded():
    req = m.build_request(brief(), policy())
    prompt = m.render_model_prompt(req, policy())
    assert "Do not use memory, web, news, or outside market knowledge." in prompt
    assert "Do not give probabilities, signals, entries, exits, orders, leverage, sizing" in prompt
    assert "Output one JSON object" in prompt
    assert req["request_id"] in prompt
    assert "openai" not in prompt.lower()
    assert "anthropic" not in prompt.lower()


def test_valid_ready_response_passes():
    b = brief()
    req = m.build_request(b, policy())
    result = m.validate_response(req, valid_response(req, b), policy(), b)
    assert result["passed"] is True
    assert result["can_trade"] is False
    assert result["capital_permission"] == "DENY"


def test_valid_blocked_response_passes_diagnostic_only():
    b = blocked_brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    result = m.validate_response(req, response, policy(), b)
    assert result["analysis_mode"] == "DIAGNOSTIC_ONLY"
    assert result["operator_disposition"] == "REFRESH_DATA"


def test_unknown_evidence_reference_is_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["evidence_refs"] = ["EVD-doesnotexist"]
    with pytest.raises(ValueError, match="evidence_refs invalid"):
        m.validate_response(req, response, policy(), b)


def test_missing_evidence_reference_is_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["evidence_refs"] = []
    with pytest.raises(ValueError, match="evidence_refs invalid"):
        m.validate_response(req, response, policy(), b)


def test_novel_market_fact_flag_is_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["novel_market_fact"] = True
    with pytest.raises(ValueError, match="novel market fact"):
        m.validate_response(req, response, policy(), b)


def test_new_numeric_literal_is_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["text"] = "The model assigns a support score of 73."
    with pytest.raises(ValueError, match="new numeric literal forbidden"):
        m.validate_response(req, response, policy(), b)


def test_existing_numeric_literal_from_brief_is_allowed():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["text"] = "The cited deterministic support score is 6.5."
    result = m.validate_response(req, response, policy(), b)
    assert result["passed"] is True


@pytest.mark.parametrize("text", [
    "Buy now because the thesis is strong.",
    "Use an entry after confirmation.",
    "Place an order after the next close.",
    "Increase leverage if the case strengthens.",
    "Set a stop loss under support.",
    "Take profit near resistance.",
    "Allocate capital to the watched case.",
])
def test_execution_language_is_refused(text):
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["text"] = text
    with pytest.raises(ValueError, match="execution/trade language forbidden"):
        m.validate_response(req, response, policy(), b)


def test_probability_language_is_refused_even_without_percent():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["text"] = "The probability of continuation appears elevated."
    with pytest.raises(ValueError, match="probability language forbidden"):
        m.validate_response(req, response, policy(), b)


def test_url_or_external_reference_is_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["questions"][0]["text"] = "Should we verify this at https://example.com?"
    with pytest.raises(ValueError, match="external URL/reference forbidden"):
        m.validate_response(req, response, policy(), b)


def test_external_sources_flag_is_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["external_sources_used"] = True
    with pytest.raises(ValueError, match="external_sources_used"):
        m.validate_response(req, response, policy(), b)


def test_probability_flag_is_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["probability_claimed"] = True
    with pytest.raises(ValueError, match="probability_claimed"):
        m.validate_response(req, response, policy(), b)


@pytest.mark.parametrize("field,value", [
    ("signals_allowed", True),
    ("orders_allowed", True),
    ("execution_authority", "MODEL"),
    ("can_trade", True),
    ("capital_permission", "ALLOW"),
    ("confers_authority", True),
])
def test_effect_authority_drift_is_refused(field, value):
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response[field] = value
    with pytest.raises(ValueError, match="unsafe analyst response"):
        m.validate_response(req, response, policy(), b)


def test_extra_root_field_is_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["secret_action"] = "none"
    with pytest.raises(ValueError, match="root key set mismatch"):
        m.validate_response(req, response, policy(), b)


def test_response_request_binding_mismatch_is_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["request_id"] = "wrong"
    with pytest.raises(ValueError, match="request_id mismatch"):
        m.validate_response(req, response, policy(), b)


def test_source_brief_digest_mismatch_is_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    mutated = copy.deepcopy(b)
    mutated["regime"]["label"] = "RANGE"
    with pytest.raises(ValueError, match="request/source reconstruction mismatch"):
        m.validate_response(req, response, policy(), mutated)


def test_duplicate_claim_ids_are_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    second = copy.deepcopy(response["claims"][0])
    response["claims"].append(second)
    with pytest.raises(ValueError, match="duplicate claim_id"):
        m.validate_response(req, response, policy(), b)


def test_duplicate_question_ids_are_refused():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    second = copy.deepcopy(response["questions"][0])
    response["questions"].append(second)
    with pytest.raises(ValueError, match="duplicate question_id"):
        m.validate_response(req, response, policy(), b)


def test_too_many_claims_are_refused():
    b = brief()
    p = policy()
    req = m.build_request(b, p)
    response = valid_response(req, b)
    base = response["claims"][0]
    response["claims"] = []
    for i in range(p["max_claims"] + 1):
        row = copy.deepcopy(base)
        row["claim_id"] = f"C{i}"
        response["claims"].append(row)
    with pytest.raises(ValueError, match="claims count invalid"):
        m.validate_response(req, response, p, b)


def test_blocked_brief_cannot_return_thesis():
    b = blocked_brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["kind"] = "THESIS"
    with pytest.raises(ValueError, match="kind not allowed"):
        m.validate_response(req, response, policy(), b)


def test_blocked_brief_cannot_return_wait_for_confirmation():
    b = blocked_brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["operator_disposition"] = "WAIT_FOR_CONFIRMATION"
    with pytest.raises(ValueError, match="operator disposition not allowed"):
        m.validate_response(req, response, policy(), b)


def test_question_execution_language_is_refused_too():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["questions"][0]["text"] = "Should the operator buy after confirmation?"
    with pytest.raises(ValueError, match="execution/trade language forbidden"):
        m.validate_response(req, response, policy(), b)


def test_core_module_has_no_model_network_or_process_transport_imports():
    tree = __import__("ast").parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in __import__("ast").walk(tree):
        if isinstance(node, __import__("ast").Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, __import__("ast").ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {
        "requests", "urllib", "httpx", "aiohttp", "socket", "subprocess",
        "openai", "anthropic", "google", "cohere", "mistralai"
    }
    assert imported.isdisjoint(forbidden)

def test_request_evidence_catalog_tamper_is_refused_by_reconstruction():
    b = brief()
    p = policy()
    req = m.build_request(b, p)
    response = valid_response(req, b)
    req["evidence_catalog"][0]["payload"] = {"tampered": True}
    # Recompute the local evidence id to make structural validation pass; reconstruction must still catch it.
    prefix = req["evidence_catalog"][0]["evidence_id"].split("-", 1)[0]
    req["evidence_catalog"][0]["evidence_id"] = prefix + "-" + m.stable_sha256({"tampered": True})[:12]
    req["evidence_catalog"].sort(key=lambda row: row["evidence_id"])
    with pytest.raises(ValueError, match="request/source reconstruction mismatch"):
        m.validate_response(req, response, p, b)


def test_render_prompt_refuses_tampered_response_contract():
    req = m.build_request(brief(), policy())
    req["response_contract"]["max_claims"] += 1
    with pytest.raises(ValueError, match="response contract/policy mismatch"):
        m.render_model_prompt(req, policy())


def test_metadata_date_number_is_not_numeric_fact_whitelisted():
    b = brief()
    allowed = m.allowed_numeric_literals(b)
    assert "2026" not in allowed
    assert "24" not in allowed
    assert "6.5" in allowed
    assert "119600" in allowed


def test_metadata_only_number_cannot_be_reused_as_new_claim_number():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["text"] = "The interpretation adds a score of 2026."
    with pytest.raises(ValueError, match="new numeric literal forbidden"):
        m.validate_response(req, response, policy(), b)


def test_wrong_decision_brief_input_producer_is_refused():
    b = brief()
    b["provenance"]["input_producer"] = "frozen_product_sample"
    with pytest.raises(ValueError, match="unexpected Decision Brief input producer"):
        m.build_request(b, policy())


def test_wrong_decision_brief_generator_is_refused():
    b = brief()
    b["provenance"]["generator"] = "tools/tradingos_decision_brief.py"
    with pytest.raises(ValueError, match="unexpected Decision Brief generator"):
        m.build_request(b, policy())


def test_decision_brief_source_set_must_be_exact_and_unique():
    b = brief()
    b["provenance"]["input_sources"] = b["provenance"]["input_sources"][:-1]
    with pytest.raises(ValueError, match="input source set mismatch"):
        m.build_request(b, policy())


def test_derivatives_context_is_first_class_evidence_catalog_item():
    req = m.build_request(brief(), policy())
    assert any(row["kind"] == "derivatives_context" for row in req["evidence_catalog"])

def test_structured_output_json_schema_accepts_safe_response():
    import jsonschema
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    schema = json.loads((ROOT / "schemas" / "TRADINGOS_AI_ANALYST_RESPONSE_V1.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(response, schema)


def test_structured_output_json_schema_rejects_authority_drift_and_extra_keys():
    import jsonschema
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    schema = json.loads((ROOT / "schemas" / "TRADINGOS_AI_ANALYST_RESPONSE_V1.schema.json").read_text(encoding="utf-8"))
    unsafe = copy.deepcopy(response)
    unsafe["can_trade"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(unsafe, schema)
    extra = copy.deepcopy(response)
    extra["hidden"] = "field"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(extra, schema)

def test_policy_v1_cannot_expand_claim_limit():
    p = policy()
    p["max_claims"] = 13
    with pytest.raises(ValueError, match="exact v1 limit"):
        m.validate_policy(p)


def test_policy_v1_cannot_add_claim_kind():
    p = policy()
    p["allowed_claim_kinds"].append("TRADE_PLAN")
    with pytest.raises(ValueError, match="allowed claim kinds contract mismatch"):
        m.validate_policy(p)


def test_policy_v1_rejects_extra_key():
    p = policy()
    p["allow_model_tools"] = True
    with pytest.raises(ValueError, match="policy key set mismatch"):
        m.validate_policy(p)


def test_scenario_read_requires_scenario_evidence():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["kind"] = "SCENARIO_READ"
    non_scenario = next(row["evidence_id"] for row in req["evidence_catalog"] if row["kind"] == "decision")
    response["claims"][0]["evidence_refs"] = [non_scenario]
    with pytest.raises(ValueError, match="scenario read requires scenario evidence"):
        m.validate_response(req, response, policy(), b)


def test_invalidation_read_requires_invalidation_evidence():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["claims"][0]["kind"] = "INVALIDATION_READ"
    non_inv = next(row["evidence_id"] for row in req["evidence_catalog"] if row["kind"] == "regime")
    response["claims"][0]["evidence_refs"] = [non_inv]
    with pytest.raises(ValueError, match="invalidation read requires invalidation evidence"):
        m.validate_response(req, response, policy(), b)


def test_operator_question_must_be_question():
    b = brief()
    req = m.build_request(b, policy())
    response = valid_response(req, b)
    response["questions"][0]["text"] = "Review the invalidation"
    with pytest.raises(ValueError, match="must end with question mark"):
        m.validate_response(req, response, policy(), b)

def test_pre_seal_r77_bridge_producer_is_refused_after_r77_1():
    b = brief()
    b["provenance"]["input_producer"] = "tools/tradingos_market_decision_bridge.py"
    with pytest.raises(ValueError, match="unexpected Decision Brief input producer"):
        m.build_request(b, policy())

