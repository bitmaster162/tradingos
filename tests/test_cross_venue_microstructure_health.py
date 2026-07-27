from __future__ import annotations

import json

from tools import cross_venue_microstructure_health as health_module
from tools.cross_venue_microstructure_health import DEGRADED, HEALTHY_COLLECTING, evaluate_health
from tools.cross_venue_microstructure_health_telegram_notify import notification_kind


def healthy_inputs() -> dict:
    return {
        "report": {
            "classification": "cross_venue_microstructure_forward_collecting",
            "trade_id_integrity": {"binance": {"missing_ids": 0}, "coinbase": {"missing_ids": 0}},
            "gap_backfill": {"page_budget_exhausted": False},
            "research_readiness": {"ready": False},
            "can_trade": False,
        },
        "loop": {"status": "sleeping", "pid": 123},
        "last_run": {"status": "completed_data_only", "exit_code": 0},
        "manifest_verification": {"passed": True},
        "report_age_minutes": 0.5,
        "loop_age_minutes": 0.5,
        "last_run_age_minutes": 0.5,
        "loop_pid_alive": True,
        "max_age_seconds": 180,
    }


def test_healthy_collection_passes_without_trade_permission() -> None:
    report = evaluate_health(**healthy_inputs())
    assert report["classification"] == HEALTHY_COLLECTING
    assert report["failed_hard_gates"] == []
    assert report["can_trade"] is False


def test_unresolved_gap_fails_closed() -> None:
    inputs = healthy_inputs()
    inputs["report"]["trade_id_integrity"]["binance"]["missing_ids"] = 4
    report = evaluate_health(**inputs)
    assert report["classification"] == DEGRADED
    assert "binance_trade_id_gaps_zero" in report["failed_hard_gates"]


def test_stale_dead_loop_fails_closed() -> None:
    inputs = healthy_inputs()
    inputs["loop_age_minutes"] = 10.0
    inputs["loop_pid_alive"] = False
    report = evaluate_health(**inputs)
    assert report["classification"] == DEGRADED
    assert {"loop_status_fresh", "loop_pid_alive"}.issubset(report["failed_hard_gates"])


def test_notifier_emits_only_degraded_or_recovery_transitions() -> None:
    assert notification_kind({"classification": HEALTHY_COLLECTING}, {}) == "healthy_no_notification"
    assert notification_kind({"classification": DEGRADED}, {}) == "microstructure_degraded"
    assert notification_kind({"classification": HEALTHY_COLLECTING}, {"last_classification": DEGRADED}) == "microstructure_recovered"


def test_storage_guard_degraded_fails_health_closed() -> None:
    inputs = healthy_inputs()
    inputs["storage_guard"] = {
        "classification": "cross_venue_microstructure_storage_degraded",
        "failed_hard_gates": ["free_bytes_above_hard_floor"],
        "can_trade": False,
    }
    inputs["storage_guard_age_minutes"] = 0.1
    report = evaluate_health(**inputs)
    assert report["classification"] == DEGRADED
    assert "storage_guard_not_degraded" in report["failed_hard_gates"]


def test_manifest_hash_mismatch_is_tolerated_only_during_running_cycle_when_paths_exist() -> None:
    inputs = healthy_inputs()
    inputs["loop"]["status"] = "running_once"
    inputs["manifest_verification"] = {"passed": False, "paths_exist": True}
    report = evaluate_health(**inputs)
    assert report["classification"] == HEALTHY_COLLECTING
    assert "collection_manifest_verified" not in report["failed_hard_gates"]

    inputs["loop"]["status"] = "sleeping"
    report = evaluate_health(**inputs)
    assert report["classification"] == DEGRADED
    assert "collection_manifest_verified" in report["failed_hard_gates"]


def test_manifest_verification_reloads_loop_if_collection_starts_while_hashing(tmp_path, monkeypatch) -> None:
    loop_path = tmp_path / "loop.json"
    manifest_path = tmp_path / "manifest.json"
    loop_path.write_text(json.dumps({"ts": "before", "status": "sleeping"}), encoding="utf-8")
    manifest_path.write_text(json.dumps({"generated_at": "before"}), encoding="utf-8")

    def fake_verify(_manifest, _root):
        loop_path.write_text(json.dumps({"ts": "after", "status": "running_once"}), encoding="utf-8")
        return {"passed": False, "paths_exist": True, "files": []}

    monkeypatch.setattr(health_module, "verify_manifest", fake_verify)

    loop, _manifest, verification = health_module.verify_manifest_coherently(manifest_path, loop_path, tmp_path)

    assert loop["status"] == "running_once"
    assert verification["runtime_state_changed_during_verification"] is True
    assert verification["verification_retried"] is False
