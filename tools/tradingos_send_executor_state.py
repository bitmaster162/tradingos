#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

V = "1.0.0"
AUTH_SCHEMA = "tradingos.delivery.send_authorization.v1"
PREFLIGHT_SCHEMA = "tradingos.delivery.preflight_bridge_receipt.v1"
LEDGER_SCHEMA = "tradingos.delivery.send_consumption.v1"
RECEIPT_SCHEMA = "tradingos.delivery.send_executor_receipt.v1"
GENESIS = "GENESIS"
MODE = "NO_NETWORK_TEST_MODE"
SCOPE = "ONE_TELEGRAM_SEND_ONLY"


def canonical(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(v: Any) -> str:
    return hashlib.sha256(canonical(v).encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def ts(value: str) -> datetime:
    text = str(value).strip()
    text = text[:-1] + "+00:00" if text.endswith("Z") else text
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def verify_authorization(auth: dict[str, Any], executed_at: str) -> None:
    if auth.get("schema") != AUTH_SCHEMA or auth.get("status") != "AUTHORIZED_ONE_SEND_NO_EXECUTION":
        raise ValueError("valid R21 one-send authorization required")
    if auth.get("scope") != SCOPE:
        raise ValueError("authorization scope mismatch")
    target = auth.get("target")
    contract = auth.get("contract")
    if not isinstance(target, dict) or not isinstance(contract, dict):
        raise ValueError("malformed send authorization")
    if contract.get("send_execution_authorized") is not True:
        raise ValueError("send execution not authorized")
    if contract.get("single_use_required") is not True or contract.get("consumption_ledger_required") is not True:
        raise ValueError("single-use ledger contract missing")
    if contract.get("send_performed") is not False or contract.get("network_call") is not False:
        raise ValueError("authorization already crosses execution boundary")
    if contract.get("deployment_authorized") is not False or contract.get("webhook_registration_authorized") is not False:
        raise ValueError("authorization contains forbidden privilege")
    if contract.get("executor_must_revalidate_fresh_state") is not True:
        raise ValueError("fresh-state revalidation contract missing")
    for key in ("source_receipt_sha256", "manifest_sha256", "config_semantic_sha256", "guard_audit_record_hash"):
        value = target.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"invalid authorization target {key}")
    for key in ("authorization_id", "review_id"):
        value = auth.get(key)
        if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"invalid {key}")
    now = ts(executed_at)
    authorized = ts(str(auth.get("authorized_at")))
    expires = ts(str(auth.get("expires_at")))
    if now < authorized:
        raise ValueError("executed_at precedes authorization")
    if now > expires:
        raise ValueError("send authorization expired")


def verify_fresh_preflight(auth: dict[str, Any], preflight: dict[str, Any], executed_at: str, max_age_seconds: int) -> None:
    if not isinstance(max_age_seconds, int) or not 1 <= max_age_seconds <= 300:
        raise ValueError("invalid fresh preflight max age")
    if preflight.get("schema") != PREFLIGHT_SCHEMA:
        raise ValueError("R20 preflight bridge receipt required")
    contract = preflight.get("contract")
    runtime = preflight.get("runtime")
    guard_runtime = preflight.get("guard_runtime")
    fingerprints = preflight.get("fingerprints")
    safety = preflight.get("safety")
    if not all(isinstance(x, dict) for x in (contract, runtime, guard_runtime, fingerprints, safety)):
        raise ValueError("malformed R20 preflight receipt")
    if preflight.get("status") != "ALLOW_READY_NO_SEND" or preflight.get("guard_decision") != "ALLOW_READY" or preflight.get("guard_reason") != "PREFLIGHT_READY":
        raise ValueError("fresh ALLOW_READY_NO_SEND preflight required")
    if contract.get("preflight_only") is not True or contract.get("network_call") is not False:
        raise ValueError("preflight is not no-network")
    if contract.get("allow_ready_is_not_delivery") is not True or contract.get("delivery_send_authorized") is not False:
        raise ValueError("preflight crosses send boundary")
    if contract.get("deployment_authorized") is not False or contract.get("webhook_registration_authorized") is not False:
        raise ValueError("preflight contains forbidden privilege")
    if safety.get("source_deploy_permission") != "ALLOW":
        raise ValueError("fresh preflight source permission is not ALLOW")
    if runtime.get("all_present") is not True or runtime.get("values_persisted") is not False or runtime.get("values_hashed_by_bridge") is not False:
        raise ValueError("fresh runtime contract incomplete")
    if not all(guard_runtime.get(k) is True for k in ("destination_bound", "bot_present", "secret_present")):
        raise ValueError("fresh guard runtime incomplete")
    target = auth["target"]
    if preflight.get("destination_alias") != target.get("destination_alias"):
        raise ValueError("destination alias changed since authorization")
    if fingerprints.get("manifest_sha256") != target.get("manifest_sha256"):
        raise ValueError("delivery manifest changed since authorization")
    if fingerprints.get("config_semantic_sha256") != target.get("config_semantic_sha256"):
        raise ValueError("security config changed since authorization")
    if preflight.get("request_id") == target.get("source_request_id"):
        raise ValueError("fresh preflight must use a new request_id")
    now = ts(executed_at)
    attempted = ts(str(preflight.get("attempted_at")))
    age = (now - attempted).total_seconds()
    if age < 0:
        raise ValueError("executed_at precedes fresh preflight")
    if age > max_age_seconds:
        raise ValueError("fresh preflight expired")


