from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("impact", ROOT / "tools" / "tradingos_operator_impact.py")
assert SPEC and SPEC.loader
impact = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(impact)


def attribution(outcome="UNRESOLVED", event_id="evt1"):
    return {
        "schema": "tradingos.value_attribution.report.v1", "version": "1.0.0",
        "summary": {}, "directional_proof": {},
        "events": [{"event_id": event_id, "opened_at": "2026-08-09T16:00:00Z", "symbol": "BTCUSDT", "kind": "LEVEL_PROXIMITY", "priority": "HIGH", "outcome": outcome, "resolution_hours": None, "contract_type": "DIRECTIONAL_TRIGGER_CONFIRMATION"}],
        "contract": {}, "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def test_no_feedback_is_not_auto_promoted_from_confirmed_event(tmp_path):
    report = impact.build_report(attribution("CONFIRMED"), [])
    assert report["events"][0]["operator_impact"] == "NO_FEEDBACK"
    assert report["summary"]["positive_impact_count"] == 0
    assert report["summary"]["feedback_coverage"] == 0


def test_record_explicit_feedback_and_hash_chain(tmp_path):
    ledger = tmp_path / "impact.ndjson"
    status, row = impact.record_feedback(ledger, attribution(), "evt1", "CAUSED_REVIEW", "2026-08-09T17:00:00Z", "Reviewed the setup")
    assert status == "APPENDED"
    assert row["source"] == "EXPLICIT_OPERATOR_FEEDBACK"
    rows = impact.verify_ledger(ledger)
    assert rows[0]["prev_record_hash"] == "GENESIS"
    assert rows[0]["record_hash"]


def test_duplicate_same_feedback_is_suppressed(tmp_path):
    ledger = tmp_path / "impact.ndjson"
    impact.record_feedback(ledger, attribution(), "evt1", "HELPFUL", "2026-08-09T17:00:00Z", "ok")
    status, _ = impact.record_feedback(ledger, attribution(), "evt1", "HELPFUL", "2026-08-09T18:00:00Z", "ok")
    assert status == "DUPLICATE_SUPPRESSED"
    assert len(impact.verify_ledger(ledger)) == 1


def test_conflicting_feedback_rejected(tmp_path):
    ledger = tmp_path / "impact.ndjson"
    impact.record_feedback(ledger, attribution(), "evt1", "HELPFUL", "2026-08-09T17:00:00Z")
    try:
        impact.record_feedback(ledger, attribution(), "evt1", "FALSE_ALARM", "2026-08-09T18:00:00Z")
    except ValueError as exc:
        assert "contradictory overwrite" in str(exc)
    else:
        raise AssertionError("expected rejection")


def test_unknown_event_rejected(tmp_path):
    try:
        impact.record_feedback(tmp_path / "x", attribution(), "missing", "HELPFUL", "2026-08-09T17:00:00Z")
    except ValueError as exc:
        assert "not present" in str(exc)
    else:
        raise AssertionError("expected rejection")


def test_tamper_detection(tmp_path):
    ledger = tmp_path / "impact.ndjson"
    impact.record_feedback(ledger, attribution(), "evt1", "IGNORED", "2026-08-09T17:00:00Z")
    row = json.loads(ledger.read_text())
    row["impact"] = "HELPFUL"
    ledger.write_text(json.dumps(row) + "\n")
    try:
        impact.verify_ledger(ledger)
    except ValueError as exc:
        assert "record_hash mismatch" in str(exc)
    else:
        raise AssertionError("expected tamper detection")


def test_summary_separates_positive_false_alarm_ignored(tmp_path):
    attr = attribution("CONFIRMED", "a")
    attr["events"] += [
        {**attr["events"][0], "event_id": "b", "outcome": "INVALIDATED"},
        {**attr["events"][0], "event_id": "c", "outcome": "EXPIRED"},
        {**attr["events"][0], "event_id": "d", "outcome": "UNRESOLVED"},
    ]
    ledger = tmp_path / "impact.ndjson"
    impact.record_feedback(ledger, attr, "a", "AVOIDED_ACTION", "2026-08-09T17:00:00Z")
    impact.record_feedback(ledger, attr, "b", "FALSE_ALARM", "2026-08-09T17:01:00Z")
    impact.record_feedback(ledger, attr, "c", "IGNORED", "2026-08-09T17:02:00Z")
    report = impact.build_report(attr, impact.verify_ledger(ledger))
    assert report["summary"]["events"] == 4
    assert report["summary"]["feedback_count"] == 3
    assert report["summary"]["positive_impact_count"] == 1
    assert report["summary"]["false_alarm"] == 1
    assert report["summary"]["ignored"] == 1
    assert report["summary"]["no_feedback"] == 1


def test_note_length_guard(tmp_path):
    try:
        impact.record_feedback(tmp_path / "x", attribution(), "evt1", "HELPFUL", "2026-08-09T17:00:00Z", "x" * 501)
    except ValueError as exc:
        assert "<= 500" in str(exc)
    else:
        raise AssertionError("expected note guard")
