from __future__ import annotations

import json
from pathlib import Path

from tools.cross_venue_microstructure_post_snapshot_launch_audit import (
    REQUIRED_PIPELINE_SCRIPTS,
    build_report,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_text(path: Path, text: str = "# stub\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def contract() -> dict:
    return {
        "status": "locked_skeleton",
        "experiments": {
            "exp_a": {
                "script": "tools/exp_a.py",
                "implementation_status": "implemented_locked",
                "supports_lock_path": True,
            },
            "exp_b": {
                "script": "tools/exp_b.py",
                "implementation_status": "implemented_locked",
                "supports_lock_path": True,
            },
        },
        "execution_contract": {
            "exact_snapshot_id_required": True,
            "sealed_snapshot_required": True,
            "credentials_allowed": False,
            "network_required": False,
            "orders_allowed": False,
            "signals_allowed": False,
            "observer_registration_allowed": False,
            "paper_or_live_promotion_allowed": False,
        },
        "runtime_boundary": {"can_trade": False},
    }


def queue() -> dict:
    return {
        "status": "locked_preregistration_queue",
        "portfolio_budget": {
            "max_hypotheses": 2,
            "registered_hypotheses": 2,
            "max_total_configurations": 30,
            "used_configurations": 0,
            "used_oos_openings": 0,
        },
        "hypotheses": [
            {"status": "registered_pending_first_seal", "grid": {"total_configurations": 10}},
            {"status": "registered_pending_first_seal", "grid": {"total_configurations": 20}},
        ],
    }


def approval_template() -> dict:
    return {
        "status": "template_not_granted",
        "approval": {
            "manual_approval_granted": False,
            "validation_opening_allowed": False,
            "can_trade": False,
            "prohibitions": {
                "parameter_search_allowed": False,
                "reoptimization_allowed": False,
                "observer_registration_allowed": False,
                "paper_execution_allowed": False,
                "live_execution_allowed": False,
                "signals_allowed": False,
                "orders_allowed": False,
            },
        },
    }


def autopilot() -> dict:
    return {"failed_checks": [], "can_trade": False}


def transition(state: str = "waiting_for_minimum_time_window") -> dict:
    return {"transition_state": state, "remaining_hours": 50.0, "snapshot_id": None, "can_trade": False}


def seed_files(root: Path) -> None:
    for rel in REQUIRED_PIPELINE_SCRIPTS:
        write_text(root / rel)
    write_text(root / "tools/exp_a.py")
    write_text(root / "tools/exp_b.py")
    watchdog_text = "\n".join(
        [
            "cross_venue_microstructure_post_seal_auto_run_guard.py",
            "--execute",
            "cross_venue_microstructure_candidate_governance_gate.py",
            "cross_venue_microstructure_candidate_review_pack.py",
            "cross_venue_microstructure_validation_protocol_builder.py",
            "cross_venue_microstructure_validation_approval_audit.py",
            "cross_venue_microstructure_validation_runner_skeleton.py",
            "cross_venue_microstructure_research_runner_telegram_notify.py",
        ]
    )
    write_text(root / "ops/autostart/Run-CrossVenueMicrostructureWatchdogLoop.ps1", watchdog_text)


def test_post_snapshot_launch_audit_waits_when_chain_is_ready(tmp_path: Path) -> None:
    seed_files(tmp_path)

    report = build_report(
        root=tmp_path,
        contract=contract(),
        queue=queue(),
        approval_template=approval_template(),
        autopilot=autopilot(),
        snapshot_transition=transition(),
    )

    assert report["decision"] == "post_snapshot_launch_ready_waiting_for_snapshot"
    assert report["failed_checks"] == []
    assert report["queue"]["max_total_configurations"] == 30
    assert report["can_trade"] is False


def test_post_snapshot_launch_audit_marks_ready_for_locked_runner(tmp_path: Path) -> None:
    seed_files(tmp_path)

    report = build_report(
        root=tmp_path,
        contract=contract(),
        queue=queue(),
        approval_template=approval_template(),
        autopilot=autopilot(),
        snapshot_transition=transition("sealed_snapshot_ready_for_train_research_batch"),
    )

    assert report["decision"] == "post_snapshot_launch_ready_for_locked_runner"
    assert report["next_action"] == "let_watchdog_run_post_seal_guard_execute_once"
    assert report["runtime_boundary"]["orders_allowed"] is False


def test_post_snapshot_launch_audit_blocks_missing_script_and_unsafe_template(tmp_path: Path) -> None:
    seed_files(tmp_path)
    (tmp_path / REQUIRED_PIPELINE_SCRIPTS[0]).unlink()
    template = approval_template()
    template["approval"]["prohibitions"]["orders_allowed"] = True

    report = build_report(
        root=tmp_path,
        contract=contract(),
        queue=queue(),
        approval_template=template,
        autopilot={"failed_checks": ["watchdog_required_exits_zero"], "can_trade": False},
        snapshot_transition=transition(),
    )

    assert report["decision"] == "post_snapshot_launch_needs_repair"
    assert f"file:{REQUIRED_PIPELINE_SCRIPTS[0]}" in report["failed_checks"]
    assert "approval_template:all_execution_prohibitions_false" in report["failed_checks"]
    assert "autopilot_failed_checks_empty" in report["failed_checks"]
