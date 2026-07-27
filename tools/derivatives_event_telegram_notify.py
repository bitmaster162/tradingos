#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
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
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
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


def latest_observation(observer: dict[str, Any]) -> dict[str, Any]:
    latest = observer.get("latest_observation")
    return latest if isinstance(latest, dict) else {}


def notification_key(observer: dict[str, Any]) -> str:
    latest = latest_observation(observer)
    selected = observer.get("selected_config") if isinstance(observer.get("selected_config"), dict) else {}
    return "|".join(
        [
            str(selected.get("strategy_id") or "unknown_strategy"),
            str(latest.get("bar_ts") or "unknown_bar"),
            str(latest.get("status") or "unknown_status"),
        ]
    )


def build_card(observer: dict[str, Any], scoreboard: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    latest = latest_observation(observer)
    selected = observer.get("selected_config") if isinstance(observer.get("selected_config"), dict) else {}
    summary = scoreboard.get("summary") if isinstance(scoreboard.get("summary"), dict) else {}
    promotion = gate.get("promotion") if isinstance(gate.get("promotion"), dict) else {}
    return {
        "generated_at": now_iso(),
        "alert_type": "derivatives_event_watch",
        "runtime_boundary": {
            "classification": "derivatives_event_watch_alert_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "observer_id": observer.get("observer_id"),
        "strategy_id": selected.get("strategy_id"),
        "family": selected.get("family"),
        "symbol": "BTCUSDT",
        "interval": selected.get("interval"),
        "side": selected.get("side"),
        "regime_filter": selected.get("regime_filter"),
        "bar_ts": latest.get("bar_ts"),
        "close": latest.get("close"),
        "price_move_atr": latest.get("price_move_atr"),
        "oi_delta_pct": latest.get("oi_delta_pct"),
        "funding": latest.get("funding"),
        "volume_z": latest.get("volume_z"),
        "close_location": latest.get("close_location"),
        "atr": latest.get("atr"),
        "stop_atr": selected.get("stop_atr"),
        "take_atr": selected.get("take_atr"),
        "max_hold_bars": selected.get("max_hold_bars"),
        "observer_status": latest.get("status"),
        "observer_events_written": latest.get("events_written"),
        "scoreboard_classification": summary.get("classification"),
        "scoreboard_signals": summary.get("observer_signal_events"),
        "scoreboard_resolved": summary.get("resolved"),
        "scoreboard_expectancy_r": summary.get("expectancy_r"),
        "gate_decision": gate.get("decision"),
        "paper_design_review_allowed": promotion.get("paper_design_review_allowed"),
        "paper_execution_allowed": promotion.get("paper_execution_allowed"),
        "live_execution_allowed": promotion.get("live_execution_allowed"),
        "operator_rule": "WATCH ONLY. No entry, no paper intent, no orders. Wait for forward scoreboard and promotion gate.",
        "decision": "derivatives_event_watch_card_no_trade_permission",
        "can_trade": False,
    }


def render_message(card: dict[str, Any], prefix: str = "") -> str:
    lines = [
        "Trading OS DERIVATIVES-EVENT WATCH",
        "",
        f"Symbol/TF: {card.get('symbol')} {card.get('interval')}",
        f"Side: {card.get('side')}",
        f"Strategy: {card.get('strategy_id')}",
        f"Family/regime: {card.get('family')} / {card.get('regime_filter')}",
        f"Bar: {card.get('bar_ts')} close {card.get('close')}",
        "",
        f"Price move ATR: {card.get('price_move_atr')}",
        f"OI delta %: {card.get('oi_delta_pct')}",
        f"Funding: {card.get('funding')}",
        f"Volume z: {card.get('volume_z')}",
        f"Close location: {card.get('close_location')}",
        "",
        f"RR: 1:{card.get('take_atr')} max hold bars: {card.get('max_hold_bars')}",
        f"Forward scoreboard: {card.get('scoreboard_classification')} resolved {card.get('scoreboard_resolved')} expR {card.get('scoreboard_expectancy_r')}",
        f"Gate: {card.get('gate_decision')}",
        "",
        "Boundary: WATCH ONLY. No entry, no paper intent, no orders.",
    ]
    if prefix:
        lines = [prefix, ""] + lines
    return "\n".join(lines)


def render_card_md(card: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Derivatives Event Watch Card",
            "",
            f"- Generated: `{card.get('generated_at')}`",
            f"- Strategy: `{card.get('strategy_id')}`",
            f"- Symbol / TF / side: `{card.get('symbol')}` / `{card.get('interval')}` / `{card.get('side')}`",
            f"- Bar: `{card.get('bar_ts')}` close `{card.get('close')}`",
            f"- Price move ATR: `{card.get('price_move_atr')}`",
            f"- OI delta pct: `{card.get('oi_delta_pct')}`",
            f"- Funding: `{card.get('funding')}`",
            f"- Scoreboard: `{card.get('scoreboard_classification')}` resolved `{card.get('scoreboard_resolved')}` expectancy `{card.get('scoreboard_expectancy_r')}`R",
            f"- Gate: `{card.get('gate_decision')}`",
            f"- Can trade: `{card.get('can_trade')}`",
            "",
            "## Operator Rule",
            "",
            f"- {card.get('operator_rule')}",
            "",
        ]
    )


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
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {"ok": False, "error_type": "HTTPError", "http_status": exc.code}
        return {
            "ok": False,
            "error_type": "HTTPError",
            "http_status": exc.code,
            "description": str(parsed.get("description"))[:300] if isinstance(parsed, dict) and parsed.get("description") else None,
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
    parser = argparse.ArgumentParser(description="Telegram watch alert for derivatives-event observer signals")
    parser.add_argument("--observer-json-path", default="docs/DERIVATIVES_EVENT_FORWARD_OBSERVER_2026-06-26.json")
    parser.add_argument("--scoreboard-json-path", default="docs/DERIVATIVES_EVENT_FORWARD_SCOREBOARD_2026-06-26.json")
    parser.add_argument("--gate-json-path", default="docs/DERIVATIVES_EVENT_PROMOTION_GATE_2026-06-26.json")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/derivatives_event_telegram_notify_state.json")
    parser.add_argument("--card-json-path", default="logs/forward_paper_feed/latest_derivatives_event_watch_card.json")
    parser.add_argument("--card-md-path", default="logs/forward_paper_feed/latest_derivatives_event_watch_card.md")
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_EVENT_TELEGRAM_NOTIFY_2026-06-26")
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--message-prefix", default="DERIVATIVES EVENT WATCH - observer-only. No entry, no paper intent, no orders.")
    args = parser.parse_args()

    observer_path = resolve_path(args.observer_json_path)
    scoreboard_path = resolve_path(args.scoreboard_json_path)
    gate_path = resolve_path(args.gate_json_path)
    state_path = resolve_path(args.state_path)
    card_json_path = resolve_path(args.card_json_path)
    card_md_path = resolve_path(args.card_md_path)
    out_prefix = resolve_path(args.out_prefix)

    observer = read_json(observer_path, {})
    scoreboard = read_json(scoreboard_path, {})
    gate = read_json(gate_path, {})
    state = read_json(state_path, {})
    if not isinstance(observer, dict):
        observer = {}
    if not isinstance(scoreboard, dict):
        scoreboard = {}
    if not isinstance(gate, dict):
        gate = {}
    if not isinstance(state, dict):
        state = {}

    latest = latest_observation(observer)
    signal = latest.get("signal") is True
    events_written = int(latest.get("events_written") or 0)
    key = notification_key(observer) if observer else "missing_observer"
    notified = set(str(item) for item in state.get("notified_keys", []))
    card = build_card(observer, scoreboard, gate) if observer else {}
    message = render_message(card, args.message_prefix) if card else ""
    telegram_response: dict[str, Any] | None = None

    if card:
        write_json(card_json_path, card)
        card_md_path.parent.mkdir(parents=True, exist_ok=True)
        card_md_path.write_text(render_card_md(card), encoding="utf-8")

    if not observer:
        decision = "skipped_missing_observer_report"
    elif not signal or events_written <= 0:
        decision = "skipped_no_new_signal"
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
                telegram_response = {"ok": False, "error": str(exc)[:300]}
                decision = "telegram_send_error"

    if decision in {"sent", "dry_run_ready"}:
        notified.add(key)
    state.update(
        {
            "notified_keys": sorted(notified)[-500:],
            "last_checked_at": now_iso(),
            "last_decision": decision,
            "last_key": key,
            "last_signal": signal,
            "last_events_written": events_written,
        }
    )
    write_json(state_path, state)

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "derivatives_event_telegram_watch_alert_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "observer_path": rel_path(observer_path),
        "scoreboard_path": rel_path(scoreboard_path),
        "gate_path": rel_path(gate_path),
        "state_path": rel_path(state_path),
        "card_json_path": rel_path(card_json_path) if card else None,
        "card_md_path": rel_path(card_md_path) if card else None,
        "notification_key": key,
        "signal": signal,
        "events_written": events_written,
        "decision": decision,
        "telegram_response_ok": telegram_response.get("ok") if isinstance(telegram_response, dict) else None,
        "telegram_error_type": telegram_response.get("error_type") if isinstance(telegram_response, dict) else None,
        "telegram_http_status": telegram_response.get("http_status") if isinstance(telegram_response, dict) else None,
        "message_preview": message,
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Derivatives Event Telegram Notify",
                "",
                f"Generated: `{report['generated_at']}`",
                "",
                f"- Decision: `{decision}`.",
                f"- Signal: `{signal}`.",
                f"- Events written: `{events_written}`.",
                f"- Notification key: `{key}`.",
                f"- Telegram response ok: `{report['telegram_response_ok']}`.",
                "- Boundary: watch alert only; no orders.",
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
    print(json.dumps({"decision": decision, "signal": signal, "events_written": events_written, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if decision not in {"telegram_api_error", "telegram_send_error"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
