from __future__ import annotations
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'tools'/'tradingos_market_radar.py'
spec=importlib.util.spec_from_file_location('radar',PATH); assert spec and spec.loader
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def watch(bias='WATCH_LONG',conflict=None,attention=70):
    return {'schema':m.WATCHTOWER_SCHEMA,'captured_at':'2026-08-09T17:00:00Z','matrix':[{'symbol':'BTCUSDT','bias':bias,'attention_score':attention,'weighted_confluence':6 if bias=='WATCH_LONG' else -6,'conflict':conflict,'timeframes':{'1h':{'state':'LONG'},'4h':{'state':'LONG'},'1d':{'state':'LONG'}}}], 'safety':{'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY'}}

def liq(state='BALANCED',quality='PASS',flags=None,attention=30):
    return {'schema':m.LIQUIDITY_SCHEMA,'captured_at':'2026-08-09T17:00:01Z','matrix':[{'symbol':'BTCUSDT','state':state,'quality':quality,'attention_score':attention,'spread_bps':0.1,'nearest_bid_wall':None,'nearest_ask_wall':None,'flags':flags or []}], 'safety':{'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY'}}

def test_liquidity_cannot_create_bias() -> None:
    r=m.build_radar(watch('NO_ACTION'),liq('BID_HEAVY'))['matrix'][0]
    assert r['bias']=='NO_ACTION'
    assert r['decision_quality']=='NO_ACTION'

def test_opposing_liquidity_adds_caution_veto_only() -> None:
    r=m.build_radar(watch('WATCH_LONG'),liq('ASK_HEAVY'))['matrix'][0]
    assert r['bias']=='WATCH_LONG'
    assert r['decision_quality']=='CAUTION'
    assert 'MICROSTRUCTURE_OPPOSES_LONG' in r['vetoes']

def test_partial_liquidity_does_not_modify_watchtower_priority() -> None:
    r=m.build_radar(watch('WATCH_LONG',attention=73),liq('INSUFFICIENT_DEPTH_COVERAGE','PARTIAL',attention=99))['matrix'][0]
    assert r['priority_score']==73
    assert r['decision_quality']=='CONTEXT_PARTIAL'
    assert 'LIQUIDITY_CONTEXT_PARTIAL' in r['notes']

def test_near_wall_becomes_directional_friction() -> None:
    r=m.build_radar(watch('WATCH_LONG'),liq('BALANCED',flags=['NEAR_ASK_WALL']))['matrix'][0]
    assert 'NEAR_ASK_WALL_FRICTION' in r['vetoes']
    assert r['decision_quality']=='CAUTION'

def test_unsafe_inputs_fail_closed() -> None:
    w=watch(); w['safety']['can_trade']=True
    try: m.build_radar(w,liq())
    except ValueError as exc: assert 'unsafe' in str(exc)
    else: raise AssertionError('unsafe watchtower accepted')

def test_html_repeats_read_only_boundary() -> None:
    page=m.render_html(m.build_radar(watch(),liq()))
    assert 'Liquidity may add friction/veto context but cannot create a directional bias.' in page
    assert 'can_trade=false' in page
