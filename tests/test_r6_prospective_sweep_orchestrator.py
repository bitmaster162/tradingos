from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R90_PATH = ROOT / "tools" / "r6_prospective_sweep_orchestrator.py"
HELPER_PATH = ROOT / "tests" / "test_r6_event_definition_contract.py"

spec = importlib.util.spec_from_file_location("r90", R90_PATH)
assert spec and spec.loader
r90 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = r90
spec.loader.exec_module(r90)

hs = importlib.util.spec_from_file_location("r89_helpers_r90", HELPER_PATH)
assert hs and hs.loader
helpers = importlib.util.module_from_spec(hs)
sys.modules[hs.name] = helpers
hs.loader.exec_module(helpers)


def snapshot() -> dict:
    e = helpers.payload()["r84_evidence"]
    return {"schema": r90.r84.SCHEMA, "requested_symbols": ["BTCUSDT"],
            "evidence": [e], "can_trade": False, "capital_permission": "DENY"}


def state(*, include_runtime: bool = True) -> dict:
    p = helpers.payload()
    decision = p["r84_evidence"]["quote"]["decision_time_ms"]
    out = {"schema": r90.STATE_SCHEMA, "observed_at_ms": decision - 1000,
           "provenance": "TEST_PREDECISION_STATE", "ledger_state": {
               "existing_event_ids": [], "resolved_calibration_n": 0,
               "resolved_holdout_n": 0, "holdout_freeze": "NOT_STARTED"},
           "can_trade": False, "capital_permission": "DENY"}
    if include_runtime:
        out["regime_shadow_by_symbol"] = {"BTCUSDT": "BALANCE"}
        out["cost_model"] = copy.deepcopy(p["cost_model"])
        out["risk_state"] = copy.deepcopy(p["risk_state"])
    return out


def bars(_symbol: str, _decision: int):
    return copy.deepcopy(helpers.make_bars())


def test_happy_path_reaches_registration_candidate_without_write_authority():
    out = r90.process_snapshot(snapshot(), state(), bars)
    assert out["result"] == "SWEEP_COMPLETE"
    assert out["structural_candidate_count"] == 1
    assert out["registration_candidate_count"] == 1
    assert out["ledger_write_authority"] is False
    assert out["can_trade"] is False
    assert out["registration_candidates"][0]["registration_candidate"] is True


def test_missing_runtime_state_blocks_only_after_structure():
    out = r90.process_snapshot(snapshot(), state(include_runtime=False), bars)
    assert out["structural_candidate_count"] == 1
    assert out["registration_candidate_count"] == 0
    row = out["results"][0]
    assert row["status"] == "STRUCTURAL_CANDIDATE_BLOCKED_RUNTIME_STATE"
    assert row["reason"] == "regime_shadow_missing"


def test_no_sequence_never_needs_runtime_assumptions():
    weak = helpers.make_bars()
    weak[77][2] = "100.8"
    weak[77][4] = "100.6"
    out = r90.process_snapshot(snapshot(), state(include_runtime=False), lambda *_: copy.deepcopy(weak))
    assert out["structural_candidate_count"] == 0
    assert out["registration_candidate_count"] == 0
    assert out["results"][0]["stage"] == "R89_SEQUENCE"
    assert out["results"][0]["status"] == "NO_CANDIDATE_FAIL_CLOSED"


def test_risk_failure_cannot_register():
    s = state()
    s["risk_state"]["operational_risk_state"] = "UNKNOWN"
    out = r90.process_snapshot(snapshot(), s, bars)
    assert out["structural_candidate_count"] == 1
    assert out["registration_candidate_count"] == 0
    assert out["results"][0]["stage"] == "R89_R88_R85"


def test_existing_event_id_dedupes_at_registration_adapter():
    first = r90.process_snapshot(snapshot(), state(), bars)
    event_id = first["registration_candidates"][0]["event_id"]
    s = state()
    s["ledger_state"]["existing_event_ids"] = [event_id]
    out = r90.process_snapshot(snapshot(), s, bars)
    assert out["registration_candidate_count"] == 0
    assert out["results"][0]["reason"] == "DUPLICATE_EVENT_ID_NO_WRITE"


def test_runtime_state_after_market_decision_is_rejected():
    s = state()
    decision = snapshot()["evidence"][0]["quote"]["decision_time_ms"]
    s["observed_at_ms"] = decision + 1
    out = r90.process_snapshot(snapshot(), s, bars)
    assert out["structural_candidate_count"] == 1
    assert out["registration_candidate_count"] == 0
    assert out["results"][0]["reason"] == "runtime_state_after_decision"


def test_duplicate_symbol_in_snapshot_fails_entire_sweep():
    snap = snapshot()
    snap["evidence"].append(copy.deepcopy(snap["evidence"][0]))
    out = r90.process_snapshot(snap, state(), bars)
    assert out["result"] == "SWEEP_FAIL_CLOSED"
    assert out["reason"] == "SNAPSHOT_SYMBOL_SET_INVALID"


def test_missing_cost_or_risk_is_fail_closed():
    s = state()
    del s["cost_model"]
    out = r90.process_snapshot(snapshot(), s, bars)
    assert out["results"][0]["reason"] == "cost_model_missing"
    s = state()
    del s["risk_state"]
    out = r90.process_snapshot(snapshot(), s, bars)
    assert out["results"][0]["reason"] == "risk_state_missing"


def test_calibration_30_stops_registration_before_holdout_freeze():
    s = state()
    s["ledger_state"]["resolved_calibration_n"] = 30
    out = r90.process_snapshot(snapshot(), s, bars)
    assert out["registration_candidate_count"] == 0
    assert out["results"][0]["reason"] == "R6_HOLDOUT_FREEZE_REQUIRED"


def test_result_handoff_contains_no_sheet_write_authority():
    out = r90.process_snapshot(snapshot(), state(), bars)
    assert out["sole_resolver_handoff_required"] is True
    assert all(x["ledger_write_authority"] is False for x in out["registration_candidates"])
    assert out["capital_permission"] == "DENY"


def test_ctha_rejection_precedes_sequence_scan():
    snap = snapshot()
    state_1d = snap["evidence"][0]["target_ctha"]["1d"]
    state_1d["ema_state"] = "BEAR"
    state_1d["bos_down_vs_prior5"] = True
    weak = helpers.make_bars()
    weak[77][2] = "100.8"
    weak[77][4] = "100.6"
    out = r90.process_snapshot(snap, state(include_runtime=False), lambda *_: copy.deepcopy(weak))
    row = out["results"][0]
    assert row["stage"] == "R89_CTHA"
    assert row["reason"] == "ctha_material_htf_bear_contradiction"
    assert out["structural_candidate_count"] == 0
