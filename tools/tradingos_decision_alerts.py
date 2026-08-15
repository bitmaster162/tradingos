#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "1.2.1"
COCKPIT_SCHEMA = "tradingos.decision_cockpit.v1"
ALERT_SCHEMA = "tradingos.decision_alert.v1"


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def parse_time(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("as_of must be an ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        raise ValueError("as_of must include timezone")
    return dt.astimezone(timezone.utc)


def _nonempty_str(payload: dict[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: {key} must be a non-empty string")
    return value.strip()


def validate_cockpit(cockpit: dict[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(cockpit, dict):
        raise ValueError(f"{label}: cockpit must be an object")
    if cockpit.get("schema") != COCKPIT_SCHEMA:
        raise ValueError(f"{label}: unsupported cockpit schema")

    safety = cockpit.get("safety")
    if not isinstance(safety, dict):
        raise ValueError(f"{label}: safety must be an object")
    if (
        safety.get("signals_allowed") is not False
        or safety.get("orders_allowed") is not False
        or safety.get("can_trade") is not False
        or safety.get("capital_permission") != "DENY"
        or ("signals" in safety and safety.get("signals") is not False)
        or ("orders" in safety and safety.get("orders") is not False)
        or ("uses_credentials" in safety and safety.get("uses_credentials") is not False)
        or ("read_only_analysis" in safety and safety.get("read_only_analysis") is not True)
    ):
        raise ValueError(f"{label}: unsafe cockpit permission")

    executive = cockpit.get("executive")
    if not isinstance(executive, dict):
        raise ValueError(f"{label}: executive must be an object")
    stance = _nonempty_str(executive, "stance", f"{label}.executive")
    levels = cockpit.get("levels")
    if not isinstance(levels, dict):
        raise ValueError(f"{label}: levels must be an object")
    for field in ("last", "support", "resistance"):
        value = levels.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{label}: levels.{field} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError(f"{label}: levels.{field} must be finite and strictly positive")
    if float(levels["support"]) >= float(levels["resistance"]):
        raise ValueError(f"{label}: levels.support must be lower than levels.resistance")

    risk_flags = cockpit.get("risk_flags")
    if not isinstance(risk_flags, list):
        raise ValueError(f"{label}: risk_flags must be a list")
    for item in risk_flags:
        if not isinstance(item, dict):
            raise ValueError(f"{label}: risk_flags entries must be objects")
        if not isinstance(item.get("label"), str) or not item["label"].strip():
            raise ValueError(f"{label}: risk flag label must be a non-empty string")

    quality = cockpit.get("quality")
    if not isinstance(quality, dict):
        raise ValueError(f"{label}: quality must be an object")
    blockers_value = quality.get("blockers")
    if not isinstance(blockers_value, list):
        raise ValueError(f"{label}: quality.blockers must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in blockers_value):
        raise ValueError(f"{label}: quality.blockers entries must be non-empty strings")

    status = _nonempty_str(cockpit, "status", label)
    identity = {
        "brief_id": _nonempty_str(cockpit, "brief_id", label),
        "symbol": _nonempty_str(cockpit, "symbol", label),
        "timeframe": _nonempty_str(cockpit, "timeframe", label),
        "as_of": _nonempty_str(cockpit, "as_of", label),
        "status": status,
        "stance": stance,
    }
    identity["as_of_dt"] = parse_time(identity["as_of"])
    return identity


def validate_comparison(current: dict[str, Any], previous: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    current_id = validate_cockpit(current, "current")
    previous_id = validate_cockpit(previous, "previous")
    if previous_id["symbol"] != current_id["symbol"]:
        raise ValueError("current/previous symbol mismatch")
    if previous_id["timeframe"] != current_id["timeframe"]:
        raise ValueError("current/previous timeframe mismatch")
    if previous_id["as_of_dt"] >= current_id["as_of_dt"]:
        raise ValueError("previous as_of must be strictly earlier than current as_of")
    return current_id, previous_id


def level_state(c: dict[str, Any]) -> str:
    executive = c["executive"]
    levels = c["levels"]
    stance = str(executive.get("stance", "NO_ACTION")).strip()
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
    return {item["label"].strip() for item in c["risk_flags"]}


def blockers(c: dict[str, Any]) -> set[str]:
    return {item.strip() for item in c["quality"]["blockers"]}


def event(kind: str, priority: str, title: str, detail: str) -> dict[str, str]:
    return {"kind": kind, "priority": priority, "title": title, "detail": detail}


def build(current: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if previous is None:
        identity = validate_cockpit(current, "current")
    else:
        identity, _ = validate_comparison(current, previous)

    executive = current["executive"]
    events: list[dict[str, str]] = []
    status = identity["status"]
    stance = identity["stance"]
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
        previous_executive = previous["executive"]
        previous_status = str(previous.get("status", "UNKNOWN")).strip()
        previous_stance = str(previous_executive.get("stance", "NO_ACTION")).strip()
        if status != previous_status:
            events.append(event("STATUS_CHANGE", "HIGH", "Data/decision status changed", f"{previous_status} -> {status}"))
        if stance != previous_stance:
            events.append(event("STANCE_CHANGE", "HIGH", "Operator stance changed", f"{previous_stance} -> {stance}"))
        previous_level = level_state(previous)
        if level != previous_level and level in {"LONG_TRIGGER_ZONE", "SHORT_TRIGGER_ZONE"}:
            events.append(event("LEVEL_CROSS", "HIGH", "Price entered a trigger zone", f"{previous_level} -> {level}"))
        for label in sorted(risk_labels(current) - risk_labels(previous)):
            events.append(event("NEW_RISK_FLAG", "MEDIUM", "New risk veto/flag", label))
        for item in sorted(blockers(current) - blockers(previous)):
            events.append(event("NEW_BLOCKER", "CRITICAL", "New data blocker", item))
        if not events:
            events.append(event("NO_MATERIAL_CHANGE", "INFO", "No material decision change", "Stance, status, trigger zone, blockers and risk flags are unchanged."))

    notify_events = [item for item in events if item["priority"] in {"CRITICAL", "HIGH", "MEDIUM"} and item["kind"] not in {"BASELINE", "NO_MATERIAL_CHANGE"}]
    priority_order = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "INFO": 0}
    priority = max((item["priority"] for item in events), key=lambda item: priority_order[item])
    fingerprint_payload = {
        "symbol": identity["symbol"],
        "timeframe": identity["timeframe"],
        "status": status,
        "stance": stance,
        "level_state": level,
        "risks": sorted(risk_labels(current)),
        "blockers": sorted(blockers(current)),
    }
    dedupe_key = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    return {
        "schema": ALERT_SCHEMA,
        "version": VERSION,
        "brief_id": identity["brief_id"],
        "symbol": identity["symbol"],
        "timeframe": identity["timeframe"],
        "as_of": identity["as_of"],
        "decision": "NOTIFY" if notify_events else "SILENT",
        "priority": priority,
        "level_state": level,
        "events": events,
        "dedupe_key": dedupe_key,
        "next_action": executive.get("next"),
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def render_md(alert: dict[str, Any]) -> str:
    lines = [
        f"# {alert.get('symbol')} Decision Alert",
        "",
        f"**{alert['decision']} · {alert['priority']} · {alert['level_state']} · {alert['timeframe']}**",
        "",
    ]
    for item in alert["events"]:
        lines.append(f"- **{item['kind']} / {item['priority']}** — {item['title']}: {item['detail']}")
    lines += [
        "",
        f"Next: {alert.get('next_action')}",
        "",
        f"Dedupe: `{alert['dedupe_key']}`",
        "",
        "_Read-only alert. signals=false · orders=false · can_trade=false · capital_permission=DENY._",
    ]
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
    parser = argparse.ArgumentParser(description="Generate a deduplicable read-only alert from Decision Cockpit state")
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        paths = generate(args.current.resolve(), args.out_dir.resolve(), args.previous.resolve() if args.previous else None)
        payload = read(paths["json"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"result": "PASS", "decision": payload["decision"], "priority": payload["priority"], "outputs": {key: str(value) for key, value in paths.items()}, "can_trade": False, "capital_permission": "DENY"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
