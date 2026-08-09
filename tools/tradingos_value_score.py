#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tradingos_operator_impact as impact_tool
from tradingos_market_memory_state import parse_time, time_text

VERSION = "1.0.0"
SCHEMA = "tradingos.value_score.report.v1"
WINDOWS = (("7d", timedelta(days=7)), ("30d", timedelta(days=30)))
MIN_EVENTS = 3
MIN_FEEDBACK = 2
MIN_RESOLVED = 2
POSITIVE = {"HELPFUL", "CAUSED_REVIEW", "AVOIDED_ACTION"}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _events(attribution: dict[str, Any]) -> list[dict[str, Any]]:
    if attribution.get("schema") != "tradingos.value_attribution.report.v1":
        raise ValueError("unsupported attribution report schema")
    events = attribution.get("events")
    if not isinstance(events, list):
        raise ValueError("attribution events must be a list")
    result = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
            raise ValueError("attribution contains invalid event")
        parse_time(str(event.get("opened_at")))
        result.append(event)
    return result


def _window(events: list[dict[str, Any]], feedback_rows: list[dict[str, Any]], as_of: str, delta: timedelta) -> dict[str, Any]:
    now = parse_time(as_of)
    cutoff = now - delta
    selected = [e for e in events if cutoff <= parse_time(str(e["opened_at"])) <= now]
    ids = {e["event_id"] for e in selected}
    feedback = [r for r in feedback_rows if r.get("event_id") in ids and parse_time(str(r.get("recorded_at"))) <= now]
    feedback_by_event = {r["event_id"]: r for r in feedback}

    outcomes = {key: 0 for key in ("CONFIRMED", "INVALIDATED", "EXPIRED", "UNRESOLVED")}
    for event in selected:
        outcome = str(event.get("outcome", "UNRESOLVED"))
        if outcome not in outcomes:
            raise ValueError(f"unsupported event outcome: {outcome}")
        outcomes[outcome] += 1
    resolved = outcomes["CONFIRMED"] + outcomes["INVALIDATED"] + outcomes["EXPIRED"]
    event_count = len(selected)
    feedback_count = len(feedback_by_event)
    positive_count = sum(1 for row in feedback_by_event.values() if row.get("impact") in POSITIVE)
    false_alarm_count = sum(1 for row in feedback_by_event.values() if row.get("impact") == "FALSE_ALARM")
    ignored_count = sum(1 for row in feedback_by_event.values() if row.get("impact") == "IGNORED")
    helpful_count = sum(1 for row in feedback_by_event.values() if row.get("impact") == "HELPFUL")
    caused_review = sum(1 for row in feedback_by_event.values() if row.get("impact") == "CAUSED_REVIEW")
    avoided_action = sum(1 for row in feedback_by_event.values() if row.get("impact") == "AVOIDED_ACTION")

    coverage = feedback_count / event_count if event_count else None
    positive_rate = positive_count / feedback_count if feedback_count else None
    false_alarm_rate = false_alarm_count / feedback_count if feedback_count else None
    confirmation_rate = outcomes["CONFIRMED"] / resolved if resolved else None

    gaps = []
    if event_count < MIN_EVENTS:
        gaps.append(f"events<{MIN_EVENTS}")
    if feedback_count < MIN_FEEDBACK:
        gaps.append(f"feedback<{MIN_FEEDBACK}")
    if resolved < MIN_RESOLVED:
        gaps.append(f"resolved<{MIN_RESOLVED}")

    if gaps:
        score = None
        grade = "INSUFFICIENT_EVIDENCE"
    else:
        assert coverage is not None and positive_rate is not None and false_alarm_rate is not None and confirmation_rate is not None
        score = round(100.0 * (0.25 * coverage + 0.35 * positive_rate + 0.25 * confirmation_rate + 0.15 * (1.0 - false_alarm_rate)), 1)
        grade = "STRONG" if score >= 75 else "USEFUL" if score >= 60 else "MIXED" if score >= 40 else "WEAK"

    return {
        "window_start": time_text(cutoff),
        "window_end": time_text(now),
        "events": event_count,
        "resolved": resolved,
        "confirmed": outcomes["CONFIRMED"],
        "invalidated": outcomes["INVALIDATED"],
        "expired": outcomes["EXPIRED"],
        "unresolved": outcomes["UNRESOLVED"],
        "confirmation_rate": round(confirmation_rate, 4) if confirmation_rate is not None else None,
        "feedback_count": feedback_count,
        "feedback_coverage": round(coverage, 4) if coverage is not None else None,
        "positive_impact_count": positive_count,
        "positive_impact_rate": round(positive_rate, 4) if positive_rate is not None else None,
        "false_alarm": false_alarm_count,
        "false_alarm_rate": round(false_alarm_rate, 4) if false_alarm_rate is not None else None,
        "ignored": ignored_count,
        "helpful": helpful_count,
        "caused_review": caused_review,
        "avoided_action": avoided_action,
        "score": score,
        "grade": grade,
        "evidence_gaps": gaps,
    }


