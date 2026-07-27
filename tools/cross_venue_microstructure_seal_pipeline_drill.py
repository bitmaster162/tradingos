#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def create_synthetic_snapshot(active_root: Path, snapshot_id: str) -> Path:
    snapshot_dir = active_root / "data" / "research_snapshots_cross_venue_microstructure" / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    db_path = snapshot_dir / "microstructure.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('synthetic_drill','true')")
        conn.commit()
    finally:
        conn.close()
    features_path = snapshot_dir / "minute_features.csv"
    write_synthetic_features(features_path)
    state_path = snapshot_dir / "SNAPSHOT_STATE.json"
    write_json(
        state_path,
        {
            "schema_version": 1,
            "created_at": now_iso(),
            "synthetic_drill": True,
            "can_trade": False,
        },
    )
    files = [
        {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in (db_path, features_path, state_path)
    ]
    manifest = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "profile": "BTC_CROSS_VENUE_MICROSTRUCTURE_SQLITE_V2",
        "created_at": now_iso(),
        "dataset_sha256": "synthetic-drill",
        "files": files,
        "synthetic_drill": True,
        "can_trade": False,
    }
    write_json(snapshot_dir / "SNAPSHOT_MANIFEST.json", manifest)
    write_json(
        snapshot_dir / "VERIFICATION.json",
        {
            "verified_at": now_iso(),
            "passed": True,
            "sqlite_integrity": "ok",
            "synthetic_drill": True,
            "can_trade": False,
        },
    )
    return snapshot_dir


def run_step(command: list[str], *, cwd: Path, env: dict[str, str], timeout_s: int) -> dict[str, Any]:
    started_at = now_iso()
    completed = subprocess.run(
        command,
        cwd=cwd,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout_s,
    )
    return {
        "command": command,
        "started_at": started_at,
        "finished_at": now_iso(),
        "return_code": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Cross-Venue Microstructure Seal Pipeline Drill",
            "",
            f"- Generated: `{report['generated_at']}`.",
            f"- Decision: `{report['decision']}`.",
            f"- Snapshot: `{report['snapshot_id']}`.",
            f"- Steps passed: `{report['steps_passed']}/{report['steps_total']}`.",
            f"- Runner decision: `{report.get('runner_decision')}`.",
            f"- Experiments completed/failed: `{report.get('runner_completed')}` / `{report.get('runner_failed')}`.",
            f"- Tested configs: `{report.get('runner_tested_total')}`.",
            "- Synthetic drill only; output is not alpha evidence and not trading permission.",
            "- `can_trade=false`.",
            "",
        ]
    )


