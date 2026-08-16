#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradingos_market_memory_state import parse_time, time_text
import tradingos_value_attribution as value_attribution
import tradingos_operator_impact as operator_impact

VERSION = "1.1.1"
SCHEMA = "tradingos.value_score.report.v1"
WINDOWS = (("7d", timedelta(days=7)), ("30d", timedelta(days=30)))
MIN_EVENTS = 3
MIN_FEEDBACK = 2
MIN_RESOLVED = 2
POSITIVE = {"HELPFUL", "CAUSED_REVIEW", "AVOIDED_ACTION"}
TERMINAL = {"CONFIRMED", "INVALIDATED", "EXPIRED"}
WEIGHT_FEEDBACK_COVERAGE = 0.25
WEIGHT_POSITIVE_IMPACT_RATE = 0.35
WEIGHT_OBJECTIVE_CONFIRMATION_RATE = 0.25
WEIGHT_LOW_SUBJECTIVE_FALSE_ALARM_RATE = 0.15
SAFETY = {
    "signals_allowed": False,
    "orders_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
}


def _score_contract() -> dict[str, Any]:
    return {
        "minimum_evidence": {"events": MIN_EVENTS, "feedback": MIN_FEEDBACK, "resolved": MIN_RESOLVED},
        "weights": {
            "feedback_coverage": WEIGHT_FEEDBACK_COVERAGE,
            "positive_impact_rate": WEIGHT_POSITIVE_IMPACT_RATE,
            "objective_confirmation_rate": WEIGHT_OBJECTIVE_CONFIRMATION_RATE,
            "low_subjective_false_alarm_rate": WEIGHT_LOW_SUBJECTIVE_FALSE_ALARM_RATE,
        },
        "positive_impacts": sorted(POSITIVE),
        "score_is_null_until_minimum_evidence": True,
        "minimum_gate_only": True,
        "statistical_significance_claim": False,
        "predictive_performance_claim": False,
        "trading_edge_claim": False,
        "objective_outcome_and_operator_feedback_are_separate": True,
        "subjective_false_alarm_is_not_objective_model_error": True,
        "pnl_attribution": False,
        "hypothetical_pnl": False,
        "score_is_product_usefulness_metric": True,
        "score_is_not_trading_signal": True,
        "score_is_not_risk_sizing_input": True,
    }


# Compatibility snapshot only. Report generation always constructs a fresh contract.
SCORE_CONTRACT = _score_contract()


def _grade(score: float) -> str:
    if score >= 75:
        return "STRONG"
    if score >= 60:
        return "USEFUL"
    if score >= 40:
        return "MIXED"
    return "WEAK"


