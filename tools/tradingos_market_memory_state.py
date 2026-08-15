from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

COCKPIT_SCHEMA = "tradingos.decision_cockpit.v1"
ALERT_SCHEMA = "tradingos.decision_alert.v1"
IDENTITY_FIELDS = ("brief_id", "symbol", "timeframe", "as_of")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def time_text(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _nonempty_str(payload: dict[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: {key} must be a non-empty string")
    return value.strip()


def _safe(payload: dict[str, Any], label: str) -> None:
    safety = payload.get("safety")
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
        raise ValueError(f"{label}: unsafe permission")


def _validate_levels(levels: Any, label: str) -> dict[str, float | None]:
    if not isinstance(levels, dict):
        raise ValueError(f"{label}: levels must be an object")
    normalized: dict[str, float | None] = {}
    for field in ("last", "support", "resistance"):
        value = levels.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{label}: levels.{field} must be numeric")
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{label}: levels.{field} must be finite and strictly positive")
        normalized[field] = number
    if normalized["support"] >= normalized["resistance"]:
        raise ValueError(f"{label}: levels.support must be lower than levels.resistance")
    for field in ("to_support_pct", "to_resistance_pct"):
        value = levels.get(field)
        if value is None:
            normalized[field] = None
        elif not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError(f"{label}: levels.{field} must be finite numeric or null")
        else:
            normalized[field] = float(value)
    return normalized


def validate_cockpit(cockpit: dict[str, Any], label: str = "cockpit") -> dict[str, Any]:
    if not isinstance(cockpit, dict):
        raise ValueError(f"{label}: payload must be an object")
    if cockpit.get("schema") != COCKPIT_SCHEMA:
        raise ValueError(f"{label}: unsupported cockpit schema")
    _safe(cockpit, label)

    identity = {field: _nonempty_str(cockpit, field, label) for field in IDENTITY_FIELDS}
    identity["as_of_dt"] = parse_time(identity["as_of"])
    status = _nonempty_str(cockpit, "status", label)

    executive = cockpit.get("executive")
    if not isinstance(executive, dict):
        raise ValueError(f"{label}: executive must be an object")
    stance = _nonempty_str(executive, "stance", f"{label}.executive")
    optional_text: dict[str, str | None] = {}
    for field in ("regime", "grade", "next"):
        value = executive.get(field)
        if value is None:
            optional_text[field] = None
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label}.executive.{field} must be a non-empty string or null")
        else:
            optional_text[field] = value.strip()
    margin = executive.get("margin")
    if margin is None:
        normalized_margin = None
    elif (
        not isinstance(margin, (int, float))
        or isinstance(margin, bool)
        or not math.isfinite(float(margin))
    ):
        raise ValueError(f"{label}.executive.margin must be finite numeric or null")
    else:
        normalized_margin = float(margin)

    levels = _validate_levels(cockpit.get("levels"), label)

    risk_flags = cockpit.get("risk_flags")
    if not isinstance(risk_flags, list):
        raise ValueError(f"{label}: risk_flags must be a list")
    risk_labels: list[str] = []
    for item in risk_flags:
        if not isinstance(item, dict):
            raise ValueError(f"{label}: risk_flags entries must be objects")
        risk_labels.append(_nonempty_str(item, "label", f"{label}.risk_flag"))

    quality = cockpit.get("quality")
    if not isinstance(quality, dict):
        raise ValueError(f"{label}: quality must be an object")
    blockers = quality.get("blockers")
    if not isinstance(blockers, list):
        raise ValueError(f"{label}: quality.blockers must be a list")
    normalized_blockers: list[str] = []
    for item in blockers:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label}: quality.blockers entries must be non-empty strings")
        normalized_blockers.append(item.strip())

    identity.update(
        {
            "status": status,
            "stance": stance,
            "regime": optional_text["regime"],
            "evidence_grade": optional_text["grade"],
            "score_margin": normalized_margin,
            "next_action": optional_text["next"],
            "levels": levels,
            "risk_labels": sorted(set(risk_labels)),
            "blockers": sorted(set(normalized_blockers)),
        }
    )
    return identity


def validate_alert(alert: dict[str, Any], label: str = "alert") -> dict[str, Any]:
    if not isinstance(alert, dict):
        raise ValueError(f"{label}: payload must be an object")
    if alert.get("schema") != ALERT_SCHEMA:
        raise ValueError(f"{label}: unsupported alert schema")
    _safe(alert, label)

    identity = {field: _nonempty_str(alert, field, label) for field in IDENTITY_FIELDS}
    identity["as_of_dt"] = parse_time(identity["as_of"])
    identity["decision"] = _nonempty_str(alert, "decision", label)
    identity["priority"] = _nonempty_str(alert, "priority", label)
    identity["level_state"] = _nonempty_str(alert, "level_state", label)
    identity["dedupe_key"] = _nonempty_str(alert, "dedupe_key", label)
    if len(identity["dedupe_key"]) != 24 or any(ch not in "0123456789abcdef" for ch in identity["dedupe_key"]):
        raise ValueError(f"{label}: dedupe_key must be 24-character lowercase hex")

    events = alert.get("events")
    if not isinstance(events, list):
        raise ValueError(f"{label}: events must be a list")
    kinds: list[str] = []
    for item in events:
        if not isinstance(item, dict):
            raise ValueError(f"{label}: events entries must be objects")
        kinds.append(_nonempty_str(item, "kind", f"{label}.event"))
        for field in ("priority", "title", "detail"):
            _nonempty_str(item, field, f"{label}.event")
    identity["event_kinds"] = sorted(set(kinds))
    return identity



def _hash_hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{label} must be a 64-character lowercase SHA-256 hex string")
    return value


def validate_persisted_state(state: Any, label: str = "state") -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError(f"{label} must be an object")
    if set(state) - {"cockpit", "alert"}:
        raise ValueError(f"{label} contains unsupported sections")
    cockpit = state.get("cockpit")
    if not isinstance(cockpit, dict):
        raise ValueError(f"{label}.cockpit must be an object")

    for field in ("symbol", "timeframe", "status", "stance"):
        normalized = _nonempty_str(cockpit, field, f"{label}.cockpit")
        if cockpit[field] != normalized:
            raise ValueError(f"{label}.cockpit.{field} must be normalized")
    for field in ("regime", "evidence_grade", "next_action"):
        value = cockpit.get(field)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label}.cockpit.{field} must be non-empty string or null")
            if value != value.strip():
                raise ValueError(f"{label}.cockpit.{field} must be normalized")
    margin = cockpit.get("score_margin")
    if margin is not None and (
        not isinstance(margin, (int, float))
        or isinstance(margin, bool)
        or not math.isfinite(float(margin))
    ):
        raise ValueError(f"{label}.cockpit.score_margin must be finite numeric or null")
    _validate_levels(cockpit.get("levels"), f"{label}.cockpit")
    for field in ("risk_flags", "blockers"):
        values = cockpit.get(field)
        if not isinstance(values, list):
            raise ValueError(f"{label}.cockpit.{field} must be a list")
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError(f"{label}.cockpit.{field} entries must be non-empty strings")
        normalized_values = sorted({item.strip() for item in values})
        if values != normalized_values:
            raise ValueError(f"{label}.cockpit.{field} must be sorted, unique and normalized")

    if "alert" in state:
        alert = state["alert"]
        if not isinstance(alert, dict):
            raise ValueError(f"{label}.alert must be an object")
        for field in ("decision", "priority", "level_state", "dedupe_key"):
            normalized = _nonempty_str(alert, field, f"{label}.alert")
            if alert[field] != normalized:
                raise ValueError(f"{label}.alert.{field} must be normalized")
        if len(alert["dedupe_key"]) != 24 or any(ch not in "0123456789abcdef" for ch in alert["dedupe_key"]):
            raise ValueError(f"{label}.alert.dedupe_key must be 24-character lowercase hex")
        kinds = alert.get("event_kinds")
        if not isinstance(kinds, list):
            raise ValueError(f"{label}.alert.event_kinds must be a list")
        if any(not isinstance(item, str) or not item.strip() for item in kinds):
            raise ValueError(f"{label}.alert.event_kinds entries must be non-empty strings")
        normalized_kinds = sorted({item.strip() for item in kinds})
        if kinds != normalized_kinds:
            raise ValueError(f"{label}.alert.event_kinds must be sorted, unique and normalized")
    return state

