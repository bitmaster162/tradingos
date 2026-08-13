#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import statistics
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradingos_market_memory_state import canonical, parse_time, safe, sha, time_text

VERSION = "1.0.0"
SCHEMA = "tradingos.value_attribution.record.v1"
REPORT_SCHEMA = "tradingos.value_attribution.report.v1"
GENESIS = "GENESIS"
TERMINAL = {"CONFIRMED", "INVALIDATED", "EXPIRED"}
IGNORE_KINDS = {"BASELINE", "NO_MATERIAL_CHANGE"}


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _record_hash(body: dict[str, Any]) -> str:
    return sha(body)


def verify_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    previous = GENESIS
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or row.get("schema") != SCHEMA:
            raise ValueError(f"ledger line {line_no}: invalid schema")
        if row.get("sequence") != len(records) + 1:
            raise ValueError(f"ledger line {line_no}: non-contiguous sequence")
        if row.get("prev_record_hash") != previous:
            raise ValueError(f"ledger line {line_no}: prev_record_hash mismatch")
        claimed = row.get("record_hash")
        body = dict(row); body.pop("record_hash", None)
        if not isinstance(claimed, str) or _record_hash(body) != claimed:
            raise ValueError(f"ledger line {line_no}: record_hash mismatch")
        parse_time(str(row.get("recorded_at")))
        records.append(row); previous = claimed
    return records


