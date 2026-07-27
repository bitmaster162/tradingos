from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools import bitunix_wo105_v3r3_forward_health as module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def fixture_paths(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "lock": tmp_path / "lock.json",
        "loop": tmp_path / "loop.json",
        "status": tmp_path / "status.json",
        "first": tmp_path / "first.json",
        "packet": tmp_path / "packet.json",
        "ws": tmp_path / "ws.json",
        "rest": tmp_path / "rest",
        "ledger": tmp_path / "ledger.jsonl",
    }
    write_json(paths["lock"], {"cohort_id": "v3r3", "parameter_cohort_sha256": "abc", "forward_start_at": "2026-07-14T19:30:00Z", "can_trade": False})
    write_json(paths["loop"], {"ts": datetime.now(timezone.utc).isoformat(), "status": "public_ws_capture_running", "pid": 123, "can_trade": False})
    write_json(paths["status"], {"forward_events": 0, "terminal_forward_events": 0, "terminal_forward_progress": "0/30", "edge_evaluated": False, "can_trade": False})
    write_json(paths["first"], {"decision": "bitunix_wo105_v3_first_cycle_accepted_shadow_only", "checks": {"post_floor_rest_snapshot": True}, "failures": [], "can_trade": False})
    write_json(paths["packet"], {"decision": "bitunix_wo105_v3_packet_no_current_causal_setup", "blockers": [], "source_read_failures": [], "setup_status": "NO_SETUP", "packet_written": False, "evaluation_run": False, "can_trade": False})

    capture = tmp_path / "capture"
    write_json(
        capture / "PUBLIC_CAPTURE_MANIFEST.json",
        {
            "subscription_acceptance": {"accepted": True},
            "hold": False,
            "frames_total": 1000,
            "trade_prints_total": 400,
            "parse_kinds": {"DepthUpdate": 500},
            "reconnects": 0,
            "max_recv_silence_ms": 500,
            "error_taxonomy": {"NETWORK": 0, "PARSER": 0, "LOCAL": 0, "STORAGE": 0},
            "credentials_used": 0,
            "private_calls": 0,
            "order_calls": 0,
            "can_trade": False,
        },
    )
    write_json(paths["ws"], {"runs": [{"run_dir": str(capture), "accepted": True}], "can_trade": False})

    for index in range(20):
        failures = ["5m:insufficient_closed_bars:189<300"] if index == 0 else []
        write_json(
            paths["rest"] / f"run_{index:02d}" / "PUBLIC_REST_SNAPSHOT_MANIFEST.json",
            {
                "decision": "bitunix_wo105_public_rest_snapshot_partial_hold" if failures else "bitunix_wo105_public_rest_snapshot_collected",
                "failures": failures,
                "bar_counts": {"5m": 189 if failures else 300},
                "http_receipts": [{"credentials_used": 0, "private_calls": 0, "order_calls": 0}],
                "can_trade": False,
            },
        )
    return paths


def build(paths: dict[str, Path]) -> dict:
    return module.build_report(
        lock_path=paths["lock"],
        loop_status_path=paths["loop"],
        status_path=paths["status"],
        first_cycle_path=paths["first"],
        packet_path=paths["packet"],
        ws_intake_path=paths["ws"],
        rest_root=paths["rest"],
        ledger_path=paths["ledger"],
        loop_freshness_seconds=900,
        minimum_rest_acceptance_pct=95.0,
    )


def test_health_passes_with_one_fail_closed_rest_snapshot(tmp_path: Path) -> None:
    report = build(fixture_paths(tmp_path))

    assert report["decision"] == "bitunix_wo105_v3r3_forward_health_pass_with_excluded_snapshots"
    assert report["rest_quality"]["acceptance_pct"] == 95.0
    assert report["ws_quality"]["accepted_runs"] == 1
    assert report["failures"] == []
    assert "rest_snapshots_excluded_fail_closed" in report["warnings"]
    assert report["can_trade"] is False


def test_health_blocks_any_order_call_in_public_capture(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    ws = json.loads(paths["ws"].read_text(encoding="utf-8"))
    capture_manifest = Path(ws["runs"][0]["run_dir"]) / "PUBLIC_CAPTURE_MANIFEST.json"
    payload = json.loads(capture_manifest.read_text(encoding="utf-8"))
    payload["order_calls"] = 1
    write_json(capture_manifest, payload)

    report = build(paths)

    assert report["decision"] == "bitunix_wo105_v3r3_forward_health_blocked"
    assert "ws_capture_quality_invalid" in report["failures"]
    assert "ws_non_public_effect_detected" in report["failures"]
    assert report["can_trade"] is False
