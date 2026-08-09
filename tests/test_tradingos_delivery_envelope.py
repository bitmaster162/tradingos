from __future__ import annotations
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

env=load('delivery_envelope_test',ROOT/'tools'/'tradingos_delivery_envelope.py')

def fixtures():
    safe={"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY"}
    event_id='e06b58fec2365666d555f0ad'
    alert={"schema":"tradingos.decision_alert.v1","brief_id":"b1","symbol":"BTCUSDT","decision":"NOTIFY","priority":"HIGH","level_state":"LONG_TRIGGER_ZONE","events":[{"kind":"LEVEL_PROXIMITY","title":"Long scenario trigger is close","detail":"Near resistance"}],"dedupe_key":"d1","next_action":"Wait for closed 4h confirmation; do not place an order.","safety":safe}
    cockpit={"schema":"tradingos.decision_cockpit.v1","executive":{"stance":"WATCH_LONG"},"levels":{"last":65207.7,"support":64111.0,"resistance":65358.0},"safety":safe}
    attribution={"schema":"tradingos.value_attribution.report.v1","events":[{"event_id":event_id,"symbol":"BTCUSDT","kind":"LEVEL_PROXIMITY","outcome":"UNRESOLVED"}],"safety":safe}
    actions={"schema":"tradingos.operator_impact.actions.v1","events":[{"event_id":event_id,"actions":[{"impact":"HELPFUL","label":"Helpful","action_token":"oi1:e06b58fec2365666d555f0ad:H:676672c4"}]}],"safety":safe}
    value={"schema":"tradingos.value_score.report.v1","windows":{"7d":{"score":None,"grade":"INSUFFICIENT_EVIDENCE","evidence_gaps":["events<3"]},"30d":{"score":None,"grade":"INSUFFICIENT_EVIDENCE","evidence_gaps":["events<3"]}},"safety":safe}
    return alert,cockpit,attribution,actions,value

def test_envelope_is_render_only_and_read_only():
    payload=env.build(*fixtures())
    assert payload['contract']['render_only'] is True
    assert payload['contract']['feedback_write'] is False
    assert payload['contract']['network_call'] is False
    assert payload['safety']['deploy_permission']=='DENY'
    assert payload['value_proof']['7d']['score'] is None

def test_envelope_rejects_silent_alert():
    rows=list(fixtures()); rows[0]=dict(rows[0]); rows[0]['decision']='SILENT'
    try: env.build(*rows)
    except ValueError as exc: assert 'NOTIFY' in str(exc)
    else: raise AssertionError('expected SILENT rejection')

def test_envelope_rejects_unsafe_source():
    rows=list(fixtures()); rows[1]=dict(rows[1]); rows[1]['safety']=dict(rows[1]['safety']); rows[1]['safety']['can_trade']=True
    try: env.build(*rows)
    except ValueError as exc: assert 'read-only' in str(exc)
    else: raise AssertionError('expected unsafe source rejection')
