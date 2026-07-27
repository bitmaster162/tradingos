#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


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


def read_history(path: Path, lookback_hours: float) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_iso(row.get("ts"))
            if ts is None or ts < cutoff:
                continue
            rows.append(row)
    except OSError:
        return []
    return rows


def slope_per_hour(rows: list[dict[str, Any]], key: str) -> float | None:
    points: list[tuple[datetime, float]] = []
    for row in rows:
        ts = parse_iso(row.get("ts"))
        value = row.get(key)
        if ts is None or not isinstance(value, (int, float)):
            continue
        points.append((ts, float(value)))
    if len(points) < 2:
        return None
    points.sort(key=lambda item: item[0])
    first_ts, first_value = points[0]
    last_ts, last_value = points[-1]
    hours = (last_ts - first_ts).total_seconds() / 3600.0
    if hours <= 0:
        return None
    return (last_value - first_value) / hours


def estimate_hours_to_target(current: float, target: float, rate_per_hour: float | None) -> float | None:
    deficit = max(0.0, target - current)
    if deficit <= 0:
        return 0.0
    if rate_per_hour is None or rate_per_hour <= 0:
        return None
    return deficit / rate_per_hour


def context_deficits(contexts: dict[str, Any], min_context_bars: int) -> dict[str, dict[str, Any]]:
    required = ["long_liquidation_flush", "short_liquidation_squeeze"]
    out: dict[str, dict[str, Any]] = {}
    for name in required:
        value = int(contexts.get(name) or 0)
        out[name] = {
            "current": value,
            "required": min_context_bars,
            "deficit": max(0, min_context_bars - value),
            "ready": value >= min_context_bars,
        }
    mixed = int(contexts.get("mixed") or 0)
    out["mixed_observed"] = {"current": mixed, "required": 0, "deficit": 0, "ready": True}
    return out


