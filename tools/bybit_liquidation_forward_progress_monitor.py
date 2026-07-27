#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


def slope_per_hour(rows: list[dict[str, Any]], key: str) -> float | None:
    points: list[tuple[datetime, float]] = []
    for row in rows:
        ts = parse_iso(row.get("ts"))
        value = as_float(row.get(key))
        if ts is None or value is None:
            continue
        points.append((ts, value))
    if len(points) < 2:
        return None
    points.sort(key=lambda item: item[0])
    first_ts, first_value = points[0]
    last_ts, last_value = points[-1]
    hours = (last_ts - first_ts).total_seconds() / 3600.0
    if hours <= 0:
        return None
    return (last_value - first_value) / hours


def estimate_hours(deficit: int, rate_per_hour: float | None) -> float | None:
    if deficit <= 0:
        return 0.0
    if rate_per_hour is None or rate_per_hour <= 0:
        return None
    return deficit / rate_per_hour


def horizon_progress(forward: dict[str, Any], required_per_horizon: int) -> dict[str, dict[str, Any]]:
    by_horizon = forward.get("by_horizon") if isinstance(forward.get("by_horizon"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for horizon, summary in sorted(by_horizon.items(), key=lambda item: int(item[0]) if str(item[0]).isdigit() else 999999):
        data = summary if isinstance(summary, dict) else {}
        n = as_int(data.get("n"))
        out[str(horizon)] = {
            "current": n,
            "required": required_per_horizon,
            "deficit": max(0, required_per_horizon - n),
            "ready": n >= required_per_horizon,
            "mean_after_cost_bps": data.get("mean_after_cost_bps"),
            "passes_cost_buffer": bool(data.get("passes_cost_buffer")),
            "winrate_positive_pct": data.get("winrate_positive_pct"),
        }
    return out


def classify(forward_decision: str, sample_ready: bool, resolution_ready: bool) -> tuple[str, str]:
    if forward_decision == "bybit_liquidation_forward_observer_passed_for_manual_review":
        return "bybit_forward_progress_passed_for_manual_review", "manual review can inspect the forward candidate; no paper/live permission"
    if forward_decision == "bybit_liquidation_forward_observer_failed_gate_for_tombstone_review":
        return "bybit_forward_progress_failed_gate_for_tombstone_review", "manual tombstone review can inspect the failed forward gate"
    if not sample_ready:
        return "bybit_forward_progress_collecting_sample", "keep collecting post-lock liquidation bars until locked sample thresholds are met"
    if not resolution_ready:
        return "bybit_forward_progress_pending_horizon_resolution", "wait for enough future bars to resolve each locked horizon"
    return "bybit_forward_progress_ready_for_outcome_gate", "rerun observer; outcome gate can now be scored without pending-resolution ambiguity"


def render_markdown(report: dict[str, Any]) -> str:
    sample = report["sample_progress"]
    velocity = report["velocity"]
    lines = [
        "# Bybit Liquidation Forward Progress Monitor",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        "",
        "## Sample Progress",
        "",
        f"- Event bars: `{sample['event_bars']['current']}` / `{sample['event_bars']['required']}`; deficit `{sample['event_bars']['deficit']}`.",
        f"- Liquidation events: `{sample['liquidation_events']['current']}` / `{sample['liquidation_events']['required']}`; deficit `{sample['liquidation_events']['deficit']}`.",
        f"- Resolved records: `{sample['resolved_records']}`.",
        "",
        "## Horizon Progress",
        "",
        "| Horizon | N | Required | Deficit | Ready | Mean after cost | Pass |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for horizon, item in report["horizon_progress"].items():
        lines.append(
            f"| {horizon} | `{item['current']}` | `{item['required']}` | `{item['deficit']}` | "
            f"`{item['ready']}` | `{item.get('mean_after_cost_bps')}` | `{item.get('passes_cost_buffer')}` |"
        )
    lines.extend(
        [
            "",
            "## Velocity / ETA",
            "",
            f"- History points: `{velocity['history_points']}`.",
            f"- Event-bars/hour: `{velocity['event_bars_per_hour']}`.",
            f"- Min horizon N/hour: `{velocity['min_horizon_n_per_hour']}`.",
            f"- Estimated hours to sample event bars: `{velocity['estimated_hours_to_sample_event_bars']}`.",
            f"- Estimated hours to horizon resolution: `{velocity['estimated_hours_to_horizon_resolution']}`.",
            "",
            "## Blockers",
            "",
        ]
    )
    for blocker in report["blockers"] or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Progress monitor only.",
            "- Does not generate trade signals, paper entries, alerts by default, or orders.",
            "- `can_trade=false`.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    forward = read_json(args.forward_observer)
    lock = read_json(args.lock)
    evidence = forward.get("evidence") if isinstance(forward.get("evidence"), dict) else {}
    gate = lock.get("forward_gate_required") if isinstance(lock.get("forward_gate_required"), dict) else {}
    required_event_bars = as_int(gate.get("minimum_new_event_bars"), as_int(evidence.get("minimum_resolved_event_bars_per_horizon"), 15))
    required_liquidations = as_int(gate.get("minimum_new_liquidation_events"), 30)
    required_per_horizon = as_int(evidence.get("minimum_resolved_event_bars_per_horizon"), required_event_bars)
    event_bars = as_int(evidence.get("new_event_bars"))
    liquidation_events = as_int(evidence.get("new_liquidation_events"))
    resolved_records = as_int(evidence.get("resolved_records"))
    horizon = horizon_progress(forward, required_per_horizon)
    horizon_deficits = [item["deficit"] for item in horizon.values()]
    min_horizon_n = min((item["current"] for item in horizon.values()), default=0)
    max_horizon_deficit = max(horizon_deficits, default=0)
    sample_ready = event_bars >= required_event_bars and liquidation_events >= required_liquidations
    resolution_ready = bool(horizon) and all(item["ready"] for item in horizon.values())
    decision, next_action = classify(str(forward.get("decision") or ""), sample_ready, resolution_ready)

    history_path = resolve_path(args.history)
    point = {
        "ts": now_iso(),
        "forward_decision": forward.get("decision"),
        "event_bars": event_bars,
        "liquidation_events": liquidation_events,
        "resolved_records": resolved_records,
        "min_horizon_n": min_horizon_n,
        "max_horizon_deficit": max_horizon_deficit,
        "can_trade": False,
    }
    append_jsonl(history_path, point)
    history = read_history(history_path, args.lookback_hours)
    event_bar_rate = slope_per_hour(history, "event_bars")
    min_horizon_rate = slope_per_hour(history, "min_horizon_n")
    event_bar_deficit = max(0, required_event_bars - event_bars)
    hours_to_event_bars = estimate_hours(event_bar_deficit, event_bar_rate)
    hours_to_horizon = estimate_hours(max_horizon_deficit, min_horizon_rate)

    blockers = sorted(
        set(
            [
                *(str(item) for item in forward.get("blockers") or []),
                *(["minimum_sample_event_bars"] if event_bars < required_event_bars else []),
                *(["minimum_liquidation_events"] if liquidation_events < required_liquidations else []),
                *(["minimum_resolved_event_bars_per_horizon"] if not resolution_ready else []),
            ]
        )
    )
    return {
        "generated_at": now_iso(),
        "tool": "tools/bybit_liquidation_forward_progress_monitor.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "forward_observer_decision": forward.get("decision"),
        "sample_progress": {
            "event_bars": {
                "current": event_bars,
                "required": required_event_bars,
                "deficit": event_bar_deficit,
                "ready": event_bars >= required_event_bars,
            },
            "liquidation_events": {
                "current": liquidation_events,
                "required": required_liquidations,
                "deficit": max(0, required_liquidations - liquidation_events),
                "ready": liquidation_events >= required_liquidations,
            },
            "resolved_records": resolved_records,
            "sample_ready": sample_ready,
            "resolution_ready": resolution_ready,
        },
        "horizon_progress": horizon,
        "velocity": {
            "lookback_hours": args.lookback_hours,
            "history_points": len(history),
            "event_bars_per_hour": round(event_bar_rate, 6) if event_bar_rate is not None else None,
            "min_horizon_n_per_hour": round(min_horizon_rate, 6) if min_horizon_rate is not None else None,
            "estimated_hours_to_sample_event_bars": round(hours_to_event_bars, 3) if hours_to_event_bars is not None else None,
            "estimated_hours_to_horizon_resolution": round(hours_to_horizon, 3) if hours_to_horizon is not None else None,
        },
        "blockers": blockers,
        "sources": {
            "forward_observer": args.forward_observer,
            "lock": args.lock,
            "history": portable(history_path),
        },
        "next_action": next_action,
        "boundary": {
            "progress_monitor_only": True,
            "emits_trade_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Progress monitor for the accepted Bybit liquidation forward observer.")
    parser.add_argument("--forward-observer", default="docs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_2026-07-02.json")
    parser.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_LOCK_2026-07-02.json")
    parser.add_argument("--history", default="logs/bybit_liquidation_forward_progress/history.jsonl")
    parser.add_argument("--lookback-hours", type=float, default=24.0)
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_PROGRESS_2026-07-02")
    args = parser.parse_args()

    report = build_report(args)
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "event_bars": report["sample_progress"]["event_bars"],
                "horizon_progress": report["horizon_progress"],
                "blockers": report["blockers"],
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
