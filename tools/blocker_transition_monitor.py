from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_PREFIX = "docs/BLOCKER_TRANSITION_MONITOR_2026-06-30"
DEFAULT_STATE_PATH = "logs/blocker_transition_monitor/state.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            data = json.loads(raw.decode(encoding))
            return data if isinstance(data, dict) else {"_value": data}
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {"_value": data}
    except json.JSONDecodeError as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest_file(patterns: list[str]) -> Path | None:
    for pattern in patterns:
        files = [path for path in ROOT.glob(pattern) if path.is_file()]
        if files:
            return max(files, key=lambda path: (path.stat().st_mtime, str(path)))
    return None


def deep_get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Source:
    key: str
    path: Path | None
    data: dict[str, Any]

    def relpath(self) -> str | None:
        if self.path is None:
            return None
        try:
            return self.path.relative_to(ROOT).as_posix()
        except ValueError:
            return self.path.as_posix()


def load_sources() -> dict[str, Source]:
    specs = {
        "unified_readiness": [
            "docs/UNIFIED_READINESS_MATRIX_2026-06-30*.json",
        ],
        "readiness_pulse": [
            "docs/TRADINGOS_READINESS_PULSE_2026-06-30*.json",
        ],
        "liquidation_quality": [
            "docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30*.json",
        ],
        "microstructure_progress": [
            "docs/CROSS_VENUE_MICROSTRUCTURE_READINESS_PROGRESS_2026-06-30*.json",
            "docs/CROSS_VENUE_MICROSTRUCTURE_READINESS_PROGRESS_2026-06-25*.json",
        ],
        "microstructure_snapshot_gate": [
            "docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-30*.json",
            "docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25*.json",
        ],
        "document_forward_scoreboard": [
            "docs/DOCUMENT_RULE_FORWARD_SCOREBOARD_2026-06-30*.json",
        ],
    }
    out: dict[str, Source] = {}
    for key, patterns in specs.items():
        path = latest_file(patterns)
        out[key] = Source(key=key, path=path, data=read_json(path))
    return out


def extract_state(sources: dict[str, Source]) -> dict[str, Any]:
    unified = sources["unified_readiness"].data
    pulse = sources["readiness_pulse"].data
    liq = sources["liquidation_quality"].data
    micro = sources["microstructure_progress"].data
    snap = sources["microstructure_snapshot_gate"].data
    doc = sources["document_forward_scoreboard"].data

    blockers = sorted(
        {
            *[str(x) for x in as_list(deep_get(unified, "summary.hard_blockers"))],
            *[str(x) for x in as_list(deep_get(pulse, "summary.hard_blockers"))],
            *[str(x) for x in as_list(deep_get(pulse, "hard_blockers"))],
        }
        - {""}
    )

    return {
        "source_paths": {key: source.relpath() for key, source in sources.items()},
        "can_trade": any(bool(source.data.get("can_trade")) for source in sources.values()),
        "blockers": blockers,
        "readiness": {
            "decision": unified.get("decision"),
            "ready_components": deep_get(unified, "summary.ready_components"),
            "trade_enabled_components": deep_get(unified, "summary.trade_enabled_components"),
        },
        "pulse": {
            "decision": pulse.get("decision"),
            "ready": pulse.get("ready"),
            "can_trade": pulse.get("can_trade"),
        },
        "liquidation": {
            "decision": liq.get("decision"),
            "collector_status": deep_get(liq, "collector.status"),
            "collector_pid_alive": deep_get(liq, "collector.pid_alive"),
            "status_age_minutes": as_float(deep_get(liq, "collector.status_age_minutes")),
            "events": deep_get(liq, "events.events", 0),
            "first_event_time": deep_get(liq, "events.first_event_time"),
            "last_event_time": deep_get(liq, "events.last_event_time"),
            "hard_failures": [
                gate.get("name")
                for gate in as_list(liq.get("gates"))
                if isinstance(gate, dict) and gate.get("severity") == "hard" and not gate.get("passed")
            ],
            "soft_failures": [
                gate.get("name")
                for gate in as_list(liq.get("gates"))
                if isinstance(gate, dict) and gate.get("severity") == "soft" and not gate.get("passed")
            ],
        },
        "microstructure": {
            "decision": micro.get("decision"),
            "gate_decision": micro.get("gate_decision"),
            "health_classification": micro.get("health_classification"),
            "span_hours": as_float(micro.get("span_hours")),
            "remaining_hours": as_float(micro.get("remaining_hours")),
            "earliest_time_gate_at_utc": micro.get("earliest_time_gate_at_utc"),
            "failed_checks": as_list(micro.get("failed_checks")),
        },
        "snapshot_gate": {
            "decision": snap.get("decision"),
            "passed": deep_get(snap, "summary.passed"),
            "total": deep_get(snap, "summary.total"),
            "failed": as_list(deep_get(snap, "summary.failed")),
            "snapshot_id": snap.get("snapshot_id"),
        },
        "document_forward": {
            "decision": doc.get("decision"),
            "signals": deep_get(doc, "summary.signals", 0),
            "resolved": deep_get(doc, "summary.resolved", 0),
            "winrate_pct": deep_get(doc, "summary.winrate_pct"),
            "expectancy_r": deep_get(doc, "summary.expectancy_r"),
        },
    }


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            if key in {"source_paths"}:
                continue
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten(value, child_prefix))
        return out
    return {prefix: data}


