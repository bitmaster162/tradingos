#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.binance_spot_perp_aggressor_flow_collector import (  # noqa: E402
    MARKET_SPECS,
    MINUTE_MS,
    connect_db,
    export_features,
)


SEALED_DECISIONS = {
    "spot_perp_flow_snapshot_sealed",
    "spot_perp_flow_snapshot_already_sealed_verified",
}
FINAL_ARTIFACTS = {
    "database": "flow_snapshot.sqlite3",
    "features": "minute_features.csv",
    "source_report": "SOURCE_DATA_QUALITY.json",
    "collection_contract": "COLLECTION_CONTRACT.json",
    "manifest": "MANIFEST.json",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso(value: datetime | None = None) -> str:
    current = value or now_utc()
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def read_json_bytes(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_root_not_object:{portable(path)}")
    return payload, raw


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_float(value: Any, default: float = -1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def gate_checks(
    *,
    source_report: dict[str, Any],
    contract: dict[str, Any],
    source_db: Path,
    current_time: datetime,
) -> tuple[dict[str, bool], dict[str, Any]]:
    coverage = source_report.get("coverage") if isinstance(source_report.get("coverage"), dict) else {}
    integrity = source_report.get("integrity") if isinstance(source_report.get("integrity"), dict) else {}
    readiness = (
        source_report.get("research_readiness")
        if isinstance(source_report.get("research_readiness"), dict)
        else {}
    )
    runtime = (
        source_report.get("runtime_boundary")
        if isinstance(source_report.get("runtime_boundary"), dict)
        else {}
    )
    collection_gate = (
        contract.get("collection_gate") if isinstance(contract.get("collection_gate"), dict) else {}
    )
    contract_runtime = (
        contract.get("runtime_boundary") if isinstance(contract.get("runtime_boundary"), dict) else {}
    )
    minimum_hours = safe_float(collection_gate.get("minimum_forward_hours"))
    minimum_coverage = safe_float(collection_gate.get("minimum_dual_market_minute_coverage_pct"))
    maximum_lag = safe_float(collection_gate.get("maximum_fresh_lag_seconds"))
    overlap_start = parse_ts(coverage.get("overlap_start"))
    overlap_end = parse_ts(coverage.get("overlap_end"))
    generated_at = parse_ts(source_report.get("generated_at"))
    report_age_seconds = (
        max(0.0, (current_time - generated_at).total_seconds()) if generated_at is not None else None
    )
    lags = coverage.get("fresh_lag_seconds") if isinstance(coverage.get("fresh_lag_seconds"), dict) else {}
    expected_minutes = safe_int(coverage.get("expected_overlap_minutes"))
    required_minutes = int(round(minimum_hours * 60.0)) if minimum_hours >= 0 else -1
    spot_integrity = integrity.get("spot") if isinstance(integrity.get("spot"), dict) else {}
    perp_integrity = (
        integrity.get("perpetual") if isinstance(integrity.get("perpetual"), dict) else {}
    )
    false_runtime_fields = (
        "credentials_allowed",
        "hypothesis_registered",
        "strategy_search_allowed",
        "signals_allowed",
        "paper_entries_allowed",
        "telegram_send_allowed",
        "orders_allowed",
        "can_trade",
    )
    report_runtime_closed = all(runtime.get(field) is False for field in false_runtime_fields)
    contract_runtime_closed = all(
        contract_runtime.get(field) is False
        for field in (
            "signals_allowed",
            "paper_entries_allowed",
            "telegram_send_allowed",
            "orders_allowed",
            "can_trade",
        )
    )
    checks = {
        "contract_status_data_collection_only": contract.get("status")
        == "data_collection_only_no_strategy_claim",
        "contract_can_trade_false": contract.get("can_trade") is False,
        "contract_runtime_closed": contract_runtime_closed
        and contract_runtime.get("collector_only") is True,
        "source_report_can_trade_false": source_report.get("can_trade") is False,
        "source_runtime_closed": report_runtime_closed,
        "source_db_exists": source_db.is_file(),
        "readiness_gate_ready": readiness.get("ready") is True,
        "ready_classification_exact": source_report.get("classification")
        == "binance_spot_perp_aggressor_flow_ready_for_seal_review",
        "minimum_forward_span_reached": safe_float(coverage.get("span_hours")) >= minimum_hours >= 0,
        "minimum_aligned_coverage_reached": safe_float(
            coverage.get("dual_market_coverage_pct")
        )
        >= minimum_coverage
        >= 0,
        "minimum_overlap_minutes_reached": expected_minutes >= required_minutes > 0,
        "overlap_bounds_valid": overlap_start is not None
        and overlap_end is not None
        and overlap_end >= overlap_start,
        "spot_has_zero_id_gaps": safe_int(spot_integrity.get("missing_ids")) == 0,
        "perpetual_has_zero_id_gaps": safe_int(perp_integrity.get("missing_ids")) == 0,
        "aggressor_semantics_valid": coverage.get("aggressor_side_semantics_valid") is True
        and safe_int(coverage.get("invalid_aggressor_side_rows"), default=0) == 0,
        "source_report_fresh": report_age_seconds is not None
        and maximum_lag >= 0
        and report_age_seconds <= maximum_lag,
        "spot_input_fresh": safe_float(lags.get("spot")) <= maximum_lag
        and safe_float(lags.get("spot")) >= 0,
        "perpetual_input_fresh": safe_float(lags.get("perpetual")) <= maximum_lag
        and safe_float(lags.get("perpetual")) >= 0,
    }
    details = {
        "minimum_forward_hours": minimum_hours,
        "minimum_dual_market_coverage_pct": minimum_coverage,
        "maximum_fresh_lag_seconds": maximum_lag,
        "required_overlap_minutes": required_minutes,
        "expected_overlap_minutes": expected_minutes,
        "report_age_seconds": round(report_age_seconds, 3) if report_age_seconds is not None else None,
        "start_ms": int(overlap_start.timestamp() * 1000) if overlap_start is not None else None,
        "end_minute_ms": int(overlap_end.timestamp() * 1000) if overlap_end is not None else None,
        "cutoff_ms_exclusive": (
            int(overlap_end.timestamp() * 1000) + MINUTE_MS if overlap_end is not None else None
        ),
    }
    return checks, details


def initialise_snapshot_db(path: Path) -> None:
    conn = connect_db(path)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()


def copy_bounded_snapshot(source_db: Path, target_db: Path, *, start_ms: int, cutoff_ms: int) -> None:
    initialise_snapshot_db(target_db)
    source_uri = f"file:{source_db.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(source_uri, uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("ATTACH DATABASE ? AS sealed", (str(target_db.resolve()),))
    try:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO sealed.trades(
                market,symbol,agg_trade_id,event_time_ms,price,quantity,notional,
                buyer_is_maker,aggressor_side
            )
            SELECT market,symbol,agg_trade_id,event_time_ms,price,quantity,notional,
                   buyer_is_maker,aggressor_side
            FROM main.trades
            WHERE event_time_ms>=? AND event_time_ms<?
            ORDER BY market,agg_trade_id""",
            (start_ms, cutoff_ms),
        )
        conn.execute(
            """INSERT INTO sealed.minute_features(
                market,symbol,minute_ms,trades,notional,buy_notional,sell_notional,
                delta_notional,delta_ratio,price_first,price_last,return_bps
            )
            SELECT market,symbol,minute_ms,trades,notional,buy_notional,sell_notional,
                   delta_notional,delta_ratio,price_first,price_last,return_bps
            FROM main.minute_features
            WHERE minute_ms>=? AND minute_ms<?
            ORDER BY minute_ms,market""",
            (start_ms, cutoff_ms),
        )
        metadata = {
            "snapshot_start_ms_inclusive": str(start_ms),
            "snapshot_cutoff_ms_exclusive": str(cutoff_ms),
            "snapshot_completed_minutes_only": "true",
            "snapshot_can_trade": "false",
        }
        conn.executemany(
            "INSERT OR REPLACE INTO sealed.metadata(key,value) VALUES(?,?)",
            metadata.items(),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("DETACH DATABASE sealed")
        conn.close()


def validate_snapshot(path: Path, *, start_ms: int, cutoff_ms: int) -> dict[str, Any]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        out_of_bounds_trades = int(
            conn.execute(
                "SELECT COUNT(*) FROM trades WHERE event_time_ms<? OR event_time_ms>=?",
                (start_ms, cutoff_ms),
            ).fetchone()[0]
        )
        out_of_bounds_features = int(
            conn.execute(
                "SELECT COUNT(*) FROM minute_features WHERE minute_ms<? OR minute_ms>=?",
                (start_ms, cutoff_ms),
            ).fetchone()[0]
        )
        invalid_sides = int(
            conn.execute(
                "SELECT COUNT(*) FROM trades WHERE aggressor_side NOT IN ('BUY','SELL')"
            ).fetchone()[0]
        )
        expected_minutes = int((cutoff_ms - start_ms) // MINUTE_MS)
        per_market: dict[str, dict[str, Any]] = {}
        minute_sets: dict[str, set[int]] = {}
        for market in MARKET_SPECS:
            row = conn.execute(
                "SELECT MIN(agg_trade_id),MAX(agg_trade_id),COUNT(*) FROM trades WHERE market=?",
                (market,),
            ).fetchone()
            first_id, last_id, trade_rows = row
            missing_ids = (
                int(last_id - first_id + 1 - trade_rows)
                if first_id is not None and last_id is not None
                else -1
            )
            minute_sets[market] = {
                int(item[0])
                for item in conn.execute(
                    "SELECT minute_ms FROM minute_features WHERE market=? AND trades>0",
                    (market,),
                )
            }
            per_market[market] = {
                "trade_rows": int(trade_rows),
                "first_id": first_id,
                "last_id": last_id,
                "missing_ids": missing_ids,
                "minute_feature_rows": len(minute_sets[market]),
            }
        common_minutes = minute_sets["spot"] & minute_sets["perpetual"]
        aligned_coverage_pct = (
            len(common_minutes) / expected_minutes * 100.0 if expected_minutes > 0 else 0.0
        )
        validation = {
            "start_ms_inclusive": start_ms,
            "cutoff_ms_exclusive": cutoff_ms,
            "expected_minutes": expected_minutes,
            "common_complete_minutes": len(common_minutes),
            "aligned_coverage_pct": round(aligned_coverage_pct, 6),
            "out_of_bounds_trades": out_of_bounds_trades,
            "out_of_bounds_features": out_of_bounds_features,
            "invalid_aggressor_side_rows": invalid_sides,
            "markets": per_market,
        }
        validation["passed"] = (
            out_of_bounds_trades == 0
            and out_of_bounds_features == 0
            and invalid_sides == 0
            and all(item["trade_rows"] > 0 for item in per_market.values())
            and all(item["missing_ids"] == 0 for item in per_market.values())
        )
        return validation
    finally:
        conn.close()


def snapshot_id(start_ms: int, cutoff_ms: int, database_hash: str) -> str:
    start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end = datetime.fromtimestamp((cutoff_ms - MINUTE_MS) / 1000, tz=timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    return f"SPOT_PERP_FLOW_V1_{start}_{end}_{database_hash[:12]}"


def artifact_record(path: Path) -> dict[str, Any]:
    return {"file": path.name, "size": path.stat().st_size, "sha256": file_sha256(path)}


def verify_existing_receipt(output_dir: Path) -> tuple[bool, list[str], dict[str, Any]]:
    receipt_path = output_dir / "SEAL_RECEIPT.json"
    try:
        receipt, _ = read_json_bytes(receipt_path)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return False, [f"receipt_unreadable:{type(exc).__name__}"], {}
    failures: list[str] = []
    if receipt.get("decision") != "spot_perp_flow_snapshot_sealed":
        failures.append("receipt_decision_invalid")
    if receipt.get("can_trade") is not False:
        failures.append("receipt_trade_lock_invalid")
    artifacts = receipt.get("artifacts") if isinstance(receipt.get("artifacts"), dict) else {}
    for name, expected_file in FINAL_ARTIFACTS.items():
        record = artifacts.get(name) if isinstance(artifacts.get(name), dict) else {}
        file_name = str(record.get("file") or "")
        if file_name != expected_file or Path(file_name).name != file_name:
            failures.append(f"artifact_path_invalid:{name}")
            continue
        path = output_dir / file_name
        if not path.is_file():
            failures.append(f"artifact_missing:{name}")
            continue
        if path.stat().st_size != safe_int(record.get("size")):
            failures.append(f"artifact_size_mismatch:{name}")
        if file_sha256(path) != record.get("sha256"):
            failures.append(f"artifact_hash_mismatch:{name}")
    manifest_path = output_dir / FINAL_ARTIFACTS["manifest"]
    if manifest_path.is_file():
        try:
            manifest, _ = read_json_bytes(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            failures.append("manifest_unreadable")
        else:
            if manifest.get("snapshot_id") != receipt.get("snapshot_id"):
                failures.append("manifest_snapshot_id_mismatch")
            if manifest.get("can_trade") is not False:
                failures.append("manifest_trade_lock_invalid")
    return not failures, failures, receipt


def build_report_base(*, generated_at: str, source_report: Path, contract: Path, source_db: Path, output_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "tool": "tools/binance_spot_perp_aggressor_flow_snapshot_guard.py",
        "source_paths": {
            "data_quality_report": portable(source_report),
            "collection_contract": portable(contract),
            "live_database": portable(source_db),
            "sealed_output_dir": portable(output_dir),
        },
        "runtime_boundary": {
            "snapshot_guard_only": True,
            "research_run": False,
            "validation_open": False,
            "oos_open": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "telegram_send_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def run_guard(
    *,
    source_report_path: Path,
    contract_path: Path,
    source_db: Path,
    output_dir: Path,
    current_time: datetime | None = None,
) -> tuple[dict[str, Any], int]:
    current = (current_time or now_utc()).astimezone(timezone.utc)
    base = build_report_base(
        generated_at=now_iso(current),
        source_report=source_report_path,
        contract=contract_path,
        source_db=source_db,
        output_dir=output_dir,
    )
    receipt_path = output_dir / "SEAL_RECEIPT.json"
    if receipt_path.is_file():
        verified, failures, receipt = verify_existing_receipt(output_dir)
        decision = (
            "spot_perp_flow_snapshot_already_sealed_verified"
            if verified
            else "spot_perp_flow_snapshot_receipt_integrity_failure"
        )
        return (
            {
                **base,
                "decision": decision,
                "sealed": verified,
                "snapshot_id": receipt.get("snapshot_id"),
                "integrity_failures": failures,
                "receipt": portable(receipt_path),
                "next_action": (
                    "manual prospective preregistration review; do not run research automatically"
                    if verified
                    else "stop and manually audit the sealed snapshot artifacts"
                ),
            },
            0 if verified else 1,
        )

    lock_path = output_dir / ".snapshot.in_progress"
    orphaned = [name for name in FINAL_ARTIFACTS.values() if (output_dir / name).exists()]
    if lock_path.exists() or orphaned:
        return (
            {
                **base,
                "decision": "spot_perp_flow_snapshot_incomplete_state_manual_review",
                "sealed": False,
                "blockers": (["in_progress_lock_present"] if lock_path.exists() else [])
                + [f"orphaned_artifact:{name}" for name in orphaned],
                "next_action": "manually inspect incomplete artifacts; automatic overwrite is forbidden",
            },
            1,
        )

    try:
        source_report, source_report_raw = read_json_bytes(source_report_path)
        contract, contract_raw = read_json_bytes(contract_path)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return (
            {
                **base,
                "decision": "spot_perp_flow_snapshot_guard_blocked_input_error",
                "sealed": False,
                "blockers": [f"input_error:{type(exc).__name__}:{exc}"],
                "next_action": "restore readable canonical inputs before sealing",
            },
            1,
        )

    checks, gate = gate_checks(
        source_report=source_report,
        contract=contract,
        source_db=source_db,
        current_time=current,
    )
    failed_checks = [name for name, passed in checks.items() if not passed]
    if failed_checks:
        hard_failures = {
            "contract_status_data_collection_only",
            "contract_can_trade_false",
            "contract_runtime_closed",
            "source_report_can_trade_false",
            "source_runtime_closed",
        }
        blocked = bool(hard_failures.intersection(failed_checks))
        return (
            {
                **base,
                "decision": (
                    "spot_perp_flow_snapshot_guard_blocked_contract"
                    if blocked
                    else "spot_perp_flow_snapshot_guard_waiting_data_gate"
                ),
                "sealed": False,
                "checks": checks,
                "failed_checks": failed_checks,
                "gate": gate,
                "next_action": (
                    "repair trade-lock or contract violations before any snapshot action"
                    if blocked
                    else "keep the collector unchanged until every data gate passes"
                ),
            },
            1 if blocked else 0,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return (
            {
                **base,
                "decision": "spot_perp_flow_snapshot_incomplete_state_manual_review",
                "sealed": False,
                "blockers": ["in_progress_lock_present"],
                "next_action": "manually inspect the in-progress lock; automatic overwrite is forbidden",
            },
            1,
        )
    with os.fdopen(lock_fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"started_at": now_iso(current), "pid": os.getpid(), "can_trade": False}))

    token = uuid.uuid4().hex
    temporary = {
        name: output_dir / f".{file_name}.{token}.tmp"
        for name, file_name in FINAL_ARTIFACTS.items()
    }
    try:
        start_ms = int(gate["start_ms"])
        cutoff_ms = int(gate["cutoff_ms_exclusive"])
        copy_bounded_snapshot(source_db, temporary["database"], start_ms=start_ms, cutoff_ms=cutoff_ms)
        snapshot_validation = validate_snapshot(
            temporary["database"], start_ms=start_ms, cutoff_ms=cutoff_ms
        )
        minimum_coverage = float(gate["minimum_dual_market_coverage_pct"])
        if not snapshot_validation["passed"]:
            raise RuntimeError("snapshot_validation_failed")
        if float(snapshot_validation["aligned_coverage_pct"]) < minimum_coverage:
            raise RuntimeError("snapshot_aligned_coverage_below_gate")

        snapshot_conn = connect_db(temporary["database"])
        feature_rows = export_features(snapshot_conn, temporary["features"])
        snapshot_conn.commit()
        snapshot_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        snapshot_conn.execute("PRAGMA journal_mode=DELETE")
        snapshot_conn.close()
        temporary["source_report"].write_bytes(source_report_raw)
        temporary["collection_contract"].write_bytes(contract_raw)

        db_hash = file_sha256(temporary["database"])
        sealed_id = snapshot_id(start_ms, cutoff_ms, db_hash)
        artifact_records = {
            name: artifact_record(temporary[name])
            for name in ("database", "features", "source_report", "collection_contract")
        }
        for name, record in artifact_records.items():
            record["file"] = FINAL_ARTIFACTS[name]
        manifest = {
            "schema_version": 1,
            "created_at": now_iso(current),
            "snapshot_id": sealed_id,
            "contract_id": contract.get("contract_id"),
            "selection": {
                "start_ms_inclusive": start_ms,
                "cutoff_ms_exclusive": cutoff_ms,
                "start_utc": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
                "last_minute_utc": datetime.fromtimestamp(
                    (cutoff_ms - MINUTE_MS) / 1000, tz=timezone.utc
                ).isoformat(),
                "completed_utc_minutes_only": True,
            },
            "validation": snapshot_validation,
            "feature_rows": feature_rows,
            "artifacts": artifact_records,
            "research_run": False,
            "validation_open": False,
            "oos_open": False,
            "can_trade": False,
        }
        write_json(temporary["manifest"], manifest)
        all_records = {**artifact_records, "manifest": artifact_record(temporary["manifest"])}
        all_records["manifest"]["file"] = FINAL_ARTIFACTS["manifest"]

        for name, final_name in FINAL_ARTIFACTS.items():
            os.replace(temporary[name], output_dir / final_name)
        receipt = {
            "schema_version": 1,
            "sealed_at": now_iso(current),
            "decision": "spot_perp_flow_snapshot_sealed",
            "snapshot_id": sealed_id,
            "contract_id": contract.get("contract_id"),
            "selection": manifest["selection"],
            "validation": snapshot_validation,
            "artifacts": all_records,
            "source_hashes": {
                "data_quality_report_sha256": bytes_sha256(source_report_raw),
                "collection_contract_sha256": bytes_sha256(contract_raw),
            },
            "research_run": False,
            "validation_open": False,
            "oos_open": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "telegram_send_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        }
        receipt_tmp = output_dir / f".SEAL_RECEIPT.{token}.tmp"
        write_json(receipt_tmp, receipt)
        os.replace(receipt_tmp, receipt_path)
        lock_path.unlink()
        return (
            {
                **base,
                "decision": "spot_perp_flow_snapshot_sealed",
                "sealed": True,
                "snapshot_id": sealed_id,
                "checks": checks,
                "failed_checks": [],
                "gate": gate,
                "snapshot_validation": snapshot_validation,
                "receipt": portable(receipt_path),
                "next_action": "manual prospective preregistration review; do not run research automatically",
            },
            0,
        )
    except Exception as exc:
        # Keep the lock and temporary files as crash evidence. A human must review them.
        return (
            {
                **base,
                "decision": "spot_perp_flow_snapshot_seal_failed_manual_review",
                "sealed": False,
                "checks": checks,
                "failed_checks": [],
                "gate": gate,
                "blockers": [f"seal_error:{type(exc).__name__}:{exc}"],
                "in_progress_lock": portable(lock_path),
                "next_action": "stop and inspect the retained lock and temporary artifacts",
            },
            1,
        )


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Binance Spot/Perpetual Aggressor Flow Snapshot Guard",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Sealed: `{str(report.get('sealed') is True).lower()}`",
            f"- Snapshot ID: `{report.get('snapshot_id')}`",
            f"- Failed checks: `{', '.join(report.get('failed_checks') or []) or 'none'}`",
            f"- Next action: {report.get('next_action')}",
            "",
            "## Boundary",
            "",
            "- Exactly-once data snapshot guard only.",
            "- Does not run research or open validation/OOS.",
            "- Does not emit signals, paper entries, Telegram messages or orders.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seal Binance Spot/Perpetual aggressor-flow data exactly once after every gate passes"
    )
    parser.add_argument(
        "--source-report",
        default="docs/BINANCE_SPOT_PERP_AGGRESSOR_FLOW_DATA_QUALITY_2026-07-15.json",
    )
    parser.add_argument(
        "--contract",
        default="configs/BINANCE_SPOT_PERP_AGGRESSOR_FLOW_COLLECTION_CONTRACT_2026-07-15.json",
    )
    parser.add_argument(
        "--source-db", default="data/binance_spot_perp_aggressor_flow/flow.sqlite3"
    )
    parser.add_argument(
        "--sealed-output-dir", default="data/sealed/binance_spot_perp_aggressor_flow_v1"
    )
    parser.add_argument(
        "--out-prefix",
        default="docs/BINANCE_SPOT_PERP_AGGRESSOR_FLOW_SNAPSHOT_GUARD_2026-07-15",
    )
    args = parser.parse_args()

    report, exit_code = run_guard(
        source_report_path=resolve_path(args.source_report),
        contract_path=resolve_path(args.contract),
        source_db=resolve_path(args.source_db),
        output_dir=resolve_path(args.sealed_output_dir),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "sealed": report.get("sealed") is True,
                "snapshot_id": report.get("snapshot_id"),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
