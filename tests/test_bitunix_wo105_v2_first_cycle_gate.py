from __future__ import annotations

import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator_v2 as evaluator
from tools import bitunix_wo105_v2_first_cycle_gate as module


ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json").read_text(encoding="utf-8")
)


def safe_boundary() -> dict:
    return {
        "signals_allowed": False,
        "paper_entries_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def complete_milestones(floor: int) -> dict[str, int]:
    return {
        "loop_transitioned_after_floor": floor + 1_000,
        "post_floor_rest_snapshot": floor + 5 * 60 * 1_000,
        "post_floor_ws_independently_accepted": floor + 31 * 60 * 1_000,
        "post_floor_packet_assembler_ran": floor + 31 * 60 * 1_000 + 1_000,
    }


def write_forward_rest(root: Path, generated_at: str) -> None:
    run = root / "run_forward"
    run.mkdir(parents=True)
    (run / "PUBLIC_REST_SNAPSHOT_MANIFEST.json").write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "decision": "bitunix_wo105_public_rest_snapshot_collected",
                "snapshot_phase": "FORWARD",
                "failures": [],
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )


def test_pre_floor_is_waiting_not_failed(tmp_path: Path) -> None:
    floor = evaluator.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None
    report = module.build_report(
        LOCK,
        loop_status={"status": "waiting_forward_floor", **safe_boundary(), "credentials_allowed": False},
        rest_root=tmp_path,
        ws_intake=None,
        packet_status=None,
        current_ms=floor - 1,
    )

    assert report["decision"] == "bitunix_wo105_v2_first_cycle_waiting_forward_floor"
    assert report["overdue"] == []
    assert report["can_trade"] is False


def test_missing_rest_after_deadline_blocks_operationally(tmp_path: Path) -> None:
    floor = evaluator.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None
    report = module.build_report(
        LOCK,
        loop_status={"status": "public_ws_and_rest_collecting", **safe_boundary(), "credentials_allowed": False},
        rest_root=tmp_path,
        ws_intake=None,
        packet_status=None,
        current_ms=floor + module.REST_GRACE_MS + 1,
    )

    assert report["decision"] == "bitunix_wo105_v2_first_cycle_operational_blocked"
    assert "post_floor_rest_snapshot" in report["overdue"]
    assert report["automatic_restart_attempted"] is False


def test_complete_first_cycle_passes_shadow_only(tmp_path: Path) -> None:
    floor = evaluator.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None
    write_forward_rest(tmp_path, "2026-07-14T12:05:03Z")
    ws = {
        "generated_at": "2026-07-14T12:31:00Z",
        "decision": "bitunix_wo105_ws_intake_ready",
        "accepted_runs": 1,
        "runtime_boundary": safe_boundary(),
        "can_trade": False,
    }
    packet = {
        "generated_at": "2026-07-14T12:31:01Z",
        "decision": "bitunix_wo105_packet_no_current_causal_setup",
        "rest_eligible_runs": 1,
        "ws_accepted_runs": 1,
        "packet_written": False,
        "evaluation_run": False,
        **safe_boundary(),
    }
    report = module.build_report(
        LOCK,
        loop_status={"status": "cycle_complete_shadow_only", **safe_boundary(), "credentials_allowed": False},
        rest_root=tmp_path,
        ws_intake=ws,
        packet_status=packet,
        milestones=complete_milestones(floor),
        current_ms=floor + module.PACKET_GRACE_MS,
    )

    assert report["decision"] == "bitunix_wo105_v2_first_cycle_accepted_shadow_only"
    assert all(report["checks"].values())
    assert report["diagnostics"]["packet_written"] is False
    assert report["edge_evaluated"] is False
    assert report["can_trade"] is False


def test_late_milestone_cannot_masquerade_as_on_time(tmp_path: Path) -> None:
    floor = evaluator.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None
    write_forward_rest(tmp_path, "2026-07-14T12:05:03Z")
    ws = {
        "decision": "bitunix_wo105_ws_intake_ready",
        "accepted_runs": 1,
        "runtime_boundary": safe_boundary(),
        "can_trade": False,
    }
    packet = {
        "rest_eligible_runs": 1,
        "ws_accepted_runs": 1,
        "packet_written": False,
        "evaluation_run": False,
        **safe_boundary(),
    }
    milestones = complete_milestones(floor)
    milestones["post_floor_packet_assembler_ran"] = floor + module.PACKET_GRACE_MS + 1
    report = module.build_report(
        LOCK,
        loop_status={"status": "cycle_complete_shadow_only", **safe_boundary(), "credentials_allowed": False},
        rest_root=tmp_path,
        ws_intake=ws,
        packet_status=packet,
        milestones=milestones,
        current_ms=floor + module.PACKET_GRACE_MS + 1,
    )

    assert report["decision"] == "bitunix_wo105_v2_first_cycle_operational_blocked"
    assert "post_floor_packet_assembler_ran" in report["overdue"]


def test_milestone_journal_keeps_earliest_safe_observation(tmp_path: Path) -> None:
    path = tmp_path / "milestones.jsonl"
    cohort = LOCK["cohort_id"]
    rows = [
        {
            "milestone": "loop_transitioned_after_floor",
            "observed_at": "2026-07-14T12:02:00Z",
            "cohort_id": cohort,
            **safe_boundary(),
        },
        {
            "milestone": "loop_transitioned_after_floor",
            "observed_at": "2026-07-14T12:01:00Z",
            "cohort_id": cohort,
            **safe_boundary(),
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    milestones, failures = module.load_milestones(path, cohort_id=cohort)

    assert failures == []
    assert milestones["loop_transitioned_after_floor"] == evaluator.parse_iso_ms("2026-07-14T12:01:00Z")


def test_boundary_drift_fails_immediately(tmp_path: Path) -> None:
    floor = evaluator.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None
    report = module.build_report(
        LOCK,
        loop_status={"status": "waiting_forward_floor", **safe_boundary(), "orders_allowed": True},
        rest_root=tmp_path,
        ws_intake=None,
        packet_status=None,
        current_ms=floor - 1,
    )

    assert report["decision"] == "bitunix_wo105_v2_first_cycle_hold_integrity_or_boundary_invalid"
    assert "loop_orders_allowed_not_false" in report["failures"]
