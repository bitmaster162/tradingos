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

from tools.cross_venue_microstructure_research_runner import now_iso  # noqa: E402
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
    decision = str(report.get("decision") or "")
    if not report:
        return "missing_runner_report"
    if decision in {"blocked_waiting_for_sealed_snapshot", "blocked_missing_exact_snapshot_id"}:
        return "waiting_no_notification"
    if decision == "microstructure_research_batch_failed":
        return "microstructure_research_failed"
    if decision == "microstructure_snapshot_verification_failed":
        return "microstructure_snapshot_verification_failed"
    if decision == "microstructure_candidates_require_validation_review":
        return "microstructure_candidates_require_review"
    if decision in {
        "microstructure_research_batch_completed_no_candidate",
        "microstructure_research_batch_already_completed_for_snapshot",
    }:
        return "microstructure_research_completed"
    return "microstructure_research_unknown_decision"


def notification_key(kind: str, report: dict[str, Any]) -> str:
    return "|".join(
        [
            kind,
            str(report.get("snapshot_id") or "no_snapshot"),
            str(report.get("run_id") or "no_run"),
            str(report.get("decision") or "no_decision"),
        ]
    )


def render_message(kind: str, report: dict[str, Any]) -> str:
    results = report.get("experiment_results") if isinstance(report.get("experiment_results"), list) else []
    lines = [
        "Trading OS MICROSTRUCTURE RESEARCH",
        "",
        f"Kind: {kind}",
        f"Decision: {report.get('decision')}",
        f"Snapshot: {report.get('snapshot_id')}",
        f"Run ID: {report.get('run_id')}",
        f"Experiments: {report.get('experiments')}",
        f"Completed/failed: {report.get('completed')}/{report.get('failed')}",
        f"Candidates: {report.get('candidate_count')}",
        f"Train-qualified total: {report.get('train_qualified_total')}",
        f"Tested configs total: {report.get('tested_total')}",
        "",
    ]
    if results:
        lines.append("Experiment decisions:")
        for item in results[:10]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('experiment')}: {item.get('decision')} ({item.get('tested')} tested)")
        lines.append("")
    lines.append("Boundary: RESEARCH NOTIFICATION ONLY. No signals or orders.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicated Telegram notifier for microstructure research runner results")
    parser.add_argument("--runner-report", default="docs/CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_2026-06-25.json")
    parser.add_argument("--state", default="logs/cross_venue_microstructure/research_runner_telegram_state.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_TELEGRAM_2026-06-25")
    parser.add_argument("--env-file", action="append", default=[".env", "configs/telegram.env", "configs/.env"])
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_CHAT_ID")
    parser.add_argument("--timeout-s", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report_path = resolve_path(args.runner_report)
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
        decision = "skipped_missing_runner_report"
    elif kind == "waiting_no_notification" and not args.force:
        decision = "skipped_waiting_for_sealed_snapshot"
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
            "last_runner_decision": str(report.get("decision") or ""),
            "last_snapshot_id": str(report.get("snapshot_id") or ""),
            "last_run_id": str(report.get("run_id") or ""),
        }
    )
    write_json(state_path, state)
    output = {
        "generated_at": now_iso(),
        "runner_report": str(report_path),
        "state_path": str(state_path),
        "kind": kind,
        "notification_key": key,
        "decision": decision,
        "runner_decision": report.get("decision"),
        "snapshot_id": report.get("snapshot_id"),
        "run_id": report.get("run_id"),
        "telegram_response_ok": response.get("ok") if isinstance(response, dict) else None,
        "message_preview": message,
        "runtime_boundary": {
            "research_notification_only": True,
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
                "# Cross-Venue Microstructure Research Runner Telegram",
                "",
                f"- Decision: `{decision}`.",
                f"- Kind: `{kind}`.",
                f"- Runner decision: `{report.get('decision')}`.",
                f"- Snapshot: `{report.get('snapshot_id')}`.",
                f"- Telegram response ok: `{output['telegram_response_ok']}`.",
                "- Research notification only; no signals or orders.",
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
