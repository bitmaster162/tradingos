#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import html
import json
import math
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tradingos_market_memory as memory_tool
from tradingos_market_memory_state import (
    parse_time,
    sha,
    source_identity,
    time_text,
    validate_alert,
    validate_cockpit,
)

VERSION = "1.1.1"
SCHEMA = "tradingos.value_attribution.record.v1"
REPORT_SCHEMA = "tradingos.value_attribution.report.v1"
GENESIS = "GENESIS"
TERMINAL = {"CONFIRMED", "INVALIDATED", "EXPIRED"}
IGNORE_KINDS = {"BASELINE", "NO_MATERIAL_CHANGE"}
RECORD_TYPES = {"EVENT_OPEN", "EVENT_RESOLUTION"}
SAFETY = {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"}
EVENT_ID_HEX = 24
HASH_HEX = 64
REQUIRED_CONFIRMATION_PRESSURES = {"Price/OI alignment", "Spot CVD"}
CANONICAL_EVENT_KINDS = {
    "BASELINE", "NO_MATERIAL_CHANGE", "LEVEL_PROXIMITY", "LEVEL_CROSS", "STANCE_CHANGE",
    "STATUS_BLOCKED", "STATUS_CHANGE", "NEW_BLOCKER", "NEW_RISK_FLAG",
}
CANONICAL_PRIORITIES = {"CRITICAL", "HIGH", "MEDIUM", "INFO"}
CANONICAL_LEVEL_STATES = {"LONG_TRIGGER_ZONE", "SHORT_TRIGGER_ZONE", "NEAR_RESISTANCE", "NEAR_SUPPORT", "MID_RANGE"}
EVENT_PRIORITY = {
    "STATUS_BLOCKED": "CRITICAL",
    "NEW_BLOCKER": "CRITICAL",
    "LEVEL_PROXIMITY": "HIGH",
    "LEVEL_CROSS": "HIGH",
    "STANCE_CHANGE": "HIGH",
    "STATUS_CHANGE": "HIGH",
    "NEW_RISK_FLAG": "MEDIUM",
    "BASELINE": "INFO",
    "NO_MATERIAL_CHANGE": "INFO",
}
PRIORITY_RANK = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "INFO": 0}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _hash_hex(value: Any, length: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise ValueError(f"{label} must be {length}-character lowercase hex")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{label} must be normalized")
    return value


def _finite(value: Any, label: str, *, positive: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        qualifier = "finite and strictly positive" if positive else "finite"
        raise ValueError(f"{label} must be {qualifier}")
    return number


def _validate_pressure(cockpit: dict[str, Any]) -> dict[str, set[str]]:
    pressure = cockpit.get("pressure")
    if not isinstance(pressure, list):
        raise ValueError("cockpit.pressure must be a list")
    by_direction = {"LONG": set(), "SHORT": set()}
    for index, item in enumerate(pressure):
        label = f"cockpit.pressure[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be an object")
        name = item.get("label")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label}.label must be a non-empty string")
        if name != name.strip():
            raise ValueError(f"{label}.label must be normalized")
        direction = item.get("direction")
        if direction not in {"LONG", "SHORT"}:
            raise ValueError(f"{label}.direction must be LONG or SHORT")
        _finite(item.get("strength"), f"{label}.strength")
        observation = item.get("observation")
        if not isinstance(observation, str):
            raise ValueError(f"{label}.observation must be a string")
        by_direction[direction].add(name)
    return by_direction


def _expected_level_state(cockpit_id: dict[str, Any]) -> str:
    levels = cockpit_id["levels"]
    stance = cockpit_id["stance"]
    last = float(levels["last"])
    support = float(levels["support"])
    resistance = float(levels["resistance"])
    overhead = (resistance / last - 1.0) * 100.0
    if 0 <= overhead <= 0.25 and stance == "WATCH_LONG":
        return "LONG_TRIGGER_ZONE"
    if 0 <= overhead <= 0.50:
        return "NEAR_RESISTANCE"
    below = (last / support - 1.0) * 100.0
    if 0 <= below <= 0.50 and stance == "WATCH_SHORT":
        return "SHORT_TRIGGER_ZONE"
    if 0 <= below <= 1.00:
        return "NEAR_SUPPORT"
    return "MID_RANGE"


def _transition_detail(detail: str, label: str, current: str, allowed_previous: set[str] | None = None) -> None:
    if detail.count(" -> ") != 1:
        raise ValueError(f"{label} detail must be '<previous> -> <current>'")
    previous, target = detail.split(" -> ", 1)
    previous = _nonempty(previous, f"{label} previous")
    target = _nonempty(target, f"{label} target")
    if previous == target:
        raise ValueError(f"{label} detail must describe a real transition")
    if target != current:
        raise ValueError(f"{label} target does not match current packet")
    if allowed_previous is not None and previous not in allowed_previous:
        raise ValueError(f"{label} previous value is unsupported")


