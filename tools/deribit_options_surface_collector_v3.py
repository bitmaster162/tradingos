#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "DERIBIT_OPTIONS_SURFACE_COLLECTOR_V3.json"
DEFAULT_LOCK = ROOT / "configs" / "DERIBIT_OPTIONS_SURFACE_COLLECTOR_V3_LOCK.json"
DEFAULT_RUNTIME = ROOT / "data" / "forward" / "deribit_options_surface_v3"


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


def load_base_collector(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("tradingos_deribit_collector_v2_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("base_collector_import_spec_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def predecessor_paths(config: dict[str, Any]) -> tuple[Path, Path, Path]:
    predecessor = config.get("predecessor") if isinstance(config.get("predecessor"), dict) else {}
    return (
        resolve_repo_path(predecessor.get("script")),
        resolve_repo_path(predecessor.get("contract")),
        resolve_repo_path(predecessor.get("lock")),
    )


def base_integrity(config: dict[str, Any]) -> tuple[bool, list[str]]:
    script_path, contract_path, lock_path = predecessor_paths(config)
    missing = [str(path) for path in (script_path, contract_path, lock_path) if not path.is_file()]
    if missing:
        return False, [f"missing:{path}" for path in missing]
    base = load_base_collector(script_path)
    contract = base.read_json(contract_path)
    lock = base.read_json(lock_path)
    passed, failures = base.verify_lock(lock, script_path, contract_path, contract)
    if contract.get("contract_id") != "DERIBIT_BTC_OPTIONS_SURFACE_FORWARD_COLLECTOR_V2":
        failures.append("base_contract_id")
        passed = False
    return passed, failures


def build_lock(script_path: Path, config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    base_script, base_contract, base_lock = predecessor_paths(config)
    return {
        "schema_version": 1,
        "sealed_at": now_iso(),
        "contract_id": config.get("contract_id"),
        "forward_floor_utc": config.get("forward_floor_utc"),
        "script_sha256": sha256_file(script_path),
        "config_sha256": sha256_file(config_path),
        "base_script_sha256": sha256_file(base_script),
        "base_contract_sha256": sha256_file(base_contract),
        "base_lock_sha256": sha256_file(base_lock),
        "predecessor_rows_admitted": False,
        "directional_hypothesis_registered": False,
        "credentials_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def verify_lock(
    lock: dict[str, Any], script_path: Path, config_path: Path, config: dict[str, Any]
) -> tuple[bool, list[str]]:
    base_script, base_contract, base_lock = predecessor_paths(config)
    base_ok, base_failures = base_integrity(config)
    checks = {
        "lock_present": bool(lock),
        "contract_matches": lock.get("contract_id") == config.get("contract_id"),
        "floor_matches": lock.get("forward_floor_utc") == config.get("forward_floor_utc"),
        "script_hash_matches": script_path.is_file() and lock.get("script_sha256") == sha256_file(script_path),
        "config_hash_matches": config_path.is_file() and lock.get("config_sha256") == sha256_file(config_path),
        "base_script_hash_matches": base_script.is_file() and lock.get("base_script_sha256") == sha256_file(base_script),
        "base_contract_hash_matches": base_contract.is_file() and lock.get("base_contract_sha256") == sha256_file(base_contract),
        "base_lock_hash_matches": base_lock.is_file() and lock.get("base_lock_sha256") == sha256_file(base_lock),
        "base_integrity": base_ok,
        "predecessor_rows_false": lock.get("predecessor_rows_admitted") is False,
        "hypothesis_false": lock.get("directional_hypothesis_registered") is False,
        "credentials_false": lock.get("credentials_allowed") is False,
        "orders_false": lock.get("orders_allowed") is False,
        "can_trade_false": lock.get("can_trade") is False,
    }
    failures = [name for name, passed in checks.items() if not passed]
    failures.extend(f"base:{failure}" for failure in base_failures)
    return not failures, failures


def refresh_instruments(
    base: ModuleType,
    config: dict[str, Any],
    runtime_dir: Path,
    fetcher: Callable[[str, int], dict[str, Any]],
    clock_ms: Callable[[], int],
    reason: str,
) -> dict[str, Any]:
    source = config["source"]
    params = {"currency": source["currency"], "kind": source["kind"], "expired": "false"}
    timeout = int(config["collection"]["request_timeout_seconds"])
    response = fetcher(base.endpoint(config, source["instruments_method"], params), timeout)
    fetched_ms = clock_ms()
    cache = {
        "fetched_at": datetime.fromtimestamp(fetched_ms / 1000.0, tz=timezone.utc).isoformat(timespec="seconds"),
        "fetched_at_ms": fetched_ms,
        "refresh_reason": reason,
        "response": response,
    }
    stamp = datetime.fromtimestamp(fetched_ms / 1000.0, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base.write_gzip_json(runtime_dir / "raw" / "instruments" / f"{stamp}.json.gz", cache)
    base.write_gzip_json(runtime_dir / "instruments_latest.json.gz", cache)
    return cache


def collect_snapshot(
    config: dict[str, Any],
    runtime_dir: Path,
    *,
    base: ModuleType,
    fetcher: Callable[[str, int], dict[str, Any]] | None = None,
    clock_ms: Callable[[], int] = now_ms,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[int, dict[str, Any]]:
    fetch = fetcher or base.fetch_json
    source = config["source"]
    collection = config["collection"]
    timeout = int(collection["request_timeout_seconds"])
    params = {"currency": source["currency"], "kind": source["kind"]}
    cache_path = runtime_dir / "instruments_latest.json.gz"
    cache = base.read_gzip_json(cache_path)
    current_ms = clock_ms()
    cache_age_ms = current_ms - int(cache.get("fetched_at_ms") or 0)
    max_age_ms = int(collection["instrument_cache_max_age_seconds"]) * 1000
    refresh_count = 0
    refresh_reasons: list[str] = []
    refresh_error = None

    cached_rows = cache.get("response", {}).get("result")
    if not isinstance(cached_rows, list) or cache_age_ms > max_age_ms:
        reason = "cache_missing" if not isinstance(cached_rows, list) else "cache_age_exceeded"
        try:
            cache = refresh_instruments(base, config, runtime_dir, fetch, clock_ms, reason)
            refresh_count += 1
            refresh_reasons.append(reason)
            sleep_fn(float(collection.get("inter_request_delay_seconds") or 0.0))
        except Exception as exc:  # network boundary
            refresh_error = f"{type(exc).__name__}:{exc}"

    instruments = cache.get("response", {}).get("result")
    if not isinstance(instruments, list):
        return 1, {
            "generated_at": now_iso(),
            "decision": "deribit_options_v3_instruments_unavailable",
            "error": refresh_error,
            "can_trade": False,
        }

    try:
        summary_response = fetch(base.endpoint(config, source["summary_method"], params), timeout)
    except Exception as exc:  # network boundary
        return 1, {
            "generated_at": now_iso(),
            "decision": "deribit_options_v3_summary_fetch_failed",
            "error": f"{type(exc).__name__}:{exc}",
            "can_trade": False,
        }

    collected_ms = clock_ms()
    summary_raw = {
        "fetched_at": datetime.fromtimestamp(collected_ms / 1000.0, tz=timezone.utc).isoformat(timespec="seconds"),
        "fetched_at_ms": collected_ms,
        "response": summary_response,
    }
    day = datetime.fromtimestamp(collected_ms / 1000.0, tz=timezone.utc).strftime("%Y%m%d")
    base.append_gzip_jsonl(runtime_dir / "raw" / "summaries" / f"{day}.jsonl.gz", summary_raw)
    surface = base.derive_surface(instruments, summary_response["result"], collected_ms, config)

    join_failed = surface.get("quality_checks", {}).get("join_rate") is False
    reactive_allowed = bool(collection.get("reactive_instrument_refresh_on_join_failure"))
    reactive_limit = int(collection.get("maximum_reactive_refreshes_per_cycle") or 0)
    reactive_triggered = bool(join_failed and reactive_allowed and refresh_count < reactive_limit)
    if reactive_triggered:
        try:
            cache = refresh_instruments(base, config, runtime_dir, fetch, clock_ms, "join_rate_failure")
            refresh_count += 1
            refresh_reasons.append("join_rate_failure")
            instruments = cache["response"]["result"]
            surface = base.derive_surface(instruments, summary_response["result"], collected_ms, config)
        except Exception as exc:  # network boundary
            refresh_error = f"{type(exc).__name__}:{exc}"

    cache_fetched_ms = int(cache.get("fetched_at_ms") or 0)
    surface.update(
        {
            "collector_contract_id": config.get("contract_id"),
            "forward_floor_utc": config.get("forward_floor_utc"),
            "predecessor_rows_admitted": False,
            "instrument_cache_age_seconds": round(max(0, collected_ms - cache_fetched_ms) / 1000.0, 3),
            "instrument_refresh_count": refresh_count,
            "instrument_refresh_reasons": refresh_reasons,
            "reactive_refresh_triggered": reactive_triggered,
            "instrument_refresh_error": refresh_error,
            "source_server_us_in": summary_response.get("usIn"),
            "source_server_us_out": summary_response.get("usOut"),
        }
    )
    base.append_jsonl(runtime_dir / "surface_metrics.jsonl", surface)
    healthy = surface.get("quality_pass") is True and refresh_error is None
    decision = "deribit_options_v3_surface_snapshot_healthy" if healthy else "deribit_options_v3_surface_snapshot_degraded"
    return 0, {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "surface": surface,
        "directional_hypothesis_registered": False,
        "runtime_boundary": {
            "collector_only": True,
            "credentials_allowed": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def run_once(config_path: Path, lock_path: Path, runtime_dir: Path) -> tuple[int, dict[str, Any]]:
    config = read_json(config_path)
    lock_ok, failures = verify_lock(read_json(lock_path), Path(__file__).resolve(), config_path, config)
    if not lock_ok:
        report = {
            "generated_at": now_iso(),
            "decision": "deribit_options_v3_collector_integrity_blocked",
            "lock_failures": failures,
            "can_trade": False,
        }
        write_json(runtime_dir / "LATEST.json", report)
        return 2, report
    base_script, _, _ = predecessor_paths(config)
    code, report = collect_snapshot(config, runtime_dir, base=load_base_collector(base_script))
    report["lock_verified"] = True
    write_json(runtime_dir / "LATEST.json", report)
    return code, report


def loop(config_path: Path, lock_path: Path, runtime_dir: Path, sleep_seconds: int) -> int:
    status_path = runtime_dir / "loop_status.json"
    while True:
        write_json(status_path, {"updated_at": now_iso(), "status": "running_once", "pid": os.getpid(), "can_trade": False})
        code, report = run_once(config_path, lock_path, runtime_dir)
        if code == 2:
            write_json(status_path, {"updated_at": now_iso(), "status": "integrity_blocked", "pid": os.getpid(), "decision": report.get("decision"), "can_trade": False})
            return code
        status = "sleeping" if code == 0 else "sleeping_after_fetch_failure"
        write_json(status_path, {"updated_at": now_iso(), "status": status, "pid": os.getpid(), "sleep_seconds": sleep_seconds, "decision": report.get("decision"), "orders_allowed": False, "can_trade": False})
        time.sleep(sleep_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward-only Deribit BTC options surface collector V3")
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
        base_ok, base_failures = base_integrity(config)
        if config.get("status") != "fixed_collector_contract" or config.get("can_trade") is not False or not base_ok:
            parser.error(f"unsafe collector contract or base integrity failure: {base_failures}")
        write_json(lock_path, build_lock(Path(__file__).resolve(), config_path, config))
        print(json.dumps({"decision": "deribit_options_v3_collector_lock_sealed", "can_trade": False}, indent=2))
        return 0
    if args.action == "run-once":
        code, report = run_once(config_path, lock_path, runtime_dir)
        surface = report.get("surface") or {}
        print(json.dumps({"decision": report.get("decision"), "quality": surface.get("quality"), "refresh_reasons": surface.get("instrument_refresh_reasons"), "can_trade": False}, indent=2))
        return code
    if args.sleep_seconds < 60:
        parser.error("--sleep-seconds must be at least 60")
    return loop(config_path, lock_path, runtime_dir, args.sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
