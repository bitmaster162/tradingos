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


def observer_latest(observer: dict[str, Any]) -> dict[str, Any]:
    latest = observer.get("latest_result") if isinstance(observer.get("latest_result"), dict) else {}
    return latest if isinstance(latest, dict) else {}


def notification_key(card: dict[str, Any]) -> str:
    signal = card.get("latest_signal") if isinstance(card.get("latest_signal"), dict) else {}
    return str(
        signal.get("signal_key")
        or "|".join(
            [
                str(card.get("strategy_id") or "unknown_strategy"),
                str(card.get("status") or "unknown_status"),
                str(card.get("latest_closed_bar_ts") or "unknown_bar"),
            ]
        )
    )


def render_card_md(card: dict[str, Any]) -> str:
    signal = card.get("latest_signal") if isinstance(card.get("latest_signal"), dict) else {}
    snapshot = signal.get("feature_snapshot") if isinstance(signal.get("feature_snapshot"), dict) else {}
    lines = [
        "# Range Refined Signal Card",
        "",
        f"Generated: `{card.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Observer-only RANGE signal card.",
        "- Does not create a paper-entry intent.",
        "- Does not send exchange orders.",
        "- Does not grant live trading permission.",
        "",
        "## State",
        "",
        f"- Status: `{card.get('status')}`.",
        f"- Strategy: `{card.get('strategy_id')}`.",
        f"- Filter: `{card.get('filter_mode')}`.",
        f"- Symbol / TF: `{card.get('symbol')}` / `{card.get('interval')}`.",
        f"- Side: `{card.get('side')}`.",
        f"- Latest closed bar: `{card.get('latest_closed_bar_ts')}` close `{card.get('latest_closed_close')}`.",
        f"- Raw/refined signals: `{card.get('raw_signals_on_latest_bar')}` / `{card.get('refined_signals_on_latest_bar')}`.",
        f"- Data degraded: `{card.get('data_degraded')}`.",
        f"- Missing filter inputs: `{card.get('missing_filter_inputs')}`.",
        "",
    ]
    if card.get("status") == "range_refined_signal_observed":
        lines.extend(
            [
                "## Observed Signal",
                "",
                f"- Signal key: `{signal.get('signal_key')}`.",
                f"- ATR: `{signal.get('atr')}`.",
                f"- RR: `{signal.get('rr')}`.",
                f"- Max hold bars: `{signal.get('max_hold_bars')}`.",
                f"- Filter checks: `{signal.get('filter_checks')}`.",
                f"- Range high / low: `{snapshot.get('range_high')}` / `{snapshot.get('range_low')}`.",
                f"- Funding: `{snapshot.get('funding')}`.",
                f"- OI delta pct: `{snapshot.get('oi_delta_pct')}`.",
                f"- Spot/perp divergence pct: `{snapshot.get('spot_perp_divergence_pct')}`.",
                "",
            ]
        )
    lines.extend(["## Operator Rule", "", "- Treat this as an observation only. Do not manually execute it without a separate execution review.", ""])
    return "\n".join(lines)


