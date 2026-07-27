#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import deribit_options_readiness_guard_v2 as readiness  # noqa: E402
from tools import deribit_options_surface_collector_v3 as collector  # noqa: E402


V2_RUNTIME = ROOT / "HANDOFF" / "INCOMING" / "codex" / "20260711_deribit_options_surface_collector" / "runtime_v2"
V3_COLLECTOR_RUNTIME = ROOT / "data" / "forward" / "deribit_options_surface_v3"
V3_READINESS_RUNTIME = ROOT / "data" / "forward" / "deribit_options_readiness_v2"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def predecessor_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        (row for row in rows if isinstance(row.get("collected_at_ms"), int)),
        key=lambda row: int(row["collected_at_ms"]),
    )
    gaps = [int(right["collected_at_ms"]) - int(left["collected_at_ms"]) for left, right in zip(ordered, ordered[1:])]
    join_failures = sum(row.get("quality_checks", {}).get("join_rate") is False for row in ordered)
    return {
        "records": len(ordered),
        "quality_pass_records": sum(row.get("quality_pass") is True for row in ordered),
        "join_rate_failure_records": join_failures,
        "maximum_gap_seconds": round(max(gaps, default=0) / 1000.0, 3),
        "cumulative_max_gap_gate_recoverable_by_waiting": False if max(gaps, default=0) > 900_000 else True,
        "rows_admitted_to_v3": False,
        "price_outcomes_read": False,
    }


