#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ACTIVE_ROOT = HERE.parents[3]
COLLECTOR_ROOT = ACTIVE_ROOT / "HANDOFF" / "INCOMING" / "codex" / "20260711_deribit_options_surface_collector"
READINESS_ROOT = ACTIVE_ROOT / "HANDOFF" / "INCOMING" / "codex" / "20260711_deribit_options_readiness_guard"
DEFAULT_SOURCE = COLLECTOR_ROOT / "runtime_v2" / "surface_metrics.jsonl"
DEFAULT_READINESS = READINESS_ROOT / "runtime" / "LATEST.json"
SLOT_MS = 300_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


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


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def valid_surface_rows(path: Path) -> list[dict[str, Any]]:
    by_time: dict[int, dict[str, Any]] = {}
    for row in read_jsonl(path):
        near = row.get("near_expiry") if isinstance(row.get("near_expiry"), dict) else {}
        required = (
            row.get("quality_pass") is True,
            isinstance(row.get("collected_at_ms"), int),
            isinstance(row.get("underlying_price"), (int, float)),
            float(row.get("underlying_price") or 0.0) > 0,
            isinstance(near.get("expiry_ms"), int),
            isinstance(near.get("dte"), (int, float)),
            isinstance(near.get("atm_iv_pct"), (int, float)),
            isinstance(near.get("moneyness_skew_proxy_pp"), (int, float)),
            isinstance(near.get("two_sided_quote_coverage"), (int, float)),
            row.get("can_trade") is False,
        )
        if not all(required):
            continue
        by_time[int(row["collected_at_ms"])] = row
    return [by_time[key] for key in sorted(by_time)]


