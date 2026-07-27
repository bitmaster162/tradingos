#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ACTIVE_ROOT = HERE.parents[3]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_ms() -> int:
    return int(time.time() * 1000)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def count_gzip_jsonl(paths: list[Path]) -> tuple[int, list[str]]:
    count = 0
    errors: list[str] = []
    for path in paths:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        json.loads(line)
                        count += 1
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}:{type(exc).__name__}")
    return count, errors


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def build_lock(script_path: Path, contract_path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sealed_at": now_iso(),
        "contract_id": contract.get("contract_id"),
        "script_sha256": sha256_file(script_path),
        "contract_sha256": sha256_file(contract_path),
        "registers_hypothesis": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def verify_lock(lock: dict[str, Any], script_path: Path, contract_path: Path, contract: dict[str, Any]) -> tuple[bool, list[str]]:
    checks = {
        "lock_present": bool(lock),
        "contract_matches": lock.get("contract_id") == contract.get("contract_id"),
        "script_hash_matches": lock.get("script_sha256") == sha256_file(script_path),
        "contract_hash_matches": lock.get("contract_sha256") == sha256_file(contract_path),
        "hypothesis_false": lock.get("registers_hypothesis") is False,
        "orders_false": lock.get("orders_allowed") is False,
        "can_trade_false": lock.get("can_trade") is False,
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def collector_integrity(collector_root: Path, lock_name: str) -> dict[str, Any]:
    lock = read_json(collector_root / lock_name)
    script_path = collector_root / "collector.py"
    contract_path = collector_root / "CONTRACT.json"
    checks = {
        "lock_present": bool(lock),
        "script_present": script_path.is_file(),
        "contract_present": contract_path.is_file(),
        "script_hash_matches": script_path.is_file() and lock.get("script_sha256") == sha256_file(script_path),
        "contract_hash_matches": contract_path.is_file() and lock.get("contract_sha256") == sha256_file(contract_path),
        "contract_v2": lock.get("contract_id") == "DERIBIT_BTC_OPTIONS_SURFACE_FORWARD_COLLECTOR_V2",
        "can_trade_false": lock.get("can_trade") is False,
    }
    return {"checks": checks, "passed": all(checks.values()), "failed": [name for name, passed in checks.items() if not passed]}


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def evaluate(records: list[dict[str, Any]], raw_count: int, raw_errors: list[str], current_ms: int, contract: dict[str, Any], collector_lock_ok: bool) -> dict[str, Any]:
    interval_ms = int(contract["schedule"]["interval_seconds"]) * 1000
    gate = contract["research_gate"]
    valid_records: list[dict[str, Any]] = []
    schema_invalid = 0
    for record in records:
        timestamp = record.get("collected_at_ms")
        quality = record.get("quality")
        if not isinstance(timestamp, int) or not isinstance(quality, dict):
            schema_invalid += 1
            continue
        required = ("join_rate", "mark_iv_coverage", "open_interest_coverage", "distinct_expiries")
        if any(finite(quality.get(name)) is None for name in required):
            schema_invalid += 1
            continue
        valid_records.append(record)

    slots: dict[int, list[dict[str, Any]]] = {}
    for record in valid_records:
        slot = (int(record["collected_at_ms"]) // interval_ms) * interval_ms
        slots.setdefault(slot, []).append(record)
    ordered_slots = sorted(slots)
    healthy_slots = 0
    for slot in ordered_slots:
        slot_healthy = False
        for record in slots[slot]:
            quality = record["quality"]
            if (
                record.get("quality_pass") is True
                and float(quality["join_rate"]) >= float(gate["minimum_join_rate"])
                and float(quality["mark_iv_coverage"]) >= float(gate["minimum_mark_iv_coverage"])
                and float(quality["open_interest_coverage"]) >= float(gate["minimum_open_interest_coverage"])
                and int(quality["distinct_expiries"]) >= int(gate["minimum_distinct_expiries"])
            ):
                slot_healthy = True
                break
        healthy_slots += int(slot_healthy)

    expected_slots = ((ordered_slots[-1] - ordered_slots[0]) // interval_ms + 1) if ordered_slots else 0
    coverage = healthy_slots / expected_slots if expected_slots else 0.0
    span_days = ((ordered_slots[-1] - ordered_slots[0]) / 86_400_000) if len(ordered_slots) >= 2 else 0.0
    gaps = [right - left for left, right in zip(ordered_slots, ordered_slots[1:])]
    max_gap_seconds = max(gaps) / 1000.0 if gaps else 0.0
    latest_ms = max((int(record["collected_at_ms"]) for record in valid_records), default=0)
    freshness_seconds = (current_ms - latest_ms) / 1000.0 if latest_ms else None
    checks = {
        "collector_lock_verified": collector_lock_ok if gate["collector_lock_required"] else True,
        "schema_valid": schema_invalid == 0 and bool(valid_records),
        "raw_provenance_complete": raw_count >= len(valid_records) and not raw_errors if gate["raw_provenance_required"] else True,
        "fresh": freshness_seconds is not None and freshness_seconds <= float(contract["schedule"]["maximum_freshness_seconds"]),
        "span_days": span_days >= float(gate["minimum_span_days"]),
        "healthy_slots": healthy_slots >= int(gate["minimum_healthy_slots"]),
        "scheduled_coverage": coverage >= float(gate["minimum_scheduled_coverage"]),
        "maximum_gap": max_gap_seconds <= float(contract["schedule"]["maximum_admitted_gap_seconds"]),
    }
    ready = all(checks.values())
    return {
        "decision": "deribit_options_ready_for_preregistration_review" if ready else "deribit_options_forward_data_collecting",
        "metrics": {
            "records": len(records),
            "valid_records": len(valid_records),
            "schema_invalid": schema_invalid,
            "unique_slots": len(ordered_slots),
            "healthy_slots": healthy_slots,
            "oversampled_records": max(0, len(valid_records) - len(ordered_slots)),
            "expected_slots": expected_slots,
            "scheduled_coverage": round(coverage, 8),
            "span_days": round(span_days, 8),
            "maximum_gap_seconds": round(max_gap_seconds, 3),
            "latest_freshness_seconds": round(freshness_seconds, 3) if freshness_seconds is not None else None,
            "raw_records": raw_count,
            "raw_errors": raw_errors,
        },
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "research_gate_ready": ready,
        "can_trade": False,
    }


def run_once(active_root: Path, contract_path: Path, lock_path: Path, runtime_dir: Path) -> tuple[int, dict[str, Any]]:
    contract = read_json(contract_path)
    lock_ok, failures = verify_lock(read_json(lock_path), Path(__file__).resolve(), contract_path, contract)
    if not lock_ok:
        report = {"generated_at": now_iso(), "decision": "deribit_options_readiness_integrity_blocked", "lock_failures": failures, "can_trade": False}
        write_json(runtime_dir / "LATEST.json", report)
        return 2, report
    collector_root = active_root / str(contract["collector_relative_root"])
    collector_runtime = collector_root / str(contract["collector_runtime"])
    records = read_jsonl(collector_runtime / "surface_metrics.jsonl")
    raw_paths = sorted((collector_runtime / "raw" / "summaries").glob("*.jsonl.gz"))
    raw_count, raw_errors = count_gzip_jsonl(raw_paths)
    collector_check = collector_integrity(collector_root, str(contract["collector_lock"]))
    report = evaluate(records, raw_count, raw_errors, now_ms(), contract, collector_check["passed"])
    report.update(
        {
            "schema_version": 1,
            "generated_at": now_iso(),
            "contract_id": contract.get("contract_id"),
            "collector_integrity": collector_check,
            "lock_verified": True,
            "runtime_boundary": {"monitor_only": True, "registers_hypothesis": False, "signals_allowed": False, "orders_allowed": False, "can_trade": False},
            "can_trade": False,
        }
    )
    write_json(runtime_dir / "LATEST.json", report)
    return 0, report


def loop(active_root: Path, contract_path: Path, lock_path: Path, runtime_dir: Path, sleep_seconds: int) -> int:
    status_path = runtime_dir / "loop_status.json"
    while True:
        write_json(status_path, {"updated_at": now_iso(), "status": "running_once", "pid": os.getpid(), "can_trade": False})
        code, report = run_once(active_root, contract_path, lock_path, runtime_dir)
        if code:
            write_json(status_path, {"updated_at": now_iso(), "status": "integrity_blocked", "pid": os.getpid(), "decision": report.get("decision"), "can_trade": False})
            return code
        write_json(status_path, {"updated_at": now_iso(), "status": "sleeping", "pid": os.getpid(), "sleep_seconds": sleep_seconds, "decision": report.get("decision"), "healthy_slots": report.get("metrics", {}).get("healthy_slots"), "orders_allowed": False, "can_trade": False})
        time.sleep(sleep_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only readiness guard for Deribit options forward data")
    parser.add_argument("action", choices=["seal-lock", "run-once", "loop"])
    parser.add_argument("--active-root", default=str(ACTIVE_ROOT))
    parser.add_argument("--contract", default=str(HERE / "CONTRACT.json"))
    parser.add_argument("--lock", default=str(HERE / "IMMUTABLE_LOCK.json"))
    parser.add_argument("--runtime-dir", default=str(HERE / "runtime"))
    parser.add_argument("--sleep-seconds", type=int, default=300)
    parser.add_argument("--acknowledge-contract", action="store_true")
    args = parser.parse_args()
    contract_path = Path(args.contract).resolve()
    lock_path = Path(args.lock).resolve()
    runtime_dir = Path(args.runtime_dir).resolve()
    if args.action == "seal-lock":
        if not args.acknowledge_contract:
            parser.error("seal-lock requires --acknowledge-contract")
        contract = read_json(contract_path)
        if contract.get("status") != "fixed_readiness_contract" or contract.get("can_trade") is not False:
            parser.error("unsafe or invalid readiness contract")
        write_json(lock_path, build_lock(Path(__file__).resolve(), contract_path, contract))
        print(json.dumps({"decision": "deribit_options_readiness_lock_sealed", "can_trade": False}, indent=2))
        return 0
    if args.action == "run-once":
        code, report = run_once(Path(args.active_root), contract_path, lock_path, runtime_dir)
        print(json.dumps({"decision": report.get("decision"), "metrics": report.get("metrics"), "failed_checks": report.get("failed_checks"), "can_trade": False}, indent=2))
        return code
    if args.sleep_seconds < 60:
        parser.error("--sleep-seconds must be at least 60")
    return loop(Path(args.active_root), contract_path, lock_path, runtime_dir, args.sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
