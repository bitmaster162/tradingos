#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

VERSION = "1.0.0"


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def safe(cockpit: dict[str, Any]) -> None:
    s = cockpit.get("safety", {})
    if s.get("can_trade") is not False or s.get("capital_permission") != "DENY":
        raise ValueError("unsafe cockpit trading permission")
    signals = s.get("signals_allowed", s.get("signals"))
    orders = s.get("orders_allowed", s.get("orders"))
    if signals is not False or orders is not False:
        raise ValueError("unsafe cockpit signal/order permission")


def level_state(c: dict[str, Any]) -> str:
    e, levels = c.get("executive", {}), c.get("levels", {})
    stance = str(e.get("stance", "NO_ACTION"))
    last, support, resistance = num(levels.get("last")), num(levels.get("support")), num(levels.get("resistance"))
    if last and resistance:
        overhead = (resistance / last - 1.0) * 100.0
        if 0 <= overhead <= 0.25 and stance == "WATCH_LONG":
            return "LONG_TRIGGER_ZONE"
        if 0 <= overhead <= 0.50:
            return "NEAR_RESISTANCE"
    if last and support:
        below = (last / support - 1.0) * 100.0
        if 0 <= below <= 0.50 and stance == "WATCH_SHORT":
            return "SHORT_TRIGGER_ZONE"
        if 0 <= below <= 1.00:
            return "NEAR_SUPPORT"
    return "MID_RANGE"


def risk_labels(c: dict[str, Any]) -> set[str]:
    return {str(x.get("label")) for x in c.get("risk_flags", []) if isinstance(x, dict) and x.get("label")}


def blockers(c: dict[str, Any]) -> set[str]:
    return {str(x) for x in c.get("quality", {}).get("blockers", [])}


def event(kind: str, priority: str, title: str, detail: str) -> dict[str, str]:
    return {"kind": kind, "priority": priority, "title": title, "detail": detail}


def build(current: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    safe(current)
    if previous is not None:
        safe(previous)
    e = current.get("executive", {})
    events: list[dict[str, str]] = []
    status = str(current.get("status", "UNKNOWN"))
    stance = str(e.get("stance", "NO_ACTION"))
    level = level_state(current)

    if status != "READY":
        events.append(event("STATUS_BLOCKED", "CRITICAL", "Decision input blocked", f"status={status}"))

    if previous is None:
        if level == "LONG_TRIGGER_ZONE":
            events.append(event("LEVEL_PROXIMITY", "HIGH", "Long scenario trigger is close", "Price is within 0.25% of resistance while stance is WATCH_LONG."))
        elif level == "SHORT_TRIGGER_ZONE":
            events.append(event("LEVEL_PROXIMITY", "HIGH", "Short scenario trigger is close", "Price is within 0.50% of support while stance is WATCH_SHORT."))
        if not events:
            events.append(event("BASELINE", "INFO", "Alert baseline established", "No prior Cockpit state is available; future material changes can now be deduplicated."))
    else:
        pe = previous.get("executive", {})
        prev_status, prev_stance = str(previous.get("status", "UNKNOWN")), str(pe.get("stance", "NO_ACTION"))
        if status != prev_status:
            events.append(event("STATUS_CHANGE", "HIGH", "Data/decision status changed", f"{prev_status} -> {status}"))
        if stance != prev_stance:
            events.append(event("STANCE_CHANGE", "HIGH", "Operator stance changed", f"{prev_stance} -> {stance}"))
        prev_level = level_state(previous)
        if level != prev_level and level in {"LONG_TRIGGER_ZONE", "SHORT_TRIGGER_ZONE"}:
            events.append(event("LEVEL_CROSS", "HIGH", "Price entered a trigger zone", f"{prev_level} -> {level}"))
        for label in sorted(risk_labels(current) - risk_labels(previous)):
            events.append(event("NEW_RISK_FLAG", "MEDIUM", "New risk veto/flag", label))
        for item in sorted(blockers(current) - blockers(previous)):
            events.append(event("NEW_BLOCKER", "CRITICAL", "New data blocker", item))
        if not events:
            events.append(event("NO_MATERIAL_CHANGE", "INFO", "No material decision change", "Stance, status, trigger zone, blockers and risk flags are unchanged."))

    notify_events = [x for x in events if x["priority"] in {"CRITICAL", "HIGH", "MEDIUM"} and x["kind"] not in {"BASELINE", "NO_MATERIAL_CHANGE"}]
    priority_order = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "INFO": 0}
    priority = max((x["priority"] for x in events), key=lambda x: priority_order[x])
    fingerprint_payload = {
        "status": status,
        "stance": stance,
        "level_state": level,
        "risks": sorted(risk_labels(current)),
        "blockers": sorted(blockers(current)),
    }
    dedupe_key = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    return {
        "schema": "tradingos.decision_alert.v1",
        "version": VERSION,
        "brief_id": current.get("brief_id"),
        "symbol": current.get("symbol"),
        "as_of": current.get("as_of"),
        "decision": "NOTIFY" if notify_events else "SILENT",
        "priority": priority,
        "level_state": level,
        "events": events,
        "dedupe_key": dedupe_key,
        "next_action": e.get("next"),
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def render_md(a: dict[str, Any]) -> str:
    lines = [f"# {a.get('symbol')} Decision Alert", "", f"**{a['decision']} · {a['priority']} · {a['level_state']}**", ""]
    for x in a["events"]:
        lines.append(f"- **{x['kind']} / {x['priority']}** — {x['title']}: {x['detail']}")
    lines += ["", f"Next: {a.get('next_action')}", "", f"Dedupe: `{a['dedupe_key']}`", "", "_Read-only alert. signals=false · orders=false · can_trade=false · capital_permission=DENY._"]
    return "\n".join(lines) + "\n"


def generate(current_path: Path, out_dir: Path, previous_path: Path | None = None) -> dict[str, Path]:
    current = read(current_path)
    previous = read(previous_path) if previous_path else None
    payload = build(current, previous)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "alert.json", "markdown": out_dir / "alert.md"}
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    paths["markdown"].write_text(render_md(payload), encoding="utf-8", newline="\n")
    return paths


def main() -> int:
    p = argparse.ArgumentParser(description="Generate a deduplicable read-only alert from Decision Cockpit state")
    p.add_argument("--current", type=Path, required=True)
    p.add_argument("--previous", type=Path)
    p.add_argument("--out-dir", type=Path, required=True)
    a = p.parse_args()
    try:
        paths = generate(a.current.resolve(), a.out_dir.resolve(), a.previous.resolve() if a.previous else None)
        payload = read(paths["json"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2)); return 2
    print(json.dumps({"result": "PASS", "decision": payload["decision"], "priority": payload["priority"], "outputs": {k: str(v) for k, v in paths.items()}, "can_trade": False, "capital_permission": "DENY"}, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
