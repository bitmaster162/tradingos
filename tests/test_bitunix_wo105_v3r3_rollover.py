from __future__ import annotations

import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator_v3 as evaluator
from tools import bitunix_wo105_v3r3_rollover as rollover


ROOT = Path(__file__).resolve().parents[1]


def test_v3r3_rollover_preserves_parameters_and_versions_only_runtime_interface() -> None:
    predecessor = json.loads(
        (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R2_2026-07-14.json").read_text(
            encoding="utf-8"
        )
    )
    audit = {
        "decision": "bitunix_wo105_v3r2_zero_event_adapter_interface_gap_confirmed",
        "failures": [],
        "outcome_metrics_inspected": False,
        "can_trade": False,
    }
    bindings = {
        "liquidation_context_v3": ("tools/context_v3.py", "a" * 64),
        "packet_assembler_v5": ("tools/assembler_v5.py", "b" * 64),
        "predecessor_v3r2_interface_audit": ("docs/audit.json", "c" * 64),
    }

    tombstone, lock = rollover.build_artifacts(
        predecessor=predecessor,
        audit=audit,
        frozen_at="2026-07-14T18:45:00Z",
        forward_start="2026-07-14T19:30:00Z",
        source_bindings=bindings,
    )

    assert tombstone["status"] == "TOMBSTONED_POST_FLOOR_ZERO_EVENT_ADAPTER_INTERFACE_FAILURE"
    assert tombstone["strategy_failure"] is False
    assert tombstone["events_observed"] == 0
    assert lock["cohort_id"] == "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3R3_20260714"
    assert lock["params"] == predecessor["params"]
    assert lock["parameter_cohort_sha256"] == evaluator.canonical_sha256(lock["params"])
    assert lock["bindings"]["liquidation_context"] == "tools/bitunix_wo105_liquidation_context_v3.py"
    assert lock["bindings"]["packet_assembler"] == "tools/bitunix_wo105_packet_assembler_v5.py"
    assert lock["operational_rollover"]["outcome_metrics_inspected"] is False
    assert lock["can_trade"] is False
