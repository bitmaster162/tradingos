#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tradingos_delivery_guard as guard

V = "1.0.0"
PLAN_SCHEMA = "tradingos.delivery.telegram_request_plan.v1"
AUTH_SCHEMA = "tradingos.delivery.send_authorization.v1"
REVIEW_SCHEMA = "tradingos.delivery.send_review.v1"
MANIFEST_SCHEMA = "tradingos.delivery.telegram.v1"
SCOPE = "ONE_TELEGRAM_SEND_ONLY"
H64 = re.compile(r"^[0-9a-f]{64}$")
ID32 = re.compile(r"^[0-9a-f]{32}$")
PLACEHOLDER_PREFIX = "${"


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


def verify_auth(auth: dict[str, Any], compiled_at: str) -> None:
    if auth.get("schema") != AUTH_SCHEMA or auth.get("status") != "AUTHORIZED_ONE_SEND_NO_EXECUTION":
        raise ValueError("valid R21 one-send authorization required")
    if auth.get("scope") != SCOPE:
        raise ValueError("authorization scope mismatch")
    if not ID32.fullmatch(str(auth.get("authorization_id", ""))) or not ID32.fullmatch(str(auth.get("review_id", ""))):
        raise ValueError("invalid authorization identity")
    target = auth.get("target")
    contract = auth.get("contract")
    if not isinstance(target, dict) or not isinstance(contract, dict):
        raise ValueError("malformed send authorization")
    for key in ("source_receipt_sha256", "manifest_sha256", "config_semantic_sha256", "guard_audit_record_hash"):
        if not H64.fullmatch(str(target.get(key, ""))):
            raise ValueError(f"invalid authorization target {key}")
    if contract.get("send_execution_authorized") is not True:
        raise ValueError("send execution not authorized")
    if contract.get("single_use_required") is not True or contract.get("consumption_ledger_required") is not True:
        raise ValueError("single-use contract missing")
    if contract.get("send_performed") is not False or contract.get("network_call") is not False:
        raise ValueError("authorization already crosses execution boundary")
    if contract.get("deployment_authorized") is not False or contract.get("webhook_registration_authorized") is not False:
        raise ValueError("authorization contains forbidden privilege")
    compiled = ts(compiled_at)
    authorized = ts(str(auth.get("authorized_at")))
    expires = ts(str(auth.get("expires_at")))
    if compiled < authorized:
        raise ValueError("compiled_at precedes authorization")
    if compiled > expires:
        raise ValueError("send authorization expired")


def _validate_keyboard(markup: Any) -> dict[str, Any]:
    if not isinstance(markup, dict) or set(markup) != {"inline_keyboard"}:
        raise ValueError("only InlineKeyboardMarkup is supported")
    keyboard = markup["inline_keyboard"]
    if not isinstance(keyboard, list) or not keyboard:
        raise ValueError("inline keyboard must be non-empty")
    clean: list[list[dict[str, str]]] = []
    for row in keyboard:
        if not isinstance(row, list) or not row:
            raise ValueError("invalid inline keyboard row")
        out_row = []
        for button in row:
            if not isinstance(button, dict) or set(button) != {"text", "callback_data"}:
                raise ValueError("only text+callback_data buttons are supported")
            text = button.get("text")
            data = button.get("callback_data")
            if not isinstance(text, str) or not text:
                raise ValueError("invalid inline button text")
            if not isinstance(data, str) or not 1 <= len(data.encode("utf-8")) <= 64:
                raise ValueError("invalid callback_data")
            out_row.append({"text": text, "callback_data": data})
        clean.append(out_row)
    return {"inline_keyboard": clean}


