#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

V = "1.0.0"
CFG = "tradingos.delivery.security_config.v1"
AUD = "tradingos.delivery.security_audit.v1"
GEN = "GENESIS"
ENV = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
RID = re.compile(r"^[A-Za-z0-9._:-]{12,128}$")
H64 = re.compile(r"^[0-9a-f]{64}$")


def canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sha(value) -> str:
    return sha_text(canon(value))


def ts(value: str) -> datetime:
    text = value.strip()
    text = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def env_name(value: str) -> str:
    if not isinstance(value, str) or not ENV.fullmatch(value):
        raise ValueError("invalid environment-variable name")
    return value


def validate(config):
    if config.get("schema") != CFG or config.get("version") != 1:
        raise ValueError("unsupported security config")
    mode = config.get("mode")
    permission = config.get("deploy_permission")
    adapter_id = config.get("adapter_id")
    credentials = config.get("credentials")
    limits = config.get("rate_limits")
    bindings = config.get("destination_bindings")
    if mode not in {"DISABLED", "ENABLED"} or permission not in {"DENY", "ALLOW"}:
        raise ValueError("invalid security config")
    if not isinstance(adapter_id, str) or not adapter_id:
        raise ValueError("invalid security config")
    if not all(isinstance(item, dict) for item in (credentials, limits, bindings)):
        raise ValueError("invalid security config objects")
    if {"bot_token", "telegram_bot_token", "secret", "callback_hmac_secret", "password", "api_key"} & set(credentials):
        raise ValueError("inline credentials forbidden")

    token_env = env_name(credentials.get("telegram_bot_token_env"))
    secret_env = env_name(credentials.get("callback_hmac_secret_env"))
    delivery_limit = limits.get("delivery_attempts_per_minute")
    callback_limit = limits.get("callbacks_per_minute")
    callback_age = config.get("callback_max_age_seconds")
    if not isinstance(delivery_limit, int) or not 1 <= delivery_limit <= 60:
        raise ValueError("invalid limits")
    if not isinstance(callback_limit, int) or not 1 <= callback_limit <= 120:
        raise ValueError("invalid limits")
    if not isinstance(callback_age, int) or not 30 <= callback_age <= 3600:
        raise ValueError("invalid limits")

    normalized = {}
    for alias, binding in bindings.items():
        digest = binding.get("destination_sha256") if isinstance(binding, dict) else None
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", alias):
            raise ValueError("invalid destination binding")
        if not isinstance(binding, dict) or binding.get("transport") != "telegram":
            raise ValueError("invalid destination binding")
        destination_env = env_name(binding.get("destination_env"))
        digest = digest or ""
        if mode == "ENABLED" and not H64.fullmatch(digest):
            raise ValueError("enabled destination requires sha256 binding")
        if digest and not H64.fullmatch(digest):
            raise ValueError("invalid destination hash")
        normalized[alias] = {
            "transport": "telegram",
            "destination_env": destination_env,
            "destination_sha256": digest,
        }
    if mode == "ENABLED" and not normalized:
        raise ValueError("enabled mode requires destination")

    return {
        "schema": CFG,
        "version": 1,
        "mode": mode,
        "deploy_permission": permission,
        "adapter_id": adapter_id,
        "credentials": {
            "telegram_bot_token_env": token_env,
            "callback_hmac_secret_env": secret_env,
        },
        "destination_bindings": normalized,
        "rate_limits": {
            "delivery_attempts_per_minute": delivery_limit,
            "callbacks_per_minute": callback_limit,
        },
        "callback_max_age_seconds": callback_age,
    }


