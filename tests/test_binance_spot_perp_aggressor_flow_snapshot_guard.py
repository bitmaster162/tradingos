from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tools.binance_spot_perp_aggressor_flow_collector import (
    connect_db,
    insert_trades,
    parse_agg_trade,
    rebuild_minutes,
)
from tools.binance_spot_perp_aggressor_flow_snapshot_guard import run_guard


MINUTE_MS = 60_000


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def raw(trade_id: int, timestamp: int, *, maker: bool) -> dict:
    return {"a": trade_id, "T": timestamp, "m": maker, "p": "100", "q": "1"}


def build_source_db(path: Path) -> None:
    conn = connect_db(path)
    rows = []
    timestamps = [1_000, 60_001, 120_001, 180_001, 240_001]
    for market, base_id in (("spot", 0), ("perpetual", 100)):
        for offset, timestamp in enumerate(timestamps, start=1):
            rows.append(
                parse_agg_trade(
                    raw(base_id + offset, timestamp, maker=offset % 2 == 0),
                    market=market,
                    symbol="BTCUSDT",
                )
            )
    insert_trades(conn, rows)
    rebuild_minutes(conn, {0, 60_000, 120_000, 180_000, 240_000})
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def contract_payload() -> dict:
    return {
        "schema_version": 1,
        "contract_id": "TEST_SPOT_PERP_FLOW_FORWARD_V1",
        "status": "data_collection_only_no_strategy_claim",
        "collection_gate": {
            "minimum_forward_hours": 0.05,
            "minimum_dual_market_minute_coverage_pct": 95.0,
            "maximum_fresh_lag_seconds": 120.0,
            "requires_zero_internal_aggregate_trade_id_gaps": True,
            "requires_valid_aggressor_side_for_every_row": True,
            "requires_sealed_snapshot_before_research": True,
        },
        "runtime_boundary": {
            "collector_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "telegram_send_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def report_payload(current: datetime, *, ready: bool = True) -> dict:
    return {
        "generated_at": current.isoformat(),
        "classification": (
            "binance_spot_perp_aggressor_flow_ready_for_seal_review"
            if ready
            else "binance_spot_perp_aggressor_flow_forward_collecting"
        ),
        "coverage": {
            "span_hours": 0.05 if ready else 0.016667,
            "dual_market_coverage_pct": 100.0,
            "expected_overlap_minutes": 3 if ready else 1,
            "overlap_start": "1970-01-01T00:01:00+00:00",
            "overlap_end": "1970-01-01T00:03:00+00:00" if ready else "1970-01-01T00:01:00+00:00",
            "fresh_lag_seconds": {"spot": 1.0, "perpetual": 1.0},
            "invalid_aggressor_side_rows": 0,
            "aggressor_side_semantics_valid": True,
        },
        "integrity": {
            "spot": {"missing_ids": 0},
            "perpetual": {"missing_ids": 0},
        },
        "research_readiness": {
            "ready": ready,
            "blockers": [] if ready else ["minimum_forward_span_not_reached"],
        },
        "runtime_boundary": {
            "credentials_allowed": False,
            "hypothesis_registered": False,
            "strategy_search_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "telegram_send_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def fixture_paths(tmp_path: Path, *, ready: bool = True) -> tuple[Path, Path, Path, Path, datetime]:
    current = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    source_report = tmp_path / "data_quality.json"
    contract = tmp_path / "contract.json"
    source_db = tmp_path / "live" / "flow.sqlite3"
    output_dir = tmp_path / "sealed"
    source_db.parent.mkdir()
    build_source_db(source_db)
    write_json(source_report, report_payload(current, ready=ready))
    write_json(contract, contract_payload())
    return source_report, contract, source_db, output_dir, current


def invoke(tmp_path: Path, *, ready: bool = True):
    source_report, contract, source_db, output_dir, current = fixture_paths(
        tmp_path, ready=ready
    )
    report, exit_code = run_guard(
        source_report_path=source_report,
        contract_path=contract,
        source_db=source_db,
        output_dir=output_dir,
        current_time=current,
    )
    return report, exit_code, source_report, contract, source_db, output_dir, current


def test_waiting_gate_creates_no_snapshot_artifacts(tmp_path: Path) -> None:
    report, exit_code, _, _, _, output_dir, _ = invoke(tmp_path, ready=False)

    assert exit_code == 0
    assert report["decision"] == "spot_perp_flow_snapshot_guard_waiting_data_gate"
    assert report["sealed"] is False
    assert not output_dir.exists()
    assert report["can_trade"] is False


def test_ready_gate_seals_exact_completed_minute_bounds(tmp_path: Path) -> None:
    report, exit_code, _, _, _, output_dir, _ = invoke(tmp_path)

    assert exit_code == 0
    assert report["decision"] == "spot_perp_flow_snapshot_sealed"
    assert report["snapshot_validation"]["passed"] is True
    assert report["snapshot_validation"]["common_complete_minutes"] == 3
    assert report["runtime_boundary"]["research_run"] is False
    assert report["can_trade"] is False
    expected = {
        "flow_snapshot.sqlite3",
        "minute_features.csv",
        "SOURCE_DATA_QUALITY.json",
        "COLLECTION_CONTRACT.json",
        "MANIFEST.json",
        "SEAL_RECEIPT.json",
    }
    assert expected.issubset({path.name for path in output_dir.iterdir()})
    assert not (output_dir / ".snapshot.in_progress").exists()

    conn = sqlite3.connect(output_dir / "flow_snapshot.sqlite3")
    trade_count, minimum, maximum = conn.execute(
        "SELECT COUNT(*),MIN(event_time_ms),MAX(event_time_ms) FROM trades"
    ).fetchone()
    feature_count = conn.execute("SELECT COUNT(*) FROM minute_features").fetchone()[0]
    conn.close()
    assert trade_count == 6
    assert feature_count == 6
    assert minimum == 60_001
    assert maximum == 180_001


def test_second_run_verifies_receipt_without_rewriting_snapshot(tmp_path: Path) -> None:
    first, first_code, source_report, contract, source_db, output_dir, current = invoke(tmp_path)
    receipt_path = output_dir / "SEAL_RECEIPT.json"
    receipt_bytes = receipt_path.read_bytes()
    receipt_mtime = receipt_path.stat().st_mtime_ns

    second, second_code = run_guard(
        source_report_path=source_report,
        contract_path=contract,
        source_db=source_db,
        output_dir=output_dir,
        current_time=current,
    )

    assert first_code == 0
    assert second_code == 0
    assert second["decision"] == "spot_perp_flow_snapshot_already_sealed_verified"
    assert second["snapshot_id"] == first["snapshot_id"]
    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_mtime_ns == receipt_mtime
    assert second["can_trade"] is False


def test_tampered_artifact_fails_receipt_integrity_check(tmp_path: Path) -> None:
    _, first_code, source_report, contract, source_db, output_dir, current = invoke(tmp_path)
    with (output_dir / "minute_features.csv").open("a", encoding="utf-8") as handle:
        handle.write("tampered\n")

    report, exit_code = run_guard(
        source_report_path=source_report,
        contract_path=contract,
        source_db=source_db,
        output_dir=output_dir,
        current_time=current,
    )

    assert first_code == 0
    assert exit_code == 1
    assert report["decision"] == "spot_perp_flow_snapshot_receipt_integrity_failure"
    assert "artifact_hash_mismatch:features" in report["integrity_failures"]
    assert report["sealed"] is False
    assert report["can_trade"] is False
