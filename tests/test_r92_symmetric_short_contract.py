from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "r6_short_event_contract.py"
SPEC = importlib.util.spec_from_file_location("r92short", PATH)
assert SPEC and SPEC.loader
r92 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r92
SPEC.loader.exec_module(r92)

BASE = 1_700_000_000_000
STEP = r92.base.INTERVAL_MS


def make_bars() -> list[list[object]]:
    rows = []
    for i in range(80):
        ot = BASE + i * STEP
        rows.append([ot, "100", "101", "99", "100", "10", ot + STEP - 1])
    rows[70][2] = "103"
    rows[71][3] = "97"
    rows[76][1:6] = ["100.5", "103.5", "100", "101.5", "20"]
    rows[77][1:6] = ["101.5", "101.7", "98", "98.2", "30"]
    rows[78][1:6] = ["98.3", "99.5", "96.5", "96.8", "25"]
    rows[79][1:6] = ["96.8", "97.2", "95", "95.2", "25"]
    return rows

def tf_state(decision: int, prior_low: float) -> dict:
    return {"open_time_ms": decision - 2 * STEP, "close_time_ms": decision - STEP,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "volume": 10.0, "history_bars": 130, "ema25": 101.0, "ema99": 102.0,
            "ema_state": "BEAR", "rsi14": 45.0, "ao5_34": -1.0,
            "bos_up_vs_prior5": False, "bos_down_vs_prior5": True,
            "prior_high": 110.0, "prior_low": prior_low,
            "swept_bsl_rejected": False, "swept_ssl_reclaimed": False,
            "indicator_completeness": "FULL"}


def quote(decision: int) -> dict:
    q = {"schema": "tradingos.binance_spot_event_time_quote.v1", "version": "1.0.0",
         "source": "binance_spot_diff_depth_local_book", "symbol": "BTCUSDT",
         "stream": "btcusdt@depth@100ms", "event_time_ms": decision - 200,
         "received_at_ms": decision - 100, "age_ms": 100, "snapshot_update_id": 100,
         "snapshot_sha256": "a" * 64, "first_update_id": 101, "final_update_id": 101,
         "bid": "95.10", "bid_qty": "2", "ask": "95.11", "ask_qty": "3",
         "raw_event_sha256": "b" * 64, "decision_time_ms": decision,
         "decision_age_ms": 200, "events_applied": 1, "can_trade": False,
         "capital_permission": "DENY", "restart_count": 0}
    q["provenance_sha256"] = r92.compiler.evaluator.base.r82.stable_sha256(q)
    return q


def evidence(decision: int) -> dict:
    lows = {"15m": 92.0, "1h": 90.0, "4h": 85.0, "1d": 80.0, "1w": 70.0, "1M": 60.0}
    target = {tf: tf_state(decision, lows[tf]) for tf in r92.base.REQUIRED_TFS}
    refs = {"BTCUSDT": copy.deepcopy(target), "ETHUSDT": copy.deepcopy(target)}
    return {"schema": r92.compiler.evaluator.base.R84_SYMBOL_SCHEMA, "symbol": "BTCUSDT",
            "observed_at_ms": decision, "target_ctha": target, "reference_ctha": refs,
            "relative_strength": {}, "quote": quote(decision),
            "admission_authority": "EVIDENCE_ONLY_FROZEN_CURRENT_RULES_APPLY",
            "can_trade": False, "capital_permission": "DENY"}

def payload() -> dict:
    bars = make_bars()
    decision = int(bars[-1][6]) + 5 * 60 * 1000
    return {"schema": r92.INPUT_SCHEMA, "r84_evidence": evidence(decision),
            "m15_closed_bars": bars,
            "cost_model": {"entry_fee_rate": "0.0005", "exit_fee_rate": "0.0005",
                           "entry_slippage_bps": "2", "exit_slippage_bps": "2"},
            "risk_state": {"proposed_risk_pct": "0.5", "aggregate_open_risk_pct": "0",
                           "drawdown_pct": "0", "base_risk_unit_pct": "0.5",
                           "entries_today": 0, "consecutive_net_losses": 0,
                           "pause_until_ms": 0, "completed_week_expectancy_r": None,
                           "operational_risk_state": "PASS"},
            "expectancy": {"state": "RESEARCH_PENDING"}}


def test_short_happy_path_compiles():
    out = r92.compile_candidate(payload())
    assert out["candidate_compiled"] is True
    ev = out["compiler_result"]["r92_evaluation"]
    assert ev["trial_eligibility"] == "R6_SHADOW_TRIAL_ELIGIBLE"
    assert ev["direction"] == "SHORT"
    assert ev["gates"]["invalidation"]["evidence"]["price"] == "103.5"
    assert ev["gates"]["target"]["evidence"]["price"] == "92.0"
    assert float(ev["gates"]["economics"]["evidence"]["reward"]) > 0


