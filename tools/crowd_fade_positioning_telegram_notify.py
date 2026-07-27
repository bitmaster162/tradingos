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
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OBSERVER = ROOT / "docs" / "CROWD_FADE_POSITIONING_SHADOW_OBSERVER_2026-06-19.json"
DEFAULT_SCOREBOARD = ROOT / "docs" / "CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19.json"
DEFAULT_STATE = ROOT / "logs" / "forward_paper_feed" / "crowd_fade_positioning_telegram_notify_state.json"
DEFAULT_CARD_JSON = ROOT / "logs" / "forward_paper_feed" / "latest_crowd_fade_watch_card.json"
DEFAULT_CARD_MD = ROOT / "logs" / "forward_paper_feed" / "latest_crowd_fade_watch_card.md"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CROWD_FADE_POSITIONING_TELEGRAM_NOTIFY_2026-06-19"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seconds_since(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def resolve_path(value: str | Path) -> Path:
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


def latest_payload(observer: dict[str, Any]) -> dict[str, Any]:
    latest = observer.get("latest") if isinstance(observer.get("latest"), dict) else {}
    return latest if isinstance(latest, dict) else {}


def notification_key(observer: dict[str, Any]) -> str:
    latest = latest_payload(observer)
    return "|".join(
        [
            str(observer.get("strategy_id") or "unknown_strategy"),
            str(latest.get("signal_time") or "unknown_signal_time"),
            str(latest.get("side_hint") or "unknown_side"),
            str(latest.get("ratio_z") or "unknown_ratio_z"),
        ]
    )


def build_card(observer: dict[str, Any], scoreboard: dict[str, Any]) -> dict[str, Any]:
    latest = latest_payload(observer)
    summary = scoreboard.get("summary") if isinstance(scoreboard.get("summary"), dict) else {}
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "crowd_fade_watch_alert_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_exchange_credentials": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "alert_type": "crowd_fade_watch",
        "symbol": "BTCUSDT",
        "strategy_id": observer.get("strategy_id"),
        "candidate_classification": observer.get("candidate_classification"),
        "observer_status": latest.get("status"),
        "signal_found": latest.get("signal_found"),
        "signal_time": latest.get("signal_time"),
        "side_hint": latest.get("side_hint"),
        "ratio": latest.get("ratio"),
        "ratio_z": latest.get("ratio_z"),
        "funding": latest.get("funding"),
        "oi_delta": latest.get("oi_delta"),
        "scoreboard_classification": summary.get("classification"),
        "scoreboard_signal_events": summary.get("observer_signal_events"),
        "scoreboard_resolved": summary.get("resolved"),
        "scoreboard_expectancy_r": summary.get("expectancy_r"),
        "operator_rule": "Watch alert only. Do not enter from this alert; wait for full pre-trade review and forward evidence.",
        "can_trade": False,
        "decision": "crowd_fade_watch_card_no_trade_permission",
    }


def render_message(card: dict[str, Any], prefix: str) -> str:
    lines = [
        "Trading OS CROWD-FADE WATCH",
        "",
        f"Symbol: {card.get('symbol')}",
        f"Status: {card.get('observer_status')}",
        f"Strategy: {card.get('strategy_id')}",
        f"Signal time: {card.get('signal_time')}",
        f"Side: {card.get('side_hint')}",
        "",
        f"Long/short ratio: {card.get('ratio')}",
        f"Ratio z-score: {card.get('ratio_z')}",
        f"Funding: {card.get('funding')}",
        f"OI delta: {card.get('oi_delta')}",
        "",
        f"Forward scoreboard: {card.get('scoreboard_classification')}",
        f"Resolved outcomes: {card.get('scoreboard_resolved')}",
        f"Forward expectancy R: {card.get('scoreboard_expectancy_r')}",
        "",
        "Boundary: WATCH ONLY. No entry, no paper intent, no orders.",
    ]
    if prefix:
        lines = [prefix, ""] + lines
    return "\n".join(lines)


