from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "r6_admission_evaluator.py"
SPEC = importlib.util.spec_from_file_location("r85", PATH)
assert SPEC and SPEC.loader
r85 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r85
SPEC.loader.exec_module(r85)

NOW = 1_000_200


def quote(symbol: str = "BTCUSDT") -> dict:
    q = {
        "schema": "tradingos.binance_spot_event_time_quote.v1", "version": "1.0.0",
        "source": "binance_spot_diff_depth_local_book", "symbol": symbol,
        "stream": f"{symbol.lower()}@depth@100ms", "event_time_ms": 1_000_000,
        "received_at_ms": 1_000_100, "age_ms": 100, "snapshot_update_id": 100,
        "snapshot_sha256": "a" * 64, "first_update_id": 101, "final_update_id": 101,
        "bid": "99.99", "bid_qty": "2", "ask": "100", "ask_qty": "3",
        "raw_event_sha256": "b" * 64, "decision_time_ms": 1_000_200,
        "decision_age_ms": 200, "events_applied": 1, "can_trade": False,
        "capital_permission": "DENY", "restart_count": 0,
    }
    q["provenance_sha256"] = r85.r82.stable_sha256(q)
    return q


def evidence(symbol: str = "BTCUSDT") -> dict:
    blank_tf = {tf: {"close": 1.0} for tf in r85.REQUIRED_TFS}
    return {
        "schema": r85.R84_SYMBOL_SCHEMA, "symbol": symbol, "observed_at_ms": NOW,
        "target_ctha": copy.deepcopy(blank_tf),
        "reference_ctha": {"BTCUSDT": copy.deepcopy(blank_tf), "ETHUSDT": copy.deepcopy(blank_tf)},
        "relative_strength": {}, "quote": quote(symbol),
        "admission_authority": "EVIDENCE_ONLY_FROZEN_CURRENT_RULES_APPLY",
        "can_trade": False, "capital_permission": "DENY",
    }


def bundle() -> dict:
    return {
        "setup_family": r85.SETUP_FAMILY, "direction": "LONG",
        "ctha": {"status": "PASS", "material_contradiction": False, "direction": "LONG",
                 "mapped_timeframes": list(r85.REQUIRED_TFS), "known_at_ms": 900_000,
                 "evidence_refs": ["r84:test"]},
        "p": {"status": "PASS", "direction": "LONG", "bias": "bullish",
              "liquidity_objective": "SSL sweep then upside liquidity", "htf_invalidation": "95",
              "reaction_id": "RX1", "known_at_ms": 905_000},
        "r": {"setup_family": r85.SETUP_FAMILY, "reaction_id": "RX1", "events": [
            {"type": "SWEEP_RECLAIM", "direction": "LONG", "reaction_id": "RX1", "time_ms": 910_000},
            {"type": "DISPLACEMENT", "direction": "LONG", "reaction_id": "RX1", "time_ms": 920_000},
            {"type": "MSS", "direction": "LONG", "reaction_id": "RX1", "time_ms": 930_000},
            {"type": "BOS", "direction": "LONG", "reaction_id": "RX1", "time_ms": 940_000},
        ]},
        "c": [{"type": "FVG", "status": "PASS", "direction": "LONG",
               "reaction_id": "RX1", "known_at_ms": 950_000, "linked_to_displacement": True}],
        "invalidation": {"price": "95", "known_at_ms": 955_000},
        "targets": [
            {"price": "110", "valid": True, "known_at_ms": 956_000},
            {"price": "105", "valid": True, "known_at_ms": 957_000},
        ],
        "chosen_target": "105",
        "cost_model": {"entry_fee_rate": "0.001", "exit_fee_rate": "0.001",
                       "entry_slippage_bps": "2", "exit_slippage_bps": "2"},
        "risk": {"proposed_risk_pct": "0.5", "aggregate_open_risk_pct": "1.0",
                 "drawdown_pct": "2", "base_risk_unit_pct": "0.5", "entries_today": 2,
                 "consecutive_net_losses": 0, "pause_until_ms": 0,
                 "completed_week_expectancy_r": "0.1", "operational_risk_state": "PASS"},
        "expectancy": {"state": "RESEARCH_PENDING"},
    }


def payload() -> dict:
    return {"schema": r85.INPUT_SCHEMA, "decision_time_ms": NOW,
            "r84_evidence": evidence(), "decision_bundle": bundle()}


def evaluate(p: dict | None = None) -> dict:
    return r85.evaluate(p or payload())


def test_research_pending_is_trial_eligible_but_not_a_grade():
    out = evaluate()
    assert out["trial_eligibility"] == "R6_SHADOW_TRIAL_ELIGIBLE"
    assert out["a_grade_candidate"] is False
    assert out["calibration_registration_candidate"] is True
    assert out["ledger_write_authority"] is False


