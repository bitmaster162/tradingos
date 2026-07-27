#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


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


def redact(value: str | None) -> str | None:
    if not value:
        return None
    return f"<redacted:{len(value)} chars>"


def send_telegram(token: str, chat_id: str, text: str, timeout_s: int) -> dict[str, Any]:
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = Request(endpoint, data=body, method="POST")
    with urlopen(request, timeout=timeout_s) as response:
        payload = response.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        parsed = {"ok": False, "raw": payload}
    if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict):
        result = dict(parsed["result"])
        if isinstance(result.get("chat"), dict):
            chat = dict(result["chat"])
            if "username" in chat:
                chat["username"] = "<redacted>"
            if "first_name" in chat:
                chat["first_name"] = "<redacted>"
            if "last_name" in chat:
                chat["last_name"] = "<redacted>"
            result["chat"] = chat
        if "text" in result:
            result["text"] = "<redacted>"
        parsed["result"] = result
    return parsed if isinstance(parsed, dict) else {"ok": False, "raw_type": type(parsed).__name__}


def render_message(label: str) -> str:
    return "\n".join(
        [
            "Trading OS TELEGRAM TRANSPORT SMOKE",
            "",
            f"Label: {label}",
            f"Generated: {now_iso()}",
            "",
            "Boundary: transport test only.",
            "No trading signal. No orders. No private exchange credentials.",
        ]
    )


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Telegram Transport Smoke",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Telegram transport check only.",
            "- No trading signal.",
            "- No exchange credentials.",
            "- No orders.",
            "",
            "## Result",
            "",
            f"- Decision: `{report.get('decision')}`.",
            f"- Send requested: `{report.get('send_requested')}`.",
            f"- Token present: `{report.get('token_present')}`.",
            f"- Chat ID present: `{report.get('chat_id_present')}`.",
            f"- Telegram response ok: `{report.get('telegram_response_ok')}`.",
            f"- Can trade: `{report.get('can_trade')}`.",
            "",
            "## Message Preview",
            "",
            "```text",
            str(report.get("message_preview") or ""),
            "```",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe Telegram transport smoke test")
    parser.add_argument("--out-prefix", default="docs/TELEGRAM_TRANSPORT_SMOKE_2026-06-16")
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--label", default="manual_transport_smoke")
    parser.add_argument("--send", action="store_true", help="Actually send one benign Telegram smoke message.")
    args = parser.parse_args()

    env_files = [resolve_path(item) for item in args.env_file]
    token, token_source = env_value(args.token_env, env_files)
    chat_id, chat_id_source = env_value(args.chat_id_env, env_files)
    message = render_message(args.label)
    telegram_response: dict[str, Any] | None = None

    if not token or not chat_id:
        decision = "skipped_missing_telegram_env"
    elif not args.send:
        decision = "dry_run_ready"
    else:
        try:
            telegram_response = send_telegram(token, chat_id, message, args.timeout_s)
            decision = "sent" if telegram_response.get("ok") else "telegram_api_error"
        except Exception as exc:  # noqa: BLE001
            telegram_response = {"ok": False, "error": str(exc)}
            decision = "telegram_send_error"

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "telegram_transport_smoke_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_exchange_credentials": False,
        },
        "decision": decision,
        "send_requested": bool(args.send),
        "token_present": bool(token),
        "chat_id_present": bool(chat_id),
        "token_source": token_source,
        "chat_id_source": chat_id_source,
        "token_redacted": redact(token),
        "chat_id_redacted": redact(chat_id),
        "telegram_response_ok": telegram_response.get("ok") if isinstance(telegram_response, dict) else None,
        "telegram_response_redacted": telegram_response,
        "message_preview": message,
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
                "decision": decision,
                "send_requested": bool(args.send),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if decision not in {"telegram_api_error", "telegram_send_error"} else 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
