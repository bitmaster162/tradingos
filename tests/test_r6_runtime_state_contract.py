from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "r6_runtime_state_contract.py"
SPEC = importlib.util.spec_from_file_location("r91", PATH)
assert SPEC and SPEC.loader
r91 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r91
SPEC.loader.exec_module(r91)

OBS = int(datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)


def base_payload() -> dict:
    return {
        "schema": r91.INPUT_SCHEMA,
        "observed_at_ms": OBS,
        "provenance": "TEST_LEDGER_READBACK_PREDECISION",
        "operational_risk_state": "PASS",
        "ledger_state": {
            "existing_event_ids": [],
            "resolved_calibration_n": 0,
            "resolved_holdout_n": 0,
            "holdout_freeze": "NOT_STARTED",
        },
        "ledger_events": [],
    }


def event(event_id: str, registered: int, resolved: int | None = None,
          outcome: str | None = None) -> dict:
    return {
        "event_id": event_id,
        "registered_at_ms": registered,
        "resolved_at_ms": resolved,
        "outcome_r": outcome,
    }


def with_events(items: list[dict]) -> dict:
    p = base_payload()
    p["ledger_events"] = items
    p["ledger_state"]["existing_event_ids"] = [x["event_id"] for x in items]
    p["ledger_state"]["resolved_calibration_n"] = sum(
        1 for x in items if x.get("resolved_at_ms") is not None
    )
    return p


def test_empty_ledger_builds_frozen_cost_and_lower_risk_unit():
    out = r91.build_runtime_state(base_payload())
    assert out["cost_model"] == {
        "entry_fee_rate": "0.0005", "exit_fee_rate": "0.0005",
        "entry_slippage_bps": "2", "exit_slippage_bps": "2"}
    assert out["cost_stress_model"]["base_total_bps_per_side"] == "7"
    assert out["cost_stress_model"]["stress_total_bps_per_side"] == "17"
    assert out["risk_state"]["proposed_risk_pct"] == "0.5"
    assert out["risk_state"]["aggregate_open_risk_pct"] == "0.0"


def test_runtime_state_is_directly_consumable_by_r90():
    out = r91.build_runtime_state(base_payload())
    assert r91.r90._state(out) == out
    runtime, regime = r91.r90._symbol_state(out, "BTCUSDT", OBS + 1)
    assert runtime["cost_model"]["entry_fee_rate"] == "0.0005"
    assert runtime["risk_state"]["operational_risk_state"] == "PASS"
    assert regime == r91.REGIME_LABEL


def test_two_open_events_use_one_percent_aggregate_risk():
    p = with_events([
        event("E1", OBS - 10_000), event("E2", OBS - 5_000),
    ])
    out = r91.build_runtime_state(p)
    assert out["risk_state"]["aggregate_open_risk_pct"] == "1.0"
    assert out["risk_state"]["entries_today"] == 2


def test_three_losses_activate_one_hour_pause():
    items = [
        event("L1", OBS - 400_000, OBS - 300_000, "-1"),
        event("L2", OBS - 300_000, OBS - 200_000, "-0.5"),
        event("L3", OBS - 200_000, OBS - 100_000, "-0.2"),
    ]
    out = r91.build_runtime_state(with_events(items))
    assert out["risk_state"]["consecutive_net_losses"] == 3
    assert out["risk_state"]["pause_until_ms"] == OBS - 100_000 + 3_600_000


def test_negative_completed_week_halves_next_risk_unit():
    prior = int(datetime(2026, 9, 10, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
    items = [event("W1", prior - 1_000, prior, "-1"),
             event("W2", prior + 1_000, prior + 2_000, "0.2")]
    out = r91.build_runtime_state(with_events(items))
    assert Decimal(out["risk_state"]["completed_week_expectancy_r"]) < 0
    assert out["risk_state"]["proposed_risk_pct"] == "0.25"


def test_positive_completed_week_keeps_half_percent():
    prior = int(datetime(2026, 9, 10, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
    items = [event("W1", prior - 1_000, prior, "1"),
             event("W2", prior + 1_000, prior + 2_000, "0.2")]
    out = r91.build_runtime_state(with_events(items))
    assert Decimal(out["risk_state"]["completed_week_expectancy_r"]) > 0
    assert out["risk_state"]["proposed_risk_pct"] == "0.5"


def test_drawdown_is_derived_from_resolved_r_path():
    items = [event("D1", OBS - 4_000, OBS - 3_000, "2"),
             event("D2", OBS - 2_000, OBS - 1_000, "-3")]
    out = r91.build_runtime_state(with_events(items))
    assert Decimal(out["risk_state"]["drawdown_pct"]) > 0


def test_duplicate_event_ids_fail_closed():
    p = base_payload()
    p["ledger_state"]["existing_event_ids"] = ["E1", "E1"]
    with pytest.raises(ValueError, match="existing_event_ids_duplicate"):
        r91.build_runtime_state(p)


def test_event_after_observation_is_rejected():
    p = with_events([event("E1", OBS + 1)])
    with pytest.raises(ValueError, match="ledger_event_after_observation"):
        r91.build_runtime_state(p)


def test_ledger_event_must_exist_in_id_readback():
    p = with_events([event("E1", OBS - 1_000)])
    p["ledger_state"]["existing_event_ids"] = []
    with pytest.raises(ValueError, match="ledger_event_not_in_existing_ids"):
        r91.build_runtime_state(p)


def test_unknown_operational_state_is_preserved_not_promoted():
    p = base_payload()
    p["operational_risk_state"] = "UNKNOWN"
    out = r91.build_runtime_state(p)
    assert out["risk_state"]["operational_risk_state"] == "UNKNOWN"


def test_all16_regimes_are_explicitly_unclassified_predecision():
    out = r91.build_runtime_state(base_payload())
    assert set(out["regime_shadow_by_symbol"]) == set(r91.r90.r84.ALL16)
    assert set(out["regime_shadow_by_symbol"].values()) == {r91.REGIME_LABEL}
    assert out["can_trade"] is False
    assert out["capital_permission"] == "DENY"
