#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

VERSION = "1.0.0"
SCHEMA = "tradingos.delivery.preflight_bridge_receipt.v1"

ROOT = Path(__file__).resolve().parent
_GUARD_PATH = ROOT / "tradingos_delivery_guard.py"
_spec = importlib.util.spec_from_file_location("tradingos_delivery_guard", _GUARD_PATH)
if not _spec or not _spec.loader:
    raise RuntimeError("TradingOS delivery guard unavailable")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def runtime_presence(config: dict[str, Any], alias: str, environ: dict[str, str] | None = None) -> dict[str, Any]:
    c = guard.validate(config)
    env = os.environ if environ is None else environ
    binding = c["destination_bindings"].get(alias)
    destination_env = binding.get("destination_env") if isinstance(binding, dict) else None
    token_env = c["credentials"]["telegram_bot_token_env"]
    secret_env = c["credentials"]["callback_hmac_secret_env"]
    names = {
        "destination": destination_env,
        "bot_token": token_env,
        "callback_hmac_secret": secret_env,
    }
    present = {
        key: bool(name and env.get(name, ""))
        for key, name in names.items()
    }
    return {
        "env_names": names,
        "present": present,
        "all_present": all(present.values()),
        "values_persisted": False,
        "values_hashed_by_bridge": False,
    }


def _assert_no_runtime_values(payload: dict[str, Any], runtime: dict[str, Any], environ: dict[str, str] | None) -> None:
    env = os.environ if environ is None else environ
    text = canonical(payload)
    for name in runtime["env_names"].values():
        if not name:
            continue
        value = env.get(name, "")
        token = json.dumps(value, ensure_ascii=False) if value else ""
        if token and token in text:
            raise ValueError(f"runtime value leaked for env {name}")


def build(
    manifest: dict[str, Any],
    config: dict[str, Any],
    audit_ledger: Path,
    destination_alias: str,
    request_id: str,
    attempted_at: str,
    environ: dict[str, str] | None = None,
    guard_module: Any = None,
) -> dict[str, Any]:
    g = guard if guard_module is None else guard_module
    validated = g.validate(config)
    runtime = runtime_presence(config, destination_alias, environ)
    receipt = g.preflight(
        manifest,
        validated,
        Path(audit_ledger),
        destination_alias,
        request_id,
        attempted_at,
        environ,
    )
    contract = receipt.get("contract", {})
    if contract.get("preflight_only") is not True:
        raise ValueError("guard receipt is not preflight-only")
    if contract.get("network_call") is not False:
        raise ValueError("guard receipt attempted network behavior")
    if contract.get("allow_ready_is_not_delivery") is not True:
        raise ValueError("guard receipt does not preserve ALLOW_READY boundary")
    decision = receipt.get("decision")
    if decision not in {"ALLOW_READY", "DENY"}:
        raise ValueError("unsupported guard preflight decision")
    if decision == "ALLOW_READY":
        rt = receipt.get("runtime", {})
        if validated["mode"] != "ENABLED" or validated["deploy_permission"] != "ALLOW":
            raise ValueError("ALLOW_READY returned from non-enabled/non-allow config")
        if not all(rt.get(k) is True for k in ("destination_bound", "bot_present", "secret_present")):
            raise ValueError("ALLOW_READY returned without complete runtime")
    bridge = {
        "schema": SCHEMA,
        "version": VERSION,
        "status": "ALLOW_READY_NO_SEND" if decision == "ALLOW_READY" else "DENY",
        "guard_decision": decision,
        "guard_reason": receipt.get("reason"),
        "request_id": receipt.get("request_id"),
        "attempted_at": attempted_at,
        "destination_alias": destination_alias,
        "fingerprints": {
            "guard_source_sha256": file_sha256(_GUARD_PATH),
            "config_semantic_sha256": g.sha(validated),
            "manifest_sha256": sha256_text(canonical(manifest)),
            "guard_audit_record_hash": receipt.get("audit_record_hash"),
        },
        "runtime": runtime,
        "guard_runtime": receipt.get("runtime"),
        "checks": {
            "destination_hash_binding_checked_by_guard": validated["mode"] == "ENABLED" and validated["deploy_permission"] == "ALLOW",
            "bot_token_presence_checked_by_guard": validated["mode"] == "ENABLED" and validated["deploy_permission"] == "ALLOW",
            "callback_hmac_secret_presence_and_min_length_checked_by_guard": validated["mode"] == "ENABLED" and validated["deploy_permission"] == "ALLOW",
            "replay_guard_checked": True,
            "rate_limit_checked": True,
            "no_network_auth_exchange": True,
        },
        "contract": {
            "preflight_only": True,
            "network_call": False,
            "allow_ready_is_not_delivery": True,
            "delivery_send_authorized": False,
            "deployment_authorized": False,
            "webhook_registration_authorized": False,
            "raw_destination_persisted": False,
            "runtime_secret_values_persisted": False,
            "runtime_secret_values_hashed_by_bridge": False,
            "separate_send_authorization_required": True,
        },
        "safety": {
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
            "source_deploy_permission": validated["deploy_permission"],
        },
    }
    _assert_no_runtime_values(bridge, runtime, environ)
    return bridge


