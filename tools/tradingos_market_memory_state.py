from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

RADAR_SCHEMA = "tradingos.market_radar.v1"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def parse_time(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def time_text(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe(payload: dict[str, Any], label: str) -> None:
    safety = payload.get("safety")
    if not isinstance(safety, dict):
        raise ValueError(f"{label}: safety missing")
    if safety.get("can_trade") is not False or safety.get("capital_permission") != "DENY":
        raise ValueError(f"{label}: unsafe trading/capital permission")
    signals = safety.get("signals_allowed", safety.get("signals"))
    orders = safety.get("orders_allowed", safety.get("orders"))
    if signals is not False or orders is not False:
        raise ValueError(f"{label}: signals/orders must be false")


def observed_at(radar: dict[str, Any] | None, cockpit: dict[str, Any] | None, alert: dict[str, Any] | None) -> str:
    candidates: list[Any] = []
    if radar is not None:
        candidates += [radar.get("watchtower_captured_at"), radar.get("liquidity_captured_at")]
    if cockpit is not None:
        candidates.append(cockpit.get("as_of"))
    if alert is not None:
        candidates.append(alert.get("as_of"))
    parsed = [parse_time(str(v)) for v in candidates if isinstance(v, str) and v]
    if not parsed:
        raise ValueError("no observation timestamp available")
    return time_text(max(parsed))


def _symbol_state(row: dict[str, Any]) -> dict[str, Any]:
    liq = row.get("liquidity", {}) if isinstance(row.get("liquidity"), dict) else {}
    tfs = row.get("timeframes", {}) if isinstance(row.get("timeframes"), dict) else {}
    return {
        "bias": str(row.get("bias", "NO_ACTION")),
        "decision_quality": str(row.get("decision_quality", "UNKNOWN")),
        "priority_score": round(float(row.get("priority_score", 0.0)), 4),
        "timeframes": {tf: str(tfs.get(tf, "UNKNOWN")) for tf in ("1h", "4h", "1d")},
        "confluence": row.get("confluence"),
        "watchtower_conflict": row.get("watchtower_conflict"),
        "liquidity": {"quality": str(liq.get("quality", "MISSING")), "state": str(liq.get("state", "MISSING")), "spread_bps": liq.get("spread_bps")},
        "vetoes": sorted({str(x) for x in row.get("vetoes", [])}),
        "notes": sorted({str(x) for x in row.get("notes", [])}),
    }


def extract_state(radar: dict[str, Any] | None = None, cockpit: dict[str, Any] | None = None, alert: dict[str, Any] | None = None) -> dict[str, Any]:
    if radar is None and cockpit is None and alert is None:
        raise ValueError("at least one observation source is required")
    symbols: dict[str, Any] = {}
    top_priority = None
    if radar is not None:
        if radar.get("schema") != RADAR_SCHEMA:
            raise ValueError("unsupported radar schema")
        safe(radar, "radar")
        matrix = radar.get("matrix")
        if not isinstance(matrix, list) or not matrix:
            raise ValueError("radar matrix must be non-empty")
        for row in matrix:
            if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
                raise ValueError("invalid radar matrix row")
            symbol = row["symbol"]
            if symbol in symbols:
                raise ValueError(f"duplicate radar symbol {symbol}")
            symbols[symbol] = _symbol_state(row)
        top_priority = radar.get("top_priority")
    state: dict[str, Any] = {"top_priority": top_priority, "symbols": {s: symbols[s] for s in sorted(symbols)}}
    if cockpit is not None:
        safe(cockpit, "cockpit")
        ex = cockpit.get("executive", {}) if isinstance(cockpit.get("executive"), dict) else {}
        levels = cockpit.get("levels", {}) if isinstance(cockpit.get("levels"), dict) else {}
        quality = cockpit.get("quality", cockpit.get("data_quality", {})); quality = quality if isinstance(quality, dict) else {}
        state["cockpit"] = {
            "symbol": cockpit.get("symbol"), "status": cockpit.get("status"), "stance": ex.get("stance"), "regime": ex.get("regime"),
            "evidence_grade": ex.get("evidence_grade", ex.get("grade")), "score_margin": ex.get("score_margin", ex.get("margin")),
            "next_action": ex.get("next", ex.get("next_action")),
            "levels": {k: levels.get(k) for k in ("last", "support", "resistance", "to_support_pct", "to_resistance_pct")},
            "risk_flags": sorted({str(x.get("label")) for x in cockpit.get("risk_flags", []) if isinstance(x, dict) and x.get("label")}),
            "blockers": sorted({str(x) for x in quality.get("blockers", [])}),
        }
    if alert is not None:
        safe(alert, "alert")
        state["alert"] = {
            "decision": alert.get("decision"), "priority": alert.get("priority"), "level_state": alert.get("level_state"), "dedupe_key": alert.get("dedupe_key"),
            "event_kinds": sorted({str(x.get("kind")) for x in alert.get("events", []) if isinstance(x, dict) and x.get("kind")}),
        }
    return state


def diff_states(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    if previous.get("top_priority") != current.get("top_priority"):
        changes.append({"scope": "market", "field": "top_priority", "from": previous.get("top_priority"), "to": current.get("top_priority")})
    ps, cs = previous.get("symbols", {}), current.get("symbols", {})
    for symbol in sorted(set(ps) | set(cs)):
        p, c = ps.get(symbol), cs.get(symbol)
        if p is None:
            changes.append({"scope": symbol, "field": "asset", "from": "MISSING", "to": "ADDED"}); continue
        if c is None:
            changes.append({"scope": symbol, "field": "asset", "from": "PRESENT", "to": "REMOVED"}); continue
        for field in ("bias", "decision_quality", "watchtower_conflict"):
            if p.get(field) != c.get(field): changes.append({"scope": symbol, "field": field, "from": p.get(field), "to": c.get(field)})
        pscore, cscore = float(p.get("priority_score", 0.0)), float(c.get("priority_score", 0.0))
        if abs(cscore - pscore) >= 5.0: changes.append({"scope": symbol, "field": "priority_score", "from": pscore, "to": cscore, "delta": round(cscore - pscore, 4)})
        for tf in ("1h", "4h", "1d"):
            pv, cv = p.get("timeframes", {}).get(tf), c.get("timeframes", {}).get(tf)
            if pv != cv: changes.append({"scope": symbol, "field": f"timeframe.{tf}", "from": pv, "to": cv})
        for field in ("quality", "state"):
            pv, cv = p.get("liquidity", {}).get(field), c.get("liquidity", {}).get(field)
            if pv != cv: changes.append({"scope": symbol, "field": f"liquidity.{field}", "from": pv, "to": cv})
        for field in ("vetoes", "notes"):
            a, r = sorted(set(c.get(field, [])) - set(p.get(field, []))), sorted(set(p.get(field, [])) - set(c.get(field, [])))
            if a or r: changes.append({"scope": symbol, "field": field, "added": a, "removed": r})
    for section in ("cockpit", "alert"):
        p, c = previous.get(section), current.get(section)
        if p is None and c is not None: changes.append({"scope": section, "field": "section", "from": "MISSING", "to": "ADDED"}); continue
        if p is not None and c is None: changes.append({"scope": section, "field": "section", "from": "PRESENT", "to": "REMOVED"}); continue
        if isinstance(p, dict) and isinstance(c, dict):
            for field in sorted(set(p) | set(c)):
                if p.get(field) != c.get(field): changes.append({"scope": section, "field": field, "from": p.get(field), "to": c.get(field)})
    return {"material_change": bool(changes), "change_count": len(changes), "changes": changes, "summary": "MATERIAL_CHANGE" if changes else "NO_MATERIAL_CHANGE"}