def render_message(card: dict[str, Any]) -> str:
    signal = card.get("latest_signal") if isinstance(card.get("latest_signal"), dict) else {}
    snapshot = signal.get("feature_snapshot") if isinstance(signal.get("feature_snapshot"), dict) else {}
    if card.get("status") != "range_refined_signal_observed":
        return "\n".join(
            [
                "Trading OS RANGE OBSERVER STATE",
                "",
                f"Status: {card.get('status')}",
                f"Symbol/TF: {card.get('symbol')} {card.get('interval')}",
                f"Side: {card.get('side')}",
                f"Strategy: {card.get('strategy_id')}",
                f"Filter: {card.get('filter_mode')}",
                f"Bar: {card.get('latest_closed_bar_ts')} close {card.get('latest_closed_close')}",
                f"Raw/refined signals: {card.get('raw_signals_on_latest_bar')}/{card.get('refined_signals_on_latest_bar')}",
                "",
                "Boundary: OBSERVER ONLY. This state is not notifiable by default and no orders were sent.",
            ]
        )
    lines = [
        "Trading OS RANGE OBSERVER SIGNAL",
        "",
        f"Status: {card.get('status')}",
        f"Symbol/TF: {card.get('symbol')} {card.get('interval')}",
        f"Side: {card.get('side')}",
        f"Strategy: {card.get('strategy_id')}",
        f"Filter: {card.get('filter_mode')}",
        f"Bar: {card.get('latest_closed_bar_ts')} close {card.get('latest_closed_close')}",
        "",
        f"ATR: {signal.get('atr')}",
        f"RR: {signal.get('rr')}",
        f"Max hold bars: {signal.get('max_hold_bars')}",
        f"Filter checks: {signal.get('filter_checks')}",
        "",
        f"Funding: {snapshot.get('funding')}",
        f"OI delta pct: {snapshot.get('oi_delta_pct')}",
        f"Spot/perp div pct: {snapshot.get('spot_perp_divergence_pct')}",
        "",
        "Boundary: OBSERVER ONLY. No paper entry and no orders.",
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


def build_card(observer: dict[str, Any]) -> dict[str, Any]:
    latest = observer_latest(observer)
    signal = latest.get("latest_signal") if isinstance(latest.get("latest_signal"), dict) else None
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_signal_card_observer_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "status": latest.get("status"),
        "strategy_id": latest.get("strategy_id"),
        "base_strategy_id": latest.get("base_strategy_id"),
        "filter_mode": latest.get("filter_mode"),
        "filters": latest.get("filters"),
        "symbol": latest.get("symbol"),
        "interval": latest.get("interval"),
        "side": latest.get("side"),
        "latest_closed_bar_ts": latest.get("latest_closed_bar_ts"),
        "latest_closed_close": latest.get("latest_closed_close"),
        "raw_signals_on_latest_bar": latest.get("raw_signals_on_latest_bar"),
        "refined_signals_on_latest_bar": latest.get("refined_signals_on_latest_bar"),
        "data_degraded": latest.get("data_degraded"),
        "missing_filter_inputs": latest.get("missing_filter_inputs"),
        "latest_signal": signal,
        "can_trade": False,
        "decision": "range_observer_card_no_trade_permission",
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Alert guard for selected refined RANGE observer signal")
    parser.add_argument("--observer-json-path", default="docs/RANGE_REFINED_FORWARD_OBSERVER_2026-06-16.json")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/range_refined_signal_alert_guard_state.json")
    parser.add_argument("--card-json-path", default="logs/forward_paper_feed/latest_range_refined_signal_card.json")
    parser.add_argument("--card-md-path", default="logs/forward_paper_feed/latest_range_refined_signal_card.md")
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_SIGNAL_ALERT_GUARD_2026-06-16")
    parser.add_argument("--notify-statuses", default="range_refined_signal_observed")
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    observer_path = resolve_path(args.observer_json_path)
    state_path = resolve_path(args.state_path)
    card_json_path = resolve_path(args.card_json_path)
    card_md_path = resolve_path(args.card_md_path)
    out_prefix = resolve_path(args.out_prefix)

    observer = read_json(observer_path, {})
    state = read_json(state_path, {})
    if not isinstance(observer, dict):
        observer = {}
    if not isinstance(state, dict):
        state = {}

    card = build_card(observer) if observer else {}
    if card:
        write_json(card_json_path, card)
        card_md_path.parent.mkdir(parents=True, exist_ok=True)
        card_md_path.write_text(render_card_md(card), encoding="utf-8")

    notify_statuses = {item.strip() for item in args.notify_statuses.split(",") if item.strip()}
    status = str(card.get("status") or "")
    key = notification_key(card) if card else "missing_card"
    notified = set(str(item) for item in state.get("notified_keys", []))
    message = render_message(card) if card else ""
    telegram_response: dict[str, Any] | None = None

    if not observer:
        decision = "skipped_missing_observer_report"
    elif not card:
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
    state["last_status"] = status
    state["last_key"] = key
    write_json(state_path, state)

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_signal_alert_guard_observer_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "observer_path": rel_path(observer_path),
        "state_path": rel_path(state_path),
        "card_json_path": rel_path(card_json_path) if card else None,
        "card_md_path": rel_path(card_md_path) if card else None,
        "status": status,
        "notification_key": key,
        "decision": decision,
        "notify_statuses": sorted(notify_statuses),
        "telegram_response_ok": telegram_response.get("ok") if isinstance(telegram_response, dict) else None,
        "message_preview": message,
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Range Refined Signal Alert Guard",
                "",
                f"Generated: `{report['generated_at']}`",
                "",
                f"- Decision: `{decision}`.",
                f"- Card status: `{status}`.",
                f"- Notification key: `{key}`.",
                f"- Telegram response ok: `{report['telegram_response_ok']}`.",
                f"- Latest card: `{report['card_md_path']}`.",
                "- Boundary: observer signal notification only; no paper entry and no orders.",
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
                "status": "ok",
                "decision": decision,
                "card_status": status,
                "card_json": report["card_json_path"],
                "card_md": report["card_md_path"],
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if decision not in {"telegram_api_error", "telegram_send_error"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