def test_short_sequence_is_mirrored_and_chronological():
    out = r92.compile_candidate(payload())
    events = out["compiler_input"]["structural_event_evidence"]
    assert [x["type"] for x in events] == ["LIQUIDITY_SWEEP_REJECT", "DISPLACEMENT", "MSS", "BOS"]
    assert all(x["direction"] == "SHORT" for x in events)
    assert [x["time_ms"] for x in events] == sorted(x["time_ms"] for x in events)

def test_short_without_bsl_reject_fails_closed():
    p = payload()
    p["m15_closed_bars"][76][4] = "103.2"
    out = r92.compile_candidate(p)
    assert out["candidate_compiled"] is False


def test_short_weak_displacement_fails_closed():
    p = payload()
    p["m15_closed_bars"][77][3] = "99.8"
    p["m15_closed_bars"][77][4] = "100.0"
    out = r92.compile_candidate(p)
    assert out["candidate_compiled"] is False


def test_short_bullish_htf_contradiction_fails_closed():
    p = payload()
    s = p["r84_evidence"]["target_ctha"]["1d"]
    s["ema_state"] = "BULL"
    s["bos_up_vs_prior5"] = True
    out = r92.compile_candidate(p)
    assert out["candidate_compiled"] is False
    assert out["details"]["error"] == "ctha_material_htf_bull_contradiction"


def test_short_requires_nearest_downside_target():
    out = r92.compile_candidate(payload())
    ev = out["compiler_result"]["r92_evaluation"]
    assert ev["gates"]["target"]["evidence"]["price"] == "92.0"


def test_short_tampered_bid_provenance_fails():
    p = payload()
    p["r84_evidence"]["quote"]["bid"] = "94.00"
    out = r92.compile_candidate(p)
    assert out["candidate_compiled"] is False


def test_contract_nonexecuting():
    assert r92.CONTRACT["authority"] == {"can_trade": False, "capital_permission": "DENY"}
    assert r92.CONTRACT["thresholds_identical_magnitude"] is True

def _load_tool(name: str, filename: str):
    path = ROOT / "tools" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_short_registration_candidate_end_to_end():
    regmod = _load_tool("r92reg_test", "r6_short_registration_adapter.py")
    r87 = _load_tool("r87_test", "r6_frozen_setup_contract.py")
    out = r92.compile_candidate(payload())
    cr = out["compiler_result"]
    decision = cr["r92_evaluation"]["decision_time_ms"]
    reg = regmod.build_candidate({"schema": regmod.INPUT_SCHEMA,
        "r92_input": cr["r92_input"], "r92_evaluation": cr["r92_evaluation"],
        "ledger_state": {"existing_event_ids": [], "resolved_calibration_n": 0,
                         "resolved_holdout_n": 0, "holdout_freeze": "NOT_STARTED"},
        "registration_contract": r87.build_contract(decision, "BALANCE")})
    assert reg["registration_candidate"] is True
    row = reg["row_values"]
    assert row["CURRENT_Decision"] == "ENTER_SHORT_AT_R92_DECISION_QUOTE"
    assert row["CURRENT_Stop"] > row["CURRENT_Entry_Price"] > row["CURRENT_Target"]
    assert reg["ledger_write_authority"] is False

def test_symmetric_orchestrator_registers_short_only():
    orch = _load_tool("r92orch_test", "r6_symmetric_sweep_orchestrator.py")
    p = payload()
    decision = p["r84_evidence"]["quote"]["decision_time_ms"]
    state = {"schema": orch.base.STATE_SCHEMA, "observed_at_ms": decision - 1000,
             "provenance": "SYNTHETIC_PREDECISION_TEST", "can_trade": False,
             "capital_permission": "DENY",
             "ledger_state": {"existing_event_ids": [], "resolved_calibration_n": 0,
                              "resolved_holdout_n": 0, "holdout_freeze": "NOT_STARTED"},
             "regime_shadow_by_symbol": {"BTCUSDT": "BALANCE"},
             "cost_model": p["cost_model"], "risk_state": p["risk_state"]}
    snap = {"schema": orch.base.r84.SCHEMA, "requested_symbols": ["BTCUSDT"],
            "evidence": [p["r84_evidence"]], "can_trade": False, "capital_permission": "DENY"}
    out = orch.process_snapshot(snap, state, fetch_m15=lambda symbol, ms: p["m15_closed_bars"])
    assert out["registration_candidate_count"] == 1
    assert out["short_candidate_count"] == 1
    assert out["long_candidate_count"] == 0
    assert out["results"][0]["direction"] == "SHORT"
    assert out["ledger_write_authority"] is False


def test_intra_sweep_risk_reservation_updates_aggregate_and_entries():
    orch = _load_tool("r92orch_reserve", "r6_symmetric_sweep_orchestrator.py")
    state = {"risk_state": {"proposed_risk_pct": "0.5", "aggregate_open_risk_pct": "1.0",
                            "entries_today": 2}}
    orch._reserve_risk(state, "BTCUSDT")
    assert state["risk_state"]["aggregate_open_risk_pct"] == "1.5"
    assert state["risk_state"]["entries_today"] == 3
