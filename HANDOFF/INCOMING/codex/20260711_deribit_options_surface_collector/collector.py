#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import statistics
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
MINUTE_MS = 60_000
DAY_MS = 86_400_000


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def write_gzip_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with gzip.open(temp, "wt", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
    temp.replace(path)


def read_gzip_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def append_gzip_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "at", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def build_lock(script_path: Path, contract_path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sealed_at": now_iso(),
        "contract_id": contract.get("contract_id"),
        "script_sha256": sha256_file(script_path),
        "contract_sha256": sha256_file(contract_path),
        "directional_hypothesis_registered": False,
        "credentials_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def verify_lock(
    lock: dict[str, Any], script_path: Path, contract_path: Path, contract: dict[str, Any]
) -> tuple[bool, list[str]]:
    checks = {
        "lock_present": bool(lock),
        "contract_matches": lock.get("contract_id") == contract.get("contract_id"),
        "script_hash_matches": lock.get("script_sha256") == sha256_file(script_path),
        "contract_hash_matches": lock.get("contract_sha256") == sha256_file(contract_path),
        "hypothesis_false": lock.get("directional_hypothesis_registered") is False,
        "credentials_false": lock.get("credentials_allowed") is False,
        "orders_false": lock.get("orders_allowed") is False,
        "can_trade_false": lock.get("can_trade") is False,
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def fetch_json(url: str, timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "TradingOS-Research-Collector/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        raise ValueError("deribit_response_missing_result_list")
    if payload.get("error"):
        raise ValueError(f"deribit_error:{payload['error']}")
    return payload


def endpoint(contract: dict[str, Any], method: str, params: dict[str, Any]) -> str:
    base = str(contract["source"]["base_url"]).rstrip("/")
    return f"{base}/{method}?{urllib.parse.urlencode(params)}"


def finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def nearest_row(rows: list[dict[str, Any]], target_strike: float) -> dict[str, Any] | None:
    valid = [row for row in rows if finite_float(row.get("strike")) is not None and finite_float(row.get("mark_iv")) is not None]
    return min(valid, key=lambda row: abs(float(row["strike"]) - target_strike)) if valid else None


def derive_surface(
    instruments: list[dict[str, Any]], summaries: list[dict[str, Any]], collected_at_ms: int, contract: dict[str, Any]
) -> dict[str, Any]:
    instrument_map = {
        str(row.get("instrument_name")): row
        for row in instruments
        if row.get("instrument_name") and row.get("is_active", True)
    }
    underlying_values = [
        value
        for value in (finite_float(row.get("underlying_price")) for row in summaries)
        if value is not None and value > 0
    ]
    underlying = statistics.median(underlying_values) if underlying_values else None
    joined: list[dict[str, Any]] = []
    for summary in summaries:
        instrument = instrument_map.get(str(summary.get("instrument_name")))
        if not instrument:
            continue
        expiry = instrument.get("expiration_timestamp")
        strike = finite_float(instrument.get("strike"))
        option_type = str(instrument.get("option_type") or "").lower()
        if expiry is None or strike is None or option_type not in {"call", "put"}:
            continue
        joined.append(
            {
                "instrument_name": summary.get("instrument_name"),
                "expiry_ms": int(expiry),
                "strike": strike,
                "option_type": option_type,
                "underlying_price": finite_float(summary.get("underlying_price")),
                "mark_iv": finite_float(summary.get("mark_iv")),
                "open_interest": finite_float(summary.get("open_interest")),
                "bid_price": finite_float(summary.get("bid_price")),
                "ask_price": finite_float(summary.get("ask_price")),
            }
        )

    by_expiry: dict[int, list[dict[str, Any]]] = {}
    for row in joined:
        by_expiry.setdefault(int(row["expiry_ms"]), []).append(row)
    expiry_rows: list[dict[str, Any]] = []
    for expiry_ms, rows in sorted(by_expiry.items()):
        expiry_underlyings = [
            float(row["underlying_price"])
            for row in rows
            if row.get("underlying_price") is not None and float(row["underlying_price"]) > 0
        ]
        expiry_underlying = statistics.median(expiry_underlyings) if expiry_underlyings else None
        calls = [row for row in rows if row["option_type"] == "call"]
        puts = [row for row in rows if row["option_type"] == "put"]
        call_by_strike = {float(row["strike"]): row for row in calls if row.get("mark_iv") is not None}
        put_by_strike = {float(row["strike"]): row for row in puts if row.get("mark_iv") is not None}
        shared_strikes = sorted(set(call_by_strike) & set(put_by_strike))
        atm_strike = (
            min(shared_strikes, key=lambda strike: abs(strike - float(expiry_underlying)))
            if shared_strikes and expiry_underlying
            else None
        )
        atm_iv = None
        if atm_strike is not None:
            atm_iv = statistics.fmean([float(call_by_strike[atm_strike]["mark_iv"]), float(put_by_strike[atm_strike]["mark_iv"])])
        put_proxy = nearest_row(puts, float(expiry_underlying) * 0.9) if expiry_underlying else None
        call_proxy = nearest_row(calls, float(expiry_underlying) * 1.1) if expiry_underlying else None
        skew_proxy = None
        if put_proxy and call_proxy:
            skew_proxy = float(put_proxy["mark_iv"]) - float(call_proxy["mark_iv"])
        call_oi = sum(float(row["open_interest"] or 0.0) for row in calls)
        put_oi = sum(float(row["open_interest"] or 0.0) for row in puts)
        two_sided = sum(row.get("bid_price") is not None and row.get("ask_price") is not None for row in rows)
        expiry_rows.append(
            {
                "expiry_ms": expiry_ms,
                "dte": round((expiry_ms - collected_at_ms) / DAY_MS, 6),
                "underlying_price": round(float(expiry_underlying), 8) if expiry_underlying is not None else None,
                "options": len(rows),
                "calls": len(calls),
                "puts": len(puts),
                "atm_strike": atm_strike,
                "atm_iv_pct": round(atm_iv, 8) if atm_iv is not None else None,
                "put_90m_iv_pct": round(float(put_proxy["mark_iv"]), 8) if put_proxy else None,
                "call_110m_iv_pct": round(float(call_proxy["mark_iv"]), 8) if call_proxy else None,
                "moneyness_skew_proxy_pp": round(skew_proxy, 8) if skew_proxy is not None else None,
                "put_call_oi_ratio": round(put_oi / call_oi, 8) if call_oi > 0 else None,
                "two_sided_quote_coverage": round(two_sided / len(rows), 8) if rows else None,
            }
        )
    with_atm = [row for row in expiry_rows if row["atm_iv_pct"] is not None]
    near = min((row for row in with_atm if row["dte"] >= 7.0), key=lambda row: row["dte"], default=None)
    medium = min((row for row in with_atm if row["dte"] >= 25.0), key=lambda row: row["dte"], default=None)
    term_spread = None
    if near and medium and near["expiry_ms"] != medium["expiry_ms"]:
        term_spread = float(medium["atm_iv_pct"]) - float(near["atm_iv_pct"])

    mark_iv_count = sum(row.get("mark_iv") is not None for row in joined)
    oi_count = sum(row.get("open_interest") is not None for row in joined)
    quality = {
        "active_instruments": len(instrument_map),
        "summary_rows": len(summaries),
        "joined_rows": len(joined),
        "join_rate": round(len(joined) / len(summaries), 8) if summaries else 0.0,
        "mark_iv_coverage": round(mark_iv_count / len(joined), 8) if joined else 0.0,
        "open_interest_coverage": round(oi_count / len(joined), 8) if joined else 0.0,
        "distinct_expiries": len(expiry_rows),
        "underlying_available": underlying is not None,
    }
    gate = contract["quality_gate"]
    checks = {
        "active_instruments": quality["active_instruments"] >= int(gate["minimum_active_instruments"]),
        "summary_rows": quality["summary_rows"] >= int(gate["minimum_summary_rows"]),
        "join_rate": quality["join_rate"] >= float(gate["minimum_join_rate"]),
        "mark_iv_coverage": quality["mark_iv_coverage"] >= float(gate["minimum_mark_iv_coverage"]),
        "open_interest_coverage": quality["open_interest_coverage"] >= float(gate["minimum_open_interest_coverage"]),
        "distinct_expiries": quality["distinct_expiries"] >= int(gate["minimum_distinct_expiries"]),
        "underlying_available": quality["underlying_available"],
    }
    reference_expiry = min((row for row in expiry_rows if row["dte"] >= 0), key=lambda row: row["dte"], default=None)
    reference_underlying = reference_expiry.get("underlying_price") if reference_expiry else underlying
    return {
        "collected_at": datetime.fromtimestamp(collected_at_ms / 1000, timezone.utc).isoformat(timespec="seconds"),
        "collected_at_ms": collected_at_ms,
        "underlying_price": round(float(reference_underlying), 8) if reference_underlying is not None else None,
        "underlying_reference": "nearest_nonexpired_expiry",
        "quality": quality,
        "quality_checks": checks,
        "quality_pass": all(checks.values()),
        "near_expiry": near,
        "medium_expiry": medium,
        "term_atm_iv_spread_pp": round(term_spread, 8) if term_spread is not None else None,
        "expiries": expiry_rows,
        "directional_signal": None,
        "can_trade": False,
    }


def run_once(contract_path: Path, lock_path: Path, runtime_dir: Path) -> tuple[int, dict[str, Any]]:
    contract = read_json(contract_path)
    lock_ok, failures = verify_lock(read_json(lock_path), Path(__file__).resolve(), contract_path, contract)
    if not lock_ok:
        report = {"generated_at": now_iso(), "decision": "deribit_options_collector_integrity_blocked", "lock_failures": failures, "can_trade": False}
        write_json(runtime_dir / "LATEST.json", report)
        return 2, report
    timeout = int(contract["collection"]["request_timeout_seconds"])
    source = contract["source"]
    params = {"currency": source["currency"], "kind": source["kind"]}
    cache_path = runtime_dir / "instruments_latest.json.gz"
    cache = read_gzip_json(cache_path)
    cache_age_ms = now_ms() - int(cache.get("fetched_at_ms") or 0)
    max_age_ms = int(contract["collection"]["instrument_cache_max_age_hours"]) * 3_600_000
    instrument_refresh_error = None
    if not isinstance(cache.get("response", {}).get("result"), list) or cache_age_ms > max_age_ms:
        try:
            response = fetch_json(endpoint(contract, source["instruments_method"], {**params, "expired": "false"}), timeout)
            cache = {"fetched_at": now_iso(), "fetched_at_ms": now_ms(), "response": response}
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            write_gzip_json(runtime_dir / "raw" / "instruments" / f"{stamp}.json.gz", cache)
            write_gzip_json(cache_path, cache)
            time.sleep(1.1)
        except Exception as exc:  # network boundary
            instrument_refresh_error = f"{type(exc).__name__}:{exc}"
    instruments = cache.get("response", {}).get("result")
    if not isinstance(instruments, list):
        report = {"generated_at": now_iso(), "decision": "deribit_options_instruments_unavailable", "error": instrument_refresh_error, "can_trade": False}
        write_json(runtime_dir / "LATEST.json", report)
        return 1, report
    try:
        summary_response = fetch_json(endpoint(contract, source["summary_method"], params), timeout)
    except Exception as exc:  # network boundary
        report = {"generated_at": now_iso(), "decision": "deribit_options_summary_fetch_failed", "error": f"{type(exc).__name__}:{exc}", "can_trade": False}
        write_json(runtime_dir / "LATEST.json", report)
        return 1, report
    collected = now_ms()
    raw_record = {"fetched_at": now_iso(), "fetched_at_ms": collected, "response": summary_response}
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    append_gzip_jsonl(runtime_dir / "raw" / "summaries" / f"{day}.jsonl.gz", raw_record)
    surface = derive_surface(instruments, summary_response["result"], collected, contract)
    surface["instrument_cache_age_seconds"] = round((collected - int(cache.get("fetched_at_ms") or 0)) / 1000.0, 3)
    surface["instrument_refresh_error"] = instrument_refresh_error
    surface["source_server_us_in"] = summary_response.get("usIn")
    surface["source_server_us_out"] = summary_response.get("usOut")
    append_jsonl(runtime_dir / "surface_metrics.jsonl", surface)
    decision = "deribit_options_surface_snapshot_healthy" if surface["quality_pass"] and not instrument_refresh_error else "deribit_options_surface_snapshot_degraded"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "surface": surface,
        "lock_verified": True,
        "directional_hypothesis_registered": False,
        "runtime_boundary": {"collector_only": True, "credentials_allowed": False, "signals_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }
    write_json(runtime_dir / "LATEST.json", report)
    return 0, report


def loop(contract_path: Path, lock_path: Path, runtime_dir: Path, sleep_seconds: int) -> int:
    status_path = runtime_dir / "loop_status.json"
    while True:
        write_json(status_path, {"updated_at": now_iso(), "status": "running_once", "pid": os.getpid(), "can_trade": False})
        code, report = run_once(contract_path, lock_path, runtime_dir)
        if code == 2:
            write_json(status_path, {"updated_at": now_iso(), "status": "integrity_blocked", "pid": os.getpid(), "decision": report.get("decision"), "can_trade": False})
            return code
        status = "sleeping" if code == 0 else "sleeping_after_fetch_failure"
        write_json(status_path, {"updated_at": now_iso(), "status": status, "pid": os.getpid(), "sleep_seconds": sleep_seconds, "decision": report.get("decision"), "orders_allowed": False, "can_trade": False})
        time.sleep(sleep_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Public Deribit BTC options surface forward collector")
    parser.add_argument("action", choices=["seal-lock", "run-once", "loop"])
    parser.add_argument("--contract", default=str(HERE / "CONTRACT.json"))
    parser.add_argument("--lock", default=str(HERE / "IMMUTABLE_LOCK_V2.json"))
    parser.add_argument("--runtime-dir", default=str(HERE / "runtime_v2"))
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
        if contract.get("status") != "fixed_collector_contract" or contract.get("can_trade") is not False:
            parser.error("unsafe or invalid collector contract")
        write_json(lock_path, build_lock(Path(__file__).resolve(), contract_path, contract))
        print(json.dumps({"decision": "deribit_options_collector_lock_sealed", "can_trade": False}, indent=2))
        return 0
    if args.action == "run-once":
        code, report = run_once(contract_path, lock_path, runtime_dir)
        surface = report.get("surface") or {}
        print(json.dumps({"decision": report.get("decision"), "quality": surface.get("quality"), "can_trade": False}, indent=2))
        return code
    if args.sleep_seconds < 60:
        parser.error("--sleep-seconds must be at least 60")
    return loop(contract_path, lock_path, runtime_dir, args.sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
