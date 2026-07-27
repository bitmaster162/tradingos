from __future__ import annotations

import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator as base
from tools import bitunix_wo105_causal_shadow_evaluator_v4 as v4
from tools import bitunix_wo105_packet_assembler_v6 as v6
from tools import bitunix_wo105_v3r4_rollover as rollover


ROOT = Path(__file__).resolve().parents[1]


def test_v3r4_rollover_changes_runtime_contract_not_strategy_parameters() -> None:
    predecessor = json.loads(
        (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R3_2026-07-14.json").read_text(
            encoding="utf-8"
        )
    )
    audit = {
        "decision": "bitunix_wo105_v3r3_zero_event_candle_receipt_order_contradiction_confirmed",
        "candidate_setups_observed": 1,
    }
    bindings = {
        "evaluator_v4": (v4.TOOL_PATH, base.sha256_file(ROOT / v4.TOOL_PATH)),
        "packet_assembler_v6": (v6.TOOL_PATH, base.sha256_file(ROOT / v6.TOOL_PATH)),
    }

    tombstone, lock = rollover.build_artifacts(
        predecessor=predecessor,
        audit=audit,
        frozen_at="2026-07-15T03:10:00Z",
        forward_start="2026-07-15T04:00:00Z",
        source_bindings=bindings,
    )

    assert tombstone["events_admitted"] == 0
    assert tombstone["outcomes_observed"] == 0
    assert tombstone["outcome_metrics_inspected"] is False
    assert lock["params"] == predecessor["params"]
    assert lock["parameter_cohort_sha256"] == predecessor["parameter_cohort_sha256"]
    assert lock["bindings"]["evaluator"] == v4.TOOL_PATH
    assert lock["bindings"]["packet_assembler"] == v6.TOOL_PATH
    assert lock["runtime_contract"]["candle_series_order"]["receipt_order"] == "not_required_monotonic"
    assert lock["can_trade"] is False


def test_v3r3_stop_script_requires_verified_job_and_zero_ledger() -> None:
    source = (ROOT / "ops" / "autostart" / "Stop-BitunixWO105V3R3ForRollover.ps1").read_text(
        encoding="utf-8"
    )

    assert "Stop-TradingOSRuntimeJobReceipt" in source
    assert "running_verified_job_contained" in source
    assert "V3R3 admitted events" in source
    assert "outcome_metrics_inspected = $false" in source
    assert "can_trade = $false" in source


def test_v3r4_wrapper_uses_new_roots_floor_and_v6_assembler() -> None:
    source = (ROOT / "ops" / "autostart" / "Run-BitunixWO105V3R4ForwardLoop.ps1").read_text(
        encoding="utf-8"
    )

    assert '-ForwardFloor "2026-07-15T04:00:00Z"' in source
    assert 'RuntimeTag "bitunix_wo105_v3r4"' in source
    assert "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json" in source
    assert 'AssemblerScriptRelativePath "tools\\bitunix_wo105_packet_assembler_v6.py"' in source
    assert 'ShadowTag "bitunix_wo105_shadow_v3r4"' in source
