from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "tools" / "tradingos_value_attribution.py"
spec = importlib.util.spec_from_file_location("attr", P); attr = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(attr)


def cockpit(ts="2026-08-09T16:00:00Z", stance="WATCH_LONG", last=99.8, long=True):
    pressure = []
    if long:
        pressure = [{"label":"Price/OI alignment","direction":"LONG"},{"label":"Spot CVD","direction":"LONG"}]
    else:
        pressure = [{"label":"Price/OI alignment","direction":"SHORT"},{"label":"Spot CVD","direction":"SHORT"}]
    return {"schema":"tradingos.decision_cockpit.v1","brief_id":"b1","symbol":"BTCUSDT","as_of":ts,"status":"READY","executive":{"stance":stance,"next":"wait"},"levels":{"last":last,"support":95.0,"resistance":100.0},"pressure":pressure,"risk_flags":[],"quality":{"blockers":[]},"safety":{"signals":False,"orders":False,"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY"}}


def alert(ts="2026-08-09T16:00:00Z", kind="LEVEL_PROXIMITY", level="LONG_TRIGGER_ZONE"):
    return {"schema":"tradingos.decision_alert.v1","brief_id":"b1","symbol":"BTCUSDT","as_of":ts,"decision":"NOTIFY","priority":"HIGH","level_state":level,"events":[{"kind":kind,"priority":"HIGH","title":"trigger close","detail":"near level"}],"dedupe_key":"abc","next_action":"wait","safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY"}}


def test_open_event_and_duplicate_suppression(tmp_path):
    ledger=tmp_path/"a.ndjson"; report,_=attr.process(ledger,cockpit(),alert()); assert report["summary"]["events"]==1 and report["summary"]["unresolved"]==1
    report,_=attr.process(ledger,cockpit(),alert()); assert report["summary"]["events"]==1


def test_long_trigger_confirms_only_after_4h_with_oi_and_spot(tmp_path):
    ledger=tmp_path/"a.ndjson"; attr.process(ledger,cockpit(),alert())
    report,_=attr.process(ledger,cockpit("2026-08-09T20:00:01Z",last=101.0,long=True),alert("2026-08-09T20:00:01Z",kind="NO_MATERIAL_CHANGE",level="MID_RANGE"))
    assert report["summary"]["confirmed"]==1 and report["directional_proof"]["confirmation_rate"]==1.0


def test_break_without_confirmation_pressures_stays_unresolved(tmp_path):
    ledger=tmp_path/"a.ndjson"; attr.process(ledger,cockpit(),alert())
    c=cockpit("2026-08-09T20:00:01Z",last=101.0); c["pressure"]=[]
    report,_=attr.process(ledger,c,alert("2026-08-09T20:00:01Z",kind="NO_MATERIAL_CHANGE",level="MID_RANGE"))
    assert report["summary"]["unresolved"]==1 and report["summary"]["confirmed"]==0


def test_long_trigger_invalidates_on_support_loss(tmp_path):
    ledger=tmp_path/"a.ndjson"; attr.process(ledger,cockpit(),alert())
    report,_=attr.process(ledger,cockpit("2026-08-09T20:01:00Z",stance="NO_ACTION",last=94.0),alert("2026-08-09T20:01:00Z",kind="NO_MATERIAL_CHANGE",level="MID_RANGE"))
    assert report["summary"]["invalidated"]==1


def test_event_expires_after_24h(tmp_path):
    ledger=tmp_path/"a.ndjson"; attr.process(ledger,cockpit(),alert())
    report,_=attr.process(ledger,cockpit("2026-08-10T16:00:01Z",last=99.0),alert("2026-08-10T16:00:01Z",kind="NO_MATERIAL_CHANGE",level="MID_RANGE"))
    assert report["summary"]["expired"]==1


def test_tampered_ledger_fails_closed(tmp_path):
    ledger=tmp_path/"a.ndjson"; attr.process(ledger,cockpit(),alert()); text=ledger.read_text(); ledger.write_text(text.replace("trigger close","tampered"))
    try: attr.verify_ledger(ledger)
    except ValueError as e: assert "record_hash mismatch" in str(e)
    else: raise AssertionError("tamper not detected")


def test_unsafe_input_rejected(tmp_path):
    c=cockpit(); c["safety"]["can_trade"]=True
    try: attr.process(tmp_path/"a.ndjson",c,alert())
    except ValueError as e: assert "unsafe" in str(e)
    else: raise AssertionError("unsafe input accepted")
