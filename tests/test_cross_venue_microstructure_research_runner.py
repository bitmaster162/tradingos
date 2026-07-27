from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from tools.cross_venue_microstructure_research_runner import (
    run_if_ready,
    sha256_file,
    snapshot_from_gate,
    verify_microstructure_snapshot,
)


def write_synthetic_features(path: Path, rows: int = 520) -> None:
    fieldnames = [
        "minute",
        "minute_ms",
        "venue",
        "product",
        "trades",
        "notional",
        "price_first",
        "price_last",
        "return_bps",
        "buy_notional",
        "sell_notional",
        "delta_notional",
        "aggressor_side_usable",
        "book_snapshots",
        "avg_spread_bps",
        "avg_top_imbalance",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        binance_price = 100_000.0
        coinbase_price = 100_000.0
        for index in range(rows):
            burst = index % 37 == 0
            base_move = 0.00002 if index % 13 else -0.00001
            dislocation = 0.00004 if index % 29 == 0 else 0.0
            binance_next = binance_price * (1.0 + base_move + dislocation + (0.00003 if burst else 0.0))
            coinbase_next = coinbase_price * (1.0 + base_move)
            for venue, price, next_price in (
                ("binance", binance_price, binance_next),
                ("coinbase", coinbase_price, coinbase_next),
            ):
                writer.writerow(
                    {
                        "minute": f"1970-01-01T00:{index % 60:02d}:00Z",
                        "minute_ms": index * 60_000,
                        "venue": venue,
                        "product": "BTCUSDT" if venue == "binance" else "BTC-USD",
                        "trades": 80 if burst else 12,
                        "notional": 100_000.0,
                        "price_first": round(price, 6),
                        "price_last": round(next_price, 6),
                        "return_bps": round((next_price / price - 1.0) * 10_000, 6),
                        "buy_notional": 53_000.0,
                        "sell_notional": 47_000.0,
                        "delta_notional": 6_000.0 if venue == "binance" and burst else (1_000.0 if venue == "binance" else ""),
                        "aggressor_side_usable": "true" if venue == "binance" else "false",
                        "book_snapshots": 3,
                        "avg_spread_bps": 4.0 if burst else 1.0,
                        "avg_top_imbalance": 0.12,
                    }
                )
            binance_price = binance_next
            coinbase_price = coinbase_next


def create_snapshot(active_root: Path, snapshot_id: str = "sealed-id-123456789abc") -> Path:
    snapshot_dir = active_root / "data" / "research_snapshots_cross_venue_microstructure" / snapshot_id
    snapshot_dir.mkdir(parents=True)
    db_path = snapshot_dir / "microstructure.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
    conn.commit()
    conn.close()
    features_path = snapshot_dir / "minute_features.csv"
    write_synthetic_features(features_path)
    state_path = snapshot_dir / "SNAPSHOT_STATE.json"
    state_path.write_text(json.dumps({"schema_version": 1, "can_trade": False}), encoding="utf-8")
    files = []
    for path in (db_path, features_path, state_path):
        files.append({"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "profile": "BTC_CROSS_VENUE_MICROSTRUCTURE_SQLITE_V2",
        "dataset_sha256": "synthetic",
        "files": files,
        "can_trade": False,
    }
    (snapshot_dir / "SNAPSHOT_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (snapshot_dir / "VERIFICATION.json").write_text(json.dumps({"passed": True, "can_trade": False}), encoding="utf-8")
    return snapshot_dir


def test_snapshot_from_gate_blocks_until_exact_sealed_snapshot() -> None:
    snapshot_id, state = snapshot_from_gate({"decision": "waiting_for_microstructure_readiness", "snapshot_id": None})

    assert snapshot_id is None
    assert state == "blocked_waiting_for_sealed_snapshot"


def test_verify_microstructure_snapshot_accepts_bytes_manifest(tmp_path: Path) -> None:
    active_root = tmp_path / "Active"
    snapshot_dir = create_snapshot(active_root)

    verification = verify_microstructure_snapshot(snapshot_dir, "sealed-id-123456789abc")

    assert verification["passed"] is True
    assert verification["sqlite_integrity"] == "ok"
    assert verification["files_checked"] == 3


def test_run_if_ready_executes_all_implemented_research_scripts(tmp_path: Path) -> None:
    active_root = tmp_path / "Active"
    snapshot_id = "sealed-id-123456789abc"
    create_snapshot(active_root, snapshot_id)
    gate_path = active_root / "docs" / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json"
    gate_path.parent.mkdir(parents=True)
    gate_path.write_text(
        json.dumps(
            {
                "decision": "microstructure_snapshot_sealed",
                "snapshot_id": snapshot_id,
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )

    code, report = run_if_ready(
        active_root=active_root,
        contract_path=Path("configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json").resolve(),
        gate_path=gate_path,
        out_prefix=active_root / "docs" / "MICRO_RUNNER",
        timeout_seconds=120,
    )

    assert code == 0
    assert report["snapshot_id"] == snapshot_id
    assert report["experiments"] == 4
    assert report["completed"] == 4
    assert report["failed"] == 0
    assert report["tested_total"] == 216 + 162 + 72 + 324
    assert report["can_trade"] is False
    latest = json.loads((active_root / "_dl" / "research_runs_cross_venue_microstructure" / "LATEST.json").read_text(encoding="utf-8"))
    assert latest["snapshot_id"] == snapshot_id
    assert latest["status"] == "completed"
