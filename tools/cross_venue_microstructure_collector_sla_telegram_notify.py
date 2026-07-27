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

from tools.cross_venue_microstructure_collector_sla_guard import now_iso  # noqa: E402
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


def is_degraded(report: dict[str, Any]) -> bool:
    return str(report.get("decision") or "").startswith("collector_sla_degraded")


def is_recovered_state(report: dict[str, Any]) -> bool:
    return str(report.get("decision") or "") in {
        "collector_sla_healthy",
        "collector_sla_baseline_recorded",
        "collector_sla_healthy_legacy_gap_rolling_out",
    }


def failure_signature(report: dict[str, Any]) -> str:
    failed = report.get("failed_checks") if isinstance(report.get("failed_checks"), list) else []
    return "|".join(
        [
            str(report.get("decision") or "missing_decision"),
            ",".join(sorted(str(item) for item in failed)) or "none",
        ]
    )


def notification_kind(report: dict[str, Any], state: dict[str, Any]) -> str:
    if not report:
        return "missing_collector_sla_report"
    current_degraded = is_degraded(report)
    previous_degraded = state.get("last_report_degraded") is True
    current_signature = failure_signature(report)
    previous_signature = str(state.get("last_degraded_signature") or "")
    if current_degraded and not previous_degraded:
        return "collector_sla_degraded"
    if current_degraded and previous_degraded and current_signature != previous_signature:
        return "collector_sla_degraded_changed"
    if is_recovered_state(report) and previous_degraded:
        return "collector_sla_recovered"
    return "collector_sla_no_notification"


def notification_key(kind: str, report: dict[str, Any], state: dict[str, Any]) -> str:
    incident_id = state.get("incident_id") if state.get("incident_open") else state.get("last_incident_id")
    return "|".join(
        [
            kind,
            str(incident_id or 0),
            failure_signature(report),
            str(report.get("data_generated_at") or "no_data_ts") if kind == "collector_sla_recovered" else "",
        ]
    )


def render_message(kind: str, report: dict[str, Any]) -> str:
    failed = report.get("failed_checks") if isinstance(report.get("failed_checks"), list) else []
    return "\n".join(
        [
            "Trading OS MICROSTRUCTURE COLLECTOR SLA",
            "",
            f"Kind: {kind}",
            f"Decision: {report.get('decision')}",
            f"Class: {report.get('classification')}",
            f"Data report: {report.get('data_generated_at')} age {report.get('report_age_minutes')}m",
            f"Inserts trades/books: {report.get('inserted_trades')}/{report.get('inserted_books')}",
            f"Archive delta T/B/F: {report.get('archive_trades_delta')}/{report.get('archive_books_delta')}/{report.get('archive_features_delta')}",
            f"Coverage trade/book: {report.get('trade_coverage_pct')}%/{report.get('book_coverage_pct')}%",
            f"Missing IDs B/C: {report.get('binance_missing_ids')}/{report.get('coinbase_missing_ids')}",
            f"Failed checks: {', '.join(str(item) for item in failed) if failed else 'none'}",
            f"Next action: {report.get('next_action')}",
            "",
            "Boundary: COLLECTOR SLA NOTIFICATION ONLY. No signals or orders.",
        ]
    )


def update_incident_state(state: dict[str, Any], report: dict[str, Any], kind: str) -> dict[str, Any]:
    state = dict(state)
    current_degraded = is_degraded(report)
    previous_degraded = state.get("last_report_degraded") is True
    if current_degraded and not previous_degraded:
        state["incident_id"] = int(state.get("incident_id") or 0) + 1
        state["incident_open"] = True
    elif is_recovered_state(report) and previous_degraded:
        state["last_incident_id"] = state.get("incident_id")
        state["incident_open"] = False
    state["last_report_degraded"] = current_degraded
    state["last_report_decision"] = str(report.get("decision") or "")
    if current_degraded:
        state["last_degraded_signature"] = failure_signature(report)
    state["last_kind"] = kind
    state["last_checked_at"] = now_iso()
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicated Telegram notifier for microstructure collector SLA degradation/recovery")
    parser.add_argument("--sla-report", default="docs/CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_GUARD_2026-06-25.json")
    parser.add_argument("--state", default="logs/cross_venue_microstructure/collector_sla_telegram_state.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_TELEGRAM_2026-06-25")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report_path = resolve_path(args.sla_report)
    state_path = resolve_path(args.state)
    out_prefix = resolve_path(args.out_prefix)
    report = read_json(report_path)
    original_state = read_json(state_path)
    kind = notification_kind(report, original_state)
    next_state = update_incident_state(original_state, report, kind) if report else dict(original_state)
    key = notification_key(kind, report, next_state)
    notified = {str(item) for item in next_state.get("notified_keys", [])} if isinstance(next_state.get("notified_keys"), list) else set()
    message = render_message(kind, report) if report else "Trading OS MICROSTRUCTURE COLLECTOR SLA\n\nMissing SLA report."
    response: dict[str, Any] | None = None

    if not report:
        decision = "skipped_missing_collector_sla_report"
    elif kind == "collector_sla_no_notification" and not args.force:
        decision = "skipped_no_notification"
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
    next_state.update(
        {
            "notified_keys": sorted(notified)[-500:],
            "last_decision": decision,
            "last_key": key,
            "last_checked_at": now_iso(),
        }
    )
    write_json(state_path, next_state)

    output = {
        "generated_at": now_iso(),
        "sla_report": str(report_path),
        "state_path": str(state_path),
        "kind": kind,
        "notification_key": key,
        "decision": decision,
        "sla_decision": report.get("decision"),
        "failed_checks": report.get("failed_checks") if isinstance(report, dict) else None,
        "telegram_response_ok": response.get("ok") if isinstance(response, dict) else None,
        "message_preview": message,
        "runtime_boundary": {
            "collector_sla_notification_only": True,
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
                "# Cross-Venue Microstructure Collector SLA Telegram",
                "",
                f"- Decision: `{decision}`.",
                f"- Kind: `{kind}`.",
                f"- SLA decision: `{report.get('decision') if isinstance(report, dict) else None}`.",
                f"- Telegram response ok: `{output['telegram_response_ok']}`.",
                "- Collector SLA notification only; no signals or orders.",
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
