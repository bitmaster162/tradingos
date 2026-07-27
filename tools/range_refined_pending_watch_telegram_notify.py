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


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


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


def latest_payload(report: dict[str, Any]) -> dict[str, Any]:
    latest = report.get("latest") if isinstance(report.get("latest"), dict) else {}
    return latest if isinstance(latest, dict) else {}


def notification_key(report: dict[str, Any]) -> str:
    latest = latest_payload(report)
    selected = report.get("selected_candidate") if isinstance(report.get("selected_candidate"), dict) else {}
    bar = latest.get("bar") if isinstance(latest.get("bar"), dict) else {}
    return "|".join(
        [
            str(selected.get("strategy_id") or "unknown_strategy"),
            str(report.get("classification") or "unknown_classification"),
            str(bar.get("bar_ts") or "unknown_bar"),
        ]
    )


def build_card(report: dict[str, Any]) -> dict[str, Any]:
    latest = latest_payload(report)
    selected = report.get("selected_candidate") if isinstance(report.get("selected_candidate"), dict) else {}
    trigger = latest.get("trigger") if isinstance(latest.get("trigger"), dict) else {}
    bar = latest.get("bar") if isinstance(latest.get("bar"), dict) else {}
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_pending_watch_prealert_warning_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "classification": report.get("classification"),
        "next_action": report.get("next_action"),
        "strategy_id": selected.get("strategy_id"),
        "base_strategy_id": selected.get("base_strategy_id"),
        "filter_mode": selected.get("filter_mode"),
        "filters": selected.get("filters"),
        "symbol": "BTCUSDT",
        "interval": selected.get("interval"),
        "side": selected.get("side"),
        "trigger": selected.get("trigger"),
        "rr": selected.get("rr"),
        "bar_ts": bar.get("bar_ts"),
        "close": bar.get("close"),
        "context_ok": latest.get("context_ok"),
        "context_blockers": latest.get("context_blockers"),
        "trigger_ok": latest.get("trigger_ok"),
        "refined_ready": latest.get("refined_ready"),
        "filter_blockers": latest.get("filter_blockers"),
        "trigger_level": trigger.get("trigger_level"),
        "distance_to_trigger": trigger.get("distance_to_trigger"),
        "distance_to_trigger_atr": trigger.get("distance_to_trigger_atr"),
        "distance_to_trigger_pct": trigger.get("distance_to_trigger_pct"),
        "trigger_progress_pct": trigger.get("trigger_progress_pct"),
        "can_trade": False,
        "decision": "range_pending_watch_card_no_trade_permission",
    }


def render_message(card: dict[str, Any], message_prefix: str = "") -> str:
    lines = [
        "Trading OS RANGE PRE-ALERT",
        "",
        f"Status: {card.get('classification')}",
        f"Symbol/TF: {card.get('symbol')} {card.get('interval')}",
        f"Side/trigger: {card.get('side')} {card.get('trigger')}",
        f"Strategy: {card.get('strategy_id')}",
        f"Bar: {card.get('bar_ts')} close {card.get('close')}",
        "",
        f"Trigger level: {card.get('trigger_level')}",
        f"Distance: {card.get('distance_to_trigger')} / {card.get('distance_to_trigger_atr')} ATR / {card.get('distance_to_trigger_pct')}%",
        f"Progress: {card.get('trigger_progress_pct')}%",
        "",
        f"Context ok: {card.get('context_ok')} blockers: {card.get('context_blockers')}",
        f"Trigger ok: {card.get('trigger_ok')}",
        f"Refined ready: {card.get('refined_ready')}",
        f"Filter blockers: {card.get('filter_blockers')}",
        "",
        "Boundary: WARNING ONLY. No entry, no paper intent, no orders.",
    ]
    if message_prefix:
        lines = [message_prefix, ""] + lines
    return "\n".join(lines)


