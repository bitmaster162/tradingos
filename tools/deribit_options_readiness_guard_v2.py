#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "DERIBIT_OPTIONS_READINESS_GUARD_V2.json"
DEFAULT_LOCK = ROOT / "configs" / "DERIBIT_OPTIONS_READINESS_GUARD_V2_LOCK.json"
DEFAULT_RUNTIME = ROOT / "data" / "forward" / "deribit_options_readiness_v2"


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


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def resolve_repo_path(value: Any) -> Path:
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def parse_iso_ms(value: Any) -> int | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module_import_spec_unavailable:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collector_paths(config: dict[str, Any]) -> tuple[Path, Path, Path, Path]:
    collector = config.get("collector") if isinstance(config.get("collector"), dict) else {}
    return (
        resolve_repo_path(collector.get("script")),
        resolve_repo_path(collector.get("config")),
        resolve_repo_path(collector.get("lock")),
        resolve_repo_path(collector.get("runtime")),
    )


def collector_integrity(config: dict[str, Any]) -> dict[str, Any]:
    script_path, config_path, lock_path, _ = collector_paths(config)
    checks = {
        "script_present": script_path.is_file(),
        "config_present": config_path.is_file(),
        "lock_present": lock_path.is_file(),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        return {"checks": checks, "passed": False, "failed": failures}
    module = load_module(script_path, "tradingos_deribit_collector_v3_for_readiness")
    collector_config = module.read_json(config_path)
    collector_lock = module.read_json(lock_path)
    lock_ok, lock_failures = module.verify_lock(collector_lock, script_path, config_path, collector_config)
    checks.update(
        {
            "lock_verified": lock_ok,
            "contract_v3": collector_config.get("contract_id") == "DERIBIT_BTC_OPTIONS_SURFACE_FORWARD_COLLECTOR_V3",
            "floor_matches": collector_config.get("forward_floor_utc") == config.get("forward_floor_utc"),
            "predecessor_rows_false": collector_lock.get("predecessor_rows_admitted") is False,
            "can_trade_false": collector_lock.get("can_trade") is False,
        }
    )
    failures = [name for name, passed in checks.items() if not passed]
    failures.extend(f"collector:{failure}" for failure in lock_failures)
    return {"checks": checks, "passed": not failures, "failed": failures}


def build_lock(script_path: Path, config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    _, _, collector_lock_path, _ = collector_paths(config)
    return {
        "schema_version": 1,
        "sealed_at": now_iso(),
        "contract_id": config.get("contract_id"),
        "forward_floor_utc": config.get("forward_floor_utc"),
        "script_sha256": sha256_file(script_path),
        "config_sha256": sha256_file(config_path),
        "collector_lock_sha256": sha256_file(collector_lock_path),
        "registers_hypothesis": False,
        "predecessor_rows_admitted": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def verify_lock(
    lock: dict[str, Any], script_path: Path, config_path: Path, config: dict[str, Any]
) -> tuple[bool, list[str]]:
    _, _, collector_lock_path, _ = collector_paths(config)
    checks = {
        "lock_present": bool(lock),
        "contract_matches": lock.get("contract_id") == config.get("contract_id"),
        "floor_matches": lock.get("forward_floor_utc") == config.get("forward_floor_utc"),
        "script_hash_matches": script_path.is_file() and lock.get("script_sha256") == sha256_file(script_path),
        "config_hash_matches": config_path.is_file() and lock.get("config_sha256") == sha256_file(config_path),
        "collector_lock_hash_matches": collector_lock_path.is_file() and lock.get("collector_lock_sha256") == sha256_file(collector_lock_path),
        "hypothesis_false": lock.get("registers_hypothesis") is False,
        "predecessor_rows_false": lock.get("predecessor_rows_admitted") is False,
        "orders_false": lock.get("orders_allowed") is False,
        "can_trade_false": lock.get("can_trade") is False,
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def read_forward_jsonl(path: Path, floor_ms: int) -> tuple[list[dict[str, Any]], int]:
    if not path.is_file():
        return [], 0
    rows: list[dict[str, Any]] = []
    bad_lines = 0
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            if isinstance(value, dict) and isinstance(value.get("collected_at_ms"), int) and value["collected_at_ms"] >= floor_ms:
                rows.append(value)
    return rows, bad_lines


def count_forward_raw(paths: list[Path], floor_ms: int) -> tuple[int, list[str]]:
    count = 0
    errors: list[str] = []
    for path in paths:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if isinstance(value, dict) and int(value.get("fetched_at_ms") or 0) >= floor_ms:
                        count += 1
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{path.name}:{type(exc).__name__}")
    return count, errors


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def evaluate(
    records: list[dict[str, Any]],
    raw_count: int,
    raw_errors: list[str],
    bad_jsonl_lines: int,
    current_ms: int,
    config: dict[str, Any],
    collector_lock_ok: bool,
) -> dict[str, Any]:
    interval_ms = int(config["schedule"]["interval_seconds"]) * 1000
    gate = config["research_gate"]
    valid_records: list[dict[str, Any]] = []
    schema_invalid = bad_jsonl_lines
    for record in records:
        quality = record.get("quality")
        required = ("join_rate", "mark_iv_coverage", "open_interest_coverage", "distinct_expiries")
        if not isinstance(record.get("collected_at_ms"), int) or not isinstance(quality, dict) or any(finite(quality.get(name)) is None for name in required):
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
        healthy = any(
            row.get("quality_pass") is True
            and float(row["quality"]["join_rate"]) >= float(gate["minimum_join_rate"])
            and float(row["quality"]["mark_iv_coverage"]) >= float(gate["minimum_mark_iv_coverage"])
            and float(row["quality"]["open_interest_coverage"]) >= float(gate["minimum_open_interest_coverage"])
            and int(row["quality"]["distinct_expiries"]) >= int(gate["minimum_distinct_expiries"])
            for row in slots[slot]
        )
        healthy_slots += int(healthy)

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
        "fresh": freshness_seconds is not None and -60.0 <= freshness_seconds <= float(config["schedule"]["maximum_freshness_seconds"]),
        "span_days": span_days >= float(gate["minimum_span_days"]),
        "healthy_slots": healthy_slots >= int(gate["minimum_healthy_slots"]),
        "scheduled_coverage": coverage >= float(gate["minimum_scheduled_coverage"]),
        "maximum_gap": bool(ordered_slots) and max_gap_seconds <= float(config["schedule"]["maximum_admitted_gap_seconds"]),
    }
    ready = all(checks.values())
    return {
        "decision": "deribit_options_v3_ready_for_observer_review" if ready else "deribit_options_v3_forward_data_collecting",
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


def run_once(config_path: Path, lock_path: Path, runtime_dir: Path) -> tuple[int, dict[str, Any]]:
    config = read_json(config_path)
    lock_ok, failures = verify_lock(read_json(lock_path), Path(__file__).resolve(), config_path, config)
    if not lock_ok:
        report = {"generated_at": now_iso(), "decision": "deribit_options_v3_readiness_integrity_blocked", "lock_failures": failures, "can_trade": False}
        write_json(runtime_dir / "LATEST.json", report)
        return 2, report
    floor_ms = parse_iso_ms(config.get("forward_floor_utc"))
    if floor_ms is None:
        report = {"generated_at": now_iso(), "decision": "deribit_options_v3_readiness_floor_invalid", "can_trade": False}
        write_json(runtime_dir / "LATEST.json", report)
        return 2, report
    _, _, _, collector_runtime = collector_paths(config)
    records, bad_lines = read_forward_jsonl(collector_runtime / "surface_metrics.jsonl", floor_ms)
    raw_paths = sorted((collector_runtime / "raw" / "summaries").glob("*.jsonl.gz"))
    raw_count, raw_errors = count_forward_raw(raw_paths, floor_ms)
    collector_check = collector_integrity(config)
    report = evaluate(records, raw_count, raw_errors, bad_lines, now_ms(), config, collector_check["passed"])
    report.update(
        {
            "schema_version": 1,
            "generated_at": now_iso(),
            "contract_id": config.get("contract_id"),
            "forward_floor_utc": config.get("forward_floor_utc"),
            "predecessor_rows_admitted": False,
            "collector_integrity": collector_check,
            "lock_verified": True,
            "runtime_boundary": {
                "monitor_only": True,
                "registers_hypothesis": False,
                "signals_allowed": False,
                "orders_allowed": False,
                "can_trade": False,
            },
            "can_trade": False,
        }
    )
    write_json(runtime_dir / "LATEST.json", report)
    return 0, report


def loop(config_path: Path, lock_path: Path, runtime_dir: Path, sleep_seconds: int) -> int:
    status_path = runtime_dir / "loop_status.json"
    while True:
        write_json(status_path, {"updated_at": now_iso(), "status": "running_once", "pid": os.getpid(), "can_trade": False})
        code, report = run_once(config_path, lock_path, runtime_dir)
        if code:
            write_json(status_path, {"updated_at": now_iso(), "status": "integrity_blocked", "pid": os.getpid(), "decision": report.get("decision"), "can_trade": False})
            return code
        write_json(status_path, {"updated_at": now_iso(), "status": "sleeping", "pid": os.getpid(), "sleep_seconds": sleep_seconds, "decision": report.get("decision"), "healthy_slots": report.get("metrics", {}).get("healthy_slots"), "orders_allowed": False, "can_trade": False})
        time.sleep(sleep_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only readiness guard for Deribit options V3 forward data")
    parser.add_argument("action", choices=["seal-lock", "run-once", "loop"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--lock", default=str(DEFAULT_LOCK))
    parser.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--sleep-seconds", type=int, default=300)
    parser.add_argument("--acknowledge-contract", action="store_true")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    lock_path = Path(args.lock).resolve()
    runtime_dir = Path(args.runtime_dir).resolve()
    if args.action == "seal-lock":
        if not args.acknowledge_contract:
            parser.error("seal-lock requires --acknowledge-contract")
        config = read_json(config_path)
        collector_check = collector_integrity(config)
        if config.get("status") != "fixed_readiness_contract" or config.get("can_trade") is not False or not collector_check["passed"]:
            parser.error(f"unsafe readiness contract or collector integrity failure: {collector_check['failed']}")
        write_json(lock_path, build_lock(Path(__file__).resolve(), config_path, config))
        print(json.dumps({"decision": "deribit_options_v3_readiness_lock_sealed", "can_trade": False}, indent=2))
        return 0
    if args.action == "run-once":
        code, report = run_once(config_path, lock_path, runtime_dir)
        print(json.dumps({"decision": report.get("decision"), "metrics": report.get("metrics"), "failed_checks": report.get("failed_checks"), "can_trade": False}, indent=2))
        return code
    if args.sleep_seconds < 60:
        parser.error("--sleep-seconds must be at least 60")
    return loop(config_path, lock_path, runtime_dir, args.sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
