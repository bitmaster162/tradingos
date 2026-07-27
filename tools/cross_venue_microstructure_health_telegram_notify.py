#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cross_venue_microstructure_health import DEGRADED, now_iso, read_json, resolve_path  # noqa: E402
from tools.forward_runtime_health_telegram_notify import env_value, send_telegram  # noqa: E402


def notification_kind(health: dict[str, Any], state: dict[str, Any]) -> str:
    classification = str(health.get("classification") or "missing")
    previous = str(state.get("last_classification") or "")
    if classification == DEGRADED:
        return "microstructure_degraded"
    if previous == DEGRADED:
        return "microstructure_recovered"
    return "healthy_no_notification"


def notification_key(kind: str, health: dict[str, Any]) -> str:
    failed = health.get("failed_hard_gates") if isinstance(health.get("failed_hard_gates"), list) else []
    return "|".join([kind, str(health.get("classification") or "missing"), ",".join(sorted(str(item) for item in failed)) or "none"])


def render_message(kind: str, health: dict[str, Any]) -> str:
    observed = health.get("observed") if isinstance(health.get("observed"), dict) else {}
    failed = health.get("failed_hard_gates") if isinstance(health.get("failed_hard_gates"), list) else []
    return "\n".join(
        [
            "Trading OS MICROSTRUCTURE HEALTH",
            "",
            f"Kind: {kind}",
            f"Classification: {health.get('classification')}",
            f"Failed gates: {', '.join(str(item) for item in failed) if failed else 'none'}",
            f"Loop: {observed.get('loop_status')} pid {observed.get('loop_pid')} alive {observed.get('loop_pid_alive')}",
            f"Ages report/loop/run: {observed.get('report_age_minutes')}/{observed.get('loop_age_minutes')}/{observed.get('last_refresh_age_minutes')}m",
            f"Missing IDs B/C: {observed.get('binance_missing_ids')}/{observed.get('coinbase_missing_ids')}",
            f"Manifest verified: {observed.get('manifest_verified')}",
            "",
            "Boundary: DATA HEALTH ONLY. No signals or orders.",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicated Telegram notifier for microstructure health")
    parser.add_argument("--health", default="docs/CROSS_VENUE_MICROSTRUCTURE_HEALTH_2026-06-24.json")
    parser.add_argument("--state", default="logs/cross_venue_microstructure/health_telegram_state.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_HEALTH_TELEGRAM_2026-06-24")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    health_path = resolve_path(args.health)
    state_path = resolve_path(args.state)
    health = read_json(health_path)
    state = read_json(state_path)
    kind = notification_kind(health, state)
    key = notification_key(kind, health)
    notified = {str(item) for item in state.get("notified_keys", [])} if isinstance(state.get("notified_keys"), list) else set()
    message = render_message(kind, health)
    response: dict[str, Any] | None = None
    if not health:
        decision = "skipped_missing_health"
    elif kind == "healthy_no_notification" and not args.force:
        decision = "skipped_healthy"
    elif key in notified and not args.force:
        decision = "skipped_duplicate"
    else:
        env_files = [resolve_path(item) for item in args.env_file]
        token = env_value("TELEGRAM_BOT_TOKEN", env_files)
        chat_id = env_value("TELEGRAM_CHAT_ID", env_files)
        if not token or not chat_id:
            decision = "skipped_missing_telegram_env"
        elif args.dry_run:
            decision = "dry_run_ready"
        else:
            try:
                response = send_telegram(token, chat_id, message, args.timeout_s)
                decision = "sent" if response.get("ok") else "telegram_api_error"
            except Exception as exc:  # noqa: BLE001
                response = {"ok": False, "error_type": type(exc).__name__}
                decision = "telegram_send_error"
    if decision in {"sent", "dry_run_ready"}:
        notified.add(key)
    state.update(
        {
            "notified_keys": sorted(notified)[-500:],
            "last_checked_at": now_iso(),
            "last_decision": decision,
            "last_kind": kind,
            "last_key": key,
            "last_classification": str(health.get("classification") or ""),
        }
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "generated_at": now_iso(),
        "health_path": str(health_path),
        "state_path": str(state_path),
        "kind": kind,
        "notification_key": key,
        "decision": decision,
        "telegram_response_ok": response.get("ok") if isinstance(response, dict) else None,
        "message_preview": message,
        "runtime_boundary": {"health_notification_only": True, "signals_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }
    prefix = resolve_path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text("\n".join(["# Cross-Venue Microstructure Health Telegram", "", f"- Decision: `{decision}`.", f"- Kind: `{kind}`.", "- Health notification only; no signals or orders.", "- `can_trade=false`.", ""]), encoding="utf-8")
    print(json.dumps({"decision": decision, "kind": kind, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if decision not in {"telegram_api_error", "telegram_send_error"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