def _validate_alert_semantics(cockpit_id: dict[str, Any], alert: dict[str, Any], alert_id: dict[str, Any]) -> None:
    decision = alert_id["decision"]
    priority = alert_id["priority"]
    level_state = alert_id["level_state"]
    if decision not in {"SILENT", "NOTIFY"}:
        raise ValueError("alert.decision must be SILENT or NOTIFY")
    if priority not in CANONICAL_PRIORITIES:
        raise ValueError("alert.priority is unsupported")
    if level_state not in CANONICAL_LEVEL_STATES:
        raise ValueError("alert.level_state is unsupported")
    expected_level = _expected_level_state(cockpit_id)
    if level_state != expected_level:
        raise ValueError("alert.level_state does not match current Cockpit")

    expected_dedupe = sha({
        "symbol": cockpit_id["symbol"],
        "timeframe": cockpit_id["timeframe"],
        "status": cockpit_id["status"],
        "stance": cockpit_id["stance"],
        "level_state": level_state,
        "risks": sorted(cockpit_id["risk_labels"]),
        "blockers": sorted(cockpit_id["blockers"]),
    })[:24]
    if alert_id["dedupe_key"] != expected_dedupe:
        raise ValueError("alert.dedupe_key does not match canonical current-state fingerprint")

    events = alert.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("alert.events must contain at least one canonical event")
    material = []
    for index, event in enumerate(events):
        kind = event.get("kind")
        event_priority = event.get("priority")
        if kind not in CANONICAL_EVENT_KINDS:
            raise ValueError(f"alert.events[{index}].kind is unsupported")
        if event_priority != EVENT_PRIORITY[kind]:
            raise ValueError(f"alert.events[{index}].priority does not match canonical event kind")
        detail = _nonempty(event.get("detail"), f"alert.events[{index}].detail")
        if kind not in IGNORE_KINDS:
            material.append(event)
        if kind in {"LEVEL_PROXIMITY", "LEVEL_CROSS"} and level_state not in {"LONG_TRIGGER_ZONE", "SHORT_TRIGGER_ZONE"}:
            raise ValueError(f"alert.events[{index}] trigger event is outside a trigger zone")
        if kind == "STATUS_BLOCKED":
            if cockpit_id["status"] == "READY" or detail != f"status={cockpit_id['status']}":
                raise ValueError(f"alert.events[{index}] STATUS_BLOCKED does not match current status")
        elif kind == "STATUS_CHANGE":
            _transition_detail(detail, f"alert.events[{index}] STATUS_CHANGE", cockpit_id["status"] )
        elif kind == "STANCE_CHANGE":
            _transition_detail(detail, f"alert.events[{index}] STANCE_CHANGE", cockpit_id["stance"] )
        elif kind == "LEVEL_CROSS":
            _transition_detail(detail, f"alert.events[{index}] LEVEL_CROSS", level_state, CANONICAL_LEVEL_STATES)
        elif kind == "NEW_BLOCKER" and detail not in set(cockpit_id["blockers"]):
            raise ValueError(f"alert.events[{index}] NEW_BLOCKER is absent from current Cockpit")
        elif kind == "NEW_RISK_FLAG" and detail not in set(cockpit_id["risk_labels"]):
            raise ValueError(f"alert.events[{index}] NEW_RISK_FLAG is absent from current Cockpit")

    quiet = [event for event in events if event.get("kind") in IGNORE_KINDS]
    if quiet and len(events) != 1:
        raise ValueError("BASELINE/NO_MATERIAL_CHANGE must be the only alert event")
    expected_decision = "NOTIFY" if material else "SILENT"
    if decision != expected_decision:
        raise ValueError("alert.decision is inconsistent with its events")
    expected_priority = max((event["priority"] for event in events), key=lambda item: PRIORITY_RANK[item])
    if priority != expected_priority:
        raise ValueError("alert.priority is inconsistent with its events")

    next_action = alert.get("next_action")
    cockpit_next = cockpit_id.get("next_action")
    if next_action is None:
        normalized_next = None
    else:
        normalized_next = _nonempty(next_action, "alert.next_action")
    if normalized_next != cockpit_next:
        raise ValueError("alert.next_action does not match current Cockpit")


def _packet_context(cockpit: dict[str, Any], alert: dict[str, Any]) -> dict[str, Any]:
    cockpit_id = validate_cockpit(cockpit, "cockpit")
    alert_id = validate_alert(alert, "alert")
    for field in ("brief_id", "symbol", "timeframe", "as_of"):
        if alert_id[field] != cockpit_id[field]:
            raise ValueError(f"cockpit/alert identity mismatch: {field}")
    _validate_alert_semantics(cockpit_id, alert, alert_id)
    pressures = _validate_pressure(cockpit)
    return {
        "brief_id": cockpit_id["brief_id"],
        "symbol": cockpit_id["symbol"],
        "timeframe": cockpit_id["timeframe"],
        "as_of": cockpit_id["as_of"],
        "as_of_dt": cockpit_id["as_of_dt"],
        "status": cockpit_id["status"],
        "stance": cockpit_id["stance"],
        "last": float(cockpit_id["levels"]["last"]),
        "support": float(cockpit_id["levels"]["support"]),
        "resistance": float(cockpit_id["levels"]["resistance"]),
        "level_state": alert_id["level_state"],
        "long_pressures": sorted(pressures["LONG"]),
        "short_pressures": sorted(pressures["SHORT"]),
        "risk_flags": list(cockpit_id["risk_labels"]),
        "blockers": list(cockpit_id["blockers"]),
        "next_action": cockpit_id.get("next_action"),
        "dedupe_key": alert_id["dedupe_key"],
        "alert_event_kinds": list(alert_id["event_kinds"]),
    }