def ledger_rows(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    previous = GENESIS
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or row.get("schema") != LEDGER_SCHEMA:
            raise ValueError(f"ledger line {line_no}: invalid schema")
        if row.get("sequence") != len(rows) + 1:
            raise ValueError(f"ledger line {line_no}: non-contiguous sequence")
        if row.get("prev_record_hash") != previous:
            raise ValueError(f"ledger line {line_no}: prev_record_hash mismatch")
        claimed = row.get("record_hash")
        body = dict(row)
        body.pop("record_hash", None)
        if not isinstance(claimed, str) or sha(body) != claimed:
            raise ValueError(f"ledger line {line_no}: record_hash mismatch")
        if row.get("execution_mode") != MODE or row.get("send_performed") is not False or row.get("network_call") is not False:
            raise ValueError(f"ledger line {line_no}: unsafe execution record")
        ts(str(row.get("executed_at")))
        rows.append(row)
        previous = claimed
    return rows


def consumed(rows: list[dict[str, Any]], authorization_id: str) -> bool:
    return any(row.get("authorization_id") == authorization_id for row in rows)


def _claim_path(ledger: Path, authorization_id: str) -> Path:
    return Path(str(Path(ledger)) + f".claims/{authorization_id}.claim")


def claim_authorization(ledger: Path, auth: dict[str, Any], executed_at: str) -> Path:
    claim = _claim_path(ledger, auth["authorization_id"])
    claim.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "tradingos.delivery.send_consumption_claim.v1",
        "version": V,
        "authorization_id": auth["authorization_id"],
        "review_id": auth["review_id"],
        "claimed_at": executed_at,
        "execution_mode": MODE,
        "network_call": False,
    }
    try:
        fd = os.open(str(claim), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError("authorization_id already claimed") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # Deliberately keep the claim if durability becomes uncertain: fail closed.
        raise
    return claim


def _ledger_lock_path(ledger: Path) -> Path:
    return Path(str(Path(ledger)) + ".lock")


def acquire_ledger_lock(ledger: Path) -> tuple[int, Path]:
    lock = _ledger_lock_path(ledger)
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError("consumption ledger is locked") from exc
    return fd, lock


def append_consumption(
    ledger: Path,
    auth: dict[str, Any],
    preflight: dict[str, Any],
    executed_at: str,
) -> tuple[dict[str, Any], Path]:
    ledger = Path(ledger)
    rows = ledger_rows(ledger)
    if consumed(rows, auth["authorization_id"]):
        raise ValueError("authorization_id already consumed")
    claim = claim_authorization(ledger, auth, executed_at)
    lock_fd = None
    lock_path = None
    try:
        lock_fd, lock_path = acquire_ledger_lock(ledger)
        os.close(lock_fd); lock_fd = None
        # Re-read after acquiring the global ledger lock.
        rows = ledger_rows(ledger)
        if consumed(rows, auth["authorization_id"]):
            raise ValueError("authorization_id already consumed")
        body = {
        "schema": LEDGER_SCHEMA,
        "version": V,
        "sequence": len(rows) + 1,
        "executed_at": executed_at,
        "prev_record_hash": rows[-1]["record_hash"] if rows else GENESIS,
        "record_type": "ONE_SEND_AUTHORIZATION_CONSUMPTION",
        "authorization_id": auth["authorization_id"],
        "review_id": auth["review_id"],
        "scope": auth["scope"],
        "execution_mode": MODE,
        "fresh_preflight": {
            "receipt_sha256": sha(preflight),
            "request_id": preflight["request_id"],
            "attempted_at": preflight["attempted_at"],
            "destination_alias": preflight["destination_alias"],
            "manifest_sha256": preflight["fingerprints"]["manifest_sha256"],
            "config_semantic_sha256": preflight["fingerprints"]["config_semantic_sha256"],
            "guard_audit_record_hash": preflight["fingerprints"]["guard_audit_record_hash"],
        },
        "state": "AUTHORIZED_CONSUMED_NO_NETWORK",
        "send_performed": False,
        "network_call": False,
        "transport_attempted": False,
        "transport_result": "NOT_EXECUTED_TEST_MODE",
        "contract": {
            "single_use_consumed": True,
            "atomic_claim_acquired": True,
            "ledger_exclusive_lock_used": True,
            "crash_after_claim_fails_closed": True,
            "production_send_consumed": False,
            "no_network_test_mode": True,
            "real_transport_executor_not_present": True,
            "deployment_authorized": False,
            "webhook_registration_authorized": False,
        },
        "safety": {
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }
        row = dict(body)
        row["record_hash"] = sha(body)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return row, claim
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        if lock_path is not None:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def plan(auth: dict[str, Any] | None, preflight: dict[str, Any] | None, executed_at: str, max_age_seconds: int = 60) -> dict[str, Any]:
    if auth is None:
        status = "BLOCKED_AUTHORIZATION_REQUIRED"
        blocker = "R21_AUTHORIZATION_REQUIRED"
    else:
        verify_authorization(auth, executed_at)
        if preflight is None:
            status = "BLOCKED_FRESH_PREFLIGHT_REQUIRED"
            blocker = "FRESH_R20_PREFLIGHT_REQUIRED"
        else:
            verify_fresh_preflight(auth, preflight, executed_at, max_age_seconds)
            status = "READY_NO_NETWORK_TEST_MODE"
            blocker = None
    return {
        "schema": "tradingos.delivery.send_executor_plan.v1",
        "version": V,
        "status": status,
        "blocker": blocker,
        "execution_mode": MODE,
        "executed_at": executed_at,
        "authorization_id": auth.get("authorization_id") if isinstance(auth, dict) else None,
        "fresh_preflight_request_id": preflight.get("request_id") if isinstance(preflight, dict) else None,
        "contract": {
            "real_transport_executor_present": False,
            "send_performed": False,
            "network_call": False,
            "single_use_ledger_required": True,
            "fresh_preflight_required": True,
            "production_send_authorized_by_plan": False,
        },
        "safety": {
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }


def execute_no_network(
    auth: dict[str, Any],
    preflight: dict[str, Any],
    ledger: Path,
    executed_at: str,
    execution_mode: str = MODE,
    max_age_seconds: int = 60,
) -> dict[str, Any]:
    if execution_mode != MODE:
        raise ValueError("only NO_NETWORK_TEST_MODE is implemented")
    verify_authorization(auth, executed_at)
    verify_fresh_preflight(auth, preflight, executed_at, max_age_seconds)
    row, claim = append_consumption(ledger, auth, preflight, executed_at)
    return {
        "schema": RECEIPT_SCHEMA,
        "version": V,
        "status": "AUTHORIZED_CONSUMED_NO_NETWORK",
        "execution_mode": MODE,
        "authorization_id": auth["authorization_id"],
        "review_id": auth["review_id"],
        "executed_at": executed_at,
        "fresh_preflight_request_id": preflight["request_id"],
        "consumption_record_hash": row["record_hash"],
        "atomic_claim_file": claim.name,
        "send_performed": False,
        "network_call": False,
        "transport_attempted": False,
        "contract": {
            "single_use_consumed": True,
            "atomic_claim_acquired": True,
            "ledger_exclusive_lock_used": True,
            "crash_after_claim_fails_closed": True,
            "production_send_consumed": False,
            "real_transport_executor_present": False,
            "separate_real_transport_executor_required": True,
            "deployment_authorized": False,
            "webhook_registration_authorized": False,
        },
        "safety": {
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Consume one R21 send authorization in a no-network single-use state machine")
    p.add_argument("--authorization", type=Path)
    p.add_argument("--fresh-preflight", type=Path)
    p.add_argument("--consumption-ledger", type=Path, required=True)
    p.add_argument("--executed-at", required=True)
    p.add_argument("--execution-mode", default=MODE)
    p.add_argument("--max-fresh-preflight-age-seconds", type=int, default=60)
    p.add_argument("--out", type=Path)
    p.add_argument("--plan-only", action="store_true")
    a = p.parse_args()
    try:
        auth = read_json(a.authorization.resolve()) if a.authorization else None
        preflight = read_json(a.fresh_preflight.resolve()) if a.fresh_preflight else None
        if a.plan_only or auth is None or preflight is None:
            payload = plan(auth, preflight, a.executed_at, a.max_fresh_preflight_age_seconds)
        else:
            payload = execute_no_network(auth, preflight, a.consumption_ledger.resolve(), a.executed_at, a.execution_mode, a.max_fresh_preflight_age_seconds)
        if a.out:
            out = a.out.resolve(); out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except Exception as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "send_performed": False, "network_call": False, "can_trade": False, "capital_permission": "DENY"}, indent=2))
        return 2
    print(json.dumps({"result": "PASS", **payload}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