def test_robust_positive_can_be_a_grade_candidate():
    p = payload()
    p["decision_bundle"]["expectancy"]["state"] = "ROBUST_POSITIVE"
    out = evaluate(p)
    assert out["trial_eligibility"] == "R6_SHADOW_TRIAL_ELIGIBLE"
    assert out["a_grade_candidate"] is True


def test_material_ctha_contradiction_fails_closed():
    p = payload()
    p["decision_bundle"]["ctha"]["material_contradiction"] = True
    out = evaluate(p)
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"
    assert out["gates"]["ctha"]["status"] == "FAIL"


def test_r_chronology_break_fails_closed():
    p = payload()
    events = p["decision_bundle"]["r"]["events"]
    events[1]["time_ms"], events[2]["time_ms"] = events[2]["time_ms"], events[1]["time_ms"]
    out = evaluate(p)
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"
    assert out["gates"]["r"]["status"] == "FAIL"


def test_wrong_reaction_confluence_fails_closed():
    p = payload()
    p["decision_bundle"]["c"][0]["reaction_id"] = "OTHER"
    out = evaluate(p)
    assert out["gates"]["c"]["status"] == "FAIL"
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"


def test_farther_target_cannot_replace_nearest():
    p = payload()
    p["decision_bundle"]["chosen_target"] = "110"
    out = evaluate(p)
    assert out["gates"]["target"]["reason"] == "CHOSEN_TARGET_NOT_NEAREST"
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"


def test_tampered_quote_provenance_fails_closed():
    p = payload()
    p["r84_evidence"]["quote"]["ask"] = "101"
    out = evaluate(p)
    assert out["gates"]["r84"]["status"] == "FAIL"
    assert "QUOTE_GATE" in out["gates"]["r84"]["reason"]


def test_stale_quote_fails_closed():
    p = payload()
    q = p["r84_evidence"]["quote"]
    q["event_time_ms"] = 997_000
    q["provenance_sha256"] = r85.r82.stable_sha256({k:v for k,v in q.items() if k != "provenance_sha256"})
    out = evaluate(p)
    assert out["gates"]["r84"]["status"] == "FAIL"
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"


def test_aggregate_risk_limit_fails_closed():
    p = payload()
    p["decision_bundle"]["risk"]["aggregate_open_risk_pct"] = "2.7"
    out = evaluate(p)
    assert out["gates"]["risk"]["reason"] == "AGGREGATE_RISK_LIMIT"
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"


def test_three_loss_pause_fails_closed():
    p = payload()
    risk = p["decision_bundle"]["risk"]
    risk["consecutive_net_losses"] = 3
    risk["pause_until_ms"] = NOW + 60_000
    out = evaluate(p)
    assert out["gates"]["risk"]["reason"] == "THREE_LOSS_PAUSE_ACTIVE"


def test_negative_week_requires_halved_risk_unit():
    p = payload()
    risk = p["decision_bundle"]["risk"]
    risk["completed_week_expectancy_r"] = "-0.1"
    risk["base_risk_unit_pct"] = "1.0"
    risk["proposed_risk_pct"] = "0.6"
    out = evaluate(p)
    assert out["gates"]["risk"]["reason"] == "NEGATIVE_WEEK_RISK_NOT_HALVED"


def test_rejected_expectancy_fails_trial():
    p = payload()
    p["decision_bundle"]["expectancy"]["state"] = "REJECTED"
    out = evaluate(p)
    assert out["gates"]["expectancy"]["status"] == "FAIL"
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"


def test_unsupported_direction_does_not_get_trial_eligibility():
    p = payload()
    p["decision_bundle"]["direction"] = "SHORT"
    out = evaluate(p)
    assert out["gates"]["setup"]["status"] == "UNKNOWN"
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"


def test_wrong_side_invalidation_fails_closed():
    p = payload()
    p["decision_bundle"]["invalidation"]["price"] = "101"
    out = evaluate(p)
    assert out["gates"]["invalidation"]["status"] == "FAIL"


def test_p_r_reaction_mismatch_fails_closed():
    p = payload()
    p["decision_bundle"]["p"]["reaction_id"] = "P_OTHER"
    out = evaluate(p)
    assert out["gates"]["reaction_link"]["status"] == "FAIL"


def test_unknown_operational_risk_fails_closed():
    p = payload()
    p["decision_bundle"]["risk"]["operational_risk_state"] = "UNKNOWN"
    out = evaluate(p)
    assert out["gates"]["risk"]["status"] == "UNKNOWN"
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"


def test_bearish_p_bias_cannot_pass_long_family():
    p = payload()
    p["decision_bundle"]["p"]["bias"] = "bearish"
    out = evaluate(p)
    assert out["gates"]["p"]["reason"] == "P_BIAS_CONTRADICTS_LONG"
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"


def test_confluence_before_reaction_cannot_be_linked():
    p = payload()
    p["decision_bundle"]["c"][0]["known_at_ms"] = 900_000
    out = evaluate(p)
    assert out["gates"]["c"]["status"] == "FAIL"
    assert out["trial_eligibility"] == "NOT_ELIGIBLE_FAIL_CLOSED"
