from __future__ import annotations
import importlib.util, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

score=load('score',ROOT/'tools'/'tradingos_value_score.py')
impact=load('impact_for_score',ROOT/'tools'/'tradingos_operator_impact.py')


def event(event_id, opened, outcome):
    return {"event_id":event_id,"opened_at":opened,"symbol":"BTCUSDT","kind":"LEVEL_PROXIMITY","priority":"HIGH","outcome":outcome,"resolution_hours":4,"contract_type":"DIRECTIONAL_TRIGGER_CONFIRMATION"}


def attribution(events):
    return {"schema":"tradingos.value_attribution.report.v1","version":"1.0.0","summary":{},"directional_proof":{},"events":events,"contract":{},"safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY"}}


def feedback(tmp_path, attr, rows):
    ledger=tmp_path/'impact.ndjson'
    for eid,label,when in rows:
        impact.record_feedback(ledger,attr,eid,label,when)
    return impact.verify_ledger(ledger)


def test_no_evidence_means_no_score():
    attr=attribution([event('a','2026-08-09T16:00:00Z','UNRESOLVED')])
    report=score.build_report(attr,[], '2026-08-09T18:10:00Z')
    assert report['windows']['7d']['score'] is None
    assert report['windows']['30d']['grade']=='INSUFFICIENT_EVIDENCE'
    assert 'events<3' in report['windows']['7d']['evidence_gaps']


def test_confirmed_without_feedback_does_not_create_value_score():
    events=[event('a','2026-08-09T12:00:00Z','CONFIRMED'),event('b','2026-08-09T13:00:00Z','CONFIRMED'),event('c','2026-08-09T14:00:00Z','CONFIRMED')]
    report=score.build_report(attribution(events),[], '2026-08-09T18:10:00Z')
    assert report['windows']['7d']['confirmation_rate']==1.0
    assert report['windows']['7d']['score'] is None
    assert 'feedback<2' in report['windows']['7d']['evidence_gaps']


def test_score_formula_is_transparent(tmp_path):
    events=[event('a','2026-08-09T12:00:00Z','CONFIRMED'),event('b','2026-08-09T13:00:00Z','INVALIDATED'),event('c','2026-08-09T14:00:00Z','EXPIRED'),event('d','2026-08-09T15:00:00Z','UNRESOLVED')]
    attr=attribution(events)
    rows=feedback(tmp_path,attr,[('a','HELPFUL','2026-08-09T16:00:00Z'),('b','FALSE_ALARM','2026-08-09T16:01:00Z'),('d','CAUSED_REVIEW','2026-08-09T16:02:00Z')])
    r=score.build_report(attr,rows,'2026-08-09T18:10:00Z')['windows']['7d']
    # coverage=.75 positive=2/3 confirmation=1/3 false_alarm=1/3
    expected=round(100*(.25*.75+.35*(2/3)+.25*(1/3)+.15*(1-1/3)),1)
    assert r['score']==expected
    assert r['grade'] in {'WEAK','MIXED','USEFUL','STRONG'}


def test_7d_and_30d_windows_are_distinct(tmp_path):
    events=[event('old','2026-07-25T12:00:00Z','CONFIRMED'),event('a','2026-08-09T12:00:00Z','CONFIRMED'),event('b','2026-08-09T13:00:00Z','INVALIDATED')]
    attr=attribution(events)
    rows=feedback(tmp_path,attr,[('old','HELPFUL','2026-07-25T16:00:00Z'),('a','HELPFUL','2026-08-09T16:00:00Z')])
    report=score.build_report(attr,rows,'2026-08-09T18:10:00Z')
    assert report['windows']['7d']['events']==2
    assert report['windows']['30d']['events']==3


def test_future_feedback_is_not_counted(tmp_path):
    events=[event('a','2026-08-09T12:00:00Z','CONFIRMED'),event('b','2026-08-09T13:00:00Z','CONFIRMED'),event('c','2026-08-09T14:00:00Z','CONFIRMED')]
    attr=attribution(events)
    rows=feedback(tmp_path,attr,[('a','HELPFUL','2026-08-10T20:00:00Z'),('b','HELPFUL','2026-08-09T16:00:00Z')])
    r=score.build_report(attr,rows,'2026-08-09T18:10:00Z')['windows']['7d']
    assert r['feedback_count']==1
    assert r['score'] is None


def test_event_after_as_of_is_excluded():
    attr=attribution([event('future','2026-08-10T00:00:00Z','CONFIRMED')])
    r=score.build_report(attr,[],'2026-08-09T18:10:00Z')['windows']['30d']
    assert r['events']==0


def test_safety_contract():
    report=score.build_report(attribution([]),[],'2026-08-09T18:10:00Z')
    assert report['safety']['can_trade'] is False
    assert report['score_contract']['pnl_attribution'] is False
    assert report['score_contract']['score_is_null_until_minimum_evidence'] is True
