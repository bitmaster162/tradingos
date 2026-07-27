#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cross_venue_microstructure_collector import FEATURE_FIELDS, portable_path, sha256_file, write_csv  # noqa: E402
from tools.cross_venue_microstructure_sqlite_collector import export_features  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def readiness_checks(
    report: dict[str, Any],
    health: dict[str, Any],
    policy: dict[str, Any],
    sla_replay: dict[str, Any] | None = None,
) -> dict[str, bool]:
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    integrity = report.get("trade_id_integrity") if isinstance(report.get("trade_id_integrity"), dict) else {}
    required_sla_replay_decision = str(policy.get("required_collector_sla_replay_decision") or "collector_sla_replay_stable")
    return {
        "policy_locked": policy.get("status") == "locked",
        "sqlite_storage": report.get("storage", {}).get("engine") == policy.get("required_storage"),
        "collector_research_ready": report.get("research_readiness", {}).get("ready") is True,
        "collector_classification_ready": report.get("classification") == "cross_venue_microstructure_ready_for_preregistered_research",
        "health_research_ready": health.get("classification") == "cross_venue_microstructure_healthy_research_ready",
        "collector_sla_replay_stable": isinstance(sla_replay, dict) and sla_replay.get("decision") == required_sla_replay_decision and sla_replay.get("can_trade") is False,
        "minimum_hours": float(coverage.get("span_hours") or 0.0) >= float(policy.get("required_minimum_hours") or 0.0),
        "dual_trade_coverage": float(coverage.get("both_trade_coverage_pct") or 0.0) >= float(policy.get("required_dual_trade_coverage_pct") or 0.0),
        "dual_book_coverage": float(coverage.get("both_book_coverage_pct") or 0.0) >= float(policy.get("required_dual_book_coverage_pct") or 0.0),
        "binance_gaps_zero": int(integrity.get("binance", {}).get("missing_ids", -1)) == int(policy.get("required_missing_trade_ids", 0)),
        "coinbase_gaps_zero": int(integrity.get("coinbase", {}).get("missing_ids", -1)) == int(policy.get("required_missing_trade_ids", 0)),
        "source_can_trade_false": report.get("can_trade") is False and health.get("can_trade") is False,
    }


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def readiness_diagnostics(
    report: dict[str, Any],
    health: dict[str, Any],
    policy: dict[str, Any],
    checks: dict[str, bool],
    sla_replay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    integrity = report.get("trade_id_integrity") if isinstance(report.get("trade_id_integrity"), dict) else {}
    span_hours = float(coverage.get("span_hours") or 0.0)
    required_hours = float(policy.get("required_minimum_hours") or 0.0)
    remaining_hours = max(0.0, required_hours - span_hours)
    generated_at = parse_utc(report.get("generated_at"))
    earliest = generated_at + timedelta(hours=remaining_hours) if generated_at else None

    trade_coverage = float(coverage.get("both_trade_coverage_pct") or 0.0)
    book_coverage = float(coverage.get("both_book_coverage_pct") or 0.0)
    required_trade_coverage = float(policy.get("required_dual_trade_coverage_pct") or 0.0)
    required_book_coverage = float(policy.get("required_dual_book_coverage_pct") or 0.0)
    binance_missing_ids = int(integrity.get("binance", {}).get("missing_ids", -1))
    coinbase_missing_ids = int(integrity.get("coinbase", {}).get("missing_ids", -1))

    metric_blockers: list[dict[str, Any]] = []
    if not checks.get("minimum_hours", False):
        metric_blockers.append({
            "gate": "minimum_hours",
            "current": round(span_hours, 6),
            "required": required_hours,
            "remaining_hours": round(remaining_hours, 6),
        })
    if not checks.get("dual_trade_coverage", False):
        metric_blockers.append({
            "gate": "dual_trade_coverage",
            "current_pct": round(trade_coverage, 6),
            "required_pct": required_trade_coverage,
            "deficit_pct": round(max(0.0, required_trade_coverage - trade_coverage), 6),
        })
    if not checks.get("dual_book_coverage", False):
        metric_blockers.append({
            "gate": "dual_book_coverage",
            "current_pct": round(book_coverage, 6),
            "required_pct": required_book_coverage,
            "deficit_pct": round(max(0.0, required_book_coverage - book_coverage), 6),
        })
    if not checks.get("binance_gaps_zero", False):
        metric_blockers.append({"gate": "binance_gaps_zero", "missing_ids": binance_missing_ids})
    if not checks.get("coinbase_gaps_zero", False):
        metric_blockers.append({"gate": "coinbase_gaps_zero", "missing_ids": coinbase_missing_ids})

    if not checks.get("policy_locked", False):
        primary_blocker = "policy_not_locked"
    elif not checks.get("sqlite_storage", False):
        primary_blocker = "storage_not_sqlite"
    elif not checks.get("minimum_hours", False):
        primary_blocker = "minimum_time_window"
    elif not checks.get("dual_trade_coverage", False) or not checks.get("dual_book_coverage", False):
        primary_blocker = "coverage_threshold"
    elif not checks.get("binance_gaps_zero", False) or not checks.get("coinbase_gaps_zero", False):
        primary_blocker = "trade_id_gaps"
    elif not checks.get("collector_research_ready", False) or not checks.get("collector_classification_ready", False):
        primary_blocker = "collector_not_research_ready"
    elif not checks.get("health_research_ready", False):
        primary_blocker = "health_not_research_ready"
    elif not checks.get("collector_sla_replay_stable", False):
        primary_blocker = "collector_sla_replay_not_stable"
    elif not checks.get("source_can_trade_false", False):
        primary_blocker = "runtime_boundary_violation"
    else:
        primary_blocker = "none"

    return {
        "status": "ready_to_seal" if all(checks.values()) else "waiting",
        "primary_blocker": primary_blocker,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "metric_blockers": metric_blockers,
        "span_hours": round(span_hours, 6),
        "required_hours": required_hours,
        "remaining_hours": round(remaining_hours, 6),
        "estimated_earliest_time_gate_at_utc": earliest.isoformat(timespec="seconds") if earliest else None,
        "eta_assumption": "minimum time estimate only; uninterrupted collection and coverage thresholds must still hold",
        "trade_coverage_pct": round(trade_coverage, 6),
        "book_coverage_pct": round(book_coverage, 6),
        "required_trade_coverage_pct": required_trade_coverage,
        "required_book_coverage_pct": required_book_coverage,
        "binance_missing_ids": binance_missing_ids,
        "coinbase_missing_ids": coinbase_missing_ids,
        "health_classification": health.get("classification"),
        "collector_sla_replay_decision": sla_replay.get("decision") if isinstance(sla_replay, dict) else None,
        "collector_sla_replay_incident_count": sla_replay.get("incident_count") if isinstance(sla_replay, dict) else None,
        "collector_sla_replay_open_incident": sla_replay.get("open_incident") if isinstance(sla_replay, dict) else None,
        "collector_sla_replay_state_transitions": sla_replay.get("state_transitions") if isinstance(sla_replay, dict) else None,
        "collector_sla_replay_degraded_observations": sla_replay.get("degraded_observations") if isinstance(sla_replay, dict) else None,
        "collector_sla_replay_stability_blocker": sla_replay.get("stability_blocker") if isinstance(sla_replay, dict) else None,
        "collector_sla_replay_latest_degraded_generated_at": sla_replay.get("latest_degraded_generated_at") if isinstance(sla_replay, dict) else None,
        "collector_sla_replay_cooldown_until_utc": sla_replay.get("stability_cooldown_until_utc") if isinstance(sla_replay, dict) else None,
        "collector_sla_replay_cooldown_remaining_minutes": sla_replay.get("stability_cooldown_remaining_minutes") if isinstance(sla_replay, dict) else None,
        "can_trade": False,
    }


def dataset_digest(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda row: str(row["path"])):
        digest.update(str(item["path"]).encode("utf-8"))
        digest.update(str(item["sha256"]).encode("ascii"))
    return digest.hexdigest()


def sqlite_backup(source: Path, destination: Path) -> None:
    source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
        check = destination_conn.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError("snapshot_sqlite_integrity_check_failed")
    finally:
        destination_conn.close()
        source_conn.close()


def snapshot_state(db_path: Path, source_report: dict[str, Any]) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        counts = {
            "trades": conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
            "book_snapshots": conn.execute("SELECT COUNT(*) FROM book_snapshots").fetchone()[0],
            "minute_features": conn.execute("SELECT COUNT(*) FROM minute_features").fetchone()[0],
        }
        ranges = {
            "trades_first_ms": conn.execute("SELECT MIN(time_ms) FROM trades").fetchone()[0],
            "trades_last_ms": conn.execute("SELECT MAX(time_ms) FROM trades").fetchone()[0],
            "books_first_ms": conn.execute("SELECT MIN(collected_ms) FROM book_snapshots").fetchone()[0],
            "books_last_ms": conn.execute("SELECT MAX(collected_ms) FROM book_snapshots").fetchone()[0],
        }
    finally:
        conn.close()
    return {
        "schema_version": 1, "created_at": now_iso(), "counts": counts, "ranges": ranges,
        "source_report_generated_at": source_report.get("generated_at"),
        "source_collection": source_report.get("collection"), "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    diagnostics = report.get("readiness_diagnostics") if isinstance(report.get("readiness_diagnostics"), dict) else {}
    return "\n".join([
        "# Cross-Venue Microstructure Snapshot Gate", "",
        f"- Generated: `{report['generated_at']}`.", f"- Decision: `{report['decision']}`.",
        f"- Passed checks: `{report['summary']['passed']}/{report['summary']['total']}`.",
        f"- Primary blocker: `{diagnostics.get('primary_blocker')}`.",
        f"- Remaining hours: `{diagnostics.get('remaining_hours')}`.",
        f"- Earliest time gate: `{diagnostics.get('estimated_earliest_time_gate_at_utc')}`.",
        f"- Snapshot ID: `{report.get('snapshot_id')}`.",
        f"- Failed checks: `{', '.join(report['summary']['failed']) or 'none'}`.",
        "- Data sealing only; no hypothesis, validation, signals or orders.", "- `can_trade=false`.", "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Automatically seal the first research-ready microstructure SQLite snapshot")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--policy", default="configs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_POLICY.json")
    parser.add_argument("--collector-sla-replay", default="docs/CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_REPLAY_2026-06-25.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25")
    args = parser.parse_args()
    active_root = Path(args.root).resolve()
    policy_path = Path(args.policy)
    if not policy_path.is_absolute():
        policy_path = active_root / policy_path
    policy = read_json(policy_path)
    source_dir = active_root / str(policy.get("source_cache_relative") or "")
    snapshot_root = active_root / str(policy.get("snapshot_root_relative") or "")
    report = read_json(active_root / str(policy.get("readiness_report") or ""))
    health = read_json(active_root / str(policy.get("health_report") or ""))
    sla_replay_path = Path(args.collector_sla_replay)
    if not sla_replay_path.is_absolute():
        sla_replay_path = active_root / sla_replay_path
    sla_replay = read_json(sla_replay_path)
    checks = readiness_checks(report, health, policy, sla_replay)
    diagnostics = readiness_diagnostics(report, health, policy, checks, sla_replay)
    latest_path = snapshot_root / "LATEST.json"
    latest = read_json(latest_path)
    decision = "waiting_for_microstructure_readiness"
    snapshot_id = latest.get("snapshot_id") if latest else None
    dataset_sha = latest.get("dataset_sha256") if latest else None
    if latest and policy.get("seal_mode") == "first_passing_readiness_epoch_once":
        decision = "snapshot_already_sealed_for_readiness_epoch"
        diagnostics["status"] = "already_sealed"
    elif all(checks.values()):
        snapshot_root.mkdir(parents=True, exist_ok=True)
        temp_dir = snapshot_root / f".tmp-{uuid.uuid4().hex}"
        temp_dir.mkdir(parents=True)
        try:
            db_destination = temp_dir / "microstructure.sqlite3"
            sqlite_backup(source_dir / "microstructure.sqlite3", db_destination)
            conn = sqlite3.connect(db_destination)
            conn.row_factory = sqlite3.Row
            try:
                features = export_features(conn, temp_dir / "minute_features.csv")
            finally:
                conn.close()
            state = snapshot_state(db_destination, report)
            write_json(temp_dir / "SNAPSHOT_STATE.json", state)
            files = [
                {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                for path in (db_destination, temp_dir / "minute_features.csv", temp_dir / "SNAPSHOT_STATE.json")
            ]
            dataset_sha = dataset_digest(files)
            snapshot_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{dataset_sha[:12]}"
            manifest = {
                "schema_version": 1, "snapshot_id": snapshot_id, "profile": policy.get("policy_id"),
                "created_at": now_iso(), "dataset_sha256": dataset_sha, "files": files,
                "source_deleted": False, "can_trade": False,
            }
            write_json(temp_dir / "SNAPSHOT_MANIFEST.json", manifest)
            write_json(temp_dir / "VERIFICATION.json", {"verified_at": now_iso(), "passed": True, "sqlite_integrity": "ok", "files": len(files), "can_trade": False})
            final_dir = snapshot_root / snapshot_id
            temp_dir.replace(final_dir)
            write_json(latest_path, {"snapshot_id": snapshot_id, "snapshot_dir": str(final_dir), "dataset_sha256": dataset_sha, "profile": policy.get("policy_id"), "created_at": manifest["created_at"], "can_trade": False})
            decision = "microstructure_snapshot_sealed"
            diagnostics["status"] = "sealed"
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
    output = {
        "generated_at": now_iso(), "policy_id": policy.get("policy_id"), "decision": decision,
        "checks": checks, "summary": {"passed": sum(checks.values()), "total": len(checks), "failed": [name for name, passed in checks.items() if not passed]},
        "snapshot_id": snapshot_id, "dataset_sha256": dataset_sha,
        "readiness_diagnostics": diagnostics,
        "runtime_boundary": {"research_data_only": True, "registers_hypothesis": False, "opens_validation": False, "signals_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }
    prefix = Path(args.out_prefix)
    if not prefix.is_absolute():
        prefix = active_root / prefix
    write_json(prefix.with_suffix(".json"), output)
    prefix.with_suffix(".md").write_text(render_markdown(output), encoding="utf-8")
    print(json.dumps({"decision": decision, "passed": output["summary"]["passed"], "total": output["summary"]["total"], "snapshot_id": snapshot_id, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