def render_card_md(card: dict[str, Any]) -> str:
    lines = [
        "# Range Pending Watch Pre-Alert Card",
        "",
        f"Generated: `{card.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Warning only.",
        "- Does not create signals or paper-entry intents.",
        "- Does not send exchange orders.",
        "- Does not grant trading permission.",
        "",
        "## State",
        "",
        f"- Classification: `{card.get('classification')}`.",
        f"- Strategy: `{card.get('strategy_id')}`.",
        f"- Symbol / TF: `{card.get('symbol')}` / `{card.get('interval')}`.",
        f"- Side / trigger: `{card.get('side')}` / `{card.get('trigger')}`.",
        f"- Bar: `{card.get('bar_ts')}` close `{card.get('close')}`.",
        f"- Trigger level: `{card.get('trigger_level')}`.",
        f"- Distance: `{card.get('distance_to_trigger')}` / `{card.get('distance_to_trigger_atr')}` ATR / `{card.get('distance_to_trigger_pct')}`%.",
        f"- Progress: `{card.get('trigger_progress_pct')}`%.",
        f"- Context ok: `{card.get('context_ok')}` blockers `{card.get('context_blockers')}`.",
        f"- Trigger ok: `{card.get('trigger_ok')}`.",
        f"- Refined ready: `{card.get('refined_ready')}`.",
        f"- Filter blockers: `{card.get('filter_blockers')}`.",
        "",
        "## Operator Rule",
        "",
        "- Treat this as proximity awareness only. Wait for the observer, scoreboard and gates.",
        "",
    ]
    return "\n".join(lines)


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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Telegram pre-alert for RANGE pending-watch proximity")
    parser.add_argument("--pending-watch-json-path", default="docs/RANGE_REFINED_PENDING_WATCH_2026-06-17.json")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/range_refined_pending_watch_telegram_state.json")
    parser.add_argument("--card-json-path", default="logs/forward_paper_feed/latest_range_pending_watch_card.json")
    parser.add_argument("--card-md-path", default="logs/forward_paper_feed/latest_range_pending_watch_card.md")
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_PENDING_WATCH_TELEGRAM_NOTIFY_2026-06-18")
    parser.add_argument(
        "--notify-classifications",
        default="range_pending_near_trigger,range_pending_trigger_active_filters_blocking,range_pending_refined_trigger_active",
    )
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--message-prefix", default="")
    args = parser.parse_args()

    pending_path = resolve_path(args.pending_watch_json_path)
    state_path = resolve_path(args.state_path)
    card_json_path = resolve_path(args.card_json_path)
    card_md_path = resolve_path(args.card_md_path)
    out_prefix = resolve_path(args.out_prefix)

    pending = read_json(pending_path, {})
    state = read_json(state_path, {})
    if not isinstance(pending, dict):
        pending = {}
    if not isinstance(state, dict):
        state = {}

    card = build_card(pending) if pending else {}
    if card:
        write_json(card_json_path, card)
        card_md_path.parent.mkdir(parents=True, exist_ok=True)
        card_md_path.write_text(render_card_md(card), encoding="utf-8")

    notify_classifications = {item.strip() for item in args.notify_classifications.split(",") if item.strip()}
    classification = str(pending.get("classification") or "")
    key = notification_key(pending) if pending else "missing_pending_watch"
    notified = set(str(item) for item in state.get("notified_keys", []))
    message = render_message(card, args.message_prefix) if card else ""
    telegram_response: dict[str, Any] | None = None

    if not pending:
        decision = "skipped_missing_pending_watch_report"
    elif not card:
        decision = "skipped_missing_card"
    elif classification not in notify_classifications:
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
    state["last_classification"] = classification
    state["last_key"] = key
    write_json(state_path, state)

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_pending_watch_telegram_prealert_warning_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "pending_watch_path": rel_path(pending_path),
        "state_path": rel_path(state_path),
        "card_json_path": rel_path(card_json_path) if card else None,
        "card_md_path": rel_path(card_md_path) if card else None,
        "classification": classification,
        "notification_key": key,
        "decision": decision,
        "notify_classifications": sorted(notify_classifications),
        "telegram_response_ok": telegram_response.get("ok") if isinstance(telegram_response, dict) else None,
        "message_preview": message,
        "can_trade": False,
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Range Pending Watch Telegram Notify",
                "",
                f"Generated: `{report['generated_at']}`",
                "",
                f"- Decision: `{decision}`.",
                f"- Classification: `{classification}`.",
                f"- Notification key: `{key}`.",
                f"- Telegram response ok: `{report['telegram_response_ok']}`.",
                "- Boundary: warning only; no entry, no paper intent, no orders.",
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
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "decision": decision,
                "classification": classification,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if decision not in {"telegram_api_error", "telegram_send_error"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