def _memory_tail(
    memory_ledger: Path,
    cockpit: dict[str, Any],
    alert: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    records = memory_tool.verify_ledger(memory_ledger)
    if not records:
        raise ValueError("market memory ledger has no accepted observation")
    identity, identity_fingerprint = source_identity(cockpit, alert)
    tail = records[-1]
    if tail.get("source_identity") != identity:
        raise ValueError("current packets do not match market memory tail source_identity")
    if tail.get("source_identity_fingerprint") != identity_fingerprint:
        raise ValueError("current packets do not match market memory tail source_identity_fingerprint")
    if tail.get("observed_at") != identity["as_of"]:
        raise ValueError("current packets do not match market memory tail observed_at")
    state = tail.get("state")
    if not isinstance(state, dict) or not isinstance(state.get("cockpit"), dict):
        raise ValueError("market memory tail has invalid state")
    if state["cockpit"].get("symbol") != identity["symbol"] or state["cockpit"].get("timeframe") != identity["timeframe"]:
        raise ValueError("market memory tail stream does not match current packets")
    if identity.get("alert_fingerprint") is None:
        raise ValueError("market memory tail must contain the current alert fingerprint")
    record_hash = _hash_hex(tail.get("record_hash"), HASH_HEX, "market memory tail record_hash")
    return tail, identity, record_hash


def _opening_context(context: dict[str, Any], source_memory_record_hash: str) -> dict[str, Any]:
    return {
        "symbol": context["symbol"],
        "timeframe": context["timeframe"],
        "brief_id": context["brief_id"],
        "stance": context["stance"],
        "status": context["status"],
        "last": context["last"],
        "support": context["support"],
        "resistance": context["resistance"],
        "level_state": context["level_state"],
        "long_pressures": list(context["long_pressures"]),
        "short_pressures": list(context["short_pressures"]),
        "risk_flags": list(context["risk_flags"]),
        "blockers": list(context["blockers"]),
        "next_action": context["next_action"],
        "source_memory_record_hash": source_memory_record_hash,
    }


def _validate_context(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    expected = {
        "symbol", "timeframe", "brief_id", "stance", "status", "last", "support", "resistance",
        "level_state", "long_pressures", "short_pressures", "risk_flags", "blockers", "next_action",
        "source_memory_record_hash",
    }
    if set(value) != expected:
        raise ValueError(f"{label} fields mismatch")
    for field in ("symbol", "timeframe", "brief_id", "stance", "status", "level_state"):
        _nonempty(value.get(field), f"{label}.{field}")
    _hash_hex(value.get("source_memory_record_hash"), HASH_HEX, f"{label}.source_memory_record_hash")
    last = _finite(value.get("last"), f"{label}.last", positive=True)
    support = _finite(value.get("support"), f"{label}.support", positive=True)
    resistance = _finite(value.get("resistance"), f"{label}.resistance", positive=True)
    if support >= resistance:
        raise ValueError(f"{label}.support must be lower than resistance")
    for field in ("long_pressures", "short_pressures", "risk_flags", "blockers"):
        items = value.get(field)
        if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
            raise ValueError(f"{label}.{field} must be a list of non-empty strings")
        normalized = sorted({item.strip() for item in items})
        if items != normalized:
            raise ValueError(f"{label}.{field} must be sorted, unique and normalized")
    expected_level = _expected_level_state({
        "stance": value["stance"],
        "levels": {"last": last, "support": support, "resistance": resistance},
    })
    if value["level_state"] != expected_level:
        raise ValueError(f"{label}.level_state does not match opening levels/stance")
    next_action = value.get("next_action")
    if next_action is not None and (not isinstance(next_action, str) or not next_action.strip() or next_action != next_action.strip()):
        raise ValueError(f"{label}.next_action must be normalized non-empty string or null")
    return value


def _event_id(alert: dict[str, Any], event: dict[str, Any], index: int, context: dict[str, Any], source_memory_hash: str) -> str:
    payload = {
        "symbol": context["symbol"],
        "timeframe": context["timeframe"],
        "brief_id": context["brief_id"],
        "opened_at": context["as_of"],
        "source_memory_record_hash": source_memory_hash,
        "dedupe_key": alert["dedupe_key"],
        "kind": event["kind"],
        "index": index,
    }
    return sha(payload)[:EVENT_ID_HEX]


def _resolution_contract(kind: str, context: dict[str, Any], detail: str) -> dict[str, Any]:
    level = context["level_state"]
    if kind in {"LEVEL_PROXIMITY", "LEVEL_CROSS"} and level in {"LONG_TRIGGER_ZONE", "SHORT_TRIGGER_ZONE"}:
        direction = "LONG" if level.startswith("LONG") else "SHORT"
        return {
            "type": "DIRECTIONAL_TRIGGER_CONFIRMATION",
            "direction": direction,
            "minimum_evaluation_hours": 4.0,
            "expiry_hours": 24.0,
            "confirmation_pressures": sorted(REQUIRED_CONFIRMATION_PRESSURES),
        }
    if kind == "STANCE_CHANGE":
        return {
            "type": "STANCE_PERSISTENCE",
            "target_stance": context["stance"],
            "minimum_evaluation_hours": 4.0,
            "expiry_hours": 24.0,
        }
    if kind in {"STATUS_BLOCKED", "STATUS_CHANGE"}:
        return {
            "type": "STATUS_PERSISTENCE",
            "target_status": context["status"],
            "minimum_evaluation_hours": 1.0,
            "expiry_hours": 24.0,
        }
    if kind == "NEW_BLOCKER":
        return {
            "type": "BLOCKER_PERSISTENCE",
            "target_label": _nonempty(detail, "NEW_BLOCKER detail"),
            "minimum_evaluation_hours": 1.0,
            "expiry_hours": 24.0,
        }
    if kind == "NEW_RISK_FLAG":
        return {
            "type": "RISK_PERSISTENCE",
            "target_label": _nonempty(detail, "NEW_RISK_FLAG detail"),
            "minimum_evaluation_hours": 1.0,
            "expiry_hours": 24.0,
        }
    return {"type": "OBSERVATION_ONLY", "minimum_evaluation_hours": 0.0, "expiry_hours": 24.0}


def _validate_contract(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    kind = value.get("type")
    allowed = {
        "DIRECTIONAL_TRIGGER_CONFIRMATION", "STANCE_PERSISTENCE", "STATUS_PERSISTENCE",
        "BLOCKER_PERSISTENCE", "RISK_PERSISTENCE", "OBSERVATION_ONLY",
    }
    if kind not in allowed:
        raise ValueError(f"{label}.type is unsupported")
    expected_fields = {
        "DIRECTIONAL_TRIGGER_CONFIRMATION": {"type", "direction", "minimum_evaluation_hours", "expiry_hours", "confirmation_pressures"},
        "STANCE_PERSISTENCE": {"type", "target_stance", "minimum_evaluation_hours", "expiry_hours"},
        "STATUS_PERSISTENCE": {"type", "target_status", "minimum_evaluation_hours", "expiry_hours"},
        "BLOCKER_PERSISTENCE": {"type", "target_label", "minimum_evaluation_hours", "expiry_hours"},
        "RISK_PERSISTENCE": {"type", "target_label", "minimum_evaluation_hours", "expiry_hours"},
        "OBSERVATION_ONLY": {"type", "minimum_evaluation_hours", "expiry_hours"},
    }[kind]
    if set(value) != expected_fields:
        raise ValueError(f"{label} fields mismatch")
    min_h = _finite(value.get("minimum_evaluation_hours"), f"{label}.minimum_evaluation_hours")
    expiry_h = _finite(value.get("expiry_hours"), f"{label}.expiry_hours")
    if min_h < 0 or expiry_h <= 0 or min_h > expiry_h:
        raise ValueError(f"{label} has invalid evaluation window")
    if kind == "DIRECTIONAL_TRIGGER_CONFIRMATION":
        if value.get("direction") not in {"LONG", "SHORT"}:
            raise ValueError(f"{label}.direction must be LONG or SHORT")
        pressures = value.get("confirmation_pressures")
        if not isinstance(pressures, list) or pressures != sorted(REQUIRED_CONFIRMATION_PRESSURES):
            raise ValueError(f"{label}.confirmation_pressures mismatch")
    elif kind == "STANCE_PERSISTENCE":
        _nonempty(value.get("target_stance"), f"{label}.target_stance")
    elif kind == "STATUS_PERSISTENCE":
        _nonempty(value.get("target_status"), f"{label}.target_status")
    elif kind in {"BLOCKER_PERSISTENCE", "RISK_PERSISTENCE"}:
        _nonempty(value.get("target_label"), f"{label}.target_label")
    return value


def _validate_safety(value: Any, label: str) -> None:
    if value != SAFETY:
        raise ValueError(f"{label} safety mismatch")


def _validate_common_record(row: dict[str, Any], line_no: int) -> tuple[str, str, Any]:
    if row.get("schema") != SCHEMA or row.get("version") != VERSION:
        raise ValueError(f"ledger line {line_no}: invalid record schema/version")
    if row.get("record_type") not in RECORD_TYPES:
        raise ValueError(f"ledger line {line_no}: unsupported record_type")
    observation_id = _hash_hex(row.get("observation_id"), HASH_HEX, f"ledger line {line_no} observation_id")
    source_hash = _hash_hex(row.get("source_memory_record_hash"), HASH_HEX, f"ledger line {line_no} source_memory_record_hash")
    if observation_id != source_hash:
        raise ValueError(f"ledger line {line_no}: observation/source memory hash mismatch")
    if not isinstance(row.get("source_memory_sequence"), int) or isinstance(row.get("source_memory_sequence"), bool) or row["source_memory_sequence"] < 1:
        raise ValueError(f"ledger line {line_no}: invalid source_memory_sequence")
    source_fp = _hash_hex(row.get("source_identity_fingerprint"), HASH_HEX, f"ledger line {line_no} source_identity_fingerprint")
    source_identity_value = row.get("source_identity")
    if not isinstance(source_identity_value, dict):
        raise ValueError(f"ledger line {line_no}: source_identity must be an object")
    expected_identity_fields = {"brief_id", "symbol", "timeframe", "as_of", "cockpit_fingerprint", "alert_fingerprint"}
    if set(source_identity_value) != expected_identity_fields:
        raise ValueError(f"ledger line {line_no}: source_identity fields mismatch")
    for field in ("brief_id", "symbol", "timeframe", "as_of"):
        _nonempty(source_identity_value.get(field), f"ledger line {line_no} source_identity.{field}")
    parse_time(source_identity_value["as_of"])
    _hash_hex(source_identity_value.get("cockpit_fingerprint"), HASH_HEX, f"ledger line {line_no} source_identity.cockpit_fingerprint")
    _hash_hex(source_identity_value.get("alert_fingerprint"), HASH_HEX, f"ledger line {line_no} source_identity.alert_fingerprint")
    if sha(source_identity_value) != source_fp:
        raise ValueError(f"ledger line {line_no}: source_identity_fingerprint mismatch")
    symbol = _nonempty(row.get("symbol"), f"ledger line {line_no} symbol")
    timeframe = _nonempty(row.get("timeframe"), f"ledger line {line_no} timeframe")
    if source_identity_value["symbol"] != symbol or source_identity_value["timeframe"] != timeframe:
        raise ValueError(f"ledger line {line_no}: source_identity stream mismatch")
    recorded_at = row.get("recorded_at")
    if not isinstance(recorded_at, str):
        raise ValueError(f"ledger line {line_no}: invalid recorded_at")
    recorded_dt = parse_time(recorded_at)
    if source_identity_value["as_of"] != recorded_at:
        raise ValueError(f"ledger line {line_no}: source_identity timestamp mismatch")
    _hash_hex(row.get("event_id"), EVENT_ID_HEX, f"ledger line {line_no} event_id")
    _validate_safety(row.get("safety"), f"ledger line {line_no}")
    return symbol, timeframe, recorded_dt


def _validate_open_semantics(row: dict[str, Any], context: dict[str, Any], line_no: int) -> None:
    kind = row["kind"]
    if kind not in CANONICAL_EVENT_KINDS or kind in IGNORE_KINDS:
        raise ValueError(f"ledger line {line_no}: unsupported attributable event kind")
    if row["priority"] != EVENT_PRIORITY[kind]:
        raise ValueError(f"ledger line {line_no}: event priority does not match canonical kind")

    expected_dedupe = sha({
        "symbol": context["symbol"],
        "timeframe": context["timeframe"],
        "status": context["status"],
        "stance": context["stance"],
        "level_state": context["level_state"],
        "risks": context["risk_flags"],
        "blockers": context["blockers"],
    })[:24]
    if row["dedupe_key"] != expected_dedupe:
        raise ValueError(f"ledger line {line_no}: dedupe_key does not match opening context")

    detail = row["detail"]
    if kind in {"LEVEL_PROXIMITY", "LEVEL_CROSS"} and context["level_state"] not in {"LONG_TRIGGER_ZONE", "SHORT_TRIGGER_ZONE"}:
        raise ValueError(f"ledger line {line_no}: trigger event is outside a trigger zone")
    if kind == "STATUS_BLOCKED":
        if context["status"] == "READY" or detail != f"status={context['status']}":
            raise ValueError(f"ledger line {line_no}: STATUS_BLOCKED does not match opening status")
    elif kind == "STATUS_CHANGE":
        _transition_detail(detail, f"ledger line {line_no} STATUS_CHANGE", context["status"])
    elif kind == "STANCE_CHANGE":
        _transition_detail(detail, f"ledger line {line_no} STANCE_CHANGE", context["stance"])
    elif kind == "LEVEL_CROSS":
        _transition_detail(detail, f"ledger line {line_no} LEVEL_CROSS", context["level_state"], CANONICAL_LEVEL_STATES)
    elif kind == "NEW_BLOCKER" and detail not in set(context["blockers"]):
        raise ValueError(f"ledger line {line_no}: NEW_BLOCKER is absent from opening context")
    elif kind == "NEW_RISK_FLAG" and detail not in set(context["risk_flags"]):
        raise ValueError(f"ledger line {line_no}: NEW_RISK_FLAG is absent from opening context")


def _validate_resolution_semantics(open_row: dict[str, Any], row: dict[str, Any], line_no: int) -> None:
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError(f"ledger line {line_no}: evidence must be an object")
    contract = open_row["resolution_contract"]
    opening = open_row["opening_context"]
    kind = contract["type"]
    common = {"elapsed_hours", "stance", "status", "last", "source_memory_record_hash"}
    extras = {
        "DIRECTIONAL_TRIGGER_CONFIRMATION": {"opening_support", "opening_resistance", "confirmation_pressures_present"},
        "STANCE_PERSISTENCE": {"target_stance"},
        "STATUS_PERSISTENCE": {"target_status"},
        "BLOCKER_PERSISTENCE": {"target_label", "condition_present"},
        "RISK_PERSISTENCE": {"target_label", "condition_present"},
        "OBSERVATION_ONLY": set(),
    }[kind]
    if set(evidence) != common | extras:
        raise ValueError(f"ledger line {line_no}: evidence fields mismatch")
    elapsed = _finite(evidence.get("elapsed_hours"), f"ledger line {line_no} evidence.elapsed_hours")
    if abs(elapsed - row["resolution_hours"]) > 1e-9:
        raise ValueError(f"ledger line {line_no}: evidence elapsed_hours mismatch")
    stance = _nonempty(evidence.get("stance"), f"ledger line {line_no} evidence.stance")
    status = _nonempty(evidence.get("status"), f"ledger line {line_no} evidence.status")
    last = _finite(evidence.get("last"), f"ledger line {line_no} evidence.last", positive=True)
    if evidence.get("source_memory_record_hash") != row.get("source_memory_record_hash"):
        raise ValueError(f"ledger line {line_no}: evidence source memory hash mismatch")
    outcome = row["outcome"]
    min_h = float(contract["minimum_evaluation_hours"])
    expiry_h = float(contract["expiry_hours"])
    if elapsed < min_h and outcome != "EXPIRED":
        raise ValueError(f"ledger line {line_no}: resolution before minimum evaluation window")
    if outcome == "EXPIRED" and elapsed < expiry_h:
        raise ValueError(f"ledger line {line_no}: premature expiry")

    expected_nonexpiry: str | None = None
    if kind == "DIRECTIONAL_TRIGGER_CONFIRMATION" and elapsed >= min_h:
        support = _finite(evidence.get("opening_support"), f"ledger line {line_no} evidence.opening_support", positive=True)
        resistance = _finite(evidence.get("opening_resistance"), f"ledger line {line_no} evidence.opening_resistance", positive=True)
        if support != float(opening["support"]) or resistance != float(opening["resistance"]):
            raise ValueError(f"ledger line {line_no}: evidence opening levels mismatch")
        present = evidence.get("confirmation_pressures_present")
        if not isinstance(present, list) or any(not isinstance(item, str) or not item.strip() for item in present):
            raise ValueError(f"ledger line {line_no}: invalid confirmation_pressures_present")
        if present != sorted(set(present)) or not set(present) <= REQUIRED_CONFIRMATION_PRESSURES:
            raise ValueError(f"ledger line {line_no}: non-normalized confirmation_pressures_present")
        needed = set(contract["confirmation_pressures"])
        direction = contract["direction"]
        if direction == "LONG":
            if last > resistance and needed <= set(present):
                expected_nonexpiry = "CONFIRMED"
            elif last < support or stance == "WATCH_SHORT":
                expected_nonexpiry = "INVALIDATED"
        else:
            if last < support and needed <= set(present):
                expected_nonexpiry = "CONFIRMED"
            elif last > resistance or stance == "WATCH_LONG":
                expected_nonexpiry = "INVALIDATED"
    elif kind == "STANCE_PERSISTENCE" and elapsed >= min_h:
        target = _nonempty(evidence.get("target_stance"), f"ledger line {line_no} evidence.target_stance")
        if target != contract["target_stance"]:
            raise ValueError(f"ledger line {line_no}: evidence target_stance mismatch")
        expected_nonexpiry = "CONFIRMED" if stance == target else "INVALIDATED"
    elif kind == "STATUS_PERSISTENCE" and elapsed >= min_h:
        target = _nonempty(evidence.get("target_status"), f"ledger line {line_no} evidence.target_status")
        if target != contract["target_status"]:
            raise ValueError(f"ledger line {line_no}: evidence target_status mismatch")
        expected_nonexpiry = "CONFIRMED" if status == target else "INVALIDATED"
    elif kind in {"BLOCKER_PERSISTENCE", "RISK_PERSISTENCE"} and elapsed >= min_h:
        target = _nonempty(evidence.get("target_label"), f"ledger line {line_no} evidence.target_label")
        if target != contract["target_label"]:
            raise ValueError(f"ledger line {line_no}: evidence target_label mismatch")
        condition = evidence.get("condition_present")
        if not isinstance(condition, bool):
            raise ValueError(f"ledger line {line_no}: evidence condition_present must be boolean")
        expected_nonexpiry = "CONFIRMED" if condition else "INVALIDATED"

    if expected_nonexpiry is not None:
        if outcome != expected_nonexpiry:
            raise ValueError(f"ledger line {line_no}: terminal outcome/evidence mismatch")
    elif outcome != "EXPIRED":
        raise ValueError(f"ledger line {line_no}: terminal outcome is not supported by evidence")


def verify_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise ValueError("attribution ledger must end with a newline")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("attribution ledger must be UTF-8") from exc

    records: list[dict[str, Any]] = []
    previous_hash = GENESIS
    stream: tuple[str, str] | None = None
    last_observation_id: str | None = None
    last_observation_time = None
    last_memory_sequence: int | None = None
    last_source_identity_fingerprint: str | None = None
    last_source_identity: dict[str, Any] | None = None
    completed_observations: set[str] = set()
    opened: dict[str, dict[str, Any]] = {}
    resolved: set[str] = set()

    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"ledger line {line_no}: blank record")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ledger line {line_no}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"ledger line {line_no}: record must be an object")
        record_type = row.get("record_type")
        common_fields = {
            "schema", "version", "sequence", "recorded_at", "prev_record_hash", "record_type",
            "observation_id", "source_memory_sequence", "source_memory_record_hash",
            "source_identity_fingerprint", "source_identity", "symbol", "timeframe", "event_id",
            "safety", "record_hash",
        }
        if record_type == "EVENT_OPEN":
            expected_record_fields = common_fields | {
                "opened_at", "kind", "priority", "title", "detail", "dedupe_key", "event_index",
                "opening_context", "resolution_contract", "initial_outcome",
            }
        elif record_type == "EVENT_RESOLUTION":
            expected_record_fields = common_fields | {
                "opened_at", "evaluated_at", "outcome", "resolution_hours", "evidence",
            }
        else:
            expected_record_fields = common_fields
        if set(row) != expected_record_fields:
            raise ValueError(f"ledger line {line_no}: record fields mismatch")
        if row.get("sequence") != len(records) + 1:
            raise ValueError(f"ledger line {line_no}: non-contiguous sequence")
        if row.get("prev_record_hash") != previous_hash:
            raise ValueError(f"ledger line {line_no}: prev_record_hash mismatch")
        claimed = row.get("record_hash")
        body = dict(row)
        body.pop("record_hash", None)
        if not isinstance(claimed, str) or sha(body) != claimed:
            raise ValueError(f"ledger line {line_no}: record_hash mismatch")

        symbol, timeframe, recorded_dt = _validate_common_record(row, line_no)
        current_stream = (symbol, timeframe)
        if stream is None:
            stream = current_stream
        elif current_stream != stream:
            raise ValueError(f"ledger line {line_no}: ledger stream identity mismatch")

        observation_id = row["observation_id"]
        memory_sequence = row["source_memory_sequence"]
        if last_observation_id is None:
            last_observation_id = observation_id
            last_observation_time = recorded_dt
            last_memory_sequence = memory_sequence
            last_source_identity_fingerprint = row["source_identity_fingerprint"]
            last_source_identity = row["source_identity"]
        elif observation_id == last_observation_id:
            if recorded_dt != last_observation_time:
                raise ValueError(f"ledger line {line_no}: same observation_id has different recorded_at")
            if memory_sequence != last_memory_sequence:
                raise ValueError(f"ledger line {line_no}: same observation_id has different source_memory_sequence")
            if row["source_identity_fingerprint"] != last_source_identity_fingerprint or row["source_identity"] != last_source_identity:
                raise ValueError(f"ledger line {line_no}: same observation_id has inconsistent source_identity")
        else:
            completed_observations.add(last_observation_id)
            if observation_id in completed_observations:
                raise ValueError(f"ledger line {line_no}: observation_id reappeared after completion")
            if recorded_dt <= last_observation_time:
                raise ValueError(f"ledger line {line_no}: observation time is not strictly increasing")
            if memory_sequence <= last_memory_sequence:
                raise ValueError(f"ledger line {line_no}: source_memory_sequence is not strictly increasing")
            last_observation_id = observation_id
            last_observation_time = recorded_dt
            last_memory_sequence = memory_sequence
            last_source_identity_fingerprint = row["source_identity_fingerprint"]
            last_source_identity = row["source_identity"]

        event_id = row["event_id"]
        if row["record_type"] == "EVENT_OPEN":
            if event_id in opened:
                raise ValueError(f"ledger line {line_no}: duplicate EVENT_OPEN")
            if row.get("initial_outcome") != "UNRESOLVED":
                raise ValueError(f"ledger line {line_no}: invalid initial_outcome")
            if row.get("opened_at") != row.get("recorded_at"):
                raise ValueError(f"ledger line {line_no}: opened_at must equal recorded_at")
            for field in ("kind", "priority", "title", "detail", "dedupe_key"):
                _nonempty(row.get(field), f"ledger line {line_no} {field}")
            if not isinstance(row.get("event_index"), int) or isinstance(row.get("event_index"), bool) or row["event_index"] < 0:
                raise ValueError(f"ledger line {line_no}: invalid event_index")
            if len(row["dedupe_key"]) != 24 or any(ch not in "0123456789abcdef" for ch in row["dedupe_key"]):
                raise ValueError(f"ledger line {line_no}: invalid dedupe_key")
            context = _validate_context(row.get("opening_context"), f"ledger line {line_no} opening_context")
            if context["symbol"] != symbol or context["timeframe"] != timeframe:
                raise ValueError(f"ledger line {line_no}: opening context stream mismatch")
            if context["brief_id"] != row["source_identity"]["brief_id"]:
                raise ValueError(f"ledger line {line_no}: opening context brief_id mismatch")
            if context["source_memory_record_hash"] != row["source_memory_record_hash"]:
                raise ValueError(f"ledger line {line_no}: opening context memory hash mismatch")
            _validate_open_semantics(row, context, line_no)
            contract = _validate_contract(row.get("resolution_contract"), f"ledger line {line_no} resolution_contract")
            expected_contract = _resolution_contract(row["kind"], {"level_state": context["level_state"], "stance": context["stance"], "status": context["status"]}, row["detail"])
            if contract != expected_contract:
                raise ValueError(f"ledger line {line_no}: resolution_contract semantic mismatch")
            expected_event_id = sha({
                "symbol": symbol,
                "timeframe": timeframe,
                "brief_id": context["brief_id"],
                "opened_at": row["opened_at"],
                "source_memory_record_hash": row["source_memory_record_hash"],
                "dedupe_key": row["dedupe_key"],
                "kind": row["kind"],
                "index": row["event_index"],
            })[:EVENT_ID_HEX]
            if event_id != expected_event_id:
                raise ValueError(f"ledger line {line_no}: event_id semantic mismatch")
            opened[event_id] = row
        else:
            if event_id not in opened:
                raise ValueError(f"ledger line {line_no}: resolution before EVENT_OPEN")
            if event_id in resolved:
                raise ValueError(f"ledger line {line_no}: duplicate terminal resolution")
            if row.get("outcome") not in TERMINAL:
                raise ValueError(f"ledger line {line_no}: invalid terminal outcome")
            if row.get("opened_at") != opened[event_id].get("opened_at"):
                raise ValueError(f"ledger line {line_no}: resolution opened_at mismatch")
            if row.get("evaluated_at") != row.get("recorded_at"):
                raise ValueError(f"ledger line {line_no}: evaluated_at must equal recorded_at")
            opened_dt = parse_time(row["opened_at"])
            if recorded_dt <= opened_dt:
                raise ValueError(f"ledger line {line_no}: resolution must use a later observation")
            hours = _finite(row.get("resolution_hours"), f"ledger line {line_no} resolution_hours")
            if hours < 0:
                raise ValueError(f"ledger line {line_no}: resolution_hours must be non-negative")
            expected_hours = (recorded_dt - opened_dt).total_seconds() / 3600.0
            if abs(hours - round(expected_hours, 4)) > 1e-9:
                raise ValueError(f"ledger line {line_no}: resolution_hours mismatch")
            _validate_resolution_semantics(opened[event_id], row, line_no)
            resolved.add(event_id)

        records.append(row)
        previous_hash = claimed
    return records


@contextmanager
def _writer_lock(ledger: Path) -> Iterator[None]:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger.with_name(ledger.name + ".lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _append_transaction_durable(path: Path, payload: bytes) -> None:
    if not payload:
        return
    existed = path.exists()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    start_size = os.lseek(fd, 0, os.SEEK_END)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError(f"short attribution write at {offset}/{len(payload)} bytes")
            offset += written
        os.fsync(fd)
    except Exception:
        try:
            os.ftruncate(fd, start_size)
            os.fsync(fd)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)
    if not existed:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _open_events(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    opens = {row["event_id"]: row for row in records if row.get("record_type") == "EVENT_OPEN"}
    resolved = {row["event_id"] for row in records if row.get("record_type") == "EVENT_RESOLUTION"}
    return {event_id: row for event_id, row in opens.items() if event_id not in resolved}


def _evaluate(open_row: dict[str, Any], context: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    opened = parse_time(open_row["opened_at"])
    now = context["as_of_dt"]
    if now <= opened:
        return None
    hours = (now - opened).total_seconds() / 3600.0
    contract = open_row["resolution_contract"]
    opening = open_row["opening_context"]
    min_h = float(contract["minimum_evaluation_hours"])
    expiry_h = float(contract["expiry_hours"])
    evidence: dict[str, Any] = {
        "elapsed_hours": round(hours, 4),
        "stance": context["stance"],
        "status": context["status"],
        "last": context["last"],
        "source_memory_record_hash": context["source_memory_record_hash"],
    }

    if contract["type"] == "DIRECTIONAL_TRIGGER_CONFIRMATION" and hours >= min_h:
        direction = contract["direction"]
        support = float(opening["support"])
        resistance = float(opening["resistance"])
        needed = set(contract["confirmation_pressures"])
        present = set(context["long_pressures"] if direction == "LONG" else context["short_pressures"])
        evidence.update({
            "opening_support": support,
            "opening_resistance": resistance,
            "confirmation_pressures_present": sorted(present & needed),
        })
        if direction == "LONG":
            if context["last"] > resistance and needed <= present:
                return "CONFIRMED", evidence
            if context["last"] < support or context["stance"] == "WATCH_SHORT":
                return "INVALIDATED", evidence
        else:
            if context["last"] < support and needed <= present:
                return "CONFIRMED", evidence
            if context["last"] > resistance or context["stance"] == "WATCH_LONG":
                return "INVALIDATED", evidence
    elif contract["type"] == "STANCE_PERSISTENCE" and hours >= min_h:
        target = contract["target_stance"]
        evidence["target_stance"] = target
        return ("CONFIRMED" if context["stance"] == target else "INVALIDATED"), evidence
    elif contract["type"] == "STATUS_PERSISTENCE" and hours >= min_h:
        target = contract["target_status"]
        evidence["target_status"] = target
        return ("CONFIRMED" if context["status"] == target else "INVALIDATED"), evidence
    elif contract["type"] == "BLOCKER_PERSISTENCE" and hours >= min_h:
        target = contract["target_label"]
        present = target in context["blockers"]
        evidence.update({"target_label": target, "condition_present": present})
        return ("CONFIRMED" if present else "INVALIDATED"), evidence
    elif contract["type"] == "RISK_PERSISTENCE" and hours >= min_h:
        target = contract["target_label"]
        present = target in context["risk_flags"]
        evidence.update({"target_label": target, "condition_present": present})
        return ("CONFIRMED" if present else "INVALIDATED"), evidence

    if hours >= expiry_h:
        return "EXPIRED", evidence
    return None


def _record_body(
    records: list[dict[str, Any]],
    observation: dict[str, Any],
    event_id: str,
    record_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "sequence": len(records) + 1,
        "recorded_at": observation["recorded_at"],
        "prev_record_hash": records[-1]["record_hash"] if records else GENESIS,
        "record_type": record_type,
        "observation_id": observation["observation_id"],
        "source_memory_sequence": observation["source_memory_sequence"],
        "source_memory_record_hash": observation["source_memory_record_hash"],
        "source_identity_fingerprint": observation["source_identity_fingerprint"],
        "source_identity": dict(observation["source_identity"]),
        "symbol": observation["symbol"],
        "timeframe": observation["timeframe"],
        "event_id": event_id,
        **payload,
        "safety": dict(SAFETY),
    }


def _finalize_row(body: dict[str, Any]) -> dict[str, Any]:
    row = dict(body)
    row["record_hash"] = sha(body)
    return row


def process(
    attribution_ledger: Path,
    memory_ledger: Path,
    cockpit: dict[str, Any],
    alert: dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    context = _packet_context(cockpit, alert)
    memory_tail, identity, memory_hash = _memory_tail(memory_ledger, cockpit, alert)
    context["source_memory_record_hash"] = memory_hash
    observation = {
        "observation_id": memory_hash,
        "source_memory_sequence": memory_tail["sequence"],
        "source_memory_record_hash": memory_hash,
        "source_identity_fingerprint": memory_tail["source_identity_fingerprint"],
        "source_identity": dict(memory_tail["source_identity"]),
        "symbol": identity["symbol"],
        "timeframe": identity["timeframe"],
        "recorded_at": memory_tail["observed_at"],
    }

    with _writer_lock(attribution_ledger):
        records = verify_ledger(attribution_ledger)
        if records:
            stream = (records[0]["symbol"], records[0]["timeframe"])
            if stream != (context["symbol"], context["timeframe"]):
                raise ValueError("attribution ledger stream identity mismatch")
            existing_observations = {row["observation_id"] for row in records}
            last_observation_id = records[-1]["observation_id"]
            last_dt = parse_time(records[-1]["recorded_at"])
            current_dt = context["as_of_dt"]
            if memory_hash == last_observation_id:
                if current_dt != last_dt:
                    raise ValueError("duplicate observation timestamp mismatch")
                return "DUPLICATE_OBSERVATION_SUPPRESSED", report(records), records
            if memory_hash in existing_observations:
                raise ValueError("historical attribution observation replay is disabled")
            if current_dt <= last_dt:
                raise ValueError("historical attribution observation is disabled")

        new_rows: list[dict[str, Any]] = []
        working = list(records)
        for event_id, open_row in sorted(_open_events(records).items()):
            result = _evaluate(open_row, context)
            if result is None:
                continue
            outcome, evidence = result
            opened_dt = parse_time(open_row["opened_at"])
            hours = round((context["as_of_dt"] - opened_dt).total_seconds() / 3600.0, 4)
            body = _record_body(
                working,
                observation,
                event_id,
                "EVENT_RESOLUTION",
                {
                    "opened_at": open_row["opened_at"],
                    "evaluated_at": observation["recorded_at"],
                    "outcome": outcome,
                    "resolution_hours": hours,
                    "evidence": evidence,
                },
            )
            row = _finalize_row(body)
            working.append(row)
            new_rows.append(row)

        existing_ids = {row["event_id"] for row in records if row.get("record_type") == "EVENT_OPEN"}
        opening_context = _opening_context(context, memory_hash)
        for index, event in enumerate(alert["events"]):
            if event["kind"] in IGNORE_KINDS:
                continue
            event_id = _event_id(alert, event, index, context, memory_hash)
            if event_id in existing_ids:
                continue
            body = _record_body(
                working,
                observation,
                event_id,
                "EVENT_OPEN",
                {
                    "opened_at": observation["recorded_at"],
                    "kind": event["kind"],
                    "priority": event["priority"],
                    "title": event["title"],
                    "detail": event["detail"],
                    "dedupe_key": alert["dedupe_key"],
                    "event_index": index,
                    "opening_context": opening_context,
                    "resolution_contract": _resolution_contract(event["kind"], context, event["detail"]),
                    "initial_outcome": "UNRESOLVED",
                },
            )
            row = _finalize_row(body)
            working.append(row)
            new_rows.append(row)
            existing_ids.add(event_id)

        if not new_rows:
            return "NO_ATTRIBUTABLE_EVENTS", report(records), records

        payload = b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
            for row in new_rows
        )
        _append_transaction_durable(attribution_ledger, payload)
        verified = verify_ledger(attribution_ledger)
        return "APPENDED", report(verified), verified


def report(records: list[dict[str, Any]]) -> dict[str, Any]:
    opens = [row for row in records if row.get("record_type") == "EVENT_OPEN"]
    resolutions = [row for row in records if row.get("record_type") == "EVENT_RESOLUTION"]
    by_id = {row["event_id"]: row for row in resolutions}
    outcomes = {key: sum(1 for row in resolutions if row.get("outcome") == key) for key in TERMINAL}
    unresolved = sum(1 for row in opens if row["event_id"] not in by_id)
    events = []
    for row in reversed(opens[-100:]):
        resolved = by_id.get(row["event_id"])
        events.append({
            "event_id": row["event_id"],
            "opened_at": row["opened_at"],
            "symbol": row["symbol"],
            "timeframe": row["timeframe"],
            "kind": row["kind"],
            "priority": row["priority"],
            "title": row["title"],
            "outcome": resolved["outcome"] if resolved else "UNRESOLVED",
            "resolution_hours": resolved.get("resolution_hours") if resolved else None,
            "contract_type": row["resolution_contract"]["type"],
            "source_memory_sequence": row["source_memory_sequence"],
            "source_memory_record_hash": row["source_memory_record_hash"],
            "resolution_source_memory_record_hash": resolved.get("source_memory_record_hash") if resolved else None,
        })
    return {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "summary": {
            "events": len(opens),
            "unresolved": unresolved,
            "confirmed": outcomes["CONFIRMED"],
            "invalidated": outcomes["INVALIDATED"],
            "expired": outcomes["EXPIRED"],
        },
        "events": events,
        "contract": {
            "market_memory_bound": True,
            "pnl_attribution": False,
            "execution_claims": False,
            "historical_outcomes_fabricated": False,
            "terminal_outcomes": ["CONFIRMED", "INVALIDATED", "EXPIRED"],
            "open_outcome": "UNRESOLVED",
        },
        "safety": dict(SAFETY),
    }


def render_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    rows = "".join(
        f'<tr><td>{html.escape(str(item["symbol"]))}</td><td>{html.escape(str(item["timeframe"]))}</td>'
        f'<td>{html.escape(str(item["kind"]))}</td><td><b>{html.escape(str(item["outcome"]))}</b></td>'
        f'<td>{html.escape(str(item["source_memory_record_hash"])[:12])}</td></tr>'
        for item in payload["events"]
    ) or '<tr><td colspan="5">No attributable events yet.</td></tr>'
    css = "body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1100px;margin:auto;padding:28px}small{color:#8fa5b7}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #263746;text-align:left}.panel{background:#0d1823;border:1px solid #263746;border-radius:16px;padding:18px}"
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>TradingOS Value Attribution</title><style>{css}</style></head><body><main>'
        '<h1>Memory-Bound Value Attribution</h1>'
        f'<p><small>Events {summary["events"]} · unresolved {summary["unresolved"]} · confirmed {summary["confirmed"]} · invalidated {summary["invalidated"]} · expired {summary["expired"]}</small></p>'
        f'<section class="panel"><table><thead><tr><th>ASSET</th><th>TF</th><th>EVENT</th><th>OUTCOME</th><th>MEMORY</th></tr></thead><tbody>{rows}</tbody></table></section>'
        '<p><small>Objective outcomes only · memory-bound · no PnL · signals=false · orders=false · can_trade=false · capital_permission=DENY</small></p>'
        '</main></body></html>'
    )


def generate(
    memory_ledger: Path,
    attribution_ledger: Path,
    cockpit_path: Path,
    alert_path: Path,
    out_dir: Path,
) -> tuple[str, dict[str, Any], dict[str, Path]]:
    cockpit = read_json(cockpit_path)
    alert = read_json(alert_path)
    status, payload, _ = process(attribution_ledger, memory_ledger, cockpit, alert)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "value_attribution.json", "html": out_dir / "value_attribution.html"}
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    paths["html"].write_text(render_html(payload), encoding="utf-8", newline="\n")
    return status, payload, paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Market-Memory-bound objective Value Attribution")
    parser.add_argument("--memory-ledger", type=Path, required=True)
    parser.add_argument("--attribution-ledger", type=Path, required=True)
    parser.add_argument("--cockpit", type=Path, required=True)
    parser.add_argument("--alert", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        status, payload, paths = generate(
            args.memory_ledger.resolve(),
            args.attribution_ledger.resolve(),
            args.cockpit.resolve(),
            args.alert.resolve(),
            args.out_dir.resolve(),
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({
        "result": "PASS",
        "process_status": status,
        "summary": payload["summary"],
        "outputs": {key: str(value) for key, value in paths.items()},
        "can_trade": False,
        "capital_permission": "DENY",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
