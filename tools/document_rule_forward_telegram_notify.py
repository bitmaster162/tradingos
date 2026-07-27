#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


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


def render_message(card: dict[str, Any]) -> str:
    obs = card.get("observation") if isinstance(card.get("observation"), dict) else {}
    conditions = card.get("conditions") if isinstance(card.get("conditions"), dict) else {}
    return "\n".join(
        [
            "TradingOS WATCH SIGNAL",
            "",
            f"Hypothesis: {card.get('hypothesis_id')}",
            f"Symbol/TF: {card.get('symbol')} {card.get('interval')}",
            f"Side: {card.get('side')}",
            f"Signal bar: {card.get('signal_bar_ts')}",
            "",
            f"Policy: {card.get('planned_entry_policy')}",
            f"Reference entry: {card.get('reference_entry')}",
            f"Reference stop: {card.get('reference_stop')}",
            f"Reference take: {card.get('reference_take')}",
            f"RR: 1:{card.get('take_atr')}",
            "",
            f"Volume regime: {obs.get('volume_regime')} z={obs.get('volume_z')}",
            f"Spot/perp div pct: {obs.get('spot_perp_divergence_pct')}",
            f"OI delta pct: {obs.get('oi_delta_pct')}",
            f"Funding: {obs.get('funding')}",
            f"ATR: {obs.get('atr')}",
            "",
            f"Conditions: {conditions}",
            "",
            "Boundary: WATCH ONLY. No orders were sent. can_trade=false.",
        ]
    )


def send_telegram(token: str, chat_id: str, text: str, timeout_s: int) -> dict[str, Any]:
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    request = Request(endpoint, data=body, method="POST")
    with urlopen(request, timeout=timeout_s) as response:
        payload = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"ok": False, "raw": payload}


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram notifier for document-rule watch-only forward observer")
    parser.add_argument("--card-path", default="logs/document_rule_forward_observer/latest_signal_card.json")
    parser.add_argument("--state-path", default="logs/document_rule_forward_observer/telegram_notify_state.json")
    parser.add_argument("--out-prefix", default="docs/DOCUMENT_RULE_FORWARD_TELEGRAM_NOTIFY_2026-06-30")
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    card_path = resolve_path(args.card_path)
    state_path = resolve_path(args.state_path)
    out_prefix = resolve_path(args.out_prefix)
    card = read_json(card_path, {})
    state = read_json(state_path, {})
    if not isinstance(card, dict):
        card = {}
    if not isinstance(state, dict):
        state = {}

    status = str(card.get("status") or "")
    key = str(card.get("signal_key") or "")
    notified = set(str(item) for item in state.get("notified_keys", []))
    message = render_message(card) if card else ""
    telegram_response: dict[str, Any] | None = None
    decision = "skipped_missing_card"
    if not card:
        decision = "skipped_missing_card"
    elif status != "watch_signal":
        decision = "skipped_status_not_notifiable"
    elif not key:
        decision = "skipped_missing_signal_key"
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

    if decision in {"sent", "dry_run_ready"} and key:
        notified.add(key)
    state.update(
        {
            "updated_at": now_iso(),
            "last_decision": decision,
            "last_key": key,
            "notified_keys": sorted(notified)[-500:],
        }
    )
    write_json(state_path, state)

    report = {
        "generated_at": now_iso(),
        "tool": "tools/document_rule_forward_telegram_notify.py",
        "runtime_boundary": {
            "classification": "telegram_watch_notify_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "card_path": portable(card_path),
        "state_path": portable(state_path),
        "card_status": status,
        "signal_key": key,
        "decision": decision,
        "telegram_response_ok": telegram_response.get("ok") if isinstance(telegram_response, dict) else None,
        "message_preview": message,
        "can_trade": False,
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    write_json(json_path, report)
    md_path.write_text(
        "\n".join(
            [
                "# Document Rule Forward Telegram Notify",
                "",
                f"Generated: `{report['generated_at']}`",
                "",
                f"- Decision: `{decision}`",
                f"- Card status: `{status}`",
                f"- Signal key: `{key}`",
                f"- Telegram response ok: `{report['telegram_response_ok']}`",
                "- Boundary: watch notification only; no orders.",
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
    print(
        json.dumps(
            {
                "decision": decision,
                "card_status": status,
                "signal_key": key,
                "telegram_response_ok": report["telegram_response_ok"],
                "json": portable(json_path),
                "md": portable(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
