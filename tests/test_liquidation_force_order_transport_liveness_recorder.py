from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.liquidation_force_order_transport_liveness_recorder import build_snapshot


NOW = datetime(2099, 1, 2, tzinfo=timezone.utc)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def fixture(tmp_path: Path) -> dict[str, Path]:
    collector = tmp_path / "tools" / "binance_force_order_real_feed_collector.py"
    collector.parent.mkdir(parents=True)
    collector.write_text("# frozen collector\n", encoding="utf-8")
    digest = hashlib.sha256(collector.read_bytes()).hexdigest()
    heartbeat = tmp_path / "heartbeat.json"
    loop_status = tmp_path / "loop_status.json"
    loop_lock = tmp_path / "loop.lock.json"
    prereg = tmp_path / "prereg.json"
    write_json(
        heartbeat,
        {
            "ts": (NOW - timedelta(seconds=5)).isoformat(),
            "last_liveness_at": (NOW - timedelta(seconds=6)).isoformat(),
            "tool": "tools/binance_force_order_real_feed_collector.py",
            "collector_pid": 456,
            "liveness_messages_seen": 9,
            "parse_errors_count": 0,
            "data_collector_only": True,
            "can_trade": False,
        },
    )
    common = {"pid": 321, "root": str(tmp_path)}
    write_json(loop_lock, common)
    write_json(
        loop_status,
        {
            **common,
            "status": "running_collector_cycle",
            "live_trading_locked": True,
            "data_collector_only": True,
        },
    )
    write_json(
        prereg,
        {
            "bindings": {
                "liquidation_collector": "tools/binance_force_order_real_feed_collector.py",
                "liquidation_collector_sha256": digest,
            }
        },
    )
    return {
        "heartbeat_path": heartbeat,
        "loop_status_path": loop_status,
        "loop_lock_path": loop_lock,
        "prereg_lock_path": prereg,
        "collector_path": collector,
    }


def build(paths: dict[str, Path], **kwargs) -> dict:
    return build_snapshot(**paths, as_of=NOW, process_alive=lambda _pid: True, **kwargs)


def test_recorder_accepts_fresh_bound_collector(tmp_path: Path, monkeypatch) -> None:
    paths = fixture(tmp_path)
    monkeypatch.setattr("tools.liquidation_force_order_transport_liveness_recorder.ROOT", tmp_path)

    snapshot = build(paths)

    assert snapshot["status"] == "transport_liveness_ok"
    assert snapshot["failures"] == []
    assert snapshot["collector_loop_pid"] == 321
    assert snapshot["can_trade"] is False


def test_recorder_rejects_stale_liveness(tmp_path: Path, monkeypatch) -> None:
    paths = fixture(tmp_path)
    monkeypatch.setattr("tools.liquidation_force_order_transport_liveness_recorder.ROOT", tmp_path)
    heartbeat = json.loads(paths["heartbeat_path"].read_text(encoding="utf-8"))
    heartbeat["last_liveness_at"] = (NOW - timedelta(minutes=4)).isoformat()
    write_json(paths["heartbeat_path"], heartbeat)

    snapshot = build(paths)

    assert snapshot["status"] == "transport_liveness_invalid"
    assert "transport_liveness_stale_or_invalid" in snapshot["failures"]


def test_recorder_rejects_prereg_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    paths = fixture(tmp_path)
    monkeypatch.setattr("tools.liquidation_force_order_transport_liveness_recorder.ROOT", tmp_path)
    paths["collector_path"].write_text("# mutated collector\n", encoding="utf-8")

    snapshot = build(paths)

    assert snapshot["status"] == "transport_liveness_invalid"
    assert "prereg_collector_hash_mismatch" in snapshot["failures"]


def test_recorder_rejects_dead_managed_loop(tmp_path: Path, monkeypatch) -> None:
    paths = fixture(tmp_path)
    monkeypatch.setattr("tools.liquidation_force_order_transport_liveness_recorder.ROOT", tmp_path)

    snapshot = build_snapshot(**paths, as_of=NOW, process_alive=lambda _pid: False)

    assert snapshot["status"] == "transport_liveness_invalid"
    assert "collector_loop_pid_dead" in snapshot["failures"]


def test_recorder_marks_fresh_cycle_start_as_transition_not_failure(tmp_path: Path, monkeypatch) -> None:
    paths = fixture(tmp_path)
    monkeypatch.setattr("tools.liquidation_force_order_transport_liveness_recorder.ROOT", tmp_path)
    heartbeat = json.loads(paths["heartbeat_path"].read_text(encoding="utf-8"))
    heartbeat.update(
        {
            "status": "starting",
            "ts": (NOW - timedelta(seconds=2)).isoformat(),
            "last_liveness_at": None,
            "liveness_messages_seen": 0,
        }
    )
    write_json(paths["heartbeat_path"], heartbeat)

    snapshot = build(paths)

    assert snapshot["status"] == "transport_liveness_transition"
    assert snapshot["failures"] == []


def test_recorder_rejects_cycle_start_that_never_reaches_liveness(tmp_path: Path, monkeypatch) -> None:
    paths = fixture(tmp_path)
    monkeypatch.setattr("tools.liquidation_force_order_transport_liveness_recorder.ROOT", tmp_path)
    heartbeat = json.loads(paths["heartbeat_path"].read_text(encoding="utf-8"))
    heartbeat.update(
        {
            "status": "connected_waiting_events",
            "ts": (NOW - timedelta(seconds=30)).isoformat(),
            "last_liveness_at": None,
            "liveness_messages_seen": 0,
        }
    )
    write_json(paths["heartbeat_path"], heartbeat)

    snapshot = build(paths)

    assert snapshot["status"] == "transport_liveness_invalid"
    assert "no_transport_liveness_messages" in snapshot["failures"]
