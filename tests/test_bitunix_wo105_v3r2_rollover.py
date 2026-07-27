from __future__ import annotations

import copy
import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator_v3 as evaluator
from tools import bitunix_wo105_v3r2_rollover as module


ROOT = Path(__file__).resolve().parents[1]


def predecessor() -> dict:
    return json.loads(
        (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R1_2026-07-14.json").read_text(
            encoding="utf-8-sig"
        )
    )


def accepted_first_cycle() -> dict:
    return {
        "cohort_id": "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3R1_20260714",
        "decision": "bitunix_wo105_v3_first_cycle_accepted_shadow_only",
        "checks": {"transition": True, "rest": True, "ws": True, "packet": True},
        "failures": [],
        "can_trade": False,
    }


def zero_status() -> dict:
    return {
        "cohort_id": "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3R1_20260714",
        "forward_events": 0,
        "terminal_forward_events": 0,
        "edge_evaluated": False,
    }


def clock_audit() -> dict:
    return {
        "decision": "bitunix_wo105_v3r1_zero_skew_contract_defect_confirmed",
        "outcome_metrics_inspected": False,
    }


def bindings() -> dict[str, tuple[str, str]]:
    return {
        "predecessor_v3r1_lock": ("configs/predecessor.json", "a" * 64),
        "predecessor_v3r1_first_cycle_gate": ("docs/gate.json", "b" * 64),
        "predecessor_v3r1_status": ("docs/status.json", "c" * 64),
        "predecessor_v3r1_runtime_stop_receipt": ("docs/stop.json", "d" * 64),
        "predecessor_v3r1_clock_contract_audit": ("docs/audit.json", "e" * 64),
        "liquidation_context_v2": ("tools/bitunix_wo105_liquidation_context_v2.py", "f" * 64),
        "packet_assembler_v4": ("tools/bitunix_wo105_packet_assembler_v4.py", "1" * 64),
    }


def test_rollover_preserves_strategy_parameter_hash_and_changes_only_operational_contract() -> None:
    old = predecessor()
    tombstone, lock = module.build_artifacts(
        predecessor=old,
        first_cycle=accepted_first_cycle(),
        status=zero_status(),
        stop_receipt={"decision": "bitunix_wo105_v3r1_runtime_stopped_verified", "can_trade": False},
        clock_audit=clock_audit(),
        frozen_at="2026-07-14T17:40:00Z",
        forward_start="2026-07-14T18:00:00Z",
        source_bindings=bindings(),
    )

    assert lock["params"] == old["params"]
    assert lock["parameter_cohort_sha256"] == old["parameter_cohort_sha256"]
    assert lock["parameter_cohort_sha256"] == evaluator.canonical_sha256(lock["params"])
    assert lock["bindings"]["liquidation_context"] == "tools/bitunix_wo105_liquidation_context_v2.py"
    assert lock["bindings"]["packet_assembler"] == "tools/bitunix_wo105_packet_assembler_v4.py"
    assert lock["runtime_contract"]["liquidation_timestamp_adapter"]["lookahead_allowed"] is False
    assert tombstone["events_observed"] == 0
    assert tombstone["outcomes_observed"] == 0
    assert tombstone["can_trade"] is False


def test_rollover_rejects_nonzero_forward_sample() -> None:
    status = zero_status()
    status["forward_events"] = 1

    try:
        module.build_artifacts(
            predecessor=copy.deepcopy(predecessor()),
            first_cycle=accepted_first_cycle(),
            status=status,
            stop_receipt={"decision": "bitunix_wo105_v3r1_runtime_stopped_verified", "can_trade": False},
            clock_audit=clock_audit(),
            frozen_at="2026-07-14T17:40:00Z",
            forward_start="2026-07-14T18:00:00Z",
            source_bindings=bindings(),
        )
    except ValueError as exc:
        assert "zero-event" in str(exc)
    else:
        raise AssertionError("nonzero V3R1 sample must block operational rollover")
