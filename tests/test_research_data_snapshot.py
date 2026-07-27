from __future__ import annotations

import json
from pathlib import Path

from tools.research_data_snapshot import create_snapshot, latest_snapshot, verify_snapshot


def write_csv(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("time,value\n" + "\n".join(rows) + "\n", encoding="utf-8")


def policy_file(tmp_path: Path, files: list[str]) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "authority": "test_local",
                "source_cache_relative": "data/cache/source",
                "snapshot_root_relative": "data/research_snapshots",
                "reject_google_drive_source": True,
                "profile": "TEST",
                "required_files": files,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_snapshot_is_copied_verified_and_resolvable(tmp_path: Path) -> None:
    active = tmp_path / "Active"
    rels = ["spot/BTCUSDT/1h_klines.csv", "futures/BTCUSDT/funding_raw.csv"]
    for rel in rels:
        write_csv(active / "data/cache/source" / rel, ["2024-01-01T00:00:00+00:00,1"])
    policy = policy_file(tmp_path, rels)
    payload = create_snapshot(active, policy)
    snapshot_dir, latest = latest_snapshot(active, policy)
    assert payload["verification"]["passed"] is True
    assert latest["snapshot_id"] == payload["manifest"]["snapshot_id"]
    assert verify_snapshot(snapshot_dir, update_verification=False)["passed"] is True


def test_source_changes_do_not_change_snapshot(tmp_path: Path) -> None:
    active = tmp_path / "Active"
    rel = "spot/BTCUSDT/1h_klines.csv"
    source = active / "data/cache/source" / rel
    write_csv(source, ["2024-01-01T00:00:00+00:00,1"])
    policy = policy_file(tmp_path, [rel])
    create_snapshot(active, policy)
    snapshot_dir, _ = latest_snapshot(active, policy)
    write_csv(source, ["2024-01-01T00:00:00+00:00,999"])
    assert verify_snapshot(snapshot_dir, update_verification=False)["passed"] is True


def test_snapshot_tamper_is_detected(tmp_path: Path) -> None:
    active = tmp_path / "Active"
    rel = "spot/BTCUSDT/1h_klines.csv"
    write_csv(active / "data/cache/source" / rel, ["2024-01-01T00:00:00+00:00,1"])
    policy = policy_file(tmp_path, [rel])
    create_snapshot(active, policy)
    snapshot_dir, _ = latest_snapshot(active, policy)
    (snapshot_dir / rel).write_text("tampered\n", encoding="utf-8")
    verification = verify_snapshot(snapshot_dir, update_verification=False)
    assert verification["passed"] is False
    assert verification["failed_files"][0]["sha256_match"] is False