def _lineage(
    attribution_ledger: Path, feedback_ledger: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    attribution_records = value_attribution.verify_ledger(attribution_ledger)
    attribution_report = value_attribution.report(attribution_records)
    feedback_rows = operator_impact.verify_ledger(feedback_ledger)
    # R28 is the authoritative binding layer between the full attribution history and feedback history.
    impact_report = operator_impact.build_report(attribution_report, attribution_ledger, feedback_rows)
    return attribution_records, feedback_rows, attribution_report, impact_report


def _event_history(records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    opens: dict[str, dict[str, Any]] = {}
    resolutions: dict[str, dict[str, Any]] = {}
    for row in records:
        event_id = row["event_id"]
        if row["record_type"] == "EVENT_OPEN":
            opens[event_id] = row
        elif row["record_type"] == "EVENT_RESOLUTION":
            resolutions[event_id] = row
    return opens, resolutions


def _window(
    opens: dict[str, dict[str, Any]],
    resolutions: dict[str, dict[str, Any]],
    feedback_rows: list[dict[str, Any]],
    as_of: str,
    delta: timedelta,
) -> dict[str, Any]:
    now = parse_time(as_of)
    cutoff = now - delta

    selected: list[tuple[dict[str, Any], str]] = []
    for event_id, opening in opens.items():
        opened = parse_time(opening["opened_at"])
        if not (cutoff <= opened <= now):
            continue
        resolution = resolutions.get(event_id)
        if resolution is not None and parse_time(resolution["evaluated_at"]) <= now:
            outcome = resolution["outcome"]
        else:
            outcome = "UNRESOLVED"
        selected.append((opening, outcome))

    selected_ids = {row["event_id"] for row, _ in selected}
    feedback_by_event = {
        row["event_id"]: row
        for row in feedback_rows
        if row["event_id"] in selected_ids and parse_time(row["recorded_at"]) <= now
    }

    outcomes = {"CONFIRMED": 0, "INVALIDATED": 0, "EXPIRED": 0, "UNRESOLVED": 0}
    for _, outcome in selected:
        outcomes[outcome] += 1

    event_count = len(selected)
    resolved = outcomes["CONFIRMED"] + outcomes["INVALIDATED"] + outcomes["EXPIRED"]
    feedback_count = len(feedback_by_event)
    positive_count = sum(1 for row in feedback_by_event.values() if row["impact"] in POSITIVE)
    false_alarm_count = sum(1 for row in feedback_by_event.values() if row["impact"] == "FALSE_ALARM")
    ignored_count = sum(1 for row in feedback_by_event.values() if row["impact"] == "IGNORED")
    helpful_count = sum(1 for row in feedback_by_event.values() if row["impact"] == "HELPFUL")
    caused_review = sum(1 for row in feedback_by_event.values() if row["impact"] == "CAUSED_REVIEW")
    avoided_action = sum(1 for row in feedback_by_event.values() if row["impact"] == "AVOIDED_ACTION")

    coverage = feedback_count / event_count if event_count else None
    positive_rate = positive_count / feedback_count if feedback_count else None
    false_alarm_rate = false_alarm_count / feedback_count if feedback_count else None
    confirmation_rate = outcomes["CONFIRMED"] / resolved if resolved else None

    for value in (coverage, positive_rate, false_alarm_rate, confirmation_rate):
        if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError("derived score rate is outside [0,1]")

    gaps: list[str] = []
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
        score = round(
            100.0
            * (
                WEIGHT_FEEDBACK_COVERAGE * coverage
                + WEIGHT_POSITIVE_IMPACT_RATE * positive_rate
                + WEIGHT_OBJECTIVE_CONFIRMATION_RATE * confirmation_rate
                + WEIGHT_LOW_SUBJECTIVE_FALSE_ALARM_RATE * (1.0 - false_alarm_rate)
            ),
            1,
        )
        if not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError("derived score is outside [0,100]")
        grade = _grade(score)

    return {
        "window_start": time_text(cutoff),
        "window_end": time_text(now),
        "event_cohort_basis": "opened_at",
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


def build_report(attribution_ledger: Path, feedback_ledger: Path, as_of: str) -> dict[str, Any]:
    as_of_dt = parse_time(as_of)
    normalized_as_of = time_text(as_of_dt)
    attribution_records, feedback_rows, attribution_report, impact_report = _lineage(
        attribution_ledger, feedback_ledger
    )
    opens, resolutions = _event_history(attribution_records)
    windows = {
        label: _window(opens, resolutions, feedback_rows, normalized_as_of, delta)
        for label, delta in WINDOWS
    }
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "as_of": normalized_as_of,
        "provenance": {
            "attribution_ledger_records": len(attribution_records),
            "attribution_events_total": len(opens),
            "attribution_report_visible_events": len(attribution_report["events"]),
            "feedback_ledger_rows": len(feedback_rows),
            "operator_impact_current_view_events": impact_report["summary"]["events"],
            "as_of_cutoff_enforced": True,
            "full_attribution_ledger_used": True,
            "full_feedback_ledger_used": True,
            "bounded_report_used_for_scoring": False,
            "lookahead_forbidden": True,
            "attribution_tail_record_hash": attribution_records[-1]["record_hash"] if attribution_records else None,
            "feedback_tail_record_hash": feedback_rows[-1]["record_hash"] if feedback_rows else None,
        },
        "windows": windows,
        "score_contract": _score_contract(),
        "safety": dict(SAFETY),
    }


def render_html(payload: dict[str, Any]) -> str:
    cards: list[str] = []
    for label in ("7d", "30d"):
        row = payload["windows"][label]
        score = "—" if row["score"] is None else f'{row["score"]:.1f}'
        coverage = "—" if row["feedback_coverage"] is None else f'{row["feedback_coverage"] * 100:.0f}%'
        confirm = "—" if row["confirmation_rate"] is None else f'{row["confirmation_rate"] * 100:.0f}%'
        gaps = ", ".join(row["evidence_gaps"]) or "minimum evidence satisfied"
        cards.append(
            f'<article><div class="k">{label.upper()} VALUE SCORE</div><div class="score">{score}</div>'
            f'<b>{html.escape(row["grade"])}</b><div class="metrics">'
            f'<span>Events <strong>{row["events"]}</strong></span>'
            f'<span>Feedback <strong>{coverage}</strong></span>'
            f'<span>Confirmed <strong>{confirm}</strong></span>'
            f'<span>False alarms <strong>{row["false_alarm"]}</strong></span>'
            f'</div><small>{html.escape(gaps)}</small></article>'
        )
    css = '*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1050px;margin:auto;padding:28px}.k{font-size:11px;letter-spacing:.16em;color:#6fdbff;font-weight:800}h1{font-size:48px;letter-spacing:-2px;margin:5px 0}.sub,small{color:#8fa5b7}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:22px 0}article,.panel{background:#0d1823;border:1px solid #263746;border-radius:18px;padding:20px}.score{font-size:58px;font-weight:800;margin:8px 0 0}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:18px 0}.metrics span{background:#09131c;padding:10px;border-radius:10px;color:#8fa5b7}.metrics strong{display:block;color:#f4f8fb;font-size:18px;margin-top:4px}.formula{line-height:1.7}@media(max-width:750px){.grid{grid-template-columns:1fr}h1{font-size:38px}}'
    formula = (
        'Full verified Attribution + Operator Impact ledgers. Event-open cohorts. Historical as-of cutoffs are enforced. '
        'Score is withheld until ≥3 events, ≥2 explicit feedback records and ≥2 resolved outcomes. Then: '
        '25% feedback coverage + 35% positive operator impact + 25% objective confirmation rate + '
        '15% low subjective false-alarm rate. No PnL, no trading authority.'
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>TradingOS Value Score</title><style>{css}</style></head><body><main>'
        '<div class="k">TRADINGOS · PROOF OF VALUE</div><h1>7 / 30 Day Value Score</h1>'
        f'<div class="sub">As of {html.escape(payload["as_of"])} · full-ledger · no-lookahead · no hypothetical PnL</div>'
        f'<div class="grid">{"".join(cards)}</div><section class="panel formula"><b>Transparent scoring contract</b><br>{html.escape(formula)}</section>'
        '</main></body></html>'
    )


def generate(attribution_ledger: Path, feedback_ledger: Path, out_dir: Path, as_of: str) -> tuple[dict[str, Any], dict[str, Path]]:
    payload = build_report(attribution_ledger, feedback_ledger, as_of)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "value_score.json", "html": out_dir / "value_score.html"}
    paths["json"].write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    paths["html"].write_text(render_html(payload), encoding="utf-8", newline="\n")
    return payload, paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate full-ledger, evidence-gated TradingOS Value Score without lookahead or PnL claims")
    parser.add_argument("--attribution-ledger", type=Path, required=True)
    parser.add_argument("--feedback-ledger", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    try:
        payload, paths = generate(
            args.attribution_ledger.resolve(),
            args.feedback_ledger.resolve(),
            args.out_dir.resolve(),
            args.as_of,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({
        "result": "PASS",
        "as_of": payload["as_of"],
        "windows": payload["windows"],
        "outputs": {key: str(value) for key, value in paths.items()},
        "can_trade": False,
        "capital_permission": "DENY",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
