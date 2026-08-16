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
from tradingos_market_memory_state import parse_time, sha, time_text
import tradingos_value_attribution as value_attribution

VERSION = "1.2.0"
ATTRIBUTION_SCHEMA = "tradingos.value_attribution.report.v1"
ATTRIBUTION_VERSION = "1.1.1"
LEDGER_SCHEMA = "tradingos.operator_impact.feedback.v1"
REPORT_SCHEMA = "tradingos.operator_impact.report.v1"
GENESIS = "GENESIS"
HASH_HEX = 64
EVENT_ID_HEX = 24
IMPACTS = {"HELPFUL", "IGNORED", "FALSE_ALARM", "CAUSED_REVIEW", "AVOIDED_ACTION"}
POSITIVE = {"HELPFUL", "CAUSED_REVIEW", "AVOIDED_ACTION"}
OUTCOMES = {"UNRESOLVED", "CONFIRMED", "INVALIDATED", "EXPIRED"}
SAFETY = {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"}
ATTRIBUTION_CONTRACT = {
    "market_memory_bound": True,
    "pnl_attribution": False,
    "execution_claims": False,
    "historical_outcomes_fabricated": False,
    "terminal_outcomes": ["CONFIRMED", "INVALIDATED", "EXPIRED"],
    "open_outcome": "UNRESOLVED",
}
FEEDBACK_CONTRACT = {
    "auto_positive_feedback_forbidden": True,
    "hypothetical_pnl_forbidden": True,
    "feedback_is_subjective_operator_input": True,
    "objective_outcome_owned_by_value_attribution": True,
}
REPORT_CONTRACT = {
    "feedback_source": "explicit operator input only",
    "auto_helpful_forbidden": True,
    "objective_outcome_and_subjective_impact_separated": True,
    "pnl_attribution": False,
    "hypothetical_pnl": False,
}
EVENT_PRIORITY = {
    "STATUS_BLOCKED": "CRITICAL",
    "NEW_BLOCKER": "CRITICAL",
    "LEVEL_PROXIMITY": "HIGH",
    "LEVEL_CROSS": "HIGH",
    "STANCE_CHANGE": "HIGH",
    "STATUS_CHANGE": "HIGH",
    "NEW_RISK_FLAG": "MEDIUM",
}
CONTRACT_BY_KIND = {
    "LEVEL_PROXIMITY": "DIRECTIONAL_TRIGGER_CONFIRMATION",
    "LEVEL_CROSS": "DIRECTIONAL_TRIGGER_CONFIRMATION",
    "STANCE_CHANGE": "STANCE_PERSISTENCE",
    "STATUS_BLOCKED": "STATUS_PERSISTENCE",
    "STATUS_CHANGE": "STATUS_PERSISTENCE",
    "NEW_BLOCKER": "BLOCKER_PERSISTENCE",
    "NEW_RISK_FLAG": "RISK_PERSISTENCE",
}
ATTRIBUTION_EVENT_FIELDS = {
    "event_id", "opened_at", "symbol", "timeframe", "kind", "priority", "title", "outcome",
    "resolution_hours", "contract_type", "source_memory_sequence", "source_memory_record_hash",
    "resolution_source_memory_record_hash",
}
EVENT_IDENTITY_FIELDS = {
    "event_id", "opened_at", "symbol", "timeframe", "kind", "priority", "title", "contract_type",
    "source_memory_sequence", "source_memory_record_hash", "attribution_open_record_hash",
}
ROW_FIELDS = {
    "schema", "version", "sequence", "recorded_at", "prev_record_hash", "record_type", "event_id",
    "impact", "note", "source", "event_identity", "event_identity_fingerprint", "contract", "safety",
    "record_hash",
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _hash_hex(value: Any, length: int, label: str) -> str:
    if not isinstance(value, str) or len(value) != length or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{label} must be {length}-character lowercase hex")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{label} must be normalized")
    return value


def _time(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a timestamp string")
    dt = parse_time(value)
    normalized = time_text(dt)
    if value != normalized:
        raise ValueError(f"{label} must be normalized UTC ISO-8601")
    return normalized


def _finite(value: Any, label: str, *, minimum: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"{label} must be finite" + (f" and >= {minimum}" if minimum is not None else ""))
    return number


def _note(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if value != value.strip():
        raise ValueError(f"{label} must be normalized")
    if len(value) > 500:
        raise ValueError(f"{label} must be <= 500 characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{label} contains forbidden control characters")
    return value


def _validate_attribution_event(event: Any, index: int) -> dict[str, Any]:
    label = f"attribution.events[{index}]"
    if not isinstance(event, dict) or set(event) != ATTRIBUTION_EVENT_FIELDS:
        raise ValueError(f"{label} fields mismatch")
    _hash_hex(event.get("event_id"), EVENT_ID_HEX, f"{label}.event_id")
    _time(event.get("opened_at"), f"{label}.opened_at")
    _nonempty(event.get("symbol"), f"{label}.symbol")
    _nonempty(event.get("timeframe"), f"{label}.timeframe")
    kind = event.get("kind")
    if kind not in EVENT_PRIORITY:
        raise ValueError(f"{label}.kind is unsupported")
    if event.get("priority") != EVENT_PRIORITY[kind]:
        raise ValueError(f"{label}.priority does not match kind")
    _nonempty(event.get("title"), f"{label}.title")
    outcome = event.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError(f"{label}.outcome is unsupported")
    if event.get("contract_type") != CONTRACT_BY_KIND[kind]:
        raise ValueError(f"{label}.contract_type does not match kind")
    seq = event.get("source_memory_sequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise ValueError(f"{label}.source_memory_sequence must be a positive integer")
    _hash_hex(event.get("source_memory_record_hash"), HASH_HEX, f"{label}.source_memory_record_hash")
    if outcome == "UNRESOLVED":
        if event.get("resolution_hours") is not None or event.get("resolution_source_memory_record_hash") is not None:
            raise ValueError(f"{label}: unresolved event must not contain resolution provenance")
    else:
        _finite(event.get("resolution_hours"), f"{label}.resolution_hours", minimum=0.0)
        _hash_hex(
            event.get("resolution_source_memory_record_hash"), HASH_HEX,
            f"{label}.resolution_source_memory_record_hash",
        )
    return event


def validate_attribution(attribution: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(attribution, dict):
        raise ValueError("attribution report must be an object")
    expected = {"schema", "version", "summary", "events", "contract", "safety"}
    if set(attribution) != expected:
        raise ValueError("attribution report fields mismatch")
    if attribution.get("schema") != ATTRIBUTION_SCHEMA or attribution.get("version") != ATTRIBUTION_VERSION:
        raise ValueError("unsupported attribution report schema/version")
    if attribution.get("contract") != ATTRIBUTION_CONTRACT:
        raise ValueError("attribution contract mismatch")
    if attribution.get("safety") != SAFETY:
        raise ValueError("unsafe attribution report")
    summary = attribution.get("summary")
    if not isinstance(summary, dict) or set(summary) != {"events", "unresolved", "confirmed", "invalidated", "expired"}:
        raise ValueError("attribution summary fields mismatch")
    for field in ("events", "unresolved", "confirmed", "invalidated", "expired"):
        value = summary.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"attribution summary.{field} must be a non-negative integer")
    events = attribution.get("events")
    if not isinstance(events, list):
        raise ValueError("attribution events must be a list")
    result: dict[str, dict[str, Any]] = {}
    outcome_counts = {"UNRESOLVED": 0, "CONFIRMED": 0, "INVALIDATED": 0, "EXPIRED": 0}
    for index, event in enumerate(events):
        item = _validate_attribution_event(event, index)
        event_id = item["event_id"]
        if event_id in result:
            raise ValueError("attribution contains duplicate event_id")
        result[event_id] = item
        outcome_counts[item["outcome"]] += 1
    if summary["events"] != summary["unresolved"] + summary["confirmed"] + summary["invalidated"] + summary["expired"]:
        raise ValueError("attribution summary outcome totals mismatch")
    expected_visible = min(summary["events"], 100)
    if len(events) != expected_visible:
        raise ValueError("attribution visible event count mismatch")
    if summary["events"] <= 100:
        if summary["unresolved"] != outcome_counts["UNRESOLVED"]:
            raise ValueError("attribution summary.unresolved mismatch")
        for name, outcome in (("confirmed", "CONFIRMED"), ("invalidated", "INVALIDATED"), ("expired", "EXPIRED")):
            if summary[name] != outcome_counts[outcome]:
                raise ValueError(f"attribution summary.{name} mismatch")
    else:
        if outcome_counts["UNRESOLVED"] > summary["unresolved"]:
            raise ValueError("visible unresolved count exceeds attribution summary")
        for name, outcome in (("confirmed", "CONFIRMED"), ("invalidated", "INVALIDATED"), ("expired", "EXPIRED")):
            if outcome_counts[outcome] > summary[name]:
                raise ValueError(f"visible {name} count exceeds attribution summary")
    return result


def _identity_from_open(row: dict[str, Any]) -> tuple[dict[str, Any], str]:
    identity = {
        "event_id": row["event_id"],
        "opened_at": row["opened_at"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "kind": row["kind"],
        "priority": row["priority"],
        "title": row["title"],
        "contract_type": row["resolution_contract"]["type"],
        "source_memory_sequence": row["source_memory_sequence"],
        "source_memory_record_hash": row["source_memory_record_hash"],
        "attribution_open_record_hash": row["record_hash"],
    }
    return identity, sha(identity)


def _attribution_lineage(
    attribution: dict[str, Any], attribution_ledger: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[dict[str, Any], str]], list[dict[str, Any]]]:
    visible = validate_attribution(attribution)
    records = value_attribution.verify_ledger(attribution_ledger)
    expected_report = value_attribution.report(records)
    if attribution != expected_report:
        raise ValueError("attribution report does not match verified attribution ledger")
    lineage: dict[str, tuple[dict[str, Any], str]] = {}
    for row in records:
        if row.get("record_type") != "EVENT_OPEN":
            continue
        identity, fingerprint = _identity_from_open(row)
        _validate_event_identity(identity, f"attribution EVENT_OPEN {row['event_id']}")
        if identity["event_id"] in lineage:
            raise ValueError("verified attribution ledger contains duplicate EVENT_OPEN")
        lineage[identity["event_id"]] = (identity, fingerprint)
    if set(visible) - set(lineage):
        raise ValueError("attribution report references event absent from verified attribution ledger")
    return visible, lineage, records


def _validate_event_identity(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != EVENT_IDENTITY_FIELDS:
        raise ValueError(f"{label} fields mismatch")
    _hash_hex(value.get("event_id"), EVENT_ID_HEX, f"{label}.event_id")
    _time(value.get("opened_at"), f"{label}.opened_at")
    _nonempty(value.get("symbol"), f"{label}.symbol")
    _nonempty(value.get("timeframe"), f"{label}.timeframe")
    kind = value.get("kind")
    if kind not in EVENT_PRIORITY:
        raise ValueError(f"{label}.kind is unsupported")
    if value.get("priority") != EVENT_PRIORITY[kind]:
        raise ValueError(f"{label}.priority does not match kind")
    _nonempty(value.get("title"), f"{label}.title")
    if value.get("contract_type") != CONTRACT_BY_KIND[kind]:
        raise ValueError(f"{label}.contract_type does not match kind")
    seq = value.get("source_memory_sequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise ValueError(f"{label}.source_memory_sequence must be a positive integer")
    _hash_hex(value.get("source_memory_record_hash"), HASH_HEX, f"{label}.source_memory_record_hash")
    _hash_hex(value.get("attribution_open_record_hash"), HASH_HEX, f"{label}.attribution_open_record_hash")
    return value


def _validate_row(row: Any, line_no: int) -> dict[str, Any]:
    label = f"ledger line {line_no}"
    if not isinstance(row, dict) or set(row) != ROW_FIELDS:
        raise ValueError(f"{label}: fields mismatch")
    if row.get("schema") != LEDGER_SCHEMA or row.get("version") != VERSION:
        raise ValueError(f"{label}: invalid schema/version")
    if row.get("record_type") != "OPERATOR_FEEDBACK":
        raise ValueError(f"{label}: invalid record_type")
    sequence = row.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ValueError(f"{label}: invalid sequence")
    _time(row.get("recorded_at"), f"{label}.recorded_at")
    _hash_hex(row.get("event_id"), EVENT_ID_HEX, f"{label}.event_id")
    if row.get("impact") not in IMPACTS:
        raise ValueError(f"{label}: invalid impact")
    _note(row.get("note"), f"{label}.note")
    if row.get("source") != "EXPLICIT_OPERATOR_FEEDBACK":
        raise ValueError(f"{label}: invalid source")
    identity = _validate_event_identity(row.get("event_identity"), f"{label}.event_identity")
    if identity["event_id"] != row["event_id"]:
        raise ValueError(f"{label}: event_id does not match event identity")
    fingerprint = _hash_hex(row.get("event_identity_fingerprint"), HASH_HEX, f"{label}.event_identity_fingerprint")
    if fingerprint != sha(identity):
        raise ValueError(f"{label}: event_identity_fingerprint mismatch")
    if parse_time(row["recorded_at"]) < parse_time(identity["opened_at"]):
        raise ValueError(f"{label}: feedback predates event")
    if row.get("contract") != FEEDBACK_CONTRACT:
        raise ValueError(f"{label}: feedback contract mismatch")
    if row.get("safety") != SAFETY:
        raise ValueError(f"{label}: safety mismatch")
    claimed = _hash_hex(row.get("record_hash"), HASH_HEX, f"{label}.record_hash")
    body = dict(row); body.pop("record_hash")
    if sha(body) != claimed:
        raise ValueError(f"{label}: record_hash mismatch")
    return row


def _verify_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_hash = GENESIS
    previous_time = None
    seen_events: set[str] = set()
    for index, row in enumerate(rows, start=1):
        _validate_row(row, index)
        if row.get("sequence") != index:
            raise ValueError(f"ledger line {index}: non-contiguous sequence")
        if row.get("prev_record_hash") != previous_hash:
            raise ValueError(f"ledger line {index}: prev_record_hash mismatch")
        current_time = parse_time(row["recorded_at"])
        if previous_time is not None and current_time < previous_time:
            raise ValueError(f"ledger line {index}: recorded_at regressed")
        if row["event_id"] in seen_events:
            raise ValueError(f"ledger line {index}: duplicate feedback event_id")
        seen_events.add(row["event_id"])
        previous_hash = row["record_hash"]
        previous_time = current_time
    return rows


def verify_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise ValueError("feedback ledger must end with newline")
    text = raw.decode("utf-8-sig")
    lines = text.splitlines()
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            raise ValueError(f"ledger line {line_no}: blank record")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ledger line {line_no}: invalid JSON") from exc
        rows.append(row)
    return _verify_rows(rows)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _append_durable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        start_size = os.fstat(fd).st_size
        offset = 0
        try:
            while offset < len(payload):
                wrote = os.write(fd, payload[offset:])
                if wrote <= 0:
                    raise OSError("short write made no progress")
                offset += wrote
            os.fsync(fd)
        except BaseException:
            os.ftruncate(fd, start_size)
            os.fsync(fd)
            raise
    finally:
        os.close(fd)
    if not existed:
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _finalize_row(body: dict[str, Any]) -> dict[str, Any]:
    row = dict(body)
    row["record_hash"] = sha(body)
    return row


def record_feedback(
    ledger: Path,
    attribution: dict[str, Any],
    attribution_ledger: Path,
    event_id: str,
    impact: str,
    recorded_at: str,
    note: str = "",
) -> tuple[str, dict[str, Any]]:
    events, lineage, _ = _attribution_lineage(attribution, attribution_ledger)
    _hash_hex(event_id, EVENT_ID_HEX, "event_id")
    if event_id not in events:
        raise ValueError("event_id is not present in the current attribution report")
    impact = impact.upper().strip() if isinstance(impact, str) else impact
    if impact not in IMPACTS:
        raise ValueError(f"impact must be one of: {', '.join(sorted(IMPACTS))}")
    if not isinstance(note, str):
        raise ValueError("note must be a string")
    note = note.strip()
    _note(note, "note")
    recorded_at = time_text(parse_time(recorded_at))
    event = events[event_id]
    if parse_time(recorded_at) < parse_time(event["opened_at"]):
        raise ValueError("feedback recorded_at cannot predate the event")
    identity, fingerprint = lineage[event_id]

    with _exclusive_lock(ledger):
        rows = verify_ledger(ledger)
        prior = next((row for row in rows if row["event_id"] == event_id), None)
        if prior is not None:
            if prior["event_identity"] != identity or prior["event_identity_fingerprint"] != fingerprint:
                raise ValueError("existing feedback event identity conflicts with current attribution")
            if prior["impact"] == impact and prior["note"] == note:
                return "DUPLICATE_SUPPRESSED", prior
            raise ValueError("feedback already exists for event_id; contradictory overwrite is disabled")
        if rows and parse_time(recorded_at) < parse_time(rows[-1]["recorded_at"]):
            raise ValueError("historical feedback backfill is disabled")
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
            "event_identity": identity,
            "event_identity_fingerprint": fingerprint,
            "contract": dict(FEEDBACK_CONTRACT),
            "safety": dict(SAFETY),
        }
        row = _finalize_row(body)
        payload = (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        start_size = ledger.stat().st_size if ledger.exists() else 0
        try:
            _append_durable(ledger, payload)
            verified = verify_ledger(ledger)
            if verified[-1]["record_hash"] != row["record_hash"]:
                raise ValueError("feedback ledger post-append verification mismatch")
        except BaseException:
            if ledger.exists() and ledger.stat().st_size > start_size:
                fd = os.open(ledger, os.O_WRONLY)
                try:
                    os.ftruncate(fd, start_size)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            raise
        return "APPENDED", row


def build_report(
    attribution: dict[str, Any], attribution_ledger: Path, feedback_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    events, lineage, _ = _attribution_lineage(attribution, attribution_ledger)
    rows = _verify_rows([dict(row) for row in feedback_rows])
    feedback_by_event: dict[str, dict[str, Any]] = {}
    historical_feedback_outside_current_view = 0
    for row in rows:
        lineage_item = lineage.get(row["event_id"])
        if lineage_item is None:
            raise ValueError("feedback event is absent from verified attribution history")
        identity, fingerprint = lineage_item
        if row["event_identity"] != identity or row["event_identity_fingerprint"] != fingerprint:
            raise ValueError("feedback immutable event identity no longer matches verified attribution history")
        if row["event_id"] not in events:
            historical_feedback_outside_current_view += 1
            continue
        feedback_by_event[row["event_id"]] = row

    output_rows = []
    counts = {impact.lower(): 0 for impact in sorted(IMPACTS)}
    positive = 0
    resolved_with_feedback = 0
    resolved_events = 0
    for event in attribution["events"]:
        event_id = event["event_id"]
        feedback = feedback_by_event.get(event_id)
        impact = feedback["impact"] if feedback else "NO_FEEDBACK"
        if feedback:
            counts[impact.lower()] += 1
            if impact in POSITIVE:
                positive += 1
        if event["outcome"] != "UNRESOLVED":
            resolved_events += 1
            if feedback:
                resolved_with_feedback += 1
        output_rows.append({
            **event,
            "operator_impact": impact,
            "feedback_recorded_at": feedback["recorded_at"] if feedback else None,
            "operator_note": feedback["note"] if feedback else None,
            "feedback_event_identity_fingerprint": feedback["event_identity_fingerprint"] if feedback else None,
        })

    event_count = len(output_rows)
    feedback_count = len(feedback_by_event)
    summary = {
        "events": event_count,
        "attribution_total_events": attribution["summary"]["events"],
        "feedback_ledger_rows": len(rows),
        "historical_feedback_outside_current_view": historical_feedback_outside_current_view,
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
        "events": output_rows,
        "contract": dict(REPORT_CONTRACT),
        "safety": dict(SAFETY),
    }


def render_html(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    cards = "".join(
        f'<article><small>{html.escape(label)}</small><b>{html.escape(str(value if value is not None else "—"))}</b></article>'
        for label, value in [
            ("EVENTS", s["events"]), ("FEEDBACK", s["feedback_count"]),
            ("COVERAGE", f'{s["feedback_coverage"]*100:.0f}%' if s["feedback_coverage"] is not None else None),
            ("POSITIVE", s["positive_impact_count"]), ("FALSE ALARM", s["false_alarm"]),
            ("NO FEEDBACK", s["no_feedback"]),
        ]
    )
    rows = "".join(
        f'<tr><td>{html.escape(str(x["symbol"]))}</td><td>{html.escape(str(x["timeframe"]))}</td>'
        f'<td>{html.escape(str(x["kind"]))}</td><td>{html.escape(str(x["outcome"]))}</td>'
        f'<td><b>{html.escape(str(x["operator_impact"]))}</b></td><td>{html.escape(str(x["operator_note"] or "—"))}</td></tr>'
        for x in payload["events"]
    ) or '<tr><td colspan="6">No events yet.</td></tr>'
    css = '*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1100px;margin:auto;padding:28px}.k{font-size:11px;letter-spacing:.16em;color:#6fdbff;font-weight:800}h1{font-size:46px;margin:5px 0}.sub,small{color:#8fa5b7}.grid{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:22px 0}article,.panel{background:#0d1823;border:1px solid #263746;border-radius:15px;padding:16px}article b{display:block;font-size:24px;margin-top:7px}table{width:100%;border-collapse:collapse}td,th{padding:11px;border-bottom:1px solid #263746;text-align:left}th{color:#8fa5b7;font-size:11px}@media(max-width:800px){.grid{grid-template-columns:repeat(2,1fr)}h1{font-size:36px}}'
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradingOS Operator Impact</title><style>{css}</style></head><body><main><div class="k">TRADINGOS · OPERATOR IMPACT</div><h1>Decision Impact</h1><div class="sub">Explicit operator feedback only · objective outcomes stay separate · no hypothetical PnL</div><div class="grid">{cards}</div><section class="panel"><table><thead><tr><th>ASSET</th><th>TF</th><th>EVENT</th><th>OUTCOME</th><th>IMPACT</th><th>NOTE</th></tr></thead><tbody>{rows}</tbody></table></section></main></body></html>'


def generate(
    attribution_path: Path, attribution_ledger: Path, feedback_ledger: Path, out_dir: Path
) -> tuple[dict[str, Any], dict[str, Path]]:
    attribution = read_json(attribution_path)
    payload = build_report(attribution, attribution_ledger, verify_ledger(feedback_ledger))
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "operator_impact.json", "html": out_dir / "operator_impact.html"}
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    paths["html"].write_text(render_html(payload), encoding="utf-8", newline="\n")
    return payload, paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Record explicit operator impact while keeping objective Value Attribution outcomes separate")
    parser.add_argument("--attribution", type=Path, required=True)
    parser.add_argument("--attribution-ledger", type=Path, required=True)
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
                args.feedback_ledger.resolve(), attribution, args.attribution_ledger.resolve(),
                args.record_event_id, args.impact, args.recorded_at, args.note
            )
        payload, paths = generate(
            args.attribution.resolve(), args.attribution_ledger.resolve(),
            args.feedback_ledger.resolve(), args.out_dir.resolve()
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False, "capital_permission": "DENY"}, indent=2)); return 2
    print(json.dumps({
        "result": "PASS", "record_status": record_status, "summary": payload["summary"],
        "outputs": {key: str(value) for key, value in paths.items()},
        "can_trade": False, "capital_permission": "DENY",
    }, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
