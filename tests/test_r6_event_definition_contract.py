from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "r6_event_definition_contract.py"
SPEC = importlib.util.spec_from_file_location("r89", PATH)
assert SPEC and SPEC.loader
r89 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r89
SPEC.loader.exec_module(r89)

BASE = 1_700_000_000_000
STEP = r89.INTERVAL_MS


def make_bars() -> list[list[object]]:
    rows: list[list[object]] = []
    for i in range(80):
        ot = BASE + i * STEP
        rows.append([ot, "100", "101", "99", "100", "10", ot + STEP - 1])
    rows[70][3] = "97"
    rows[71][2] = "103"
    rows[76][1:6] = ["99.5", "100", "96.5", "98.5", "20"]
    rows[77][1:6] = ["98.5", "102", "98.3", "101.8", "30"]
    rows[78][1:6] = ["101.7", "103.5", "100.5", "103.2", "25"]
    rows[79][1:6] = ["103.2", "105", "102.8", "104.8", "25"]
    return rows


def tf_state(decision: int, prior_high: str, *, ema: str = "BULL", bos_up: bool = True,
             bos_down: bool = False, complete: str = "FULL") -> dict:
    return {"open_time_ms": decision - 2 * STEP, "close_time_ms": decision - STEP,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "volume": 10.0, "history_bars": 130, "ema25": 99.0, "ema99": 98.0,
            "ema_state": ema, "rsi14": 55.0, "ao5_34": 1.0,
            "bos_up_vs_prior5": bos_up, "bos_down_vs_prior5": bos_down,
            "prior_high": float(prior_high), "prior_low": 90.0,
            "swept_bsl_rejected": False, "swept_ssl_reclaimed": False,
            "indicator_completeness": complete}


def quote(decision: int) -> dict:
    q = {"schema": "tradingos.binance_spot_event_time_quote.v1", "version": "1.0.0",
         "source": "binance_spot_diff_depth_local_book", "symbol": "BTCUSDT",
         "stream": "btcusdt@depth@100ms", "event_time_ms": decision - 200,
         "received_at_ms": decision - 100, "age_ms": 100, "snapshot_update_id": 100,
         "snapshot_sha256": "a" * 64, "first_update_id": 101, "final_update_id": 101,
         "bid": "104.89", "bid_qty": "2", "ask": "104.90", "ask_qty": "3",
         "raw_event_sha256": "b" * 64, "decision_time_ms": decision,
         "decision_age_ms": 200, "events_applied": 1, "can_trade": False,
         "capital_permission": "DENY", "restart_count": 0}
    q["provenance_sha256"] = r89.r88.r85.r82.stable_sha256(q)
    return q


def evidence(decision: int) -> dict:
    highs = {"15m": "108", "1h": "110", "4h": "115", "1d": "120", "1w": "130", "1M": "150"}
    target = {tf: tf_state(decision, highs[tf]) for tf in r89.REQUIRED_TFS}
    refs = {"BTCUSDT": copy.deepcopy(target), "ETHUSDT": copy.deepcopy(target)}
    return {"schema": r89.r88.r85.R84_SYMBOL_SCHEMA, "symbol": "BTCUSDT",
            "observed_at_ms": decision, "target_ctha": target, "reference_ctha": refs,
            "relative_strength": {}, "quote": quote(decision),
            "admission_authority": "EVIDENCE_ONLY_FROZEN_CURRENT_RULES_APPLY",
            "can_trade": False, "capital_permission": "DENY"}


def payload() -> dict:
    bars = make_bars()
    decision = int(bars[-1][6]) + 5 * 60 * 1000
    return {"schema": r89.INPUT_SCHEMA, "r84_evidence": evidence(decision),
            "m15_closed_bars": bars,
            "cost_model": {"entry_fee_rate": "0.001", "exit_fee_rate": "0.001",
                           "entry_slippage_bps": "2", "exit_slippage_bps": "2"},
            "risk_state": {"proposed_risk_pct": "0.5", "aggregate_open_risk_pct": "1.0",
                           "drawdown_pct": "2", "base_risk_unit_pct": "0.5",
                           "entries_today": 2, "consecutive_net_losses": 0,
                           "pause_until_ms": 0, "completed_week_expectancy_r": "0.1",
                           "operational_risk_state": "PASS"},
            "expectancy": {"state": "RESEARCH_PENDING"}}


def compile(p: dict | None = None) -> dict:
    return r89.compile_candidate(p or payload())


def test_contract_is_frozen_and_nonexecuting():
    assert r89.CONTRACT["contract_id"] == r89.CONTRACT_ID
    assert r89.CONTRACT["authority"] == {"can_trade": False, "capital_permission": "DENY"}
    assert r89.CONTRACT["displacement_true_range_atr14_min"] == "1.20"
    assert r89.CONTRACT["bullish_fvg_gap_atr14_min"] == "0.05"


def test_happy_path_compiles_through_r88_and_r85():
    out = compile()
    assert out["result"] == "R89_CANDIDATE_COMPILED_R88_PASS"
    assert out["candidate_compiled"] is True
    assert out["r88_result"]["r85_evaluation"]["trial_eligibility"] == "R6_SHADOW_TRIAL_ELIGIBLE"
    assert out["ledger_write_authority"] is False
    assert out["can_trade"] is False