def compare_states(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[dict[str, Any]]:
    if not previous:
        return []
    previous_flat = flatten(previous)
    current_flat = flatten(current)
    keys = sorted(set(previous_flat) | set(current_flat))
    changes: list[dict[str, Any]] = []
    for key in keys:
        before = previous_flat.get(key)
        after = current_flat.get(key)
        if before != after:
            changes.append({"field": key, "before": before, "after": after})
    return changes


def classify_decision(previous: dict[str, Any] | None, current: dict[str, Any], changes: list[dict[str, Any]]) -> str:
    if previous is None:
        return "blocker_transition_baseline_created"
    if not changes:
        return "blocker_transition_no_change"
    if current.get("can_trade"):
        return "blocker_transition_changed_attention_required_can_trade_true"
    important_prefixes = (
        "blockers",
        "liquidation.events",
        "liquidation.collector_status",
        "liquidation.collector_pid_alive",
        "snapshot_gate.decision",
        "snapshot_gate.snapshot_id",
        "document_forward.signals",
        "document_forward.resolved",
    )
    if any(change["field"].startswith(important_prefixes) for change in changes):
        return "blocker_transition_changed_attention_required"
    return "blocker_transition_changed_low_priority"


def render_markdown(report: dict[str, Any]) -> str:
    state = report["current_state"]
    changes = report["changes"]
    lines = [
        "# Blocker Transition Monitor",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        "",
        "## Current blockers",
    ]
    blockers = state.get("blockers") or []
    if blockers:
        lines.extend(f"- `{blocker}`" for blocker in blockers)
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Key state",
            "",
            "| Area | Value |",
            "| --- | --- |",
            f"| Readiness | `{state['readiness'].get('decision')}` |",
            f"| Liquidation feed | `{state['liquidation'].get('decision')}`; events=`{state['liquidation'].get('events')}`; collector=`{state['liquidation'].get('collector_status')}` |",
            f"| Microstructure | `{state['microstructure'].get('decision')}`; remaining_hours=`{state['microstructure'].get('remaining_hours')}` |",
            f"| Snapshot gate | `{state['snapshot_gate'].get('decision')}`; passed=`{state['snapshot_gate'].get('passed')}/{state['snapshot_gate'].get('total')}` |",
            f"| Document forward | `{state['document_forward'].get('decision')}`; signals=`{state['document_forward'].get('signals')}`; resolved=`{state['document_forward'].get('resolved')}` |",
            "",
            "## Changes since previous run",
        ]
    )
    if not changes:
        lines.append("- No previous state on baseline run, or no changes detected.")
    else:
        lines.extend(["", "| Field | Before | After |", "| --- | --- | --- |"])
        for change in changes[:80]:
            before = json.dumps(change["before"], ensure_ascii=False)
            after = json.dumps(change["after"], ensure_ascii=False)
            lines.append(f"| `{change['field']}` | `{before}` | `{after}` |")
        if len(changes) > 80:
            lines.append(f"- Truncated changes in markdown: `{len(changes) - 80}` more in JSON.")

    lines.extend(
        [
            "",
            "## Source files",
        ]
    )
    for key, path in state.get("source_paths", {}).items():
        lines.append(f"- `{key}`: `{path}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "- This monitor is observability-only.",
            "- It never sends orders and never promotes a strategy.",
            "- `can_trade=true` is treated as an attention condition, not permission to trade.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_report(state_path: Path) -> dict[str, Any]:
    sources = load_sources()
    current_state = extract_state(sources)
    previous_state = read_json(state_path).get("current_state") if state_path.exists() else None
    changes = compare_states(previous_state, current_state)
    decision = classify_decision(previous_state, current_state, changes)
    return {
        "generated_at": now_iso(),
        "tool": "tools/blocker_transition_monitor.py",
        "decision": decision,
        "can_trade": bool(current_state.get("can_trade")),
        "current_state": current_state,
        "previous_state_exists": previous_state is not None,
        "changes": changes,
        "boundary": {
            "observability_only": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "promotes_strategy": False,
            "can_trade": False,
        },
        "next_action": next_action(decision, current_state),
    }


def next_action(decision: str, state: dict[str, Any]) -> str:
    if decision == "blocker_transition_baseline_created":
        return "rerun after readiness/collector/forward observers update to detect real transitions"
    if state.get("can_trade"):
        return "stop and audit: can_trade true appeared in a source report but live trading is still locked by policy"
    liquidation = state.get("liquidation", {})
    if liquidation.get("events", 0) and liquidation.get("decision") != "liquidation_force_order_ready_for_research":
        return "rerun forceOrder data-quality gate and inspect first real event sample"
    snapshot = state.get("snapshot_gate", {})
    if snapshot.get("snapshot_id"):
        return "run post-seal microstructure chain under locked protocol"
    doc_forward = state.get("document_forward", {})
    if doc_forward.get("signals") and not doc_forward.get("resolved"):
        return "keep observer running until document-rule forward signals resolve"
    return "continue waiting on sealed microstructure snapshot, real forceOrder sample and forward outcomes"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor transitions in TradingOS blockers.")
    parser.add_argument("--out-prefix", default=DEFAULT_OUT_PREFIX)
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    parser.add_argument("--no-state-write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_prefix = ROOT / args.out_prefix
    state_path = ROOT / args.state_path
    report = build_report(state_path)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    if not args.no_state_write:
        write_json(state_path, report)
    print(json.dumps({"decision": report["decision"], "changes": len(report["changes"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
