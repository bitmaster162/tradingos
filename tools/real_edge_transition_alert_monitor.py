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


def read_json(path: str | Path) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def registry_has_tombstone(registry: dict[str, Any], tombstone_id: str) -> bool:
    entries = registry.get("entries") if isinstance(registry.get("entries"), list) else []
    return any(isinstance(item, dict) and item.get("id") == tombstone_id for item in entries)


def forward_event(report: dict[str, Any], tombstone_registry: dict[str, Any] | None = None) -> dict[str, Any]:
    if not report:
        return {
            "key": "bybit_forward_observer",
            "status": "missing",
            "event_kind": None,
            "attention_required": False,
            "summary": "forward observer report is missing",
        }
    decision = str(report.get("decision") or "")
    evidence = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
    lock = report.get("lock") if isinstance(report.get("lock"), dict) else {}
    lock_id = str(lock.get("lock_id") or "unknown_lock")
    new_event_bars = as_int(evidence.get("new_event_bars"))
    new_liquidations = as_int(evidence.get("new_liquidation_events"))
    positive_horizons = as_int(evidence.get("positive_horizons_after_cost_buffer"))
    blockers = [str(item) for item in report.get("blockers") or []]

    tombstoned = registry_has_tombstone(tombstone_registry or {}, "bybit_liquidation_forward_lock_failed")
    if tombstoned and decision == "bybit_liquidation_forward_observer_failed_gate_for_tombstone_review":
        event_kind = None
        attention = False
        decision = "bybit_liquidation_forward_observer_tombstoned_no_retune"
    elif decision == "bybit_liquidation_forward_observer_passed_for_manual_review":
        event_kind = "forward_observer_passed_for_manual_review"
        attention = True
    elif decision == "bybit_liquidation_forward_observer_failed_gate_for_tombstone_review":
        event_kind = "forward_observer_failed_gate_for_tombstone_review"
        attention = True
    elif decision == "bybit_liquidation_forward_observer_pending_resolution" and new_event_bars > 0:
        event_kind = "forward_observer_pending_future_bars"
        attention = False
    elif decision == "bybit_liquidation_forward_observer_collecting_sample" and new_event_bars > 0:
        event_kind = "forward_observer_collecting_sample"
        attention = False
    else:
        event_kind = None
        attention = False

    return {
        "key": "bybit_forward_observer",
        "status": decision or "unknown",
        "event_kind": event_kind,
        "attention_required": attention,
        "notification_key": f"bybit_forward_observer|{lock_id}|{decision}|bars:{new_event_bars}|liq:{new_liquidations}|pos:{positive_horizons}",
        "lock_id": lock_id,
        "new_event_bars": new_event_bars,
        "new_liquidation_events": new_liquidations,
        "positive_horizons": positive_horizons,
        "blockers": blockers,
        "summary": f"{decision}; bars={new_event_bars}; liquidations={new_liquidations}; positive_horizons={positive_horizons}",
    }


def forward_review_event(report: dict[str, Any], tombstone_registry: dict[str, Any] | None = None) -> dict[str, Any]:
    if not report:
        return {
            "key": "bybit_forward_review_pack",
            "status": "missing",
            "event_kind": None,
            "attention_required": False,
            "summary": "forward review pack report is missing",
        }
    decision = str(report.get("decision") or "")
    action = str(report.get("review_action") or "")
    lock_id = str(report.get("lock_id") or "unknown_lock")
    progress = report.get("progress_summary") if isinstance(report.get("progress_summary"), dict) else {}
    event_bars = as_int(progress.get("event_bars_current"))
    event_bars_required = as_int(progress.get("event_bars_required"))
    resolved_records = as_int(progress.get("resolved_records"))
    sample_ready = progress.get("sample_ready") is True
    resolution_ready = progress.get("resolution_ready") is True

    attention_actions = {
        "rerun_observer": "forward_review_ready_for_observer_rerun",
        "manual_pass_review": "forward_review_manual_pass_review_required",
        "manual_tombstone_review": "forward_review_manual_tombstone_review_required",
    }
    tombstoned = registry_has_tombstone(tombstone_registry or {}, "bybit_liquidation_forward_lock_failed")
    if tombstoned and action == "manual_tombstone_review":
        event_kind = None
        attention = False
        decision = "bybit_forward_review_pack_tombstoned_no_retune"
    else:
        event_kind = attention_actions.get(action)
        attention = event_kind is not None

    return {
        "key": "bybit_forward_review_pack",
        "status": decision or "unknown",
        "event_kind": event_kind,
        "attention_required": attention,
        "notification_key": f"bybit_forward_review|{lock_id}|{decision}|{action}|bars:{event_bars}|resolved:{resolved_records}",
        "lock_id": lock_id,
        "review_action": action or None,
        "event_bars": event_bars,
        "event_bars_required": event_bars_required,
        "resolved_records": resolved_records,
        "sample_ready": sample_ready,
        "resolution_ready": resolution_ready,
        "summary": (
            f"{decision}; action={action}; event_bars={event_bars}/{event_bars_required}; "
            f"sample_ready={sample_ready}; resolution_ready={resolution_ready}; resolved_records={resolved_records}"
        ),
    }


