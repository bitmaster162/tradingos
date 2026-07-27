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
HEALTHY_CLASSIFICATION = "forward_runtime_healthy_observing"


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


def failed_hard_gates(health: dict[str, Any]) -> list[str]:
    gates = health.get("gates") if isinstance(health.get("gates"), list) else []
    failed: list[str] = []
    for item in gates:
        if not isinstance(item, dict):
            continue
        if item.get("severity") == "hard" and not item.get("passed"):
            failed.append(str(item.get("name") or "unknown_gate"))
    return failed


def notification_kind(health: dict[str, Any], state: dict[str, Any]) -> str:
    classification = str(health.get("classification") or "unknown")
    observed = health.get("observed") if isinstance(health.get("observed"), dict) else {}
    active_allowed = observed.get("promotion_active_filter_allowed") is True
    live_allowed = observed.get("promotion_live_execution_allowed") is True
    previous_classification = str(state.get("last_classification") or "")

    if active_allowed or live_allowed:
        return "runtime_promotion_boundary_changed"
    if classification != HEALTHY_CLASSIFICATION:
        return "runtime_degraded"
    if previous_classification and previous_classification != HEALTHY_CLASSIFICATION:
        return "runtime_recovered"
    return "healthy_no_notification"


def notification_key(kind: str, health: dict[str, Any]) -> str:
    classification = str(health.get("classification") or "unknown")
    failed = ",".join(failed_hard_gates(health)) or "no_failed_hard_gates"
    observed = health.get("observed") if isinstance(health.get("observed"), dict) else {}
    promotion = observed.get("promotion_decision")
    return "|".join([kind, classification, failed, str(promotion)])


def render_message(kind: str, health: dict[str, Any]) -> str:
    observed = health.get("observed") if isinstance(health.get("observed"), dict) else {}
    failed = failed_hard_gates(health)
    lines = [
        "Trading OS RUNTIME HEALTH",
        "",
        f"Kind: {kind}",
        f"Classification: {health.get('classification')}",
        f"Generated: {health.get('generated_at')}",
        f"Next action: {health.get('next_action')}",
        "",
        f"Last run: {observed.get('last_run_status')} exit {observed.get('last_run_exit_code')} age {observed.get('last_run_age_minutes')}m",
        f"Forward loop: {observed.get('loop_status')} pid {observed.get('loop_pid')} alive {observed.get('forward_loop_pid_alive')} age {observed.get('loop_status_age_minutes')}m",
        f"Panel open: {observed.get('panel_port_open')}",
        f"Signal: {observed.get('signal_status')} bar {observed.get('latest_closed_bar')}",
        "",
        f"Promotion: {observed.get('promotion_decision')}",
        f"Active filter: {observed.get('promotion_active_filter_allowed')}",
        f"Live execution: {observed.get('promotion_live_execution_allowed')}",
        f"Data quality: {observed.get('data_quality_classification')}",
        f"Crowd loop: {observed.get('crowd_loop_status')} pid {observed.get('crowd_loop_pid')} alive {observed.get('crowd_loop_pid_alive')} age {observed.get('crowd_loop_age_minutes')}m",
        f"Crowd refresh: {observed.get('crowd_last_run_status')} exit {observed.get('crowd_last_run_exit_code')} age {observed.get('crowd_last_run_age_minutes')}m",
        f"Crowd gate: {observed.get('crowd_promotion_decision')} paper {observed.get('crowd_paper_execution_allowed')} live {observed.get('crowd_live_execution_allowed')}",
        f"Daily backup: required {observed.get('daily_backup_required')} loop {observed.get('daily_backup_loop_status')} pid {observed.get('daily_backup_loop_pid')} alive {observed.get('daily_backup_loop_pid_alive')} last {observed.get('daily_backup_last_run_status')}",
        "",
        f"Failed hard gates: {', '.join(failed) if failed else 'none'}",
        "",
        "Boundary: OBSERVABILITY ONLY. No orders were sent.",
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
    parser = argparse.ArgumentParser(description="Telegram notifier for Trading OS runtime health")
    parser.add_argument("--health-json-path", default="docs/FORWARD_RUNTIME_HEALTH_2026-06-16.json")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/runtime_health_telegram_notify_state.json")
    parser.add_argument("--out-prefix", default="docs/FORWARD_RUNTIME_HEALTH_TELEGRAM_NOTIFY_2026-06-16")
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    health_path = resolve_path(args.health_json_path)
    state_path = resolve_path(args.state_path)
    out_prefix = resolve_path(args.out_prefix)
    health = read_json(health_path, {})
    state = read_json(state_path, {})
    if not isinstance(health, dict):
        health = {}
    if not isinstance(state, dict):
        state = {}

    kind = notification_kind(health, state)
    key = notification_key(kind, health)
    notified = set(str(item) for item in state.get("notified_keys", []))
    message = render_message(kind, health)
    telegram_response: dict[str, Any] | None = None

    if not health:
        decision = "skipped_missing_health_report"
    elif kind == "healthy_no_notification" and not args.force:
        decision = "skipped_healthy"
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
    state["last_kind"] = kind
    state["last_key"] = key
    state["last_classification"] = str(health.get("classification") or "")
    write_json(state_path, state)

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "telegram_notify_runtime_health_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "health_path": str(health_path),
        "state_path": str(state_path),
        "kind": kind,
        "classification": health.get("classification"),
        "notification_key": key,
        "decision": decision,
        "telegram_response_ok": telegram_response.get("ok") if isinstance(telegram_response, dict) else None,
        "failed_hard_gates": failed_hard_gates(health),
        "message_preview": message,
        "can_trade": False,
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Forward Runtime Health Telegram Notify",
                "",
                f"Generated: `{report['generated_at']}`",
                "",
                f"- Decision: `{decision}`.",
                f"- Kind: `{kind}`.",
                f"- Classification: `{report['classification']}`.",
                f"- Notification key: `{key}`.",
                f"- Telegram response ok: `{report['telegram_response_ok']}`.",
                "- Boundary: runtime health notification only; no orders.",
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
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "decision": decision,
                "kind": kind,
                "classification": report["classification"],
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
