from __future__ import annotations
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

cb=load('feedback_callback_test',ROOT/'tools'/'tradingos_feedback_callback.py')
actions=load('feedback_actions_cb_test',ROOT/'tools'/'tradingos_feedback_actions.py')

def attribution():
    return {"schema":"tradingos.value_attribution.report.v1","events":[{"event_id":"e06b58fec2365666d555f0ad","symbol":"BTCUSDT","kind":"LEVEL_PROXIMITY","priority":"HIGH","outcome":"UNRESOLVED","opened_at":"2026-08-09T16:01:36Z"}]}

def test_verified_callback_appends_explicit_feedback(tmp_path):
    token=actions.make_token('e06b58fec2365666d555f0ad','CAUSED_REVIEW')
    receipt=cb.consume(attribution(),tmp_path/'impact.ndjson',token,'2026-08-09T18:00:00Z','Reviewed context')
    assert receipt['record_status']=='APPENDED'
    assert receipt['impact']=='CAUSED_REVIEW'
    assert receipt['source']=='EXPLICIT_OPERATOR_FEEDBACK'
    assert receipt['contract']['network_call'] is False

def test_tampered_callback_is_rejected_without_ledger_write(tmp_path):
    token=actions.make_token('e06b58fec2365666d555f0ad','HELPFUL')
    bad=token[:-1]+('0' if token[-1]!='0' else '1'); ledger=tmp_path/'impact.ndjson'
    try: cb.consume(attribution(),ledger,bad,'2026-08-09T18:00:00Z')
    except ValueError as exc: assert 'checksum' in str(exc)
    else: raise AssertionError('expected checksum rejection')
    assert not ledger.exists()
