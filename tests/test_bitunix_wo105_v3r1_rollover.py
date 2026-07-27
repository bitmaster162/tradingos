from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import bitunix_wo105_v3r1_rollover as rollover


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3_2026-07-14.json").read_text(encoding="utf-8")
)


def fixtures() -> tuple[dict, dict, dict]:
    snapshot = {
        "decision": "bitunix_wo108_v3_zero_event_operational_rollover_required",
        "rollover_eligible": True,
        "forward": {
            "forward_events": 0,
            "terminal_forward_events": 0,
            "interim_outcome_values_accessed": False,
            "interim_outcome_metrics_disclosed": False,
        },
        "ledger": {"rows": 0},
        "packet": {"present": False, "evaluation_run": False},
    }
    first_cycle = {
        "decision": "bitunix_wo105_v3_first_cycle_operational_blocked",
        "overdue": ["loop_transitioned_after_floor", "post_floor_rest_snapshot"],
        "failures": [],
    }
    stop = {
        "decision": "bitunix_wo105_v3_runtime_stopped_verified",
        "exact_script_pids_remaining": [],
        "receipt_removed": True,
        "lock_removed": True,
    }
    return snapshot, first_cycle, stop


def build(snapshot: dict, first_cycle: dict, stop: dict) -> tuple[dict, dict]:
    return rollover.build_artifacts(
        predecessor=copy.deepcopy(PREDECESSOR),
        snapshot=snapshot,
        first_cycle=first_cycle,
        stop_receipt=stop,
        frozen_at="2026-07-14T15:00:00Z",
        forward_start="2026-07-14T17:00:00Z",
        source_bindings={"proof": ("docs/proof.json", "a" * 64)},
    )


def test_rollover_preserves_parameters_and_fail_closed_scope() -> None:
    snapshot, first_cycle, stop = fixtures()
    tombstone, lock = build(snapshot, first_cycle, stop)

    assert lock["params"] == PREDECESSOR["params"]
    assert lock["parameter_cohort_sha256"] == PREDECESSOR["parameter_cohort_sha256"]
    assert lock["strategy_parameters_mutated_from_v3"] is False
    assert lock["operational_rollover"]["historical_rows_admitted"] == 0
    assert lock["can_trade"] is False
    assert lock["scope"]["orders_allowed"] is False
    assert tombstone["events_observed"] == tombstone["outcomes_observed"] == 0
    assert tombstone["restart_allowed"] is False


def test_rollover_refuses_any_observed_event() -> None:
    snapshot, first_cycle, stop = fixtures()
    snapshot["forward"]["forward_events"] = 1

    with pytest.raises(ValueError, match="zero-event"):
        build(snapshot, first_cycle, stop)


def test_rollover_refuses_unverified_stop() -> None:
    snapshot, first_cycle, stop = fixtures()
    stop["receipt_removed"] = False

    with pytest.raises(ValueError, match="safely retired"):
        build(snapshot, first_cycle, stop)
