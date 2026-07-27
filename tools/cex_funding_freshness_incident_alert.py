#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.forward_runtime_health_telegram_notify import env_value, send_telegram  # noqa: E402


DEFAULT_CONTRACT = ROOT / "configs" / "CEX_FUNDING_FRESHNESS_INCIDENT_ALERT_LOCK_2026-07-13.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def health_class(report: dict[str, Any]) -> str:
    return "healthy" if report.get("healthy") is True and report.get("decision") == "cex_funding_freshness_healthy" else "blocked"


def failure_signature(report: dict[str, Any]) -> str:
    blockers = report.get("blockers") if isinstance(report.get("blockers"), list) else []
    return "|".join([str(report.get("decision") or "watchdog_report_missing"), ",".join(sorted(str(item) for item in blockers)) or "none"])


def build_event(kind: str, incident_id: int, report: dict[str, Any], generated_at: str, signature: str) -> dict[str, Any]:
    sources = report.get("sources") if isinstance(report.get("sources"), dict) else {}
    aggregate = sources.get("aggregate") if isinstance(sources.get("aggregate"), dict) else {}
    direct = sources.get("direct") if isinstance(sources.get("direct"), dict) else {}
    blockers = [str(item) for item in report.get("blockers") or []]
    key = f"cex_funding_freshness|{incident_id}|{kind}|{signature}"
    return {
        "event_id": key,
        "generated_at": generated_at,
        "kind": kind,
        "incident_id": incident_id,
        "watchdog_decision": report.get("decision") or "watchdog_report_missing",
        "failure_signature": signature,
        "blockers": blockers,
        "aggregate_bucket_age_seconds": aggregate.get("bucket_age_seconds"),
        "direct_bucket_age_seconds": direct.get("bucket_age_seconds"),
        "latest_bucket_skew_minutes": sources.get("latest_bucket_skew_minutes"),
        "automatic_restart_allowed": False,
        "trade_signal": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def evaluate_transition(
    report: dict[str, Any],
    state: dict[str, Any],
    generated_at: str,
    maximum_pending: int = 20,
    maximum_recorded: int = 500,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    next_state = dict(state)
    current = health_class(report)
    previous = str(state.get("last_health_class") or "")
    incident_id = int(state.get("incident_id") or 0)
    incident_open = state.get("incident_open") is True
    current_signature = failure_signature(report)
    event: dict[str, Any] | None = None

    if not previous:
        kind = "baseline_recorded"
    elif previous == "healthy" and current == "blocked":
        incident_id += 1
        incident_open = True
        next_state["open_incident_signature"] = current_signature
        kind = "funding_freshness_blocked"
        event = build_event(kind, incident_id, report, generated_at, current_signature)
    elif previous == "blocked" and current == "healthy" and incident_open:
        kind = "funding_freshness_recovered"
        recovery_signature = str(state.get("open_incident_signature") or current_signature)
        event = build_event(kind, incident_id, report, generated_at, recovery_signature)
        incident_open = False
    else:
        kind = "no_transition"

    pending = [item for item in state.get("pending_notifications", []) if isinstance(item, dict)]
    recorded = [str(item) for item in state.get("recorded_keys", [])]
    if event and event["event_id"] not in {str(item.get("event_id")) for item in pending}:
        pending.append(event)

    next_state.update(
        {
            "updated_at": generated_at,
            "last_health_class": current,
            "last_watchdog_decision": report.get("decision") or "watchdog_report_missing",
            "last_failure_signature": current_signature,
            "incident_id": incident_id,
            "incident_open": incident_open,
            "last_transition_kind": kind,
            "pending_notifications": pending[-maximum_pending:],
            "recorded_keys": recorded[-maximum_recorded:],
            "automatic_restart_allowed": False,
            "can_trade": False,
        }
    )
    return kind, event, next_state


def render_message(event: dict[str, Any]) -> str:
    title = "BLOCKED" if event["kind"] == "funding_freshness_blocked" else "RECOVERED"
    return "\n".join(
        [
            f"Trading OS FUNDING DATA {title}",
            "",
            f"Incident: {event['incident_id']}",
            f"Kind: {event['kind']}",
            f"Watchdog: {event['watchdog_decision']}",
            f"Blockers: {', '.join(event['blockers']) if event['blockers'] else 'none'}",
            f"Aggregate/direct bucket age: {event['aggregate_bucket_age_seconds']}s / {event['direct_bucket_age_seconds']}s",
            f"Source skew: {event['latest_bucket_skew_minutes']}m",
            "",
            "Boundary: OPERATIONAL DATA INCIDENT ONLY.",
            "No trade signal. No paper entry. No orders.",
            "can_trade=false",
        ]
    )


def render_markdown(output: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# CEX Funding Freshness Incident Alert",
            "",
            f"- Generated: `{output['generated_at']}`",
            f"- Decision: `{output['decision']}`",
            f"- Transition kind: `{output['transition_kind']}`",
            f"- Pending notifications: `{output['pending_notifications']}`",
            f"- Telegram response ok: `{output['telegram_response_ok']}`",
            f"- Can trade: `{str(output['can_trade']).lower()}`",
            "",
            "## Message Preview",
            "",
            "```text",
            output.get("message_preview") or "none",
            "```",
            "",
            "Operational incident notification only. No signals, entries or orders.",
        ]
    ) + "\n"


def run(contract_path: Path, send_requested: bool, dry_run: bool) -> dict[str, Any]:
    contract = read_json(contract_path)
    inputs = contract["inputs"]
    policy = contract["transition_policy"]
    telegram = contract["telegram"]
    report_path = resolve_path(inputs["watchdog_report"])
    state_path = resolve_path(inputs["state"])
    ledger_path = resolve_path(inputs["ledger"])
    out_prefix = resolve_path(inputs["out_prefix"])
    report = read_json(report_path)
    state = read_json(state_path)
    generated_at = now_iso()
    kind, event, next_state = evaluate_transition(
        report,
        state,
        generated_at,
        int(policy["maximum_pending_notifications"]),
        int(policy["maximum_recorded_keys"]),
    )

    recorded = [str(item) for item in next_state.get("recorded_keys", [])]
    if event and event["event_id"] not in set(recorded):
        append_jsonl(ledger_path, event)
        recorded.append(event["event_id"])
        next_state["recorded_keys"] = recorded[-int(policy["maximum_recorded_keys"]):]

    pending = [item for item in next_state.get("pending_notifications", []) if isinstance(item, dict)]
    delivery_event = pending[0] if pending else None
    message = render_message(delivery_event) if delivery_event else ""
    response: dict[str, Any] | None = None
    if not delivery_event:
        decision = "skipped_no_transition"
    elif not send_requested:
        decision = "local_transition_recorded" if event else "pending_delivery_not_requested"
    else:
        env_files = [resolve_path(item) for item in telegram["env_files"]]
        token = env_value(str(telegram["token_env"]), env_files)
        chat_id = env_value(str(telegram["chat_id_env"]), env_files)
        if not token or not chat_id:
            decision = "local_transition_pending_missing_telegram_env"
        elif dry_run:
            decision = "dry_run_ready"
        else:
            try:
                response = send_telegram(token, chat_id, message, int(telegram["timeout_seconds"]))
                decision = "sent" if response.get("ok") else "telegram_api_error"
            except Exception as exc:  # noqa: BLE001
                response = {"ok": False, "error_type": type(exc).__name__}
                decision = "telegram_send_error"

    if delivery_event and decision == "sent":
        notified = [str(item) for item in next_state.get("notified_keys", [])]
        notified.append(str(delivery_event["event_id"]))
        next_state["notified_keys"] = notified[-500:]
        next_state["pending_notifications"] = pending[1:]
    next_state["last_delivery_decision"] = decision
    next_state["last_delivery_at"] = generated_at
    write_json(state_path, next_state)

    output = {
        "schema_version": 1,
        "generated_at": generated_at,
        "tool": "tools/cex_funding_freshness_incident_alert.py",
        "lock_id": contract.get("lock_id"),
        "decision": decision,
        "transition_kind": kind,
        "transition_event": event,
        "delivery_event_id": delivery_event.get("event_id") if delivery_event else None,
        "pending_notifications": len(next_state.get("pending_notifications", [])),
        "telegram_send_requested": send_requested,
        "telegram_response_ok": response.get("ok") if isinstance(response, dict) else None,
        "message_preview": message,
        "runtime_boundary": contract.get("runtime_boundary", {}),
        "automatic_restart_attempted": False,
        "trade_signal": False,
        "orders_allowed": False,
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), output)
    out_prefix.with_suffix(".md").write_text(render_markdown(output), encoding="utf-8")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transition-only CEX funding freshness incident alert")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = run(resolve_path(args.contract), args.send_telegram, args.dry_run)
    print(json.dumps({"decision": output["decision"], "transition_kind": output["transition_kind"], "pending": output["pending_notifications"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if output["decision"] not in {"telegram_api_error", "telegram_send_error"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
