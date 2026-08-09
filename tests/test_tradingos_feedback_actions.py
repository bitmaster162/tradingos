from __future__ import annotations
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

actions=load('feedback_actions',ROOT/'tools'/'tradingos_feedback_actions.py')
impact=load('impact_actions_test',ROOT/'tools'/'tradingos_operator_impact.py')


def attribution():
    return {"schema":"tradingos.value_attribution.report.v1","version":"1.0.0","summary":{},"directional_proof":{},"events":[{"event_id":"e06b58fec2365666d555f0ad","opened_at":"2026-08-09T16:01:36Z","symbol":"BTCUSDT","kind":"LEVEL_PROXIMITY","priority":"HIGH","outcome":"UNRESOLVED","resolution_hours":None,"contract_type":"DIRECTIONAL_TRIGGER_CONFIRMATION"}],"contract":{},"safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY"}}


def test_tokens_are_callback_sized_and_roundtrip():
    for label in impact.IMPACTS:
        token=actions.make_token('e06b58fec2365666d555f0ad',label)
        assert len(token.encode()) <= 64
        assert actions.parse_token(token)==('e06b58fec2365666d555f0ad',label)


def test_token_tamper_is_rejected():
    token=actions.make_token('e06b58fec2365666d555f0ad','HELPFUL')
    bad=token[:-1]+('0' if token[-1] != '0' else '1')
    try: actions.parse_token(bad)
    except ValueError as exc: assert 'checksum' in str(exc)
    else: raise AssertionError('expected checksum rejection')


def test_build_emits_five_explicit_actions():
    payload=actions.build(attribution())
    assert payload['contract']['token_integrity_not_authentication'] is True
    assert payload['contract']['automatic_feedback_forbidden'] is True
    assert len(payload['events'])==1
    assert {x['impact'] for x in payload['events'][0]['actions']} == impact.IMPACTS


def test_action_token_can_record_feedback(tmp_path):
    attr=attribution(); ledger=tmp_path/'impact.ndjson'
    token=actions.make_token('e06b58fec2365666d555f0ad','CAUSED_REVIEW')
    event_id,label=actions.parse_token(token)
    status,row=impact.record_feedback(ledger,attr,event_id,label,'2026-08-09T17:00:00Z','Reviewed')
    assert status=='APPENDED'
    assert row['source']=='EXPLICIT_OPERATOR_FEEDBACK'
    assert row['impact']=='CAUSED_REVIEW'