def build_lock(script_path: Path, prereg_path: Path, prereg: dict[str, Any]) -> dict[str, Any]:
    collector_lock = COLLECTOR_ROOT / "IMMUTABLE_LOCK_V2.json"
    readiness_contract = READINESS_ROOT / "CONTRACT.json"
    return {
        "schema_version": 1,
        "sealed_at": now_iso(),
        "hypothesis_id": prereg.get("hypothesis_id"),
        "forward_floor_ms": prereg.get("forward_floor_ms"),
        "script_sha256": sha256_file(script_path),
        "prereg_sha256": sha256_file(prereg_path),
        "collector_lock_sha256": sha256_file(collector_lock),
        "readiness_contract_sha256": sha256_file(readiness_contract),
        "retuning_allowed": False,
        "historical_backfill_allowed": False,
        "automatic_restart_allowed": False,
        "alerts_allowed": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def verify_lock(lock: dict[str, Any], script_path: Path, prereg_path: Path, prereg: dict[str, Any]) -> tuple[bool, list[str]]:
    collector_lock = COLLECTOR_ROOT / "IMMUTABLE_LOCK_V2.json"
    readiness_contract = READINESS_ROOT / "CONTRACT.json"
    checks = {
        "lock_present": bool(lock),
        "hypothesis_matches": lock.get("hypothesis_id") == prereg.get("hypothesis_id"),
        "floor_matches": lock.get("forward_floor_ms") == prereg.get("forward_floor_ms"),
        "script_hash_matches": lock.get("script_sha256") == sha256_file(script_path),
        "prereg_hash_matches": lock.get("prereg_sha256") == sha256_file(prereg_path),
        "collector_lock_matches": lock.get("collector_lock_sha256") == sha256_file(collector_lock),
        "readiness_contract_matches": lock.get("readiness_contract_sha256") == sha256_file(readiness_contract),
        "retuning_false": lock.get("retuning_allowed") is False,
        "historical_backfill_false": lock.get("historical_backfill_allowed") is False,
        "orders_false": lock.get("orders_allowed") is False,
        "can_trade_false": lock.get("can_trade") is False,
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def collector_integrity() -> tuple[bool, list[str]]:
    lock = read_json(COLLECTOR_ROOT / "IMMUTABLE_LOCK_V2.json")
    contract_path = COLLECTOR_ROOT / "CONTRACT.json"
    script_path = COLLECTOR_ROOT / "collector.py"
    checks = {
        "collector_lock_present": bool(lock),
        "collector_script_hash": lock.get("script_sha256") == sha256_file(script_path),
        "collector_contract_hash": lock.get("contract_sha256") == sha256_file(contract_path),
        "collector_contract_v2": lock.get("contract_id") == "DERIBIT_BTC_OPTIONS_SURFACE_FORWARD_COLLECTOR_V2",
        "collector_orders_false": lock.get("orders_allowed") is False,
        "collector_can_trade_false": lock.get("can_trade") is False,
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def find_lookback_index(times: list[int], index: int, lookback_ms: int, tolerance_ms: int) -> int | None:
    target = times[index] - lookback_ms
    candidate = bisect.bisect_right(times, target, 0, index) - 1
    if candidate < 0 or times[candidate] < target - tolerance_ms:
        return None
    return candidate


def row_change(rows: list[dict[str, Any]], times: list[int], index: int, prereg: dict[str, Any]) -> dict[str, float] | None:
    features = prereg["features"]
    lookback_ms = int(features["change_lookback_slots"]) * SLOT_MS
    tolerance_ms = int(features["change_lookback_tolerance_slots"]) * SLOT_MS
    previous_index = find_lookback_index(times, index, lookback_ms, tolerance_ms)
    if previous_index is None:
        return None
    current = rows[index]
    previous = rows[previous_index]
    current_near = current["near_expiry"]
    previous_near = previous["near_expiry"]
    if features.get("same_expiry_for_change_required") and current_near["expiry_ms"] != previous_near["expiry_ms"]:
        return None
    previous_price = float(previous["underlying_price"])
    if previous_price <= 0:
        return None
    return {
        "skew_change_pp": float(current_near["moneyness_skew_proxy_pp"]) - float(previous_near["moneyness_skew_proxy_pp"]),
        "atm_iv_change_pp": float(current_near["atm_iv_pct"]) - float(previous_near["atm_iv_pct"]),
        "underlying_return_bps": (float(current["underlying_price"]) / previous_price - 1.0) * 10_000.0,
    }


def robust_z(value: float, history: list[float], scale_floor: float) -> tuple[float, dict[str, float]] | None:
    clean = [float(item) for item in history if math.isfinite(float(item))]
    if not clean:
        return None
    center = statistics.median(clean)
    mad = statistics.median(abs(item - center) for item in clean)
    population_std = statistics.pstdev(clean) if len(clean) > 1 else 0.0
    scale = max(1.4826 * mad, population_std, float(scale_floor))
    return (value - center) / scale, {"center": center, "mad": mad, "population_std": population_std, "scale": scale}


def event_features(rows: list[dict[str, Any]], index: int, prereg: dict[str, Any]) -> dict[str, Any] | None:
    features = prereg["features"]
    times = [int(row["collected_at_ms"]) for row in rows]
    current_change = row_change(rows, times, index, prereg)
    if current_change is None:
        return None
    calibration_start_ms = times[index] - int(features["calibration_window_slots"]) * SLOT_MS
    skew_history: list[float] = []
    iv_history: list[float] = []
    for prior_index in range(index):
        if times[prior_index] < calibration_start_ms:
            continue
        change = row_change(rows, times, prior_index, prereg)
        if change is None:
            continue
        skew_history.append(change["skew_change_pp"])
        iv_history.append(change["atm_iv_change_pp"])
    minimum = int(features["minimum_calibration_changes"])
    if len(skew_history) < minimum or len(iv_history) < minimum:
        return None
    skew_z_result = robust_z(current_change["skew_change_pp"], skew_history, float(features["skew_scale_floor_pp"]))
    iv_z_result = robust_z(current_change["atm_iv_change_pp"], iv_history, float(features["atm_iv_scale_floor_pp"]))
    if skew_z_result is None or iv_z_result is None:
        return None
    skew_z, skew_calibration = skew_z_result
    iv_z, iv_calibration = iv_z_result
    return {
        **current_change,
        "skew_change_z": skew_z,
        "atm_iv_change_z": iv_z,
        "calibration_changes": len(skew_history),
        "skew_calibration": skew_calibration,
        "atm_iv_calibration": iv_calibration,
    }


def build_event(rows: list[dict[str, Any]], index: int, prereg: dict[str, Any]) -> dict[str, Any] | None:
    row = rows[index]
    near = row["near_expiry"]
    features = prereg["features"]
    if not (float(features["near_expiry_min_dte"]) <= float(near["dte"]) <= float(features["near_expiry_max_dte"])):
        return None
    if float(near["two_sided_quote_coverage"]) < float(features["minimum_two_sided_quote_coverage"]):
        return None
    values = event_features(rows, index, prereg)
    if values is None:
        return None
    if values["skew_change_z"] < float(features["skew_change_z_min"]):
        return None
    if values["atm_iv_change_z"] < float(features["atm_iv_change_z_min"]):
        return None
    if values["underlying_return_bps"] > float(features["underlying_return_1h_max_bps"]):
        return None
    observed_ms = int(row["collected_at_ms"])
    return {
        "event_id": f"{prereg['hypothesis_id']}:{observed_ms}",
        "observed_at": now_iso(),
        "event_snapshot_ms": observed_ms,
        "event_snapshot_time": row.get("collected_at"),
        "direction": "SHORT",
        "underlying_price": row["underlying_price"],
        "near_expiry_ms": near["expiry_ms"],
        "near_dte": near["dte"],
        "near_atm_iv_pct": near["atm_iv_pct"],
        "near_skew_proxy_pp": near["moneyness_skew_proxy_pp"],
        "features": values,
        "entry_rule": prereg["outcomes"]["entry"],
        "can_trade": False,
    }


def first_row_at_or_after(rows: list[dict[str, Any]], times: list[int], target_ms: int, start: int = 0) -> int | None:
    index = bisect.bisect_left(times, target_ms, lo=start)
    return index if index < len(rows) else None


def resolve_outcomes(rows: list[dict[str, Any]], events_path: Path, outcomes_path: Path, prereg: dict[str, Any]) -> int:
    events = read_jsonl(events_path)
    existing = {str(row.get("outcome_id")) for row in read_jsonl(outcomes_path)}
    times = [int(row["collected_at_ms"]) for row in rows]
    added = 0
    tolerance_ms = int(prereg["features"]["change_lookback_tolerance_slots"]) * SLOT_MS
    for event in events:
        event_ms = int(event["event_snapshot_ms"])
        entry_index = first_row_at_or_after(rows, times, event_ms + 1)
        if entry_index is None:
            continue
        entry_row = rows[entry_index]
        entry_price = float(entry_row["underlying_price"])
        entry_ms = int(entry_row["collected_at_ms"])
        for horizon in prereg["outcomes"]["horizons_minutes"]:
            horizon = int(horizon)
            outcome_id = f"{event['event_id']}:{horizon}m"
            if outcome_id in existing:
                continue
            target_ms = entry_ms + horizon * 60_000
            exit_index = first_row_at_or_after(rows, times, target_ms, start=entry_index + 1)
            if exit_index is None or times[exit_index] > target_ms + tolerance_ms:
                continue
            exit_row = rows[exit_index]
            exit_price = float(exit_row["underlying_price"])
            gross_bps = -(exit_price / entry_price - 1.0) * 10_000.0
            append_jsonl(
                outcomes_path,
                {
                    "outcome_id": outcome_id,
                    "event_id": event["event_id"],
                    "resolved_at": now_iso(),
                    "horizon_minutes": horizon,
                    "direction": "SHORT",
                    "entry_snapshot_ms": entry_ms,
                    "exit_snapshot_ms": int(exit_row["collected_at_ms"]),
                    "entry": entry_price,
                    "exit": exit_price,
                    "gross_bps": round(gross_bps, 8),
                    "net_base_bps": round(gross_bps - float(prereg["outcomes"]["base_round_trip_cost_bps"]), 8),
                    "net_stress_bps": round(gross_bps - float(prereg["outcomes"]["stress_round_trip_cost_bps"]), 8),
                    "can_trade": False,
                },
            )
            existing.add(outcome_id)
            added += 1
    return added


def outcome_summary(outcomes: list[dict[str, Any]], prereg: dict[str, Any]) -> dict[str, Any]:
    minimum = int(prereg["outcomes"]["minimum_resolved_events_per_horizon"])
    result: dict[str, Any] = {}
    for horizon in prereg["outcomes"]["horizons_minutes"]:
        rows = [row for row in outcomes if int(row.get("horizon_minutes") or 0) == int(horizon)]
        base = [float(row["net_base_bps"]) for row in rows]
        stress = [float(row["net_stress_bps"]) for row in rows]
        result[f"{horizon}m"] = {
            "resolved": len(rows),
            "mean_net_base_bps": round(statistics.fmean(base), 8) if base else None,
            "mean_net_stress_bps": round(statistics.fmean(stress), 8) if stress else None,
            "base_winrate_pct": round(sum(value > 0 for value in base) / len(base) * 100.0, 6) if base else None,
            "minimum_required": minimum,
            "threshold_ready": len(rows) >= minimum,
        }
    return result


def run_once(source_path: Path, readiness_path: Path, prereg_path: Path, lock_path: Path, runtime_dir: Path) -> tuple[int, dict[str, Any]]:
    prereg = read_json(prereg_path)
    lock = read_json(lock_path)
    lock_ok, lock_failures = verify_lock(lock, Path(__file__).resolve(), prereg_path, prereg)
    collector_ok, collector_failures = collector_integrity()
    if not lock_ok or not collector_ok:
        report = {
            "generated_at": now_iso(),
            "decision": "deribit_options_skew_observer_integrity_blocked",
            "lock_failures": lock_failures,
            "collector_failures": collector_failures,
            "can_trade": False,
        }
        write_json(runtime_dir / "LATEST.json", report)
        return 2, report

    rows = valid_surface_rows(source_path)
    readiness = read_json(readiness_path)
    readiness_ready = readiness.get("research_gate_ready") is True and readiness.get("can_trade") is False
    state_path = runtime_dir / "state.json"
    events_path = runtime_dir / "events.jsonl"
    outcomes_path = runtime_dir / "outcomes.jsonl"
    state = read_json(state_path)
    gate_opened_at_ms = int(state.get("gate_opened_at_ms") or 0)
    if readiness_ready and gate_opened_at_ms <= 0:
        readiness_ms = parse_iso_ms(readiness.get("generated_at")) or int(time.time() * 1000)
        gate_opened_at_ms = max(int(prereg["forward_floor_ms"]), readiness_ms)
        state["gate_opened_at_ms"] = gate_opened_at_ms
        state["gate_opened_at"] = datetime.fromtimestamp(gate_opened_at_ms / 1000, timezone.utc).isoformat()

    events_added = 0
    latest_source_ms = int(rows[-1]["collected_at_ms"]) if rows else None
    if readiness_ready and gate_opened_at_ms > 0 and rows:
        last_processed_ms = int(state.get("last_processed_ms") or (gate_opened_at_ms - 1))
        last_event_ms = int(state.get("last_event_ms") or 0)
        cooldown_ms = int(prereg["features"]["event_cooldown_minutes"]) * 60_000
        for index, row in enumerate(rows):
            current_ms = int(row["collected_at_ms"])
            if current_ms <= last_processed_ms or current_ms < gate_opened_at_ms:
                continue
            event = build_event(rows, index, prereg)
            if event is not None and current_ms - last_event_ms >= cooldown_ms:
                append_jsonl(events_path, event)
                events_added += 1
                last_event_ms = current_ms
            last_processed_ms = current_ms
        state["last_processed_ms"] = last_processed_ms
        state["last_event_ms"] = last_event_ms
        write_json(state_path, state)

    outcomes_added = (
        resolve_outcomes(rows, events_path, outcomes_path, prereg)
        if rows and readiness_ready and gate_opened_at_ms > 0
        else 0
    )
    events = read_jsonl(events_path)
    outcomes = read_jsonl(outcomes_path)
    decision = "deribit_options_skew_forward_collecting" if readiness_ready else "deribit_options_skew_waiting_readiness_gate"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "hypothesis_id": prereg.get("hypothesis_id"),
        "forward_floor_utc": prereg.get("forward_floor_utc"),
        "readiness_decision": readiness.get("decision"),
        "readiness_gate_ready": readiness_ready,
        "readiness_metrics": readiness.get("metrics"),
        "gate_opened_at_ms": gate_opened_at_ms or None,
        "source_rows": len(rows),
        "latest_source_ms": latest_source_ms,
        "events_total": len(events),
        "events_added": events_added,
        "outcomes_total": len(outcomes),
        "outcomes_added": outcomes_added,
        "summary": outcome_summary(outcomes, prereg),
        "lock_verified": True,
        "collector_integrity_verified": True,
        "retuning_allowed": False,
        "runtime_boundary": {
            "observer_only": True,
            "alerts_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    write_json(runtime_dir / "LATEST.json", report)
    return 0, report


def loop(source_path: Path, readiness_path: Path, prereg_path: Path, lock_path: Path, runtime_dir: Path, sleep_seconds: int) -> int:
    status_path = runtime_dir / "loop_status.json"
    while True:
        write_json(status_path, {"updated_at": now_iso(), "status": "running_once", "pid": os.getpid(), "can_trade": False})
        code, report = run_once(source_path, readiness_path, prereg_path, lock_path, runtime_dir)
        if code:
            write_json(status_path, {"updated_at": now_iso(), "status": "integrity_blocked", "pid": os.getpid(), "decision": report.get("decision"), "can_trade": False})
            return code
        write_json(
            status_path,
            {
                "updated_at": now_iso(),
                "status": "sleeping",
                "pid": os.getpid(),
                "sleep_seconds": sleep_seconds,
                "decision": report.get("decision"),
                "events_total": report.get("events_total"),
                "orders_allowed": False,
                "can_trade": False,
            },
        )
        time.sleep(sleep_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward-only Deribit options skew/IV observer")
    parser.add_argument("action", choices=["seal-lock", "run-once", "loop"])
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--readiness", default=str(DEFAULT_READINESS))
    parser.add_argument("--prereg", default=str(HERE / "PREREG.json"))
    parser.add_argument("--lock", default=str(HERE / "IMMUTABLE_LOCK.json"))
    parser.add_argument("--runtime-dir", default=str(HERE / "runtime"))
    parser.add_argument("--sleep-seconds", type=int, default=300)
    parser.add_argument("--acknowledge-preregistration", action="store_true")
    args = parser.parse_args()
    prereg_path = Path(args.prereg).resolve()
    lock_path = Path(args.lock).resolve()
    runtime_dir = Path(args.runtime_dir).resolve()
    if args.action == "seal-lock":
        if not args.acknowledge_preregistration:
            parser.error("seal-lock requires --acknowledge-preregistration")
        prereg = read_json(prereg_path)
        if prereg.get("status") != "forward_only_preregistered_waiting_readiness" or prereg.get("can_trade") is not False:
            parser.error("unsafe or invalid preregistration")
        write_json(lock_path, build_lock(Path(__file__).resolve(), prereg_path, prereg))
        print(json.dumps({"decision": "deribit_options_skew_preregistration_sealed", "can_trade": False}, indent=2))
        return 0
    if args.action == "run-once":
        code, report = run_once(Path(args.source), Path(args.readiness), prereg_path, lock_path, runtime_dir)
        print(json.dumps({"decision": report.get("decision"), "readiness_gate_ready": report.get("readiness_gate_ready"), "events_total": report.get("events_total"), "can_trade": False}, indent=2))
        return code
    if args.sleep_seconds < 60:
        parser.error("--sleep-seconds must be at least 60")
    return loop(Path(args.source), Path(args.readiness), prereg_path, lock_path, runtime_dir, args.sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