def process_inventory() -> list[dict[str, Any]]:
    command = (
        "$ErrorActionPreference='Stop'; "
        "@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine) | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    payload = json.loads(result.stdout or "[]")
    rows = payload if isinstance(payload, list) else [payload]
    return [row for row in rows if isinstance(row, dict)]


def logical_process_count(processes: list[dict[str, Any]], script_name: str) -> int:
    pattern = re.compile(
        r'(?:^|[\\/\s"])' + re.escape(script_name.lower()) + r'(?:["\s]|$)'
    )
    matches = [
        row
        for row in processes
        if pattern.search(str(row.get("CommandLine") or "").lower())
    ]
    matched_ids = {int(row.get("ProcessId") or 0) for row in matches}
    return sum(int(row.get("ParentProcessId") or 0) not in matched_ids for row in matches)


def build_report(processes: list[dict[str, Any]], predecessor_rows: list[dict[str, Any]]) -> dict[str, Any]:
    collector_config = collector.read_json(collector.DEFAULT_CONFIG)
    collector_lock = collector.read_json(collector.DEFAULT_LOCK)
    collector_lock_ok, collector_failures = collector.verify_lock(
        collector_lock,
        Path(collector.__file__).resolve(),
        collector.DEFAULT_CONFIG,
        collector_config,
    )
    readiness_config = readiness.read_json(readiness.DEFAULT_CONFIG)
    readiness_lock = readiness.read_json(readiness.DEFAULT_LOCK)
    readiness_lock_ok, readiness_failures = readiness.verify_lock(
        readiness_lock,
        Path(readiness.__file__).resolve(),
        readiness.DEFAULT_CONFIG,
        readiness_config,
    )
    collector_latest = read_json(V3_COLLECTOR_RUNTIME / "LATEST.json")
    collector_status = read_json(V3_COLLECTOR_RUNTIME / "loop_status.json")
    readiness_latest = read_json(V3_READINESS_RUNTIME / "LATEST.json")
    readiness_status = read_json(V3_READINESS_RUNTIME / "loop_status.json")
    collector_processes = logical_process_count(processes, "deribit_options_surface_collector_v3.py")
    readiness_processes = logical_process_count(processes, "deribit_options_readiness_guard_v2.py")
    quality = (collector_latest.get("surface") or {}).get("quality") or {}
    checks = {
        "collector_lock_verified": collector_lock_ok,
        "readiness_lock_verified": readiness_lock_ok,
        "future_floor_matches": collector_config.get("forward_floor_utc") == readiness_config.get("forward_floor_utc"),
        "predecessor_rows_excluded": collector_lock.get("predecessor_rows_admitted") is False and readiness_lock.get("predecessor_rows_admitted") is False,
        "collector_singleton": collector_processes == 1,
        "readiness_singleton": readiness_processes == 1,
        "collector_status_healthy": collector_status.get("status") in {"running_once", "sleeping", "sleeping_after_fetch_failure"} and collector_status.get("can_trade") is False,
        "readiness_status_healthy": readiness_status.get("status") in {"running_once", "sleeping"} and readiness_status.get("can_trade") is False,
        "collector_snapshot_healthy": collector_latest.get("decision") == "deribit_options_v3_surface_snapshot_healthy",
        "fresh_join_gate_preserved": float(quality.get("join_rate") or 0.0) >= 0.98,
        "readiness_state_consistent": (
            readiness_latest.get("research_gate_ready") is False
            and readiness_latest.get("decision") == "deribit_options_v3_forward_data_collecting"
        )
        or (
            readiness_latest.get("research_gate_ready") is True
            and readiness_latest.get("decision") == "deribit_options_v3_ready_for_observer_review"
        ),
        "observer_successor_created": False,
        "price_outcomes_read": False,
    }
    runtime_ok = all(value for name, value in checks.items() if name not in {"observer_successor_created", "price_outcomes_read"})
    readiness_metrics = readiness_latest.get("metrics") or {}
    readiness_requirements = readiness_config.get("research_gate") or {}
    decision = "deribit_options_v3_data_layer_forward_collecting" if runtime_ok else "deribit_options_v3_data_layer_integrity_blocked"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "predecessor_v2": predecessor_summary(predecessor_rows),
        "v3": {
            "forward_floor_utc": collector_config.get("forward_floor_utc"),
            "collector_contract_id": collector_config.get("contract_id"),
            "readiness_contract_id": readiness_config.get("contract_id"),
            "collector_logical_processes": collector_processes,
            "readiness_logical_processes": readiness_processes,
            "latest_quality": quality,
            "readiness_decision": readiness_latest.get("decision"),
            "readiness_metrics": readiness_metrics,
            "readiness_requirements": readiness_requirements,
            "readiness_gate_ready": readiness_latest.get("research_gate_ready") is True,
            "collector_lock_failures": collector_failures,
            "readiness_lock_failures": readiness_failures,
        },
        "forward_progress": {
            "readiness_gate_ready": readiness_latest.get("research_gate_ready") is True,
            "span_days": readiness_metrics.get("span_days"),
            "minimum_span_days": readiness_requirements.get("minimum_span_days"),
            "healthy_slots": readiness_metrics.get("healthy_slots"),
            "minimum_healthy_slots": readiness_requirements.get("minimum_healthy_slots"),
            "scheduled_coverage": readiness_metrics.get("scheduled_coverage"),
            "minimum_scheduled_coverage": readiness_requirements.get("minimum_scheduled_coverage"),
            "events_total": 0,
        },
        "runtime": {
            "all_components_passed": runtime_ok,
            "collector_logical_processes": collector_processes,
            "readiness_logical_processes": readiness_processes,
            "collector_status": collector_status.get("status"),
            "readiness_status": readiness_status.get("status"),
        },
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if name not in {"observer_successor_created", "price_outcomes_read"} and not passed],
        "anti_loop": {
            "do_not_patch_v2_in_place": True,
            "do_not_relax_join_rate_gate": True,
            "do_not_inherit_v2_rows": True,
            "do_not_create_observer_before_v3_readiness": True,
            "do_not_treat_zero_post_floor_rows_as_edge_result": True,
            "next_action": "Keep V3 collector/readiness unchanged until the locked 7d/1800-slot/95%/15m gate is reached; only then perform a manual parameter-identical observer review.",
        },
        "runtime_boundary": {
            "data_quality_only": True,
            "registers_hypothesis": False,
            "alerts_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    v2 = report["predecessor_v2"]
    v3 = report["v3"]
    return "\n".join(
        [
            "# Deribit Options V3 Data-Layer Runtime Audit",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Forward floor: `{v3['forward_floor_utc']}`",
            f"- V2 records excluded: `{v2['records']}`",
            f"- V2 join failures: `{v2['join_rate_failure_records']}`",
            f"- V2 maximum gap: `{v2['maximum_gap_seconds']}` seconds",
            f"- V3 collector/readiness logical processes: `{v3['collector_logical_processes']}/{v3['readiness_logical_processes']}`",
            f"- V3 join rate: `{v3['latest_quality'].get('join_rate')}`",
            f"- V3 readiness: `{v3['readiness_decision']}`",
            f"- V3 readiness gate open: `{v3['readiness_gate_ready']}`",
            "",
            "The old cumulative cohort is preserved but cannot recover its maximum-gap check by waiting. V3 starts a clean causal data cohort and does not create an observer, signal or order.",
            "",
            f"Next action: {report['anti_loop']['next_action']}",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime and anti-loop audit for the Deribit options V3 data layer")
    parser.add_argument("--out-prefix", default="docs/DERIBIT_OPTIONS_V3_DATA_LAYER_AUDIT_2026-07-16")
    args = parser.parse_args()
    try:
        processes = process_inventory()
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        processes = []
    report = build_report(processes, read_jsonl(V2_RUNTIME / "surface_metrics.jsonl"))
    out = (ROOT / args.out_prefix).resolve()
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "failed_checks": report["failed_checks"], "can_trade": False}, indent=2))
    return 0 if not report["failed_checks"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
