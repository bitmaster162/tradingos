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

from tools.cross_venue_microstructure_snapshot_gate import now_iso  # noqa: E402
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


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def readiness_diagnostics(gate: dict[str, Any]) -> dict[str, Any]:
    diagnostics = gate.get("readiness_diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else {}


def failed_checks(gate: dict[str, Any]) -> list[str]:
    summary = gate.get("summary") if isinstance(gate.get("summary"), dict) else {}
    failed = summary.get("failed")
    if isinstance(failed, list):
        return [str(item) for item in failed]
    diagnostics = readiness_diagnostics(gate)
    diag_failed = diagnostics.get("failed_checks")
    if isinstance(diag_failed, list):
        return [str(item) for item in diag_failed]
    return []


def notification_kind(gate: dict[str, Any]) -> str:
    if not gate:
        return "missing_snapshot_gate_report"
    decision = str(gate.get("decision") or "")
    snapshot_id = gate.get("snapshot_id")
    diagnostics = readiness_diagnostics(gate)
    primary = str(diagnostics.get("primary_blocker") or "")
    remaining = safe_float(diagnostics.get("remaining_hours"))

    if decision in {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"}:
        return "microstructure_snapshot_sealed" if snapshot_id else "sealed_decision_missing_snapshot_id"
    if decision != "waiting_for_microstructure_readiness":
        return "microstructure_snapshot_gate_unknown_decision"
    if remaining is not None and remaining <= 0 and primary not in {"", "none", "minimum_time_window"}:
        return "microstructure_readiness_blocked_after_time_window"
    if primary == "minimum_time_window" and remaining is not None:
        if remaining <= 1:
            return "microstructure_snapshot_eta_1h"
        if remaining <= 6:
            return "microstructure_snapshot_eta_6h"
        if remaining <= 24:
            return "microstructure_snapshot_eta_24h"
    return "waiting_no_notification"


def notification_key(kind: str, gate: dict[str, Any]) -> str:
    diagnostics = readiness_diagnostics(gate)
    if kind.startswith("microstructure_snapshot_eta_"):
        bucket = kind.rsplit("_", 1)[-1]
        return "|".join([kind, str(gate.get("snapshot_id") or "no_snapshot"), bucket])
    if kind == "microstructure_readiness_blocked_after_time_window":
        return "|".join([kind, str(diagnostics.get("primary_blocker") or "unknown"), ",".join(sorted(failed_checks(gate))) or "none"])
    return "|".join([kind, str(gate.get("snapshot_id") or "no_snapshot"), str(gate.get("decision") or "no_decision")])


def render_message(kind: str, gate: dict[str, Any]) -> str:
    diagnostics = readiness_diagnostics(gate)
    failed = failed_checks(gate)
    lines = [
        "Trading OS MICROSTRUCTURE SNAPSHOT",
        "",
        f"Kind: {kind}",
        f"Decision: {gate.get('decision')}",
        f"Snapshot: {gate.get('snapshot_id')}",
        f"Passed checks: {gate.get('summary', {}).get('passed')}/{gate.get('summary', {}).get('total')}" if isinstance(gate.get("summary"), dict) else "Passed checks: n/a",
        f"Primary blocker: {diagnostics.get('primary_blocker')}",
        f"Remaining hours: {diagnostics.get('remaining_hours')}",
        f"Earliest time gate UTC: {diagnostics.get('estimated_earliest_time_gate_at_utc')}",
        f"Coverage trade/book: {diagnostics.get('trade_coverage_pct')}%/{diagnostics.get('book_coverage_pct')}%",
        f"Missing IDs B/C: {diagnostics.get('binance_missing_ids')}/{diagnostics.get('coinbase_missing_ids')}",
        f"Failed checks: {', '.join(failed) if failed else 'none'}",
        "",
        "Boundary: SNAPSHOT READINESS ONLY. No signals or orders.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicated Telegram notifier for microstructure snapshot ETA/seal milestones")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--state", default="logs/cross_venue_microstructure/snapshot_gate_telegram_state.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_TELEGRAM_2026-06-25")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    gate_path = resolve_path(args.snapshot_gate)
    state_path = resolve_path(args.state)
    out_prefix = resolve_path(args.out_prefix)
    gate = read_json(gate_path)
    state = read_json(state_path)
    kind = notification_kind(gate)
    key = notification_key(kind, gate)
    notified = {str(item) for item in state.get("notified_keys", [])} if isinstance(state.get("notified_keys"), list) else set()
    message = render_message(kind, gate)
    response: dict[str, Any] | None = None

    if not gate:
        decision = "skipped_missing_snapshot_gate"
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
            "last_gate_decision": str(gate.get("decision") or ""),
            "last_snapshot_id": str(gate.get("snapshot_id") or ""),
        }
    )
    write_json(state_path, state)
    output = {
        "generated_at": now_iso(),
        "snapshot_gate": str(gate_path),
        "state_path": str(state_path),
        "kind": kind,
        "notification_key": key,
        "decision": decision,
        "gate_decision": gate.get("decision"),
        "snapshot_id": gate.get("snapshot_id"),
        "remaining_hours": readiness_diagnostics(gate).get("remaining_hours"),
        "telegram_response_ok": response.get("ok") if isinstance(response, dict) else None,
        "message_preview": message,
        "runtime_boundary": {
            "snapshot_readiness_notification_only": True,
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
                "# Cross-Venue Microstructure Snapshot Gate Telegram",
                "",
                f"- Decision: `{decision}`.",
                f"- Kind: `{kind}`.",
                f"- Gate decision: `{gate.get('decision')}`.",
                f"- Snapshot: `{gate.get('snapshot_id')}`.",
                f"- Remaining hours: `{output['remaining_hours']}`.",
                f"- Telegram response ok: `{output['telegram_response_ok']}`.",
                "- Snapshot readiness notification only; no signals or orders.",
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