def post_liq_absorption_event(report: dict[str, Any]) -> dict[str, Any]:
    if not report:
        return {
            "key": "post_liq_absorption_forward_observer",
            "status": "missing",
            "event_kind": None,
            "attention_required": False,
            "summary": "post-liq absorption forward observer report is missing",
        }
    decision = str(report.get("decision") or "")
    lock = report.get("lock") if isinstance(report.get("lock"), dict) else {}
    evidence = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
    lock_id = str(lock.get("lock_id") or "unknown_lock")
    min_n = as_int(evidence.get("selected_bucket_min_n"))
    positive_horizons = as_int(evidence.get("positive_horizons"))
    blockers = [str(item) for item in report.get("blockers") or []]

    if decision == "post_liq_absorption_forward_observer_passed_for_manual_review":
        event_kind = "post_liq_absorption_passed_for_manual_review"
        attention = True
    elif decision == "post_liq_absorption_forward_observer_failed_gate_for_tombstone_review":
        event_kind = "post_liq_absorption_failed_gate_for_tombstone_review"
        attention = True
    else:
        event_kind = None
        attention = False

    return {
        "key": "post_liq_absorption_forward_observer",
        "status": decision or "unknown",
        "event_kind": event_kind,
        "attention_required": attention,
        "notification_key": f"post_liq_absorption|{lock_id}|{decision}|min_n:{min_n}|pos:{positive_horizons}",
        "lock_id": lock_id,
        "selected_bucket_min_n": min_n,
        "positive_horizons": positive_horizons,
        "blockers": blockers,
        "summary": f"{decision}; selected_bucket_min_n={min_n}; positive_horizons={positive_horizons}; blockers={blockers}",
    }


def liquidation_timing_vol_event(report: dict[str, Any]) -> dict[str, Any]:
    if not report:
        return {
            "key": "liquidation_timing_vol_forward_observer",
            "status": "missing",
            "event_kind": None,
            "attention_required": False,
            "summary": "liquidation timing/vol forward observer report is missing",
        }
    decision = str(report.get("decision") or "")
    lock = report.get("lock") if isinstance(report.get("lock"), dict) else {}
    evidence = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
    lock_id = str(lock.get("lock_id") or "unknown_lock")
    min_n = as_int(evidence.get("selected_bucket_min_n"))
    positive_horizons = as_int(evidence.get("positive_horizons"))
    blockers = [str(item) for item in report.get("blockers") or []]

    if decision == "liquidation_timing_vol_continuation_forward_passed_for_manual_review":
        event_kind = "liquidation_timing_vol_passed_for_manual_review"
        attention = True
    elif decision == "liquidation_timing_vol_continuation_forward_failed_gate_for_tombstone_review":
        event_kind = "liquidation_timing_vol_failed_gate_for_tombstone_review"
        attention = True
    else:
        event_kind = None
        attention = False

    return {
        "key": "liquidation_timing_vol_forward_observer",
        "status": decision or "unknown",
        "event_kind": event_kind,
        "attention_required": attention,
        "notification_key": f"liquidation_timing_vol|{lock_id}|{decision}|min_n:{min_n}|pos:{positive_horizons}",
        "lock_id": lock_id,
        "selected_bucket_min_n": min_n,
        "positive_horizons": positive_horizons,
        "blockers": blockers,
        "summary": f"{decision}; selected_bucket_min_n={min_n}; positive_horizons={positive_horizons}; blockers={blockers}",
    }


