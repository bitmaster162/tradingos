from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.liquidation_force_order_transport_continuity import build_report


NOW = datetime(2099, 1, 2, tzinfo=timezone.utc)


def write_rows(path: Path, timestamps: list[datetime], *, status: str = "transport_liveness_ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, timestamp in enumerate(timestamps, start=1):
        rows.append(
            {
                "ts": timestamp.isoformat().replace("+00:00", "Z"),
                "recorded_at_ns": index,
                "heartbeat_id": f"1:{index}",
                "status": status,
                "collector_pid": 1,
                "liveness_messages_seen": index,
                "can_trade": False,
            }
        )
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_continuity_waits_for_first_liveness_proof(tmp_path: Path) -> None:
    report = build_report(tmp_path / "missing.jsonl", as_of=NOW)

    assert report["decision"] == "force_order_transport_continuity_collecting_baseline"
    assert report["blockers"] == ["waiting_first_liveness_proof"]
    assert report["can_trade"] is False


def test_continuity_observed_after_clean_window(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeat.jsonl"
    start = NOW - timedelta(hours=25)
    write_rows(ledger, [start + timedelta(minutes=index) for index in range(1501)])

    report = build_report(ledger, as_of=NOW)

    assert report["decision"] == "force_order_transport_continuity_observed"
    assert report["sample"]["observation_hours"] == 25.0
    assert report["gaps_over_threshold"] == []


def test_continuity_blocks_large_liveness_gap(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeat.jsonl"
    write_rows(
        ledger,
        [NOW - timedelta(minutes=12), NOW - timedelta(minutes=11), NOW - timedelta(minutes=1)],
    )

    report = build_report(ledger, as_of=NOW, minimum_observation_hours=0.0)

    assert report["decision"] == "force_order_transport_continuity_degraded_gaps"
    assert report["gaps_over_threshold"][0]["seconds"] == 600.0
    assert report["recovery"]["status"] == "rolling_window_recovery_estimate"
    assert report["recovery"]["earliest_recheck_at_utc"] == "2099-01-03T23:49:01Z"


def test_continuity_blocks_ledger_integrity_error(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeat.jsonl"
    timestamp = NOW - timedelta(seconds=30)
    write_rows(ledger, [timestamp])
    row = ledger.read_text(encoding="utf-8")
    ledger.write_text(row + row, encoding="utf-8")

    report = build_report(ledger, as_of=NOW, minimum_observation_hours=0.0)

    assert report["decision"] == "force_order_transport_continuity_integrity_blocked"
    assert report["integrity"]["duplicate_ids"] == 1
    assert report["integrity"]["out_of_order"] == 1


def test_continuity_degrades_when_sidecar_records_invalid_proof(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeat.jsonl"
    write_rows(ledger, [NOW - timedelta(seconds=60)])
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "ts": (NOW - timedelta(seconds=30)).isoformat(),
                    "recorded_at_ns": 2,
                    "heartbeat_id": "1:invalid",
                    "status": "transport_liveness_invalid",
                    "collector_pid": 1,
                    "liveness_messages_seen": 2,
                    "failures": ["collector_loop_pid_dead"],
                    "can_trade": False,
                }
            )
            + "\n"
        )

    report = build_report(ledger, as_of=NOW, minimum_observation_hours=0.0)

    assert report["decision"] == "force_order_transport_continuity_degraded_invalid_proofs"
    assert report["sample"]["invalid_liveness_rows"] == 1
    assert report["recovery"]["earliest_recheck_at_utc"] == "2099-01-03T23:59:31Z"


def test_continuity_allows_bounded_cycle_transition_between_proofs(tmp_path: Path) -> None:
    ledger = tmp_path / "heartbeat.jsonl"
    write_rows(ledger, [NOW - timedelta(seconds=90)])
    rows = [
        {
            "ts": (NOW - timedelta(seconds=60)).isoformat(),
            "recorded_at_ns": 2,
            "heartbeat_id": "1:transition",
            "status": "transport_liveness_transition",
            "collector_loop_pid": 1,
            "liveness_messages_seen": 0,
            "failures": [],
            "can_trade": False,
        },
        {
            "ts": (NOW - timedelta(seconds=30)).isoformat(),
            "recorded_at_ns": 3,
            "heartbeat_id": "1:after",
            "status": "transport_liveness_ok",
            "collector_loop_pid": 1,
            "liveness_messages_seen": 1,
            "failures": [],
            "can_trade": False,
        },
    ]
    with ledger.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    report = build_report(ledger, as_of=NOW, minimum_observation_hours=0.0)

    assert report["decision"] == "force_order_transport_continuity_observed"
    assert report["sample"]["transition_rows"] == 1
    assert report["sample"]["invalid_liveness_rows"] == 0
