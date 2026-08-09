#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradingos_market_memory_state import parse_time, sha

VERSION = "1.0.0"
LEDGER_SCHEMA = "tradingos.operator_impact.feedback.v1"
REPORT_SCHEMA = "tradingos.operator_impact.report.v1"
GENESIS = "GENESIS"
IMPACTS = {"HELPFUL", "IGNORED", "FALSE_ALARM", "CAUSED_REVIEW", "AVOIDED_ACTION"}
POSITIVE = {"HELPFUL", "CAUSED_REVIEW", "AVOIDED_ACTION"}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def verify_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    previous = GENESIS
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or row.get("schema") != LEDGER_SCHEMA:
            raise ValueError(f"ledger line {line_no}: invalid schema")
        if row.get("sequence") != len(rows) + 1:
            raise ValueError(f"ledger line {line_no}: non-contiguous sequence")
        if row.get("prev_record_hash") != previous:
            raise ValueError(f"ledger line {line_no}: prev_record_hash mismatch")
        claimed = row.get("record_hash")
        body = dict(row); body.pop("record_hash", None)
        if not isinstance(claimed, str) or sha(body) != claimed:
            raise ValueError(f"ledger line {line_no}: record_hash mismatch")
        parse_time(str(row.get("recorded_at")))
        if row.get("impact") not in IMPACTS:
            raise ValueError(f"ledger line {line_no}: invalid impact")
        if not isinstance(row.get("event_id"), str) or not row["event_id"]:
            raise ValueError(f"ledger line {line_no}: invalid event_id")
        rows.append(row); previous = claimed
    return rows