def render_card_md(card: dict[str, Any]) -> str:
    lines = [
        "# Crowd-Fade Watch Alert Card",
        "",
        f"- Generated: `{card.get('generated_at')}`",
        f"- Alert type: `{card.get('alert_type')}`",
        f"- Can trade: `{card.get('can_trade')}`",
        "",
        "## Signal",
        "",
        f"- Strategy: `{card.get('strategy_id')}`",
        f"- Status: `{card.get('observer_status')}`",
        f"- Signal time: `{card.get('signal_time')}`",
        f"- Side: `{card.get('side_hint')}`",
        f"- Ratio: `{card.get('ratio')}`",
        f"- Ratio z-score: `{card.get('ratio_z')}`",
        f"- Funding: `{card.get('funding')}`",
        f"- OI delta: `{card.get('oi_delta')}`",
        "",
        "## Forward Evidence",
        "",
        f"- Scoreboard: `{card.get('scoreboard_classification')}`",
        f"- Signal events: `{card.get('scoreboard_signal_events')}`",
        f"- Resolved: `{card.get('scoreboard_resolved')}`",
        f"- Expectancy R: `{card.get('scoreboard_expectancy_r')}`",
        "",
        "## Operator Rule",
        "",
        f"- {card.get('operator_rule')}",
        "",
    ]
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str, timeout_s: int) -> dict[str, Any]:
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    request = Request(endpoint, data=body, method="POST")
    try:
        with urlopen(request, timeout=timeout_s) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(payload)
        except json.JSONDecodeError:
            return {"ok": False, "error_type": "HTTPError", "http_status": exc.code}
        description = parsed_error.get("description") if isinstance(parsed_error, dict) else None
        return {
            "ok": False,
            "error_type": "HTTPError",
            "http_status": exc.code,
            "description": str(description)[:300] if description else None,
        }
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {"ok": False, "raw_type": "non_json"}
    if not isinstance(parsed, dict):
        return {"ok": False, "raw_type": type(parsed).__name__}
    return {
        "ok": bool(parsed.get("ok")),
        "description": str(parsed.get("description"))[:300] if parsed.get("description") else None,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Telegram watch alert for crowd-fade observer signals.")
    parser.add_argument("--observer-json-path", default=str(DEFAULT_OBSERVER))
    parser.add_argument("--scoreboard-json-path", default=str(DEFAULT_SCOREBOARD))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE))
    parser.add_argument("--card-json-path", default=str(DEFAULT_CARD_JSON))
    parser.add_argument("--card-md-path", default=str(DEFAULT_CARD_MD))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--transport-retry-seconds", type=int, default=21600)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--message-prefix", default="")
    args = parser.parse_args()

    observer_path = resolve_path(args.observer_json_path)
    scoreboard_path = resolve_path(args.scoreboard_json_path)
    state_path = resolve_path(args.state_path)
    card_json_path = resolve_path(args.card_json_path)
    card_md_path = resolve_path(args.card_md_path)
    out_prefix = resolve_path(args.out_prefix)

    observer = read_json(observer_path, {})
    scoreboard = read_json(scoreboard_path, {})
    state = read_json(state_path, {})
    if not isinstance(observer, dict):
        observer = {}
    if not isinstance(scoreboard, dict):
        scoreboard = {}
    if not isinstance(state, dict):
        state = {}

    latest = latest_payload(observer)
    card = build_card(observer, scoreboard) if observer else {}
    if card:
        write_json(card_json_path, card)
        card_md_path.parent.mkdir(parents=True, exist_ok=True)
        card_md_path.write_text(render_card_md(card), encoding="utf-8")

    key = notification_key(observer) if observer else "missing_observer"
    notified = set(str(item) for item in state.get("notified_keys", []))
    message = render_message(card, args.message_prefix) if card else ""
    telegram_response: dict[str, Any] | None = None
    transport_failure_age = seconds_since(state.get("last_failed_at"))
    suppressed_keys = {
        str(item)
        for item in scoreboard.get("suppressed_signal_keys", [])
        if str(item)
    }
    latest_signal_key = str(latest.get("signal_key") or "")

    if not observer:
        decision = "skipped_missing_observer_report"
    elif not latest.get("signal_found"):
        decision = "skipped_no_observer_signal"
    elif latest_signal_key and latest_signal_key in suppressed_keys:
        decision = "skipped_overlap_suppressed"
    elif key in notified and not args.force:
        decision = "skipped_duplicate"
    elif (
        state.get("last_failed_key") == key
        and not args.force
        and transport_failure_age is not None
        and transport_failure_age < args.transport_retry_seconds
    ):
        decision = "skipped_transport_retry_cooldown"
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
                telegram_response = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                }
                decision = "telegram_send_error"

    if decision in {"sent", "dry_run_ready"}:
        notified.add(key)
        state.pop("last_failed_key", None)
        state.pop("last_failed_at", None)
    elif decision in {"telegram_api_error", "telegram_send_error"}:
        state["last_failed_key"] = key
        state["last_failed_at"] = now_iso()
    state["notified_keys"] = sorted(notified)[-500:]
    state["last_checked_at"] = now_iso()
    state["last_decision"] = decision
    state["last_key"] = key
    write_json(state_path, state)

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "telegram_crowd_fade_watch_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_exchange_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "observer_path": str(observer_path),
        "scoreboard_path": str(scoreboard_path),
        "state_path": str(state_path),
        "notification_key": key,
        "decision": decision,
        "observer_status": latest.get("status"),
        "signal_found": latest.get("signal_found"),
        "signal_time": latest.get("signal_time"),
        "telegram_response_ok": telegram_response.get("ok") if isinstance(telegram_response, dict) else None,
        "telegram_error": (
            {
                key: telegram_response.get(key)
                for key in ("error_type", "http_status", "description", "error")
                if telegram_response.get(key) is not None
            }
            if isinstance(telegram_response, dict) and not telegram_response.get("ok")
            else None
        ),
        "message_preview": message,
        "can_trade": False,
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Crowd-Fade Telegram Notify",
                "",
                f"- Generated: `{report['generated_at']}`",
                f"- Decision: `{decision}`",
                f"- Observer status: `{report['observer_status']}`",
                f"- Signal found: `{report['signal_found']}`",
                f"- Telegram response ok: `{report['telegram_response_ok']}`",
                f"- Can trade: `{report['can_trade']}`",
                "",
                "## Boundary",
                "",
                "- Watch alert only.",
                "- No entry, no paper intent, no orders.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"decision": decision, "signal_found": latest.get("signal_found"), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if decision not in {"telegram_api_error", "telegram_send_error"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