def build_report(attribution: dict[str, Any], feedback_rows: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    parse_time(as_of)
    events = _events(attribution)
    windows = {label: _window(events, feedback_rows, as_of, delta) for label, delta in WINDOWS}
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "as_of": as_of,
        "windows": windows,
        "score_contract": {
            "minimum_evidence": {"events": MIN_EVENTS, "feedback": MIN_FEEDBACK, "resolved": MIN_RESOLVED},
            "weights": {
                "feedback_coverage": 0.25,
                "positive_impact_rate": 0.35,
                "objective_confirmation_rate": 0.25,
                "low_false_alarm_rate": 0.15,
            },
            "positive_impacts": sorted(POSITIVE),
            "score_is_null_until_minimum_evidence": True,
            "objective_outcome_and_operator_feedback_are_separate": True,
            "pnl_attribution": False,
            "hypothetical_pnl": False,
        },
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def render_html(payload: dict[str, Any]) -> str:
    cards = []
    for label in ("7d", "30d"):
        row = payload["windows"][label]
        score = "—" if row["score"] is None else f'{row["score"]:.1f}'
        coverage = "—" if row["feedback_coverage"] is None else f'{row["feedback_coverage"]*100:.0f}%'
        confirm = "—" if row["confirmation_rate"] is None else f'{row["confirmation_rate"]*100:.0f}%'
        gaps = ", ".join(row["evidence_gaps"]) or "minimum evidence satisfied"
        cards.append(
            f'<article><div class="k">{label.upper()} VALUE SCORE</div><div class="score">{score}</div><b>{html.escape(row["grade"])}</b>'
            f'<div class="metrics"><span>Events <strong>{row["events"]}</strong></span><span>Feedback <strong>{coverage}</strong></span><span>Confirmed <strong>{confirm}</strong></span><span>False alarms <strong>{row["false_alarm"]}</strong></span></div>'
            f'<small>{html.escape(gaps)}</small></article>'
        )
    css = '*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1050px;margin:auto;padding:28px}.k{font-size:11px;letter-spacing:.16em;color:#6fdbff;font-weight:800}h1{font-size:48px;letter-spacing:-2px;margin:5px 0}.sub,small{color:#8fa5b7}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:22px 0}article,.panel{background:#0d1823;border:1px solid #263746;border-radius:18px;padding:20px}.score{font-size:58px;font-weight:800;margin:8px 0 0}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:18px 0}.metrics span{background:#09131c;padding:10px;border-radius:10px;color:#8fa5b7}.metrics strong{display:block;color:#f4f8fb;font-size:18px;margin-top:4px}.formula{line-height:1.7}@media(max-width:750px){.grid{grid-template-columns:1fr}h1{font-size:38px}}'
    formula = 'Score is withheld until ≥3 events, ≥2 explicit feedback records and ≥2 resolved outcomes. Then: 25% feedback coverage + 35% positive impact rate + 25% objective confirmation rate + 15% low false-alarm rate. No PnL.'
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradingOS Value Score</title><style>{css}</style></head><body><main><div class="k">TRADINGOS · PROOF OF VALUE</div><h1>7 / 30 Day Value Score</h1><div class="sub">Evidence-gated · explicit operator feedback · objective outcomes · no hypothetical PnL</div><div class="grid">{"".join(cards)}</div><section class="panel formula"><b>Transparent scoring contract</b><br>{html.escape(formula)}</section></main></body></html>'


def generate(attribution_path: Path, feedback_ledger: Path, out_dir: Path, as_of: str) -> tuple[dict[str, Any], dict[str, Path]]:
    attribution = read_json(attribution_path)
    feedback_rows = impact_tool.verify_ledger(feedback_ledger)
    payload = build_report(attribution, feedback_rows, as_of)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "value_score.json", "html": out_dir / "value_score.html"}
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    paths["html"].write_text(render_html(payload), encoding="utf-8", newline="\n")
    return payload, paths


def main() -> int:
    p = argparse.ArgumentParser(description="Generate evidence-gated 7/30-day TradingOS Value Score without PnL claims")
    p.add_argument("--attribution", type=Path, required=True)
    p.add_argument("--feedback-ledger", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--as-of", required=True)
    a = p.parse_args()
    try:
        payload, paths = generate(a.attribution.resolve(), a.feedback_ledger.resolve(), a.out_dir.resolve(), a.as_of)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2)); return 2
    print(json.dumps({"result": "PASS", "windows": payload["windows"], "outputs": {k: str(v) for k, v in paths.items()}, "can_trade": False, "capital_permission": "DENY"}, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
