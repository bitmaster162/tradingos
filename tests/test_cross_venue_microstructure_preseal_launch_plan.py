from __future__ import annotations

from tools.cross_venue_microstructure_preseal_launch_plan import build_report


def queue() -> dict:
    return {
        "portfolio_budget": {"max_oos_openings": 1},
        "hypotheses": [
            {
                "hypothesis_id": "H1",
                "experiment": "exp_one",
                "family": "family_one",
                "status": "registered_pending_first_seal",
                "claim": "fixture",
                "grid": {"total_configurations": 2},
                "train_gate": {"min_trades": 10},
            }
        ],
        "can_trade": False,
    }


def contract(tmp_path) -> dict:
    script = tmp_path / "tools" / "exp_one.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    return {
        "experiments": {
            "exp_one": {
                "hypothesis_id": "H1",
                "family": "family_one",
                "script": str(script),
                "implementation_status": "implemented_locked",
            }
        },
        "can_trade": False,
    }


def valid_inputs(tmp_path) -> dict:
    return {
        "queue": queue(),
        "contract": contract(tmp_path),
        "prereg_audit": {"decision": "microstructure_prereg_queue_valid", "can_trade": False},
        "runner_contract_audit": {"decision": "microstructure_runner_contract_valid_locked", "can_trade": False},
        "snapshot_gate": {
            "decision": "waiting_for_microstructure_readiness",
            "snapshot_id": None,
            "readiness_diagnostics": {"remaining_hours": 12.5},
            "can_trade": False,
        },
        "readiness": {"decision": "readiness_progress_waiting_healthy", "remaining_hours": 12.5, "can_trade": False},
        "autopilot": {"decision": "microstructure_autopilot_waiting_for_snapshot_window", "failed_checks": [], "can_trade": False},
        "post_snapshot": {"decision": "post_snapshot_launch_ready_waiting_for_snapshot", "can_trade": False},
    }


def test_preseal_launch_plan_ready_waiting_for_snapshot(tmp_path) -> None:
    report = build_report(**valid_inputs(tmp_path))

    assert report["decision"] == "preseal_launch_plan_ready_waiting_for_snapshot"
    assert report["failed_checks"] == []
    assert report["snapshot"]["remaining_hours"] == 12.5
    assert report["hypotheses"][0]["ready_for_locked_runner_after_seal"] is True
    assert report["launch_command_after_seal"]["allowed_now"] is False
    assert report["can_trade"] is False


def test_preseal_launch_plan_blocks_failed_prereg_queue(tmp_path) -> None:
    payload = valid_inputs(tmp_path)
    payload["prereg_audit"] = {"decision": "microstructure_prereg_queue_invalid", "can_trade": False}

    report = build_report(**payload)

    assert report["decision"] == "preseal_launch_plan_blocked"
    assert "prereg_queue_valid" in report["failed_checks"]
    assert report["can_trade"] is False


def test_preseal_launch_plan_stale_when_snapshot_state_changed(tmp_path) -> None:
    payload = valid_inputs(tmp_path)
    payload["snapshot_gate"] = {"decision": "microstructure_snapshot_sealed", "snapshot_id": "snap-1", "can_trade": False}

    report = build_report(**payload)

    assert report["decision"] == "preseal_plan_stale_snapshot_state_changed"
    assert report["next_action"] == "rerun_snapshot_gate_and_runner_status_before_any_research"
    assert report["can_trade"] is False
