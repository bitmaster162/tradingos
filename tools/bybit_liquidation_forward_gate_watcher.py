#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


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
    if not p.is_file():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def write_md(path: Path, report: dict[str, Any]) -> None:
    sample = report.get("sample_progress", {})
    lines = [
        "# Bybit Liquidation Forward Gate Watcher",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Transition: `{report.get('transition_kind')}`",
        f"- Review action: `{report.get('review_action')}`",
        f"- Notification action: `{report.get('notification_action')}`",
        f"- can_trade: `{str(report.get('can_trade')).lower()}`",
        "",
        "## Sample",
        "",
        f"- Event bars: `{sample.get('event_bars_current')}/{sample.get('event_bars_required')}`",
        f"- Event bar deficit: `{sample.get('event_bars_deficit')}`",
        f"- Liquidation events: `{sample.get('liquidation_events_current')}`",
        f"- Resolved records: `{sample.get('resolved_records')}`",
        f"- Sample ready: `{sample.get('sample_ready')}`",
        f"- Resolution ready: `{sample.get('resolution_ready')}`",
        "",
        "## Next Action",
        "",
        str(report.get("next_action") or ""),
        "",
        "## Boundary",
        "",
        "Watcher only compares state and writes reports. It does not send Telegram, open paper entries or send orders.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def gate_signature(gate: dict[str, Any]) -> dict[str, Any]:
    sample = gate.get("sample_progress") if isinstance(gate.get("sample_progress"), dict) else {}
    horizons = gate.get("horizon_progress") if isinstance(gate.get("horizon_progress"), dict) else {}
    horizon_current = {}
    horizon_ready = {}
    for key, row in horizons.items():
        if not isinstance(row, dict):
            continue
        horizon_current[str(key)] = row.get("current")
        horizon_ready[str(key)] = row.get("ready") is True
    return {
        "decision": gate.get("decision"),
        "review_action": gate.get("review_action"),
        "event_bars_current": sample.get("event_bars_current"),
        "event_bars_required": sample.get("event_bars_required"),
        "event_bars_deficit": sample.get("event_bars_deficit"),
        "liquidation_events_current": sample.get("liquidation_events_current"),
        "resolved_records": sample.get("resolved_records"),
        "sample_ready": sample.get("sample_ready") is True,
        "resolution_ready": sample.get("resolution_ready") is True,
        "horizon_current": horizon_current,
        "horizon_ready": horizon_ready,
    }


def classify(current: dict[str, Any], previous: dict[str, Any]) -> tuple[str, str, str, str]:
    review_action = str(current.get("review_action") or "")
    prev_action = str(previous.get("review_action") or "")
    current_bars = current.get("event_bars_current")
    previous_bars = previous.get("event_bars_current")
    current_resolved = current.get("resolved_records")
    previous_resolved = previous.get("resolved_records")

    if review_action in {"manual_pass_review", "manual_tombstone_review"} and review_action != prev_action:
        return (
            "bybit_gate_watcher_review_transition",
            "review_action_changed",
            "dry_run_ready",
            "Manual review transition detected. Inspect the review pack; do not open paper/live trading.",
        )
    if review_action in {"manual_pass_review", "manual_tombstone_review"}:
        return (
            "bybit_gate_watcher_review_state_persistent",
            "review_state_persistent",
            "skipped_duplicate",
            "Manual review state already recorded. Avoid duplicate notification noise.",
        )
    if current.get("sample_ready") is True and previous.get("sample_ready") is not True:
        return (
            "bybit_gate_watcher_sample_ready_transition",
            "sample_ready_changed",
            "dry_run_ready",
            "Sample threshold became ready; wait for review pack confirmation before any promotion discussion.",
        )
    if current_bars != previous_bars or current_resolved != previous_resolved:
        return (
            "bybit_gate_watcher_progress_changed",
            "progress_changed",
            "skipped_progress_only",
            "Progress changed but review is still waiting. Keep collecting.",
        )
    return (
        "bybit_gate_watcher_no_change",
        "no_change",
        "skipped_no_change",
        "No state transition. Keep collecting.",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch Bybit liquidation gate transitions without sending alerts or opening trades.")
    parser.add_argument("--gate-report", default="docs/BYBIT_LIQUIDATION_FORWARD_GATE_RUNNER_2026-07-03.json")
    parser.add_argument("--state", default="logs/bybit_liquidation_forward_gate_watcher/state.json")
    parser.add_argument("--journal", default="logs/bybit_liquidation_forward_gate_watcher/events.jsonl")
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_GATE_WATCHER_2026-07-03")
    args = parser.parse_args()

    gate = read_json(args.gate_report)
    previous = read_json(args.state)
    current = gate_signature(gate)
    decision, transition_kind, notification_action, next_action = classify(current, previous)

    report = {
        "generated_at": now_iso(),
        "tool": "tools/bybit_liquidation_forward_gate_watcher.py",
        "decision": decision,
        "transition_kind": transition_kind,
        "notification_action": notification_action,
        "review_action": current.get("review_action"),
        "next_action": next_action,
        "can_trade": False,
        "orders_allowed": False,
        "sample_progress": {
            "event_bars_current": current.get("event_bars_current"),
            "event_bars_required": current.get("event_bars_required"),
            "event_bars_deficit": current.get("event_bars_deficit"),
            "liquidation_events_current": current.get("liquidation_events_current"),
            "resolved_records": current.get("resolved_records"),
            "sample_ready": current.get("sample_ready"),
            "resolution_ready": current.get("resolution_ready"),
        },
        "previous_signature": previous,
        "current_signature": current,
        "source_reports": {
            "gate_runner": args.gate_report,
        },
        "boundary": {
            "watcher_only": True,
            "sends_telegram": False,
            "emits_trade_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }

    out_json = resolve_path(f"{args.out_prefix}.json")
    out_md = resolve_path(f"{args.out_prefix}.md")
    state_path = resolve_path(args.state)
    journal_path = resolve_path(args.journal)
    report["out"] = portable(out_json)
    report["md"] = portable(out_md)
    write_json(out_json, report)
    write_md(out_md, report)
    write_json(state_path, current | {"updated_at": report["generated_at"]})
    append_jsonl(journal_path, report)

    print(
        json.dumps(
            {
                "decision": decision,
                "transition_kind": transition_kind,
                "notification_action": notification_action,
                "review_action": current.get("review_action"),
                "event_bars": current.get("event_bars_current"),
                "resolved_records": current.get("resolved_records"),
                "out": report["out"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