def render_markdown(report: dict[str, Any]) -> str:
    progress = report["progress"]
    lines = [
        "# Liquidation Sample Progress",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        "",
        "## Current Sample",
        "",
        f"- Bybit events: `{progress['events']['current']}` / `{progress['events']['required']}`; deficit `{progress['events']['deficit']}`.",
        f"- Event bars: `{progress['event_bars']['current']}` / `{progress['event_bars']['required']}`; deficit `{progress['event_bars']['deficit']}`.",
        f"- Matched price bars: `{progress['matched_price_bars']['current']}` / `{progress['matched_price_bars']['required']}`; deficit `{progress['matched_price_bars']['deficit']}`.",
        "",
        "## Context Balance",
        "",
    ]
    for name, item in report["context_progress"].items():
        lines.append(f"- `{name}`: `{item['current']}` / `{item['required']}`; deficit `{item['deficit']}`; ready `{item['ready']}`.")
    lines.extend(["", "## Velocity", ""])
    velocity = report["velocity"]
    lines.append(f"- History points in lookback: `{velocity['history_points']}`.")
    lines.append(f"- Events/hour: `{velocity['events_per_hour']}`.")
    lines.append(f"- Event-bars/hour: `{velocity['event_bars_per_hour']}`.")
    lines.append(f"- Estimated hours to 500 events: `{velocity['estimated_hours_to_min_events']}`.")
    lines.append(f"- Estimated hours to 50 event-bars: `{velocity['estimated_hours_to_min_event_bars']}`.")
    lines.extend(["", "## Blockers", ""])
    for blocker in report["blockers"] or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Progress monitor only.",
            "- It does not run strategy search, emit alerts, create paper entries, or place orders.",
            "- `can_trade=false` is preserved.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    sample = read_json(args.sample_gate)
    coverage = read_json(args.coverage)
    evidence = sample.get("evidence") if isinstance(sample.get("evidence"), dict) else {}
    thresholds = sample.get("thresholds") if isinstance(sample.get("thresholds"), dict) else {}
    contexts = evidence.get("contexts") if isinstance(evidence.get("contexts"), dict) else {}

    min_events = int(thresholds.get("min_events_for_research") or args.min_events_for_research)
    min_event_bars = int(thresholds.get("min_event_bars_for_research") or args.min_event_bars_for_research)
    min_context_bars = int(thresholds.get("min_context_bars") or args.min_context_bars)

    events = int(evidence.get("events") or 0)
    event_bars = int(evidence.get("aggregate_rows") or 0)
    matched_price_bars = int(evidence.get("matched_price_bars") or 0)
    history_path = resolve_path(args.history)
    point = {
        "ts": now_iso(),
        "events": events,
        "event_bars": event_bars,
        "matched_price_bars": matched_price_bars,
        "long_liquidation_flush": int(contexts.get("long_liquidation_flush") or 0),
        "short_liquidation_squeeze": int(contexts.get("short_liquidation_squeeze") or 0),
        "mixed": int(contexts.get("mixed") or 0),
        "sample_decision": sample.get("decision"),
        "coverage_decision": coverage.get("decision"),
        "can_trade": False,
    }
    append_jsonl(history_path, point)
    history = read_history(history_path, args.lookback_hours)
    event_rate = slope_per_hour(history, "events")
    bar_rate = slope_per_hour(history, "event_bars")
    estimate_events = estimate_hours_to_target(events, min_events, event_rate)
    estimate_bars = estimate_hours_to_target(event_bars, min_event_bars, bar_rate)

    blockers = list(sample.get("blockers") or [])
    context_progress = context_deficits(contexts, min_context_bars)
    if events >= min_events and event_bars >= min_event_bars and all(
        item.get("ready") for name, item in context_progress.items() if name != "mixed_observed"
    ):
        decision = "liquidation_sample_progress_ready_for_manual_review"
        next_action = "Run manual review of the fixed-horizon event study; keep execution disabled."
    else:
        decision = "liquidation_sample_progress_collecting"
        next_action = "Keep Bybit/Binance collectors and watchdogs running; wait for event count, event bars and context balance to clear locked thresholds."

    return {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_sample_progress_monitor.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "boundary": {
            "progress_monitor_only": True,
            "emits_alerts": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "progress": {
            "events": {"current": events, "required": min_events, "deficit": max(0, min_events - events), "ready": events >= min_events},
            "event_bars": {"current": event_bars, "required": min_event_bars, "deficit": max(0, min_event_bars - event_bars), "ready": event_bars >= min_event_bars},
            "matched_price_bars": {
                "current": matched_price_bars,
                "required": min_context_bars,
                "deficit": max(0, min_context_bars - matched_price_bars),
                "ready": matched_price_bars >= min_context_bars,
            },
        },
        "context_progress": context_progress,
        "velocity": {
            "lookback_hours": args.lookback_hours,
            "history_points": len(history),
            "events_per_hour": round(event_rate, 6) if event_rate is not None else None,
            "event_bars_per_hour": round(bar_rate, 6) if bar_rate is not None else None,
            "estimated_hours_to_min_events": round(estimate_events, 3) if estimate_events is not None else None,
            "estimated_hours_to_min_event_bars": round(estimate_bars, 3) if estimate_bars is not None else None,
        },
        "blockers": sorted(set(str(item) for item in blockers)),
        "sources": {
            "sample_gate": args.sample_gate,
            "coverage": args.coverage,
            "history": portable(history_path),
        },
        "next_action": next_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Progress monitor for real liquidation sample gates.")
    parser.add_argument("--sample-gate", default="docs/BYBIT_ALL_LIQUIDATION_SAMPLE_GATE_2026-07-01.json")
    parser.add_argument("--coverage", default="docs/LIQUIDATION_MULTI_VENUE_COVERAGE_SUMMARY_2026-07-01.json")
    parser.add_argument("--history", default="logs/liquidation_real_feed/liquidation_sample_progress_history.jsonl")
    parser.add_argument("--lookback-hours", type=float, default=24.0)
    parser.add_argument("--min-events-for-research", type=int, default=500)
    parser.add_argument("--min-event-bars-for-research", type=int, default=50)
    parser.add_argument("--min-context-bars", type=int, default=10)
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_SAMPLE_PROGRESS_2026-07-01")
    args = parser.parse_args()

    report = build_report(args)
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "decision": report["decision"],
        "events": report["progress"]["events"],
        "event_bars": report["progress"]["event_bars"],
        "velocity": report["velocity"],
        "out": portable(out.with_suffix(".json")),
        "can_trade": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