def microstructure_event(transition: dict[str, Any], unblock: dict[str, Any]) -> dict[str, Any]:
    transition_state = str(transition.get("transition_state") or "")
    snapshot_id = str(transition.get("snapshot_id") or unblock.get("snapshot_id") or "")
    unblock_decision = str(unblock.get("decision") or "")
    coverage = unblock.get("coverage") if isinstance(unblock.get("coverage"), dict) else {}
    sla = unblock.get("sla") if isinstance(unblock.get("sla"), dict) else {}
    book_coverage = as_float(coverage.get("book_coverage_pct"))
    cooldown_remaining = as_float(sla.get("cooldown_remaining_minutes"))
    blockers = [str(item) for item in unblock.get("blockers") or transition.get("failed_checks") or []]

    available_states = {
        "sealed_snapshot_ready_for_train_research_batch",
        "sealed_snapshot_research_batch_already_completed",
    }
    if snapshot_id or transition_state in available_states or unblock_decision == "microstructure_snapshot_available":
        event_kind = "microstructure_snapshot_available"
        attention = True
    else:
        event_kind = None
        attention = False

    return {
        "key": "microstructure_snapshot",
        "status": transition_state or unblock_decision or "unknown",
        "event_kind": event_kind,
        "attention_required": attention,
        "notification_key": f"microstructure_snapshot|{snapshot_id or 'no_snapshot'}|{transition_state}|{unblock_decision}",
        "snapshot_id": snapshot_id or None,
        "transition_state": transition_state or None,
        "unblock_decision": unblock_decision or None,
        "book_coverage_pct": book_coverage,
        "sla_cooldown_remaining_minutes": cooldown_remaining,
        "blockers": blockers,
        "summary": f"{transition_state or unblock_decision}; snapshot={snapshot_id or 'none'}; book_coverage={book_coverage}; cooldown={cooldown_remaining}",
    }