def render(payload: dict[str, Any]) -> str:
    e = html.escape
    rt = payload["runtime"]
    rows = "".join(
        f"<tr><td>{e(key)}</td><td><code>{e(str(rt['env_names'].get(key)))}</code></td><td>{e(str(rt['present'].get(key)).lower())}</td></tr>"
        for key in ("destination", "bot_token", "callback_hmac_secret")
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>TradingOS Preflight Bridge</title>'
        '<style>body{background:#071019;color:#edf5fa;font:14px system-ui}main{max-width:920px;margin:auto;padding:32px}'
        'article{background:#0d1823;border:1px solid #263746;border-radius:15px;padding:18px;margin:12px 0}'
        'table{width:100%;border-collapse:collapse}td,th{padding:9px;border-bottom:1px solid #263746;text-align:left}'
        'small{color:#8fa5b7}code{color:#d8eef9}</style></head><body><main>'
        f'<small>TRADINGOS · R20 PREFLIGHT EVIDENCE</small><h1>{e(payload["status"])}</h1>'
        f'<article><b>Guard</b><p>{e(str(payload["guard_decision"]))} · {e(str(payload["guard_reason"]))}</p>'
        '<p>preflight only · network=false · send authorized=false · deployment authorized=false</p></article>'
        f'<article><b>Runtime references</b><table><tr><th>ROLE</th><th>ENV NAME</th><th>PRESENT</th></tr>{rows}</table>'
        '<p>Values are neither persisted nor hashed by the bridge.</p></article>'
        '</main></body></html>'
    )


def generate(
    manifest_path: Path,
    config_path: Path,
    audit_ledger: Path,
    destination_alias: str,
    request_id: str,
    attempted_at: str,
    out_dir: Path,
    environ: dict[str, str] | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    payload = build(
        read_json(manifest_path),
        read_json(config_path),
        audit_ledger,
        destination_alias,
        request_id,
        attempted_at,
        environ,
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "preflight_bridge_receipt.json"
    html_path = out / "preflight_bridge_receipt.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    html_path.write_text(render(payload), encoding="utf-8", newline="\n")
    return payload, json_path, html_path


def main() -> int:
    p = argparse.ArgumentParser(description="Bridge TradingOS R8 outbound preflight into redacted no-send evidence")
    p.add_argument("--telegram-manifest", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--audit-ledger", type=Path, required=True)
    p.add_argument("--destination-alias", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument("--attempted-at", required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    a = p.parse_args()
    try:
        payload, jp, hp = generate(
            a.telegram_manifest.resolve(),
            a.config.resolve(),
            a.audit_ledger.resolve(),
            a.destination_alias,
            a.request_id,
            a.attempted_at,
            a.out_dir.resolve(),
        )
    except Exception as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False, "capital_permission": "DENY"}, indent=2))
        return 2
    print(json.dumps({
        "result": "PASS",
        "status": payload["status"],
        "guard_decision": payload["guard_decision"],
        "guard_reason": payload["guard_reason"],
        "delivery_send_authorized": False,
        "network_call": False,
        "json": str(jp),
        "html": str(hp),
        "can_trade": False,
        "capital_permission": "DENY",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