def test_event_sequence_is_exact_and_chronological():
    out = compile()
    events = out["r88_input"]["structural_event_evidence"]
    assert [x["type"] for x in events] == ["LIQUIDITY_SWEEP_RECLAIM", "DISPLACEMENT", "MSS", "BOS"]
    assert [x["time_ms"] for x in events] == sorted(x["time_ms"] for x in events)
    assert len({x["reaction_id"] for x in events}) == 1


def test_invalidation_and_nearest_target_are_deterministic():
    out = compile()
    levels = out["r88_input"]["structural_levels"]
    assert levels["invalidation"]["price"] == "96.5"
    assert min(float(x["price"]) for x in levels["targets"]) == 108.0
    assert out["r88_result"]["r85_evaluation"]["gates"]["target"]["evidence"]["price"] == "108.0"


def test_weak_displacement_fails_closed():
    p = payload()
    p["m15_closed_bars"][77][2] = "100.8"
    p["m15_closed_bars"][77][4] = "100.6"
    out = compile(p)
    assert out["reason"] == "R89_EVIDENCE_DEFINITION_FAILED"
    assert out["details"]["error"] == "no_complete_r89_sequence"


def test_micro_or_missing_fvg_fails_closed():
    p = payload()
    p["m15_closed_bars"][78][3] = "99.95"
    out = compile(p)
    assert out["reason"] == "R89_EVIDENCE_DEFINITION_FAILED"
    assert out["details"]["error"] == "no_complete_r89_sequence"


def test_sweep_without_reclaim_fails_closed():
    p = payload()
    p["m15_closed_bars"][76][4] = "96.8"
    out = compile(p)
    assert out["reason"] == "R89_EVIDENCE_DEFINITION_FAILED"


def test_m15_sequence_gap_fails_before_detection():
    p = payload()
    p["m15_closed_bars"][40][0] += 1
    p["m15_closed_bars"][40][6] += 1
    out = compile(p)
    assert out["details"]["error"] == "bar_40_sequence_gap"


def test_bos_stale_at_decision_fails_closed():
    p = payload()
    q = p["r84_evidence"]["quote"]
    decision = q["decision_time_ms"] + r89.MAX_DECISION_LAG_MS
    q["decision_time_ms"] = decision
    q["event_time_ms"] = decision - 200
    q["received_at_ms"] = decision - 100
    q["provenance_sha256"] = r89.r88.r85.r82.stable_sha256({k: v for k, v in q.items() if k != "provenance_sha256"})
    out = compile(p)
    assert out["details"]["error"] == "no_complete_r89_sequence"


def test_material_htf_bear_contradiction_fails_closed():
    p = payload()
    state = p["r84_evidence"]["target_ctha"]["1d"]
    state["ema_state"] = "BEAR"
    state["bos_down_vs_prior5"] = True
    out = compile(p)
    assert out["details"]["error"] == "ctha_material_htf_bear_contradiction"


def test_monthly_partial_ema99_is_allowed_with_36_months_and_ema25():
    p = payload()
    p["r84_evidence"]["target_ctha"]["1M"]["indicator_completeness"] = "PARTIAL_INSUFFICIENT_EMA99"
    p["r84_evidence"]["target_ctha"]["1M"]["ema99"] = None
    out = compile(p)
    assert out["candidate_compiled"] is True


def test_monthly_under_36_bars_fails_closed():
    p = payload()
    p["r84_evidence"]["target_ctha"]["1M"]["history_bars"] = 35
    out = compile(p)
    assert out["details"]["error"] == "ctha_1M_history_insufficient"


def test_no_overhead_target_fails_closed():
    p = payload()
    for tf in r89.REQUIRED_TFS:
        p["r84_evidence"]["target_ctha"][tf]["prior_high"] = 104.0
    out = compile(p)
    assert out["details"]["error"] == "no_r84_overhead_target"


def test_tampered_quote_provenance_fails_at_r84_gate():
    p = payload()
    p["r84_evidence"]["quote"]["ask"] = "105.00"
    out = compile(p)
    assert out["details"]["error"] == "r84_evidence_invalid"


def test_unknown_operational_risk_is_rejected_by_r88_r85():
    p = payload()
    p["risk_state"]["operational_risk_state"] = "UNKNOWN"
    out = compile(p)
    assert out["reason"] == "R88_REJECTED_R89_EVIDENCE"
    evaluation = out["details"]["r88_result"]["details"]["evaluation"]
    assert evaluation["gates"]["risk"]["status"] == "UNKNOWN"


def test_reaction_id_and_compiled_input_are_deterministic():
    a = compile(payload())
    b = compile(payload())
    assert a["r88_input_sha256"] == b["r88_input_sha256"]
    ea = a["r88_input"]["structural_event_evidence"]
    eb = b["r88_input"]["structural_event_evidence"]
    assert ea[0]["reaction_id"] == eb[0]["reaction_id"]