def render_message(events: list[dict[str, Any]], report: dict[str, Any]) -> str:
    lines = [
        "Trading OS REAL EDGE TRANSITION",
        "",
        f"Generated: {report['generated_at']}",
        f"Decision: {report['decision']}",
        "",
    ]
    for event in events:
        lines.extend(
            [
                f"[{event['key']}]",
                f"Kind: {event.get('event_kind') or 'none'}",
                f"Status: {event.get('status')}",
                f"Summary: {event.get('summary')}",
                "",
            ]
        )
    lines.extend(
        [
            "Boundary: OBSERVER/REVIEW ALERT ONLY.",
            "No paper entries, no live signals, no orders.",
            "can_trade=false",
        ]
    )
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Real Edge Transition Alert Monitor",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Telegram decision: `{report.get('telegram_decision')}`",
        "",
        "## Events",
        "",
        "| Event | Status | Kind | Attention | Summary |",
        "|---|---|---|---:|---|",
    ]
    for event in report.get("events", []):
        lines.append(
            f"| `{event.get('key')}` | `{event.get('status')}` | `{event.get('event_kind')}` | "
            f"`{event.get('attention_required')}` | {event.get('summary')} |"
        )
    lines.extend(
        [
            "",
            "## New Attention Events",
            "",
        ]
    )
    new_events = report.get("new_attention_events") or []
    if new_events:
        for event in new_events:
            lines.append(f"- `{event.get('event_kind')}`: {event.get('summary')}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Transition monitor only.",
            "- Does not create signals, paper entries or live orders.",
            "- Telegram send is disabled unless `--send-telegram` is explicitly used.",
            "- `can_trade=false`.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    state_path = resolve_path(args.state)
    state = read_json(state_path)
    notified = {str(item) for item in state.get("notified_keys", [])} if isinstance(state.get("notified_keys"), list) else set()
    tombstone_registry = read_json(args.tombstone_registry) if args.tombstone_registry else {}

    forward = forward_event(read_json(args.forward_observer), tombstone_registry)
    forward_review = forward_review_event(read_json(args.forward_review), tombstone_registry)
    post_liq = post_liq_absorption_event(read_json(args.post_liq_absorption_runner))
    timing_vol = liquidation_timing_vol_event(read_json(args.liquidation_timing_vol_runner))
    micro = microstructure_event(read_json(args.microstructure_transition), read_json(args.microstructure_unblock))
    events = [forward, forward_review, post_liq, timing_vol, micro]
    attention_events = [event for event in events if event.get("attention_required")]
    new_attention_events = [
        event for event in attention_events if str(event.get("notification_key")) not in notified or args.force
    ]

    if new_attention_events:
        decision = "real_edge_transition_attention_required"
    elif attention_events:
        decision = "real_edge_transition_attention_already_recorded"
    else:
        decision = "real_edge_transition_no_new_attention_event"

    report = {
        "generated_at": now_iso(),
        "tool": "tools/real_edge_transition_alert_monitor.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "events": events,
        "new_attention_events": new_attention_events,
        "source_reports": {
            "forward_observer": portable(resolve_path(args.forward_observer)),
            "forward_review": portable(resolve_path(args.forward_review)),
            "post_liq_absorption_runner": portable(resolve_path(args.post_liq_absorption_runner)),
            "liquidation_timing_vol_runner": portable(resolve_path(args.liquidation_timing_vol_runner)),
            "tombstone_registry": portable(resolve_path(args.tombstone_registry)) if args.tombstone_registry else None,
            "microstructure_transition": portable(resolve_path(args.microstructure_transition)),
            "microstructure_unblock": portable(resolve_path(args.microstructure_unblock)),
        },
        "state_path": portable(state_path),
        "boundary": {
            "observer_review_alert_only": True,
            "emits_trade_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }

    message = render_message(new_attention_events or events, report)
    report["message_preview"] = message
    telegram_decision = "not_requested"
    telegram_response: dict[str, Any] | None = None
    if new_attention_events and args.send_telegram:
        env_files = [resolve_path(item) for item in args.env_file]
        token = env_value(args.token_env, env_files)
        chat_id = env_value(args.chat_id_env, env_files)
        if not token or not chat_id:
            telegram_decision = "skipped_missing_telegram_env"
        elif args.dry_run:
            telegram_decision = "dry_run_ready"
        else:
            try:
                telegram_response = send_telegram(token, chat_id, message, args.timeout_s)
                telegram_decision = "sent" if telegram_response.get("ok") else "telegram_api_error"
            except Exception as exc:  # noqa: BLE001
                telegram_response = {"ok": False, "error_type": type(exc).__name__}
                telegram_decision = "telegram_send_error"
    elif new_attention_events:
        telegram_decision = "send_not_enabled"

    if new_attention_events and telegram_decision in {"not_requested", "send_not_enabled", "dry_run_ready", "sent"}:
        for event in new_attention_events:
            notified.add(str(event.get("notification_key")))
    state.update(
        {
            "updated_at": report["generated_at"],
            "last_decision": decision,
            "last_telegram_decision": telegram_decision,
            "notified_keys": sorted(notified)[-500:],
            "last_event_statuses": {event["key"]: event.get("status") for event in events},
            "can_trade": False,
        }
    )
    write_json(state_path, state)

    report["telegram_decision"] = telegram_decision
    report["telegram_response_ok"] = telegram_response.get("ok") if isinstance(telegram_response, dict) else None
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Stateful monitor for review-worthy real-edge transitions.")
    parser.add_argument("--forward-observer", default="docs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_2026-07-02.json")
    parser.add_argument("--forward-review", default="docs/BYBIT_LIQUIDATION_FORWARD_REVIEW_PACK_2026-07-02.json")
    parser.add_argument("--post-liq-absorption-runner", default="docs/POST_LIQUIDATION_ABSORPTION_FORWARD_OBSERVER_RUNNER_2026-07-03.json")
    parser.add_argument("--liquidation-timing-vol-runner", default="docs/LIQUIDATION_TIMING_VOL_FORWARD_OBSERVER_RUNNER_2026-07-03.json")
    parser.add_argument("--tombstone-registry", default="")
    parser.add_argument("--microstructure-transition", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_MONITOR_2026-07-01_UNBLOCK_STATUS_REFRESH.json")
    parser.add_argument("--microstructure-unblock", default="docs/MICROSTRUCTURE_UNBLOCK_STATUS_2026-07-02_AFTER_HEALTH_REFRESH.json")
    parser.add_argument("--state", default="logs/real_edge_transition_alert_monitor/state.json")
    parser.add_argument("--out-prefix", default="docs/REAL_EDGE_TRANSITION_ALERT_MONITOR_2026-07-02")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    report = build_report(args)
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "new_attention_events": [event.get("event_kind") for event in report.get("new_attention_events", [])],
                "telegram_decision": report.get("telegram_decision"),
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.get("telegram_decision") not in {"telegram_api_error", "telegram_send_error"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
