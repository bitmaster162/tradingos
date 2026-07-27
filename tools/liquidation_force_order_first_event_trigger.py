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
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return value if isinstance(value, dict) else {"_read_error": "not_object"}


def main() -> int:
    parser = argparse.ArgumentParser(description="First-event trigger for Binance forceOrder feed")
    parser.add_argument("--data-quality", default="docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30.json")
    parser.add_argument("--state-path", default="logs/liquidation_force_order/first_event_trigger_state.json")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_FIRST_EVENT_TRIGGER_2026-06-30")
    args = parser.parse_args()
    dq_path = resolve_path(args.data_quality)
    state_path = resolve_path(args.state_path)
    out = resolve_path(args.out_prefix)
    dq = read_json(dq_path)
    state = read_json(state_path)
    all_events = dq.get("events") if isinstance(dq.get("events"), dict) else {}
    events_block = (
        all_events.get("preregistered_sample")
        if isinstance(all_events.get("preregistered_sample"), dict)
        else all_events.get("research_universe")
        if isinstance(all_events.get("research_universe"), dict)
        else all_events
    )
    events = int(events_block.get("events") or 0)
    last_event_time = events_block.get("last_event_time")
    first_event_time = events_block.get("first_event_time")
    previously_seen = bool(state.get("first_event_seen"))

    if events <= 0:
        decision = "waiting_for_first_real_force_order_event"
        next_action = "keep collector running"
    elif not previously_seen:
        decision = "first_real_force_order_event_detected"
        state = {
            "first_event_seen": True,
            "first_detected_at": now_iso(),
            "first_event_time": first_event_time,
            "events_at_detection": events,
            "source_report": portable(dq_path),
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        next_action = "run schema review, then wait for minimum event sample before preregistering a hypothesis"
    else:
        decision = "first_real_force_order_event_already_seen"
        next_action = "continue collecting until minimum research sample is reached"

    report = {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_force_order_first_event_trigger.py",
        "decision": decision,
        "can_trade": False,
        "events": events,
        "first_event_time": first_event_time,
        "last_event_time": last_event_time,
        "state_path": portable(state_path),
        "state": state,
        "next_action": next_action,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Liquidation ForceOrder First Event Trigger",
                "",
                f"- Generated: `{report['generated_at']}`",
                f"- Decision: `{decision}`",
                f"- Can trade: `false`",
                f"- Events: `{events}`",
                f"- First event time: `{first_event_time}`",
                f"- Last event time: `{last_event_time}`",
                "",
                "## Next Action",
                "",
                f"- {next_action}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"decision": decision, "events": events, "out": portable(out.with_suffix(".json")), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