def load(path: Path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("config must be object")
    return validate(value)


def runtime(config, alias: str, environ=None, need_bot: bool = True):
    config = validate(config)
    env = os.environ if environ is None else environ
    binding = config["destination_bindings"].get(alias)
    if not binding:
        raise ValueError("destination not allowlisted")
    destination = env.get(binding["destination_env"], "")
    digest = sha_text(destination) if destination else ""
    if not destination or not hmac.compare_digest(digest, binding["destination_sha256"]):
        raise ValueError("destination binding mismatch")
    bot = env.get(config["credentials"]["telegram_bot_token_env"], "")
    secret = env.get(config["credentials"]["callback_hmac_secret_env"], "")
    if need_bot and not bot:
        raise ValueError("bot token missing")
    if len(secret.encode()) < 32:
        raise ValueError("HMAC secret too short")
    return {
        "destination_hash": digest,
        "bot_present": bool(bot),
        "secret_present": True,
    }


def audit_rows(path: Path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    previous = GEN
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        body = dict(row)
        got = body.pop("record_hash", None)
        if (
            row.get("schema") != AUD
            or row.get("sequence") != len(rows) + 1
            or row.get("prev_record_hash") != previous
            or not isinstance(got, str)
            or not hmac.compare_digest(sha(body), got)
        ):
            raise ValueError(f"audit line {number} invalid")
        ts(str(row.get("occurred_at")))
        request_id = row.get("request_id")
        if not isinstance(request_id, str) or not RID.fullmatch(request_id):
            raise ValueError("invalid audit request id")
        if row.get("direction") != "OUTBOUND":
            raise ValueError("outbound guard rejects non-outbound audit rows")
        rows.append(row)
        previous = got
    return rows


def write_audit(path, at, request_id, direction, decision, reason, adapter_id, alias, destination_hash=None, meta=None):
    ts(at)
    if not RID.fullmatch(request_id):
        raise ValueError("invalid audit record")
    if direction != "OUTBOUND" or decision not in {"ALLOW_READY", "DENY"}:
        raise ValueError("invalid outbound audit record")
    rows = audit_rows(path)
    body = {
        "schema": AUD,
        "version": V,
        "sequence": len(rows) + 1,
        "occurred_at": at,
        "prev_record_hash": rows[-1]["record_hash"] if rows else GEN,
        "request_id": request_id,
        "direction": "OUTBOUND",
        "decision": decision,
        "reason": reason,
        "adapter_id": adapter_id,
        "destination_alias": alias,
        "destination_hash_prefix": destination_hash[:16] if destination_hash else None,
        "metadata": meta or {},
        "contract": {
            "append_only": True,
            "raw_destination_persisted": False,
            "secrets_persisted": False,
            "network_call": False,
        },
    }
    row = {**body, "record_hash": sha(body)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canon(row) + "\n")
    return row


def seen(rows, request_id: str) -> bool:
    return any(row.get("request_id") == request_id for row in rows)


def recent(rows, now: datetime) -> int:
    return sum(
        1
        for row in rows
        if row.get("decision") == "ALLOW_READY"
        and 0 <= (now - ts(row["occurred_at"])).total_seconds() < 60
    )


def preflight(manifest, config, audit, alias, request_id, at, environ=None):
    config = validate(config)
    rows = audit_rows(audit)
    now = ts(at)
    decision = "DENY"
    reason = "CONFIG_DISABLED"
    rt = None
    if seen(rows, request_id):
        reason = "REPLAY_REQUEST_ID"
    elif config["mode"] != "ENABLED":
        pass
    elif config["deploy_permission"] != "ALLOW":
        reason = "DEPLOY_PERMISSION_DENY"
    else:
        try:
            rt = runtime(config, alias, environ, True)
        except ValueError as exc:
            reason = "RUNTIME_NOT_READY:" + str(exc)
        else:
            if recent(rows, now) >= config["rate_limits"]["delivery_attempts_per_minute"]:
                reason = "RATE_LIMIT"
            else:
                decision = "ALLOW_READY"
                reason = "PREFLIGHT_READY"

    contract = manifest.get("contract", {})
    if (
        manifest.get("schema") != "tradingos.delivery.telegram.v1"
        or manifest.get("mode") != "DRY_RUN"
        or contract.get("network_call") is not False
    ):
        raise ValueError("unsafe delivery manifest")

    audit_row = write_audit(
        audit,
        at,
        request_id,
        "OUTBOUND",
        decision,
        reason,
        config["adapter_id"],
        alias,
        rt.get("destination_hash") if rt else None,
        {"network_call": False},
    )
    return {
        "schema": "tradingos.delivery.preflight_receipt.v1",
        "version": V,
        "decision": decision,
        "reason": reason,
        "request_id": request_id,
        "runtime": {
            "destination_bound": bool(rt),
            "bot_present": bool(rt and rt["bot_present"]),
            "secret_present": bool(rt and rt["secret_present"]),
        },
        "audit_record_hash": audit_row["record_hash"],
        "contract": {
            "preflight_only": True,
            "network_call": False,
            "allow_ready_is_not_delivery": True,
        },
        "safety": {
            "can_trade": False,
            "capital_permission": "DENY",
            "deploy_permission": config["deploy_permission"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    command = sub.add_parser("preflight")
    for name in (
        "telegram_manifest",
        "config",
        "audit_ledger",
        "destination_alias",
        "request_id",
        "attempted_at",
    ):
        command.add_argument("--" + name.replace("_", "-"), required=True)
    args = parser.parse_args()
    try:
        result = preflight(
            json.loads(Path(args.telegram_manifest).read_text()),
            load(Path(args.config)),
            Path(args.audit_ledger),
            args.destination_alias,
            args.request_id,
            args.attempted_at,
        )
    except Exception as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"result": "PASS", **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
