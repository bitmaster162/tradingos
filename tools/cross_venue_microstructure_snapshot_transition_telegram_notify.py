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

from tools.cross_venue_microstructure_snapshot_transition_monitor import now_iso  # noqa: E402
from tools.forward_runtime_health_telegram_notify import env_value, send_telegram  # noqa: E402


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def notification_kind(report: dict[str, Any]) -> str:
    if not report:
        return "missing_snapshot_transition_report"
    state = str(report.get("transition_state") or "")
    changed = report.get("transition_changed") is True
    if state == "sealed_snapshot_ready_for_train_research_batch":
        return "snapshot_transition_ready_for_research"
    if state == "blocked_after_time_window":
        return "snapshot_transition_blocked_after_time_window"
    if state == "sealed_snapshot_research_batch_already_completed":
        return "snapshot_transition_research_batch_done"
    if state.startswith("blocked_") and changed:
        return "snapshot_transition_blocked_changed"
    return "waiting_no_notification"


def notification_key(kind: str, report: dict[str, Any]) -> str:
    return "|".join(
        [
            kind,
            str(report.get("snapshot_id") or "no_snapshot"),
            str(report.get("transition_state") or "no_state"),
            str(report.get("runner_decision") or "no_runner_decision"),
        ]
    )


def render_message(kind: str, report: dict[str, Any]) -> str:
    failed = report.get("failed_checks") if isinstance(report.get("failed_checks"), list) else []
    lines = [
        "Trading OS MICROSTRUCTURE TRANSITION",
        "",
        f"Kind: {kind}",
        f"State: {report.get('transition_state')}",
        f"Previous: {report.get('previous_transition_state')}",
        f"Changed: {report.get('transition_changed')}",
        f"Gate: {report.get('gate_decision')}",
        f"Runner: {report.get('runner_decision')}",
        f"Snapshot: {report.get('snapshot_id')}",
        f"Checks: {report.get('checks_passed')}/{report.get('checks_total')}",
        f"Primary blocker: {report.get('primary_blocker')}",
        f"Remaining hours: {report.get('remaining_hours')}",
        f"ETA UTC: {report.get('earliest_time_gate_at_utc')}",
        f"Coverage trade/book: {report.get('trade_coverage_pct')}%/{report.get('book_coverage_pct')}%",
        f"Missing IDs B/C: {report.get('binance_missing_ids')}/{report.get('coinbase_missing_ids')}",
        f"Research runner can attempt: {report.get('research_runner_can_attempt_now')}",
        f"Failed checks: {', '.join(str(item) for item in failed) if failed else 'none'}",
        f"Next action: {report.get('next_action')}",
        "",
        "Boundary: TRANSITION NOTIFICATION ONLY. No research run, no signals, no orders.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicated Telegram notifier for microstructure snapshot transition milestones")
    parser.add_argument("--transition-report", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_MONITOR_2026-06-25.json")
    parser.add_argument("--state", default="logs/cross_venue_microstructure/snapshot_transition_telegram_state.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_TELEGRAM_2026-06-25")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report_path = resolve_path(args.transition_report)
    state_path = resolve_path(args.state)
    out_prefix = resolve_path(args.out_prefix)
    report = read_json(report_path)
    state = read_json(state_path)
    kind = notification_kind(report)
    key = notification_key(kind, report)
    notified = {str(item) for item in state.get("notified_keys", [])} if isinstance(state.get("notified_keys"), list) else set()
    message = render_message(kind, report)
    response: dict[str, Any] | None = None

    if not report:
        decision = "skipped_missing_transition_report"
    elif kind == "waiting_no_notification" and not args.force:
        decision = "skipped_waiting"
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
            "last_transition_state": str(report.get("transition_state") or ""),
            "last_snapshot_id": str(report.get("snapshot_id") or ""),
        }
    )
    write_json(state_path, state)

    output = {
        "generated_at": now_iso(),
        "transition_report": str(report_path),
        "state_path": str(state_path),
        "kind": kind,
        "notification_key": key,
        "decision": decision,
        "transition_state": report.get("transition_state"),
        "snapshot_id": report.get("snapshot_id"),
        "runner_decision": report.get("runner_decision"),
        "research_runner_can_attempt_now": report.get("research_runner_can_attempt_now"),
        "telegram_response_ok": response.get("ok") if isinstance(response, dict) else None,
        "message_preview": message,
        "runtime_boundary": {
            "transition_notification_only": True,
            "runs_research_batch": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), output)
    out_prefix.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Cross-Venue Microstructure Snapshot Transition Telegram",
                "",
                f"- Decision: `{decision}`.",
                f"- Kind: `{kind}`.",
                f"- Transition state: `{report.get('transition_state')}`.",
                f"- Snapshot: `{report.get('snapshot_id')}`.",
                f"- Telegram response ok: `{output['telegram_response_ok']}`.",
                "- Transition notification only; no research run, signals or orders.",
                "- `can_trade=false`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"decision": decision, "kind": kind, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if decision not in {"telegram_api_error", "telegram_send_error"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