def run_drill(work_dir: Path, out_prefix: Path, timeout_s: int) -> tuple[int, dict[str, Any]]:
    active_root = work_dir / "Active"
    snapshot_id = f"synthetic-sealed-{compact_ts()}"
    snapshot_dir = create_synthetic_snapshot(active_root, snapshot_id)
    gate_path = active_root / "docs" / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json"
    write_json(
        gate_path,
        {
            "schema_version": 1,
            "generated_at": now_iso(),
            "decision": "microstructure_snapshot_sealed",
            "snapshot_id": snapshot_id,
            "dataset_sha256": "synthetic-drill",
            "summary": {"passed": 11, "total": 11, "failed": []},
            "readiness_diagnostics": {
                "primary_blocker": "none",
                "remaining_hours": 0.0,
                "trade_coverage_pct": 100.0,
                "book_coverage_pct": 100.0,
                "binance_missing_ids": 0,
                "coinbase_missing_ids": 0,
            },
            "synthetic_drill": True,
            "can_trade": False,
        },
    )
    env = os.environ.copy()
    env.setdefault("TELEGRAM_BOT_TOKEN", "DRILL_DRY_RUN_TOKEN")
    env.setdefault("TELEGRAM_CHAT_ID", "DRILL_DRY_RUN_CHAT")
    steps = [
        (
            "snapshot_gate_notify_dry_run",
            [
                sys.executable,
                "tools/cross_venue_microstructure_snapshot_gate_telegram_notify.py",
                "--snapshot-gate",
                str(gate_path),
                "--state",
                str(active_root / "logs" / "cross_venue_microstructure" / "snapshot_gate_telegram_state.json"),
                "--out-prefix",
                str(active_root / "docs" / "SNAPSHOT_GATE_NOTIFY"),
                "--dry-run",
            ],
        ),
        (
            "research_runner_run_if_ready",
            [
                sys.executable,
                "tools/cross_venue_microstructure_research_runner.py",
                "--active-root",
                str(active_root),
                "--snapshot-gate",
                str(gate_path),
                "--out-prefix",
                str(active_root / "docs" / "RESEARCH_RUNNER"),
                "--timeout-seconds",
                str(timeout_s),
                "run-if-ready",
            ],
        ),
        (
            "research_runner_notify_dry_run",
            [
                sys.executable,
                "tools/cross_venue_microstructure_research_runner_telegram_notify.py",
                "--runner-report",
                str(active_root / "docs" / "RESEARCH_RUNNER.json"),
                "--state",
                str(active_root / "logs" / "cross_venue_microstructure" / "research_runner_telegram_state.json"),
                "--out-prefix",
                str(active_root / "docs" / "RESEARCH_RUNNER_NOTIFY"),
                "--dry-run",
            ],
        ),
    ]
    step_reports = []
    for name, command in steps:
        result = run_step(command, cwd=ROOT, env=env, timeout_s=timeout_s)
        result["name"] = name
        step_reports.append(result)
    runner_report = read_json(active_root / "docs" / "RESEARCH_RUNNER.json")
    snapshot_notify = read_json(active_root / "docs" / "SNAPSHOT_GATE_NOTIFY.json")
    runner_notify = read_json(active_root / "docs" / "RESEARCH_RUNNER_NOTIFY.json")
    checks = {
        "all_steps_exit_zero": all(step["return_code"] == 0 for step in step_reports),
        "snapshot_notify_dry_run": snapshot_notify.get("decision") == "dry_run_ready",
        "research_runner_completed": runner_report.get("completed") == 4 and runner_report.get("failed") == 0,
        "research_runner_tested_all_configs": runner_report.get("tested_total") == 774,
        "research_runner_can_trade_false": runner_report.get("can_trade") is False,
        "research_notify_dry_run": runner_notify.get("decision") == "dry_run_ready",
    }
    decision = "microstructure_seal_pipeline_drill_passed" if all(checks.values()) else "microstructure_seal_pipeline_drill_failed"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "work_dir": str(work_dir),
        "active_root": str(active_root),
        "snapshot_id": snapshot_id,
        "snapshot_dir": str(snapshot_dir),
        "checks": checks,
        "steps_passed": sum(step["return_code"] == 0 for step in step_reports),
        "steps_total": len(step_reports),
        "steps": step_reports,
        "snapshot_notify_decision": snapshot_notify.get("decision"),
        "runner_decision": runner_report.get("decision"),
        "runner_completed": runner_report.get("completed"),
        "runner_failed": runner_report.get("failed"),
        "runner_candidate_count": runner_report.get("candidate_count"),
        "runner_tested_total": runner_report.get("tested_total"),
        "runner_notify_decision": runner_notify.get("decision"),
        "runtime_boundary": {
            "synthetic_drill_only": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    return (0 if decision.endswith("_passed") else 1), report


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic end-to-end drill for sealed microstructure snapshot pipeline")
    parser.add_argument("--work-dir")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_SEAL_PIPELINE_DRILL_2026-06-25")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    if args.work_dir:
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        code, report = run_drill(work_dir, out_prefix, max(1, args.timeout_seconds))
    else:
        with tempfile.TemporaryDirectory(prefix="microstructure-seal-drill-") as temp_name:
            code, report = run_drill(Path(temp_name).resolve(), out_prefix, max(1, args.timeout_seconds))
    print(json.dumps({"decision": report["decision"], "steps": f"{report['steps_passed']}/{report['steps_total']}", "tested_total": report.get("runner_tested_total"), "can_trade": False}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