def source_identity(cockpit: dict[str, Any], alert: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    cockpit_id = validate_cockpit(cockpit, "cockpit")
    alert_fingerprint = None
    if alert is not None:
        alert_id = validate_alert(alert, "alert")
        for field in IDENTITY_FIELDS:
            if alert_id[field] != cockpit_id[field]:
                raise ValueError(f"cockpit/alert identity mismatch: {field}")
        alert_fingerprint = sha(alert)
    identity = {
        "brief_id": cockpit_id["brief_id"],
        "symbol": cockpit_id["symbol"],
        "timeframe": cockpit_id["timeframe"],
        "as_of": cockpit_id["as_of"],
        "cockpit_fingerprint": sha(cockpit),
        "alert_fingerprint": alert_fingerprint,
    }
    return identity, sha(identity)


def observed_at(cockpit: dict[str, Any], alert: dict[str, Any] | None = None) -> str:
    identity, _ = source_identity(cockpit, alert)
    return identity["as_of"]


def extract_state(cockpit: dict[str, Any], alert: dict[str, Any] | None = None) -> dict[str, Any]:
    cockpit_id = validate_cockpit(cockpit, "cockpit")
    executive = cockpit["executive"]
    levels = cockpit_id["levels"]
    state: dict[str, Any] = {
        "cockpit": {
            "symbol": cockpit_id["symbol"],
            "timeframe": cockpit_id["timeframe"],
            "status": cockpit_id["status"],
            "stance": cockpit_id["stance"],
            "regime": cockpit_id["regime"],
            "evidence_grade": cockpit_id["evidence_grade"],
            "score_margin": cockpit_id["score_margin"],
            "next_action": cockpit_id["next_action"],
            "levels": levels,
            "risk_flags": cockpit_id["risk_labels"],
            "blockers": cockpit_id["blockers"],
        }
    }
    if alert is not None:
        alert_id = validate_alert(alert, "alert")
        for field in IDENTITY_FIELDS:
            if alert_id[field] != cockpit_id[field]:
                raise ValueError(f"cockpit/alert identity mismatch: {field}")
        state["alert"] = {
            "decision": alert_id["decision"],
            "priority": alert_id["priority"],
            "level_state": alert_id["level_state"],
            "dedupe_key": alert_id["dedupe_key"],
            "event_kinds": alert_id["event_kinds"],
        }
    return state


def diff_states(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise ValueError("states must be objects")
    changes: list[dict[str, Any]] = []
    for section in ("cockpit", "alert"):
        p = previous.get(section)
        c = current.get(section)
        if p is None and c is not None:
            changes.append({"scope": section, "field": "section", "from": "MISSING", "to": "ADDED"})
            continue
        if p is not None and c is None:
            changes.append({"scope": section, "field": "section", "from": "PRESENT", "to": "REMOVED"})
            continue
        if isinstance(p, dict) and isinstance(c, dict):
            for field in sorted(set(p) | set(c)):
                if p.get(field) != c.get(field):
                    changes.append({"scope": section, "field": field, "from": p.get(field), "to": c.get(field)})
        elif p != c:
            changes.append({"scope": section, "field": "value", "from": p, "to": c})
    return {
        "material_change": bool(changes),
        "change_count": len(changes),
        "changes": changes,
        "summary": "MATERIAL_CHANGE" if changes else "NO_MATERIAL_CHANGE",
    }