def _events(attribution: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if attribution.get("schema") != "tradingos.value_attribution.report.v1":
        raise ValueError("unsupported attribution report schema")
    events = attribution.get("events")
    if not isinstance(events, list):
        raise ValueError("attribution events must be a list")
    result: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
            raise ValueError("attribution contains invalid event")
        result[event["event_id"]] = event
    return result


def record_feedback(
    ledger: Path,
    attribution: dict[str, Any],
    event_id: str,
    impact: str,
    recorded_at: str,
    note: str = "",
) -> tuple[str, dict[str, Any]]:
    impact = impact.upper().strip()
    if impact not in IMPACTS:
        raise ValueError(f"impact must be one of: {', '.join(sorted(IMPACTS))}")
    parse_time(recorded_at)
    note = note.strip()
    if len(note) > 500:
        raise ValueError("note must be <= 500 characters")
    events = _events(attribution)
    if event_id not in events:
        raise ValueError("event_id is not present in the attribution report")
    rows = verify_ledger(ledger)
    prior = [r for r in rows if r.get("event_id") == event_id]
    if prior:
        last = prior[-1]
        if last.get("impact") == impact and last.get("note", "") == note:
            return "DUPLICATE_SUPPRESSED", last
        raise ValueError("feedback already exists for event_id; contradictory overwrite is disabled")
    event = events[event_id]
    body = {
        "schema": LEDGER_SCHEMA,
        "version": VERSION,
        "sequence": len(rows) + 1,
        "recorded_at": recorded_at,
        "prev_record_hash": rows[-1]["record_hash"] if rows else GENESIS,
        "record_type": "OPERATOR_FEEDBACK",
        "event_id": event_id,
        "impact": impact,
        "note": note,
        "source": "EXPLICIT_OPERATOR_FEEDBACK",
        "event_snapshot": {
            "symbol": event.get("symbol"),
            "kind": event.get("kind"),
            "priority": event.get("priority"),
            "outcome": event.get("outcome"),
            "opened_at": event.get("opened_at"),
        },
        "contract": {
            "auto_positive_feedback_forbidden": True,
            "hypothetical_pnl_forbidden": True,
            "feedback_is_subjective_operator_input": True,
        },
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }
    row = dict(body); row["record_hash"] = sha(body)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return "APPENDED", row


def build_report(attribution: dict[str, Any], feedback_rows: list[dict[str, Any]]) -> dict[str, Any]:
    events = _events(attribution)
    feedback_by_event = {row["event_id"]: row for row in feedback_rows}
    rows = []
    counts = {impact.lower(): 0 for impact in sorted(IMPACTS)}
    positive = 0
    resolved_with_feedback = 0
    resolved_events = 0
    for event_id, event in events.items():
        fb = feedback_by_event.get(event_id)
        impact = fb["impact"] if fb else "NO_FEEDBACK"
        if fb:
            counts[impact.lower()] += 1
            if impact in POSITIVE:
                positive += 1
        if event.get("outcome") != "UNRESOLVED":
            resolved_events += 1
            if fb:
                resolved_with_feedback += 1
        rows.append({
            **event,
            "operator_impact": impact,
            "feedback_recorded_at": fb.get("recorded_at") if fb else None,
            "operator_note": fb.get("note") if fb else None,
        })
    event_count = len(events)
    feedback_count = len([x for x in rows if x["operator_impact"] != "NO_FEEDBACK"])
    summary = {
        "events": event_count,
        "feedback_count": feedback_count,
        "feedback_coverage": round(feedback_count / event_count, 4) if event_count else None,
        "positive_impact_count": positive,
        "positive_impact_rate": round(positive / feedback_count, 4) if feedback_count else None,
        "resolved_events": resolved_events,
        "resolved_with_feedback": resolved_with_feedback,
        "resolved_feedback_coverage": round(resolved_with_feedback / resolved_events, 4) if resolved_events else None,
        **counts,
        "no_feedback": event_count - feedback_count,
    }
    return {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "summary": summary,
        "events": rows,
        "contract": {
            "feedback_source": "explicit operator input only",
            "auto_helpful_forbidden": True,
            "objective_outcome_and_subjective_impact_separated": True,
            "pnl_attribution": False,
            "hypothetical_pnl": False,
        },
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def render_html(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    cards = "".join(
        f'<article><small>{html.escape(label)}</small><b>{html.escape(str(value if value is not None else "—"))}</b></article>'
        for label, value in [
            ("EVENTS", s["events"]), ("FEEDBACK", s["feedback_count"]),
            ("COVERAGE", f'{s["feedback_coverage"]*100:.0f}%' if s["feedback_coverage"] is not None else None),
            ("POSITIVE", s["positive_impact_count"]),
            ("FALSE ALARM", s["false_alarm"]),
            ("NO FEEDBACK", s["no_feedback"]),
        ]
    )
    rows = "".join(
        f'<tr><td>{html.escape(str(x.get("symbol")))}</td><td>{html.escape(str(x.get("kind")))}</td><td>{html.escape(str(x.get("outcome")))}</td><td><b>{html.escape(str(x.get("operator_impact")))}</b></td><td>{html.escape(str(x.get("operator_note") or "—"))}</td></tr>'
        for x in payload["events"]
    ) or '<tr><td colspan="5">No events yet.</td></tr>'
    css = '*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1100px;margin:auto;padding:28px}.k{font-size:11px;letter-spacing:.16em;color:#6fdbff;font-weight:800}h1{font-size:46px;margin:5px 0}.sub,small{color:#8fa5b7}.grid{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:22px 0}article,.panel{background:#0d1823;border:1px solid #263746;border-radius:15px;padding:16px}article b{display:block;font-size:24px;margin-top:7px}table{width:100%;border-collapse:collapse}td,th{padding:11px;border-bottom:1px solid #263746;text-align:left}th{color:#8fa5b7;font-size:11px}@media(max-width:800px){.grid{grid-template-columns:repeat(2,1fr)}h1{font-size:36px}}'
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradingOS Operator Impact</title><style>{css}</style></head><body><main><div class="k">TRADINGOS · OPERATOR IMPACT</div><h1>Decision Impact</h1><div class="sub">Explicit operator feedback only · objective outcomes stay separate · no hypothetical PnL</div><div class="grid">{cards}</div><section class="panel"><table><thead><tr><th>ASSET</th><th>EVENT</th><th>OUTCOME</th><th>IMPACT</th><th>NOTE</th></tr></thead><tbody>{rows}</tbody></table></section></main></body></html>'


def generate(attribution_path: Path, feedback_ledger: Path, out_dir: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    attribution = read_json(attribution_path)
    payload = build_report(attribution, verify_ledger(feedback_ledger))
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "operator_impact.json", "html": out_dir / "operator_impact.html"}
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    paths["html"].write_text(render_html(payload), encoding="utf-8", newline="\n")
    return payload, paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Record explicit operator impact and report decision-product usefulness without PnL claims")
    parser.add_argument("--attribution", type=Path, required=True)
    parser.add_argument("--feedback-ledger", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--record-event-id")
    parser.add_argument("--impact", choices=sorted(IMPACTS))
    parser.add_argument("--recorded-at")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    try:
        attribution = read_json(args.attribution.resolve())
        record_status = None
        if any([args.record_event_id, args.impact, args.recorded_at]):
            if not all([args.record_event_id, args.impact, args.recorded_at]):
                raise ValueError("record-event-id, impact, and recorded-at are required together")
            record_status, _ = record_feedback(
                args.feedback_ledger.resolve(), attribution, args.record_event_id, args.impact, args.recorded_at, args.note
            )
        payload, paths = generate(args.attribution.resolve(), args.feedback_ledger.resolve(), args.out_dir.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2)); return 2
    print(json.dumps({"result": "PASS", "record_status": record_status, "summary": payload["summary"], "outputs": {k: str(v) for k, v in paths.items()}, "can_trade": False, "capital_permission": "DENY"}, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
