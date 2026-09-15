from __future__ import annotations

from decimal import Decimal
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
R87_PATH = ROOT / "tools" / "r6_frozen_setup_contract.py"
SPEC = importlib.util.spec_from_file_location("r87_contract", R87_PATH)
assert SPEC and SPEC.loader
r87 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r87
SPEC.loader.exec_module(r87)


def test_horizon_is_96_full_m15_bars_strictly_after_decision():
    decision = 1_000_200
    geo = r87.horizon_geometry(decision)
    first = (decision // r87.INTERVAL_MS + 1) * r87.INTERVAL_MS
    assert geo["first_eligible_open_ms"] == first
    assert geo["horizon_bar_open_ms"] == first + 95 * r87.INTERVAL_MS
    assert geo["horizon_end_ms"] == first + 96 * r87.INTERVAL_MS - 1


def test_exact_bar_boundary_still_starts_next_bar():
    decision = 10 * r87.INTERVAL_MS
    geo = r87.horizon_geometry(decision)
    assert geo["first_eligible_open_ms"] == decision + r87.INTERVAL_MS


def test_contract_is_exact_and_mutation_fails():
    c = r87.build_contract(1_000_200, "BALANCE")
    assert r87.validate_contract(c, 1_000_200) == c
    c["horizon_bars"] = 95
    with pytest.raises(ValueError, match="r87_contract_mismatch"):
        r87.validate_contract(c, 1_000_200)


def test_contract_authority_and_entry_mode_are_frozen():
    c = r87.build_contract(1_000_200, "RESET")
    assert c["contract_id"] == r87.CONTRACT_ID
    assert c["setup_family"] == r87.SETUP_FAMILY
    assert c["direction"] == "LONG"
    assert c["entry_mode"] == "ENTRY_AT_R85_DECISION_QUOTE"


def test_resolution_policy_has_time_exit_and_fail_closed_sequence():
    p = r87.resolution_policy()
    assert p["horizon_terminal"] == "TERMINAL_TIME_EXIT"
    assert p["coarse_bar_both_reachable"] == "RESOLUTION_PENDING_SEQUENCE"
    assert p["no_hindsight_extension"] is True
    assert p["can_trade"] is False
    assert p["capital_permission"] == "DENY"


def test_time_exit_uses_frozen_costs_and_initial_risk():
    costs = {
        "entry_fee_rate": "0.001",
        "exit_fee_rate": "0.001",
        "entry_slippage_bps": "2",
        "exit_slippage_bps": "2",
    }
    settlement = r87.settlement_spec(costs, "5.2")
    out = r87.time_exit_outcome("100.02", "104", settlement)
    close = Decimal("104")
    exit_fill = close * (Decimal("1") - Decimal("2") / Decimal("10000"))
    expected = (exit_fill - Decimal("100.02") - Decimal("0.001") * Decimal("100.02") - Decimal("0.001") * exit_fill) / Decimal("5.2")
    assert Decimal(out["outcome_r"]) == expected
    assert out["terminal_event"] == "TERMINAL_TIME_EXIT"


def test_time_exit_can_be_negative_without_clamping():
    costs = {
        "entry_fee_rate": "0.001",
        "exit_fee_rate": "0.001",
        "entry_slippage_bps": "2",
        "exit_slippage_bps": "2",
    }
    settlement = r87.settlement_spec(costs, "5")
    out = r87.time_exit_outcome("100", "99", settlement)
    assert Decimal(out["outcome_r"]) < 0


def test_negative_cost_is_rejected():
    costs = {
        "entry_fee_rate": "0.001",
        "exit_fee_rate": "0.001",
        "entry_slippage_bps": "2",
        "exit_slippage_bps": "-1",
    }
    with pytest.raises(ValueError, match="negative_cost_input"):
        r87.settlement_spec(costs, "5")
