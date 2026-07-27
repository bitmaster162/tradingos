#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COLLECTOR = "tools/binance_force_order_real_feed_collector.py"
TRANSITION_STATUSES = {"starting", "connected", "connected_waiting_events"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso(value: datetime | None = None) -> str:
    return (value or now_utc()).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def age_seconds(observed_at: datetime, value: Any) -> float | None:
    timestamp = parse_ts(value)
    if timestamp is None:
        return None
    return (observed_at - timestamp).total_seconds()


def build_snapshot(
    *,
    heartbeat_path: Path,
    loop_status_path: Path,
    loop_lock_path: Path,
    prereg_lock_path: Path,
    collector_path: Path,
    as_of: datetime | None = None,
    maximum_heartbeat_age_seconds: float = 90.0,
    maximum_liveness_age_seconds: float = 90.0,
    process_alive: Callable[[int], bool] = pid_is_alive,
) -> dict[str, Any]:
    observed_at = (as_of or now_utc()).astimezone(timezone.utc)
    recorded_at_ns = time.time_ns()
    failures: list[str] = []
    inputs: dict[str, dict[str, Any]] = {}

    def load(name: str, path: Path) -> dict[str, Any]:
        try:
            payload = read_json(path)
            inputs[name] = {"path": portable(path), "readable": True}
            return payload
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            inputs[name] = {
                "path": portable(path),
                "readable": False,
                "error": type(exc).__name__,
            }
            failures.append(f"{name}_unreadable")
            return {}

    heartbeat = load("heartbeat", heartbeat_path)
    loop_status = load("loop_status", loop_status_path)
    loop_lock = load("loop_lock", loop_lock_path)
    prereg = load("prereg_lock", prereg_lock_path)

    actual_collector_hash = ""
    try:
        actual_collector_hash = sha256_file(collector_path)
    except OSError as exc:
        inputs["collector"] = {
            "path": portable(collector_path),
            "readable": False,
            "error": type(exc).__name__,
        }
        failures.append("collector_unreadable")
    else:
        inputs["collector"] = {
            "path": portable(collector_path),
            "readable": True,
            "sha256": actual_collector_hash,
        }

    bindings = prereg.get("bindings") if isinstance(prereg.get("bindings"), dict) else {}
    expected_collector_path = str(bindings.get("liquidation_collector") or "")
    expected_collector_hash = str(bindings.get("liquidation_collector_sha256") or "").lower()
    if expected_collector_path != EXPECTED_COLLECTOR:
        failures.append("prereg_collector_path_mismatch")
    if not expected_collector_hash or actual_collector_hash.lower() != expected_collector_hash:
        failures.append("prereg_collector_hash_mismatch")

    heartbeat_age = age_seconds(observed_at, heartbeat.get("ts"))
    liveness_age = age_seconds(observed_at, heartbeat.get("last_liveness_at"))
    if heartbeat.get("tool") != EXPECTED_COLLECTOR:
        failures.append("heartbeat_tool_mismatch")
    if heartbeat.get("can_trade") is not False:
        failures.append("heartbeat_can_trade_boundary")
    if heartbeat.get("data_collector_only") is not True:
        failures.append("heartbeat_collector_only_boundary")
    if heartbeat_age is None or heartbeat_age < -5.0 or heartbeat_age > maximum_heartbeat_age_seconds:
        failures.append("heartbeat_stale_or_invalid")
    try:
        liveness_messages_seen = int(heartbeat.get("liveness_messages_seen") or 0)
    except (TypeError, ValueError):
        liveness_messages_seen = 0
    heartbeat_status = str(heartbeat.get("status") or "")
    transition_grace = (
        heartbeat_status in TRANSITION_STATUSES
        and liveness_messages_seen == 0
        and heartbeat_age is not None
        and 0.0 <= heartbeat_age <= 15.0
    )
    if not transition_grace:
        if liveness_age is None or liveness_age < -5.0 or liveness_age > maximum_liveness_age_seconds:
            failures.append("transport_liveness_stale_or_invalid")
        if liveness_messages_seen <= 0:
            failures.append("no_transport_liveness_messages")
    try:
        parse_errors_count = int(heartbeat.get("parse_errors_count") or 0)
    except (TypeError, ValueError):
        parse_errors_count = -1
    if parse_errors_count != 0:
        failures.append("collector_parse_errors")

    try:
        loop_pid = int(loop_lock.get("pid") or 0)
        status_pid = int(loop_status.get("pid") or 0)
    except (TypeError, ValueError):
        loop_pid = 0
        status_pid = 0
    if loop_pid <= 0 or status_pid != loop_pid:
        failures.append("collector_loop_pid_mismatch")
    elif not process_alive(loop_pid):
        failures.append("collector_loop_pid_dead")
    if str(loop_lock.get("root") or "").lower() != str(ROOT.resolve()).lower():
        failures.append("collector_loop_root_mismatch")
    if str(loop_status.get("root") or "").lower() != str(ROOT.resolve()).lower():
        failures.append("collector_status_root_mismatch")
    if str(loop_status.get("status") or "") not in {"running", "running_collector_cycle", "ran_collector_cycle"}:
        failures.append("collector_loop_status_not_running")
    if loop_status.get("live_trading_locked") is not True or loop_status.get("data_collector_only") is not True:
        failures.append("collector_loop_boundary_mismatch")

    failures = sorted(set(failures))
    if failures:
        status = "transport_liveness_invalid"
    elif transition_grace:
        status = "transport_liveness_transition"
    else:
        status = "transport_liveness_ok"
    try:
        collector_pid = int(heartbeat.get("collector_pid") or 0)
    except (TypeError, ValueError):
        collector_pid = 0
    return {
        "schema_version": 2,
        "ts": now_iso(observed_at),
        "recorded_at_ns": recorded_at_ns,
        "heartbeat_id": f"sidecar:{os.getpid()}:{recorded_at_ns}",
        "status": status,
        "tool": "tools/liquidation_force_order_transport_liveness_recorder.py",
        "collector_pid": collector_pid,
        "collector_loop_pid": loop_pid,
        "source_heartbeat_ts": heartbeat.get("ts"),
        "source_heartbeat_status": heartbeat_status,
        "source_last_liveness_at": heartbeat.get("last_liveness_at"),
        "heartbeat_age_seconds": round(heartbeat_age, 3) if heartbeat_age is not None else None,
        "liveness_age_seconds": round(liveness_age, 3) if liveness_age is not None else None,
        "liveness_messages_seen": liveness_messages_seen,
        "parse_errors_count": parse_errors_count,
        "collector_source_sha256": actual_collector_hash,
        "expected_collector_sha256": expected_collector_hash,
        "failures": failures,
        "inputs": inputs,
        "boundary": {
            "audit_only": True,
            "data_collector_only": True,
            "changes_preregistered_rules": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    with path.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description="Append-only forceOrder transport liveness sidecar")
    parser.add_argument("--heartbeat", default="logs/liquidation_force_order/liquidation_force_order_collector_heartbeat.json")
    parser.add_argument("--loop-status", default="logs/liquidation_force_order/liquidation_force_order_loop_status.json")
    parser.add_argument("--loop-lock", default="logs/liquidation_force_order/liquidation_force_order_loop.lock.json")
    parser.add_argument("--prereg-lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json")
    parser.add_argument("--collector", default=EXPECTED_COLLECTOR)
    parser.add_argument("--ledger", default="logs/liquidation_force_order/liquidation_force_order_collector_heartbeat.jsonl")
    parser.add_argument("--maximum-heartbeat-age-seconds", type=float, default=90.0)
    parser.add_argument("--maximum-liveness-age-seconds", type=float, default=90.0)
    args = parser.parse_args()
    snapshot = build_snapshot(
        heartbeat_path=resolve_path(args.heartbeat),
        loop_status_path=resolve_path(args.loop_status),
        loop_lock_path=resolve_path(args.loop_lock),
        prereg_lock_path=resolve_path(args.prereg_lock),
        collector_path=resolve_path(args.collector),
        maximum_heartbeat_age_seconds=args.maximum_heartbeat_age_seconds,
        maximum_liveness_age_seconds=args.maximum_liveness_age_seconds,
    )
    append_jsonl(resolve_path(args.ledger), snapshot)
    print(json.dumps({"status": snapshot["status"], "failures": snapshot["failures"], "can_trade": False}))
    return 0 if snapshot["status"] in {"transport_liveness_ok", "transport_liveness_transition"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
