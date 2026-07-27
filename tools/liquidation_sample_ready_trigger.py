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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ready_from_progress(progress: dict[str, Any]) -> tuple[bool, list[str]]:
    current = progress.get("progress") if isinstance(progress.get("progress"), dict) else {}
    events = current.get("events") if isinstance(current.get("events"), dict) else {}
    event_bars = current.get("event_bars") if isinstance(current.get("event_bars"), dict) else {}
    matched_price_bars = current.get("matched_price_bars") if isinstance(current.get("matched_price_bars"), dict) else {}
    contexts = progress.get("context_progress") if isinstance(progress.get("context_progress"), dict) else {}
    blockers: list[str] = []
    if not events.get("ready"):
        blockers.append("minimum_events_for_research")
    if not event_bars.get("ready"):
        blockers.append("minimum_distinct_event_bars")
    if not matched_price_bars.get("ready"):
        blockers.append("minimum_matched_price_bars")
    for name in ("long_liquidation_flush", "short_liquidation_squeeze"):
        item = contexts.get(name) if isinstance(contexts.get(name), dict) else {}
        if not item.get("ready"):
            blockers.append(f"{name}_context_sample")
    return not blockers, blockers


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation Sample Ready Trigger",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        "",
        "## Progress",
        "",
        f"- Events: `{report['progress_summary'].get('events_current')}` / `{report['progress_summary'].get('events_required')}`.",
        f"- Event bars: `{report['progress_summary'].get('event_bars_current')}` / `{report['progress_summary'].get('event_bars_required')}`.",
        f"- Matched price bars: `{report['progress_summary'].get('matched_price_bars')}` / `{report['progress_summary'].get('matched_price_bars_required')}`.",
        f"- Long flush contexts: `{report['progress_summary'].get('long_liquidation_flush_current')}` / `{report['progress_summary'].get('long_liquidation_flush_required')}`.",
        f"- Short squeeze contexts: `{report['progress_summary'].get('short_liquidation_squeeze_current')}` / `{report['progress_summary'].get('short_liquidation_squeeze_required')}`.",
        "",
        "## Blockers",
        "",
    ]
    for blocker in report.get("blockers") or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## State",
            "",
            f"- State path: `{report['state_path']}`",
            f"- Ready seen: `{report['state'].get('ready_seen')}`",
            f"- First ready at: `{report['state'].get('first_ready_at')}`",
            "",
            "## Boundary",
            "",
            "- Trigger is fail-closed and state-only.",
            "- It does not run strategy search, send Telegram, create paper entries, or place orders.",
            "- `can_trade=false` is preserved.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def summarize_progress(progress: dict[str, Any]) -> dict[str, Any]:
    current = progress.get("progress") if isinstance(progress.get("progress"), dict) else {}
    events = current.get("events") if isinstance(current.get("events"), dict) else {}
    event_bars = current.get("event_bars") if isinstance(current.get("event_bars"), dict) else {}
    matched = current.get("matched_price_bars")
    if isinstance(matched, dict):
        matched_current = matched.get("current")
        matched_required = matched.get("required")
    else:
        matched_current = matched
        matched_required = None
    contexts = progress.get("context_progress") if isinstance(progress.get("context_progress"), dict) else {}
    long_ctx = contexts.get("long_liquidation_flush") if isinstance(contexts.get("long_liquidation_flush"), dict) else {}
    short_ctx = contexts.get("short_liquidation_squeeze") if isinstance(contexts.get("short_liquidation_squeeze"), dict) else {}
    return {
        "events_current": events.get("current"),
        "events_required": events.get("required"),
        "event_bars_current": event_bars.get("current"),
        "event_bars_required": event_bars.get("required"),
        "matched_price_bars": matched_current,
        "matched_price_bars_required": matched_required,
        "long_liquidation_flush_current": long_ctx.get("current"),
        "long_liquidation_flush_required": long_ctx.get("required"),
        "short_liquidation_squeeze_current": short_ctx.get("current"),
        "short_liquidation_squeeze_required": short_ctx.get("required"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="State-only trigger when liquidation sample becomes ready for manual review.")
    parser.add_argument("--progress", default="docs/LIQUIDATION_SAMPLE_PROGRESS_2026-07-01.json")
    parser.add_argument("--state-path", default="logs/liquidation_real_feed/liquidation_sample_ready_trigger_state.json")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_SAMPLE_READY_TRIGGER_2026-07-01")
    args = parser.parse_args()

    progress_path = resolve_path(args.progress)
    state_path = resolve_path(args.state_path)
    out = resolve_path(args.out_prefix)
    progress = read_json(progress_path)
    state = read_json(state_path)
    ready, blockers = ready_from_progress(progress)
    previous_ready = bool(state.get("ready_seen"))
    summary = summarize_progress(progress)

    if not progress:
        decision = "liquidation_sample_ready_trigger_missing_progress"
        next_action = "run liquidation_sample_progress_monitor first"
    elif not ready:
        decision = "liquidation_sample_ready_trigger_waiting"
        next_action = "keep collectors and progress monitor running until all sample gates are ready"
    elif not previous_ready:
        decision = "liquidation_sample_ready_first_detected"
        state = {
            "ready_seen": True,
            "first_ready_at": now_iso(),
            "progress_report": portable(progress_path),
            "progress_summary": summary,
            "can_trade": False,
        }
        write_json(state_path, state)
        next_action = "manual review fixed-horizon liquidation event study; do not promote automatically"
    else:
        decision = "liquidation_sample_ready_already_seen"
        next_action = "manual review remains required before any research promotion"

    report = {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_sample_ready_trigger.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "ready": ready,
        "blockers": blockers,
        "progress_path": portable(progress_path),
        "progress_summary": summary,
        "state_path": portable(state_path),
        "state": state,
        "boundary": {
            "state_only": True,
            "emits_alerts": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "next_action": next_action,
    }
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "decision": decision,
        "ready": ready,
        "blockers": blockers,
        "out": portable(out.with_suffix(".json")),
        "can_trade": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