def normalize_manifest(manifest: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("mode") != "DRY_RUN":
        raise ValueError("full TradingOS Telegram DRY_RUN manifest required")
    if manifest.get("method") != "sendMessage":
        raise ValueError("Telegram sendMessage manifest required")
    contract = manifest.get("contract")
    request = manifest.get("request")
    if not isinstance(contract, dict) or contract.get("network_call") is not False:
        raise ValueError("manifest must be no-network")
    if not isinstance(request, dict):
        raise ValueError("BLOCKED_MESSAGE_PAYLOAD_REQUIRED")
    allowed = {"text", "reply_markup", "disable_web_page_preview", "link_preview_options"}
    unknown = set(request) - allowed
    if unknown:
        raise ValueError(f"unsupported Telegram request fields: {','.join(sorted(unknown))}")
    text = request.get("text")
    if not isinstance(text, str) or not 1 <= len(text) <= 4096:
        raise ValueError("Telegram text must be 1..4096 characters")
    body: dict[str, Any] = {"text": text}
    if "reply_markup" in request:
        body["reply_markup"] = _validate_keyboard(request["reply_markup"])
    legacy = request.get("disable_web_page_preview")
    modern = request.get("link_preview_options")
    if legacy is not None and modern is not None:
        raise ValueError("legacy and modern link-preview fields cannot coexist")
    normalization = {"legacy_disable_web_page_preview_seen": legacy is not None, "normalized_to_link_preview_options": False}
    if legacy is not None:
        if not isinstance(legacy, bool):
            raise ValueError("disable_web_page_preview must be boolean")
        if legacy:
            body["link_preview_options"] = {"is_disabled": True}
            normalization["normalized_to_link_preview_options"] = True
    elif modern is not None:
        if not isinstance(modern, dict) or set(modern) != {"is_disabled"} or not isinstance(modern.get("is_disabled"), bool):
            raise ValueError("only link_preview_options.is_disabled is supported")
        body["link_preview_options"] = {"is_disabled": modern["is_disabled"]}
    return body, normalization


def compile_request(auth: dict[str, Any], manifest: dict[str, Any], config: dict[str, Any], compiled_at: str) -> dict[str, Any]:
    verify_auth(auth, compiled_at)
    validated = guard.validate(config)
    target = auth["target"]
    if sha(manifest) != target.get("manifest_sha256"):
        raise ValueError("Telegram manifest changed since authorization")
    if guard.sha(validated) != target.get("config_semantic_sha256"):
        raise ValueError("security config changed since authorization")
    if validated.get("mode") != "ENABLED" or validated.get("deploy_permission") != "ALLOW":
        raise ValueError("compiler requires ENABLED/ALLOW config")
    alias = target.get("destination_alias")
    binding = validated["destination_bindings"].get(alias)
    if not isinstance(binding, dict) or binding.get("transport") != "telegram":
        raise ValueError("authorized destination binding missing")
    body, normalization = normalize_manifest(manifest)
    token_env = validated["credentials"]["telegram_bot_token_env"]
    destination_env = binding["destination_env"]
    wire_body = {"chat_id": f"${{{destination_env}}}", **body}
    path_template = f"/bot${{{token_env}}}/sendMessage"
    core = {
        "authorization_id": auth["authorization_id"],
        "review_id": auth["review_id"],
        "destination_alias": alias,
        "manifest_sha256": target["manifest_sha256"],
        "config_semantic_sha256": target["config_semantic_sha256"],
        "wire_body_template_sha256": sha(wire_body),
        "compiled_at": compiled_at,
    }
    plan = {
        "schema": PLAN_SCHEMA,
        "version": V,
        "status": "REQUEST_TEMPLATE_READY_NO_NETWORK",
        "plan_id": sha(core)[:32],
        "compiled_at": compiled_at,
        "authorization": {
            "authorization_id": auth["authorization_id"],
            "review_id": auth["review_id"],
            "scope": auth["scope"],
            "consumed_by_compiler": False,
        },
        "destination": {
            "alias": alias,
            "chat_id_source": "ENV",
            "chat_id_env": destination_env,
            "destination_sha256": binding["destination_sha256"],
            "raw_chat_id_persisted": False,
        },
        "credential_reference": {
            "bot_token_source": "ENV",
            "bot_token_env": token_env,
            "bot_token_value_persisted": False,
        },
        "http_template": {
            "method": "POST",
            "scheme": "https",
            "host": "api.telegram.org",
            "path_template": path_template,
            "content_type": "application/json",
            "telegram_method": "sendMessage",
            "body_template": wire_body,
        },
        "normalization": normalization,
        "fingerprints": {
            "authorization_sha256": sha(auth),
            "manifest_sha256": sha(manifest),
            "config_semantic_sha256": guard.sha(validated),
            "wire_body_template_sha256": sha(wire_body),
        },
        "contract": {
            "compiler_reads_runtime_env_values": False,
            "runtime_materialization_required": True,
            "authorization_consumed": False,
            "send_performed": False,
            "network_call": False,
            "transport_attempted": False,
            "http_client_present": False,
            "deployment_authorized": False,
            "webhook_registration_authorized": False,
            "separate_transport_executor_required": True,
        },
        "safety": {
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }
    text = canonical(plan)
    if PLACEHOLDER_PREFIX not in text:
        raise ValueError("runtime placeholders missing")
    return plan


def blocked_from_review(review: dict[str, Any], compiled_at: str) -> dict[str, Any]:
    if review.get("schema") != REVIEW_SCHEMA:
        raise ValueError("R21 send review required")
    return {
        "schema": PLAN_SCHEMA,
        "version": V,
        "status": "BLOCKED_AUTHORIZATION_REQUIRED",
        "compiled_at": compiled_at,
        "review_id": review.get("review_id"),
        "source_review_status": review.get("status"),
        "blocker": "AUTHORIZED_ONE_SEND_NO_EXECUTION_REQUIRED",
        "http_template": None,
        "contract": {
            "send_performed": False,
            "network_call": False,
            "transport_attempted": False,
            "authorization_consumed": False,
            "separate_transport_executor_required": True,
        },
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def write(payload: dict[str, Any], out_dir: Path) -> Path:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    path = out / "telegram_request_plan.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def main() -> int:
    p = argparse.ArgumentParser(description="Compile a redacted Telegram Bot API sendMessage request template without HTTP/network execution")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--send-authorization", type=Path)
    g.add_argument("--send-review", type=Path)
    p.add_argument("--telegram-manifest", type=Path)
    p.add_argument("--config", type=Path)
    p.add_argument("--compiled-at", required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    a = p.parse_args()
    try:
        if a.send_review:
            payload = blocked_from_review(read_json(a.send_review.resolve()), a.compiled_at)
        else:
            if not a.telegram_manifest or not a.config:
                raise ValueError("telegram-manifest and config are required with send-authorization")
            payload = compile_request(read_json(a.send_authorization.resolve()), read_json(a.telegram_manifest.resolve()), read_json(a.config.resolve()), a.compiled_at)
        path = write(payload, a.out_dir.resolve())
    except Exception as exc:
        print(json.dumps({"result":"ERROR","error":str(exc),"send_performed":False,"network_call":False,"can_trade":False}, indent=2)); return 2
    print(json.dumps({"result":"PASS","status":payload["status"],"output":str(path),"send_performed":False,"network_call":False,"can_trade":False}, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
