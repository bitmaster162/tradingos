from __future__ import annotations

import json
import os
from pathlib import Path

from tools.bitunix_wo104_status import build_report


def write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def base(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    logs = tmp_path / "logs"
    captures = tmp_path / "captures"
    write(logs / "capture_1_launch.json", {"pid": 42, "can_trade": False})
    run = captures / "run_1"
    run.mkdir(parents=True)
    intake = write(tmp_path / "intake.json", {"decision": "external_proposal_ready_for_semantic_review", "can_trade": False})
    cohort = write(tmp_path / "cohort.json", {"decision": "bitunix_wo104_cohort_scope_bound", "cohort_binding_sha256": "a" * 64, "can_trade": False})
    replay = write(tmp_path / "replay.json", {"decision": "bitunix_wo104_historical_schema_replay_pass", "frames_total": 12091, "unknown_schema": {}, "canonical_replay_status": "REPLAY_PENDING", "can_trade": False})
    return logs, captures, intake, cohort, replay


def test_status_distinguishes_live_collecting_from_completed_evidence(tmp_path: Path) -> None:
    paths = base(tmp_path)
    report = build_report(
        logs_dir=paths[0],
        captures_dir=paths[1],
        intake_report_path=paths[2],
        cohort_report_path=paths[3],
        replay_report_path=paths[4],
        process_checker=lambda pid: pid == 42,
    )
    assert report["decision"] == "bitunix_wo104_bounded_public_capture_collecting"
    assert report["phase"] == "collecting"
    assert report["can_trade"] is False


def test_status_fails_closed_if_process_stops_without_acceptance(tmp_path: Path) -> None:
    paths = base(tmp_path)
    report = build_report(
        logs_dir=paths[0],
        captures_dir=paths[1],
        intake_report_path=paths[2],
        cohort_report_path=paths[3],
        replay_report_path=paths[4],
        process_checker=lambda _pid: False,
    )
    assert report["phase"] == "blocked"
    assert "capture_stopped_without_acceptance" in report["blockers"]


def test_status_propagates_independent_capture_hold_without_promotion(tmp_path: Path) -> None:
    paths = base(tmp_path)
    run = paths[1] / "run_1"
    write(run / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json", {"decision": "bitunix_wo104_capture_invalid_hold", "can_trade": False})
    report = build_report(
        logs_dir=paths[0],
        captures_dir=paths[1],
        intake_report_path=paths[2],
        cohort_report_path=paths[3],
        replay_report_path=paths[4],
        process_checker=lambda _pid: False,
    )
    assert report["phase"] == "completed"
    assert "capture_independent_acceptance_failed" in report["blockers"]
    assert report["proposal_status"] == "PUBLIC_CONTRACT_NOT_CONFIRMED"
    assert report["setup_status"] == "FROZEN_PENDING_PUBLIC_CONTRACT"
    assert report["promotion"] == "HOLD"


def test_status_uses_newest_attempt_receipt_and_names_confirmed_shadow_state(tmp_path: Path) -> None:
    paths = base(tmp_path)
    source_receipt = write(
        paths[0] / "capture_2_source_receipt_v2.json",
        {"receipt_id": "v2", "can_trade": False},
    )
    same_timestamp_ns = 1_750_000_000_000_000_000
    os.utime(paths[0] / "capture_1_launch.json", ns=(same_timestamp_ns, same_timestamp_ns))
    os.utime(source_receipt, ns=(same_timestamp_ns, same_timestamp_ns))
    run = paths[1] / "run_1"
    write(
        run / "PUBLIC_CAPTURE_MANIFEST.json",
        {"hold": False, "hold_reasons": [], "can_trade": False},
    )
    write(
        run / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json",
        {"decision": "bitunix_wo104_public_contract_confirmed_shadow_hold", "can_trade": False},
    )

    report = build_report(
        logs_dir=paths[0],
        captures_dir=paths[1],
        intake_report_path=paths[2],
        cohort_report_path=paths[3],
        replay_report_path=paths[4],
        process_checker=lambda _pid: False,
    )

    assert report["phase"] == "completed"
    assert report["capture"]["attempt_receipt"].endswith("capture_2_source_receipt_v2.json")
    assert report["capture"]["launch_receipt"] is None
    assert report["capture"]["source_receipt"].endswith("capture_2_source_receipt_v2.json")
    assert report["capture"]["pid"] is None
    assert report["proposal_status"] == "PUBLIC_CONTRACT_CONFIRMED"
    assert report["setup_status"] == "FROZEN_SHADOW_POLICY_ORACLE"
    assert report["promotion"] == "HOLD"
    assert report["can_trade"] is False
