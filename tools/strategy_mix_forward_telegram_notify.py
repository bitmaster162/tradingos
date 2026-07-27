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


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def env_value(name: str, env_files: list[Path]) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    for path in env_files:
        loaded = load_env_file(path)
        if loaded.get(name):
            return loaded[name]
    return None


def notification_key(card: dict[str, Any]) -> str:
    latest_event = card.get("latest_event") if isinstance(card.get("latest_event"), dict) else {}
    return str(
        latest_event.get("signal_key")
        or "|".join(
            [
                str(card.get("strategy_id") or "unknown_strategy"),
                str(card.get("status") or "unknown_status"),
                str(card.get("latest_closed_bar_ts") or "unknown_bar"),
            ]
        )
    )


def render_message(card: dict[str, Any]) -> str:
    latest_event = card.get("latest_event") if isinstance(card.get("latest_event"), dict) else {}
    conditions = ", ".join(str(item) for item in card.get("conditions") or [])
    lines = [
        "Trading OS PAPER SIGNAL",
        "",
        f"Status: {card.get('status')}",
        f"Symbol/TF: {card.get('symbol')} {card.get('interval')}",
        f"Side: {latest_event.get('side') or card.get('side')}",
        f"Strategy: {card.get('strategy_id')}",
        f"Signal bar: {latest_event.get('signal_bar_ts') or card.get('latest_closed_bar_ts')}",
        f"Entry bar: {latest_event.get('entry_bar_ts') or 'n/a'}",
        "",
        f"Entry: {latest_event.get('entry')}",
        f"Stop: {latest_event.get('stop')}",
        f"Take: {latest_event.get('take')}",
        f"ATR: {latest_event.get('atr')}",
        f"Max hold bars: {latest_event.get('max_hold_bars')}",
        "",
        f"Conditions: {conditions}",
        "",
        "Boundary: PAPER ONLY. No orders were sent.",
    ]
    return "\n".join(lines)


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
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"ok": False, "raw": payload}


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram notifier for Strategy Mix forward paper cards")
    parser.add_argument("--card-json-path", default="logs/forward_paper_feed/latest_signal_card.json")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/telegram_notify_state.json")
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_FORWARD_TELEGRAM_NOTIFY_2026-06-08")
    parser.add_argument("--notify-statuses", default="paper_entry_intent")
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    card_path = resolve_path(args.card_json_path)
    state_path = resolve_path(args.state_path)
    out_prefix = resolve_path(args.out_prefix)
    card = read_json(card_path, {})
    state = read_json(state_path, {})
    if not isinstance(card, dict):
        card = {}
    if not isinstance(state, dict):
        state = {}

    notify_statuses = {item.strip() for item in args.notify_statuses.split(",") if item.strip()}
    status = str(card.get("status") or "")
    key = notification_key(card)
    notified = set(str(item) for item in state.get("notified_keys", []))
    decision = "skipped_status_not_notifiable"
    telegram_response: dict[str, Any] | None = None
    message = render_message(card)

    if not card:
        decision = "skipped_missing_card"
    elif status not in notify_statuses:
        decision = "skipped_status_not_notifiable"
    elif key in notified and not args.force:
        decision = "skipped_duplicate"
    else:
        env_files = [resolve_path(item) for item in args.env_file]
        token = env_value(args.token_env, env_files)
        chat_id = env_value(args.chat_id_env, env_files)
        if not token or not chat_id:
            decision = "skipped_missing_telegram_env"
        elif args.dry_run:
            decision = "dry_run_ready"
        else:
            try:
                telegram_response = send_telegram(token, chat_id, message, args.timeout_s)
                decision = "sent" if telegram_response.get("ok") else "telegram_api_error"
            except Exception as exc:  # noqa: BLE001
                telegram_response = {"ok": False, "error": str(exc)}
                decision = "telegram_send_error"

    if decision in {"sent", "dry_run_ready"}:
        notified.add(key)
    state["notified_keys"] = sorted(notified)[-500:]
    state["last_checked_at"] = now_iso()
    state["last_decision"] = decision
    state["last_key"] = key
    write_json(state_path, state)

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "telegram_notify_paper_signal_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "card_path": str(card_path),
        "state_path": str(state_path),
        "status": status,
        "notification_key": key,
        "decision": decision,
        "notify_statuses": sorted(notify_statuses),
        "telegram_response_ok": telegram_response.get("ok") if isinstance(telegram_response, dict) else None,
        "message_preview": message,
        "can_trade": False,
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Strategy Mix Forward Telegram Notify",
                "",
                f"Generated: `{report['generated_at']}`",
                "",
                f"- Decision: `{decision}`.",
                f"- Card status: `{status}`.",
                f"- Notification key: `{key}`.",
                f"- Telegram response ok: `{report['telegram_response_ok']}`.",
                "- Boundary: paper signal notification only; no orders.",
                "",
                "## Message Preview",
                "",
                "```text",
                message,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"json": str(out_prefix.with_suffix(".json")), "md": str(out_prefix.with_suffix(".md")), "decision": decision, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if decision not in {"telegram_api_error", "telegram_send_error"} else 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
