#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def env_value(name: str, env_files: list[Path]) -> tuple[str | None, str | None]:
    value = os.environ.get(name)
    if value:
        return value, "process_env"
    for path in env_files:
        loaded = load_env_file(path)
        if loaded.get(name):
            return loaded[name], str(path)
    return None, None


def redact_present(value: str | None) -> str | None:
    if not value:
        return None
    return f"<present:{len(value)} chars>"


def get_me(token: str, timeout_s: int) -> dict[str, Any]:
    with urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=timeout_s) as response:
        payload = response.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {"ok": False, "raw_type": "non_json"}
    if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict):
        result = dict(parsed["result"])
        if "first_name" in result:
            result["first_name"] = "<redacted>"
        parsed["result"] = result
    return parsed if isinstance(parsed, dict) else {"ok": False, "raw_type": type(parsed).__name__}


def render_markdown(report: dict[str, Any]) -> str:
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    return "\n".join(
        [
            "# Telegram Config Audit",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Audits Telegram notification config only.",
            "- Does not send Telegram messages.",
            "- Does not print token or chat id.",
            "- No trading signal, no orders, no exchange credentials.",
            "",
            "## Result",
            "",
            f"- Decision: `{report.get('decision')}`.",
            f"- Token present: `{checks.get('token_present')}`.",
            f"- Chat ID present: `{checks.get('chat_id_present')}`.",
            f"- Bot API ok: `{checks.get('bot_api_ok')}`.",
            f"- Bot username: `{checks.get('bot_username')}`.",
            f"- Expected username match: `{checks.get('expected_username_match')}`.",
            f"- Secret file excluded from manifest: `{checks.get('secret_file_excluded_from_manifest')}`.",
            f"- Transport smoke decision: `{checks.get('transport_smoke_decision')}`.",
            f"- Can trade: `{report.get('can_trade')}`.",
            "",
            "## Notes",
            "",
            "- If the token was ever pasted into chat, rotate it in `@BotFather` after setup is verified.",
            "- Keep the real token only in `configs/telegram.env` or process environment.",
            "",
        ]
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Audit local Telegram notification config without printing secrets")
    parser.add_argument("--out-prefix", default="docs/TELEGRAM_CONFIG_AUDIT_2026-06-18")
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--expected-username", default="bitevocodexbot")
    parser.add_argument("--transport-report", default="docs/TELEGRAM_TRANSPORT_SMOKE_2026-06-16.json")
    parser.add_argument("--manifest", default="MANIFEST.json")
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--skip-network-check", action="store_true")
    args = parser.parse_args()

    env_files = [resolve_path(item) for item in args.env_file]
    token, token_source = env_value(args.token_env, env_files)
    chat_id, chat_id_source = env_value(args.chat_id_env, env_files)
    manifest = read_json(resolve_path(args.manifest), {})
    transport = read_json(resolve_path(args.transport_report), {})
    manifest_files = manifest.get("files") if isinstance(manifest, dict) and isinstance(manifest.get("files"), list) else []
    manifest_paths = {str(item.get("path")) for item in manifest_files if isinstance(item, dict)}
    secret_paths_present = (
        set(str(item) for item in manifest.get("excluded_secret_paths_present", []))
        if isinstance(manifest, dict) and isinstance(manifest.get("excluded_secret_paths_present"), list)
        else set()
    )
    secret_file_excluded = "configs/telegram.env" not in manifest_paths
    secret_file_present_but_excluded = "configs/telegram.env" in secret_paths_present or not (ROOT / "configs" / "telegram.env").exists()

    bot_api: dict[str, Any] | None = None
    bot_api_error: str | None = None
    if token and not args.skip_network_check:
        try:
            bot_api = get_me(token, args.timeout_s)
        except Exception as exc:  # noqa: BLE001
            bot_api = {"ok": False}
            bot_api_error = type(exc).__name__

    bot_username = (
        bot_api.get("result", {}).get("username")
        if isinstance(bot_api, dict) and isinstance(bot_api.get("result"), dict)
        else None
    )
    checks = {
        "token_present": bool(token),
        "chat_id_present": bool(chat_id),
        "token_source": token_source,
        "chat_id_source": chat_id_source,
        "token_redacted": redact_present(token),
        "chat_id_redacted": redact_present(chat_id),
        "bot_api_ok": bot_api.get("ok") if isinstance(bot_api, dict) else None,
        "bot_api_error": bot_api_error,
        "bot_username": bot_username,
        "expected_username": args.expected_username,
        "expected_username_match": bot_username == args.expected_username if bot_username else None,
        "secret_file_excluded_from_manifest": bool(secret_file_excluded and secret_file_present_but_excluded),
        "transport_smoke_decision": transport.get("decision") if isinstance(transport, dict) else None,
        "transport_smoke_send_requested": transport.get("send_requested") if isinstance(transport, dict) else None,
        "transport_smoke_response_ok": transport.get("telegram_response_ok") if isinstance(transport, dict) else None,
    }
    passed = (
        checks["token_present"] is True
        and checks["chat_id_present"] is True
        and checks["bot_api_ok"] is True
        and checks["expected_username_match"] is True
        and checks["secret_file_excluded_from_manifest"] is True
    )
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "telegram_config_audit_no_send",
            "can_trade": False,
            "sends_orders": False,
            "sends_telegram": False,
            "uses_exchange_credentials": False,
        },
        "checks": checks,
        "decision": "telegram_config_ready_secret_excluded" if passed else "telegram_config_attention_required",
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "decision": report["decision"],
                "bot_api_ok": checks["bot_api_ok"],
                "bot_username": checks["bot_username"],
                "secret_file_excluded_from_manifest": checks["secret_file_excluded_from_manifest"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