def _append(path: Path, records: list[dict[str, Any]], payload: dict[str, Any], recorded_at: str) -> dict[str, Any]:
    body = {
        "schema": SCHEMA,
        "version": VERSION,
        "sequence": len(records) + 1,
        "recorded_at": recorded_at,
        "prev_record_hash": records[-1]["record_hash"] if records else GENESIS,
        **payload,
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }
    row = dict(body); row["record_hash"] = _record_hash(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    records.append(row)
    return row


def _pressures(cockpit: dict[str, Any], direction: str) -> set[str]:
    return {
        str(x.get("label")) for x in cockpit.get("pressure", cockpit.get("pressure_map", []))
        if isinstance(x, dict) and str(x.get("direction")) == direction and x.get("label")
    }


def _risk_labels(cockpit: dict[str, Any]) -> set[str]:
    return {str(x.get("label")) for x in cockpit.get("risk_flags", []) if isinstance(x, dict) and x.get("label")}


def _blockers(cockpit: dict[str, Any]) -> set[str]:
    quality = cockpit.get("quality", cockpit.get("data_quality", {}))
    return {str(x) for x in quality.get("blockers", [])} if isinstance(quality, dict) else set()


def _context(cockpit: dict[str, Any], alert: dict[str, Any]) -> dict[str, Any]:
    ex, levels = cockpit.get("executive", {}), cockpit.get("levels", {})
    return {
        "symbol": cockpit.get("symbol"),
        "brief_id": cockpit.get("brief_id"),
        "stance": ex.get("stance"),
        "status": cockpit.get("status"),
        "last": levels.get("last"),
        "support": levels.get("support"),
        "resistance": levels.get("resistance"),
        "level_state": alert.get("level_state"),
        "long_pressures": sorted(_pressures(cockpit, "LONG")),
        "short_pressures": sorted(_pressures(cockpit, "SHORT")),
        "risk_flags": sorted(_risk_labels(cockpit)),
        "blockers": sorted(_blockers(cockpit)),
        "next_action": alert.get("next_action"),
    }


def contract(kind: str, context: dict[str, Any], detail: str) -> dict[str, Any]:
    level = str(context.get("level_state"))
    if kind in {"LEVEL_PROXIMITY", "LEVEL_CROSS"} and level in {"LONG_TRIGGER_ZONE", "SHORT_TRIGGER_ZONE"}:
        direction = "LONG" if level.startswith("LONG") else "SHORT"
        return {
            "type": "DIRECTIONAL_TRIGGER_CONFIRMATION", "direction": direction,
            "minimum_evaluation_hours": 4, "expiry_hours": 24,
            "confirmation_requires": ["closed_price_break", "Price/OI alignment", "Spot CVD"],
            "invalidation_requires": ["opposite_stance_or_opening_range_failure"],
        }
    if kind == "STANCE_CHANGE":
        return {"type": "STANCE_PERSISTENCE", "target_stance": context.get("stance"), "minimum_evaluation_hours": 4, "expiry_hours": 24}
    if kind in {"STATUS_BLOCKED", "STATUS_CHANGE", "NEW_BLOCKER", "NEW_RISK_FLAG"}:
        return {"type": "CONDITION_PERSISTENCE", "kind": kind, "detail": detail, "minimum_evaluation_hours": 1, "expiry_hours": 24}
    return {"type": "OBSERVATION_ONLY", "minimum_evaluation_hours": 0, "expiry_hours": 24}


def _event_id(alert: dict[str, Any], event: dict[str, Any], index: int) -> str:
    payload = {
        "symbol": alert.get("symbol"), "brief_id": alert.get("brief_id"), "dedupe_key": alert.get("dedupe_key"),
        "kind": event.get("kind"), "title": event.get("title"), "detail": event.get("detail"), "index": index,
    }
    return sha(payload)[:24]


def _open_events(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    opens = {r["event_id"]: r for r in records if r.get("record_type") == "EVENT_OPEN"}
    resolved = {r["event_id"] for r in records if r.get("record_type") == "EVENT_RESOLUTION"}
    return {k: v for k, v in opens.items() if k not in resolved}


def _evaluate(open_row: dict[str, Any], cockpit: dict[str, Any], observed_at: str) -> tuple[str, dict[str, Any]] | None:
    opened = parse_time(open_row["opened_at"]); now = parse_time(observed_at)
    if now <= opened:
        return None
    hours = (now - opened).total_seconds() / 3600.0
    c = open_row["resolution_contract"]; ctx = open_row["opening_context"]
    min_h, expiry_h = float(c.get("minimum_evaluation_hours", 0)), float(c.get("expiry_hours", 24))
    ex, levels = cockpit.get("executive", {}), cockpit.get("levels", {})
    stance, last = str(ex.get("stance", "NO_ACTION")), float(levels.get("last") or 0)
    evidence = {"elapsed_hours": round(hours, 4), "stance": stance, "last": last}

    if c.get("type") == "DIRECTIONAL_TRIGGER_CONFIRMATION" and hours >= min_h:
        direction = c["direction"]; support = float(ctx.get("support") or 0); resistance = float(ctx.get("resistance") or 0)
        needed = {"Price/OI alignment", "Spot CVD"}; present = _pressures(cockpit, direction)
        evidence.update({"opening_support": support, "opening_resistance": resistance, "confirmation_pressures_present": sorted(present & needed)})
        if direction == "LONG":
            if last > resistance and needed <= present:
                return "CONFIRMED", evidence
            if (support and last < support) or stance == "WATCH_SHORT":
                return "INVALIDATED", evidence
        else:
            if support and last < support and needed <= present:
                return "CONFIRMED", evidence
            if (resistance and last > resistance) or stance == "WATCH_LONG":
                return "INVALIDATED", evidence
    elif c.get("type") == "STANCE_PERSISTENCE" and hours >= min_h:
        evidence["target_stance"] = c.get("target_stance")
        return ("CONFIRMED" if stance == c.get("target_stance") else "INVALIDATED"), evidence
    elif c.get("type") == "CONDITION_PERSISTENCE" and hours >= min_h:
        kind, detail = c.get("kind"), str(c.get("detail", ""))
        present = False
        if kind in {"STATUS_BLOCKED", "STATUS_CHANGE"}: present = str(cockpit.get("status")) != "READY"
        elif kind == "NEW_BLOCKER": present = detail in _blockers(cockpit)
        elif kind == "NEW_RISK_FLAG": present = detail in _risk_labels(cockpit)
        evidence["condition_present"] = present
        return ("CONFIRMED" if present else "INVALIDATED"), evidence
    if hours >= expiry_h:
        return "EXPIRED", evidence
    return None


def process(ledger: Path, cockpit: dict[str, Any], alert: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    safe(cockpit, "cockpit"); safe(alert, "alert")
    records = verify_ledger(ledger); observed_at = str(cockpit.get("as_of") or alert.get("as_of")); parse_time(observed_at)

    # Resolve prior events first, using the current closed observation.
    resolutions: list[dict[str, Any]] = []
    for event_id, open_row in sorted(_open_events(records).items()):
        result = _evaluate(open_row, cockpit, observed_at)
        if result is None: continue
        outcome, evidence = result
        resolutions.append(_append(ledger, records, {
            "record_type": "EVENT_RESOLUTION", "event_id": event_id, "outcome": outcome,
            "opened_at": open_row["opened_at"], "evaluated_at": observed_at,
            "resolution_hours": evidence.get("elapsed_hours"), "evidence": evidence,
        }, observed_at))

    existing_ids = {r.get("event_id") for r in records if r.get("record_type") == "EVENT_OPEN"}
    opened: list[dict[str, Any]] = []
    context = _context(cockpit, alert)
    for index, item in enumerate(alert.get("events", [])):
        if not isinstance(item, dict) or item.get("kind") in IGNORE_KINDS: continue
        event_id = _event_id(alert, item, index)
        if event_id in existing_ids: continue
        row = _append(ledger, records, {
            "record_type": "EVENT_OPEN", "event_id": event_id, "opened_at": observed_at,
            "symbol": alert.get("symbol"), "kind": item.get("kind"), "priority": item.get("priority"),
            "title": item.get("title"), "detail": item.get("detail"), "dedupe_key": alert.get("dedupe_key"),
            "opening_context": context, "resolution_contract": contract(str(item.get("kind")), context, str(item.get("detail", ""))),
            "initial_outcome": "UNRESOLVED",
        }, observed_at)
        opened.append(row); existing_ids.add(event_id)
    return report(records), records


def report(records: list[dict[str, Any]]) -> dict[str, Any]:
    opens = [r for r in records if r.get("record_type") == "EVENT_OPEN"]
    resolutions = [r for r in records if r.get("record_type") == "EVENT_RESOLUTION"]
    by_id = {r["event_id"]: r for r in resolutions}
    outcomes = {k: sum(1 for r in resolutions if r.get("outcome") == k) for k in TERMINAL}
    unresolved = sum(1 for r in opens if r["event_id"] not in by_id)
    directional = [r for r in opens if r.get("resolution_contract", {}).get("type") == "DIRECTIONAL_TRIGGER_CONFIRMATION"]
    dir_res = [by_id[r["event_id"]] for r in directional if r["event_id"] in by_id]
    dir_confirmed = sum(1 for r in dir_res if r.get("outcome") == "CONFIRMED")
    times = [float(r["resolution_hours"]) for r in resolutions if isinstance(r.get("resolution_hours"), (int, float))]
    events = []
    for row in reversed(opens[-50:]):
        resolved = by_id.get(row["event_id"])
        events.append({
            "event_id": row["event_id"], "opened_at": row["opened_at"], "symbol": row.get("symbol"), "kind": row.get("kind"),
            "priority": row.get("priority"), "title": row.get("title"), "outcome": resolved.get("outcome") if resolved else "UNRESOLVED",
            "resolution_hours": resolved.get("resolution_hours") if resolved else None,
            "contract_type": row.get("resolution_contract", {}).get("type"),
        })
    return {
        "schema": REPORT_SCHEMA, "version": VERSION,
        "summary": {"events": len(opens), "unresolved": unresolved, **{k.lower(): v for k, v in outcomes.items()},
                    "median_resolution_hours": round(statistics.median(times), 4) if times else None},
        "directional_proof": {"events": len(directional), "resolved": len(dir_res), "confirmed": dir_confirmed,
                              "confirmation_rate": round(dir_confirmed / len(dir_res), 4) if dir_res else None},
        "events": events,
        "contract": {"pnl_attribution": False, "execution_claims": False, "historical_outcomes_fabricated": False,
                     "terminal_outcomes": ["CONFIRMED", "INVALIDATED", "EXPIRED"], "open_outcome": "UNRESOLVED"},
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def render_html(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    cards = "".join(f'<div class="metric"><small>{html.escape(k.upper())}</small><b>{html.escape(str(v))}</b></div>' for k, v in s.items())
    rows = "".join(
        f'<tr><td>{html.escape(str(x["symbol"]))}</td><td>{html.escape(str(x["kind"]))}</td><td>{html.escape(str(x["priority"]))}</td><td><b>{html.escape(str(x["outcome"]))}</b></td><td>{html.escape(str(x["resolution_hours"] if x["resolution_hours"] is not None else "—"))}</td></tr>'
        for x in payload["events"]
    ) or '<tr><td colspan="5">No attributable events yet.</td></tr>'
    css = '*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1100px;margin:auto;padding:28px}.k{font-size:11px;letter-spacing:.16em;color:#6fdbff;font-weight:800}h1{font-size:46px;margin:5px 0}.sub,small{color:#8fa5b7}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:22px 0}.metric,.panel{background:#0d1823;border:1px solid #263746;border-radius:15px;padding:16px}.metric b{display:block;font-size:24px;margin-top:7px}table{width:100%;border-collapse:collapse}td,th{padding:11px;border-bottom:1px solid #263746;text-align:left}th{color:#8fa5b7;font-size:11px}@media(max-width:760px){.grid{grid-template-columns:repeat(2,1fr)}h1{font-size:36px}}'
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradingOS Value Proof</title><style>{css}</style></head><body><main><div class="k">TRADINGOS · PROOF OF VALUE</div><h1>Event Attribution</h1><div class="sub">Objective outcomes only · no PnL attribution · no execution claims</div><div class="grid">{cards}</div><section class="panel"><table><thead><tr><th>ASSET</th><th>EVENT</th><th>PRIORITY</th><th>OUTCOME</th><th>HOURS</th></tr></thead><tbody>{rows}</tbody></table></section></main></body></html>'


def generate(ledger: Path, out_dir: Path, cockpit_path: Path, alert_path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    payload, _ = process(ledger, read(cockpit_path), read(alert_path)); out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "value_attribution.json", "html": out_dir / "value_attribution.html"}
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    paths["html"].write_text(render_html(payload), encoding="utf-8", newline="\n")
    return payload, paths


def main() -> int:
    p = argparse.ArgumentParser(description="Track objective outcomes for TradingOS attention events without PnL claims")
    p.add_argument("--cockpit", type=Path, required=True); p.add_argument("--alert", type=Path, required=True)
    p.add_argument("--ledger", type=Path, required=True); p.add_argument("--out-dir", type=Path, required=True); a = p.parse_args()
    try: payload, paths = generate(a.ledger.resolve(), a.out_dir.resolve(), a.cockpit.resolve(), a.alert.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2)); return 2
    print(json.dumps({"result": "PASS", "summary": payload["summary"], "outputs": {k: str(v) for k, v in paths.items()}, "can_trade": False, "capital_permission": "DENY"}, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
