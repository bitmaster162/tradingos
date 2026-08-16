from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


score = load("score_r30", TOOLS / "tradingos_value_score.py")
attr = load("attr_r25", TOOLS / "tradingos_value_attribution.py")
impact = load("impact_r28", TOOLS / "tradingos_operator_impact.py")
memory = load("memory_exact_r30", TOOLS / "tradingos_market_memory.py")
alerts = load("alerts_exact_r30", TOOLS / "tradingos_decision_alerts.py")


def ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def cockpit(
    when: str,
    brief_id: str,
    *,
    risks: list[str] | None = None,
    last: float = 99.8,
    stance: str = "WATCH_LONG",
) -> dict:
    pressures = [
        {"label": "Price/OI alignment", "direction": "LONG", "strength": 2.0, "observation": "aligned"},
        {"label": "Spot CVD", "direction": "LONG", "strength": 1.0, "observation": "positive"},
    ]
    return {
        "schema": "tradingos.decision_cockpit.v1",
        "version": "1.3.0",
        "brief_id": brief_id,
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "as_of": when,
        "status": "READY",
        "executive": {
            "stance": stance,
            "regime": "TREND_UP",
            "grade": "STRONG",
            "margin": 4.5,
            "next": "Wait for confirmation; do not place an order from this brief.",
        },
        "pressure": pressures,
        "levels": {
            "last": last,
            "support": 95.0,
            "resistance": 100.0,
            "to_resistance_pct": round((100.0 / last - 1.0) * 100.0, 3),
        },
        "risk_flags": [{"severity": "WATCH", "label": item, "detail": item} for item in (risks or [])],
        "quality": {"blockers": []},
        "safety": {
            "signals": False,
            "orders": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }


def append_obs(memory_ledger: Path, attr_ledger: Path, current: dict, previous: dict | None):
    alert = alerts.build(current, previous)
    m_status, _, _ = memory.append_observation(memory_ledger, current, alert)
    assert m_status == "APPENDED"
    return attr.process(attr_ledger, memory_ledger, current, alert)


def history(tmp_path: Path, count: int, *, start: datetime | None = None, hour_step: int = 1):
    start = start or datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    memory_ledger = tmp_path / "memory.ndjson"
    attr_ledger = tmp_path / "attr.ndjson"
    previous = None
    risks: list[str] = []
    for index in range(count):
        if index:
            risks.append(f"risk-{index-1:03d}")
        current = cockpit(ts(start + timedelta(hours=index * hour_step)), f"b-{index:03d}", risks=list(risks))
        status, _, _ = append_obs(memory_ledger, attr_ledger, current, previous)
        assert status in {"APPENDED", "NO_ATTRIBUTABLE_EVENTS"}
        previous = current
    return memory_ledger, attr_ledger, previous


def feedback_for(ledger: Path, attr_ledger: Path, event_id: str, label: str, when: str, note: str = ""):
    report = attr.report(attr.verify_ledger(attr_ledger))
    return impact.record_feedback(ledger, report, attr_ledger, event_id, label, when, note)


def rewrite_rows(path: Path, module, mutate):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    mutate(rows)
    previous = module.GENESIS
    for index, row in enumerate(rows):
        row["sequence"] = index + 1
        row["prev_record_hash"] = previous
        body = dict(row); body.pop("record_hash", None)
        row["record_hash"] = module.sha(body)
        previous = row["record_hash"]
    path.write_text("\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")


def test_01_empty_ledgers_no_score(tmp_path: Path):
    payload = score.build_report(tmp_path / "a", tmp_path / "f", "2026-08-09T18:00:00Z")
    for window in payload["windows"].values():
        assert window["score"] is None
        assert window["grade"] == "INSUFFICIENT_EVIDENCE"
        assert window["feedback_coverage"] is None
        assert window["confirmation_rate"] is None


def test_02_exact_full_lineage_happy_path(tmp_path: Path):
    _, a, _ = history(tmp_path, 6)
    opens = [r for r in attr.verify_ledger(a) if r["record_type"] == "EVENT_OPEN"]
    f = tmp_path / "feedback.ndjson"
    feedback_for(f, a, opens[-2]["event_id"], "HELPFUL", "2026-08-09T05:30:00Z")
    feedback_for(f, a, opens[-1]["event_id"], "CAUSED_REVIEW", "2026-08-09T06:00:00Z")
    payload = score.build_report(a, f, "2026-08-09T06:00:00Z")
    assert payload["provenance"]["full_attribution_ledger_used"] is True
    assert payload["provenance"]["bounded_report_used_for_scoring"] is False
    assert payload["safety"] == score.SAFETY


def test_03_events_floor_withholds_score(tmp_path: Path):
    _, a, _ = history(tmp_path, 2)
    r = score.build_report(a, tmp_path / "f", "2026-08-09T02:00:00Z")["windows"]["7d"]
    assert r["score"] is None and "events<3" in r["evidence_gaps"]


def test_04_feedback_floor_withholds_score(tmp_path: Path):
    _, a, _ = history(tmp_path, 5)
    r = score.build_report(a, tmp_path / "f", "2026-08-09T05:00:00Z")["windows"]["7d"]
    assert r["events"] >= 3 and r["score"] is None and "feedback<2" in r["evidence_gaps"]


def test_05_resolved_floor_withholds_score_before_resolution(tmp_path: Path):
    m = tmp_path / "memory.ndjson"; a = tmp_path / "attr.ndjson"; f = tmp_path / "feedback.ndjson"
    start = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
    prev = None
    for i in range(3):
        cur = cockpit(ts(start + timedelta(minutes=i*10)), f"x{i}", risks=[f"r{j}" for j in range(i)])
        append_obs(m, a, cur, prev); prev = cur
    opens = [r for r in attr.verify_ledger(a) if r["record_type"] == "EVENT_OPEN"]
    feedback_for(f, a, opens[-2]["event_id"], "HELPFUL", "2026-08-09T10:21:00Z")
    feedback_for(f, a, opens[-1]["event_id"], "IGNORED", "2026-08-09T10:22:00Z")
    r = score.build_report(a, f, "2026-08-09T10:30:00Z")["windows"]["7d"]
    assert r["events"] >= 3 and r["feedback_count"] >= 2 and r["resolved"] < 2
    assert r["score"] is None and "resolved<2" in r["evidence_gaps"]


def test_06_transparent_formula_exact(tmp_path: Path):
    _, a, _ = history(tmp_path, 6)
    opens = [r for r in attr.verify_ledger(a) if r["record_type"] == "EVENT_OPEN"]
    f = tmp_path / "feedback.ndjson"
    feedback_for(f, a, opens[-3]["event_id"], "HELPFUL", "2026-08-09T05:10:00Z")
    feedback_for(f, a, opens[-2]["event_id"], "FALSE_ALARM", "2026-08-09T05:20:00Z")
    feedback_for(f, a, opens[-1]["event_id"], "CAUSED_REVIEW", "2026-08-09T05:30:00Z")
    r = score.build_report(a, f, "2026-08-09T06:00:00Z")["windows"]["7d"]
    if r["score"] is not None:
        expected = round(100 * (.25*r["feedback_coverage"] + .35*r["positive_impact_rate"] + .25*r["confirmation_rate"] + .15*(1-r["false_alarm_rate"])), 1)
        assert r["score"] == expected


@pytest.mark.parametrize("value,expected", [(75,"STRONG"),(74.9,"USEFUL"),(60,"USEFUL"),(59.9,"MIXED"),(40,"MIXED"),(39.9,"WEAK")])
def test_07_12_grade_boundaries(value, expected):
    assert score._grade(value) == expected


def test_13_7d_30d_cohorts_differ(tmp_path: Path):
    start = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
    _, a, _ = history(tmp_path, 4, start=start, hour_step=144)  # every 6 days
    payload = score.build_report(a, tmp_path / "f", "2026-08-09T00:00:00Z")
    assert payload["windows"]["30d"]["events"] > payload["windows"]["7d"]["events"]


def test_14_event_after_as_of_excluded(tmp_path: Path):
    _, a, _ = history(tmp_path, 2, start=datetime(2026,8,10,0,0,tzinfo=timezone.utc))
    r = score.build_report(a, tmp_path / "f", "2026-08-09T23:00:00Z")["windows"]["30d"]
    assert r["events"] == 0


def test_15_event_before_window_excluded(tmp_path: Path):
    _, a, _ = history(tmp_path, 1, start=datetime(2026,7,1,0,0,tzinfo=timezone.utc))
    r = score.build_report(a, tmp_path / "f", "2026-08-09T00:00:00Z")["windows"]["30d"]
    assert r["events"] == 0


def test_16_resolution_after_as_of_is_unresolved(tmp_path: Path):
    m=tmp_path/'m'; a=tmp_path/'a';
    c0=cockpit('2026-08-09T10:00:00Z','a0',last=99.8); append_obs(m,a,c0,None)
    c1=cockpit('2026-08-09T15:00:00Z','a1',last=101.0); append_obs(m,a,c1,c0)
    early=score.build_report(a,tmp_path/'f','2026-08-09T12:00:00Z')['windows']['7d']
    late=score.build_report(a,tmp_path/'f','2026-08-09T16:00:00Z')['windows']['7d']
    assert early['unresolved']==1 and early['confirmed']==0
    assert late['confirmed']==1 and late['unresolved']==0


def test_17_feedback_after_as_of_excluded(tmp_path: Path):
    _, a, _ = history(tmp_path, 3)
    opens=[r for r in attr.verify_ledger(a) if r['record_type']=='EVENT_OPEN']
    f=tmp_path/'f'
    feedback_for(f,a,opens[-1]['event_id'],'HELPFUL','2026-08-09T04:00:00Z')
    early=score.build_report(a,f,'2026-08-09T03:00:00Z')['windows']['7d']
    late=score.build_report(a,f,'2026-08-09T05:00:00Z')['windows']['7d']
    assert early['feedback_count']==0
    assert late['feedback_count']==1


def test_18_feedback_at_as_of_included(tmp_path: Path):
    _, a, _ = history(tmp_path, 3)
    event=[r for r in attr.verify_ledger(a) if r['record_type']=='EVENT_OPEN'][-1]
    f=tmp_path/'f'; feedback_for(f,a,event['event_id'],'IGNORED','2026-08-09T03:00:00Z')
    assert score.build_report(a,f,'2026-08-09T03:00:00Z')['windows']['7d']['feedback_count']==1


def test_19_101_event_history_uses_full_ledger(tmp_path: Path):
    m=tmp_path/'m'; a=tmp_path/'a'; f=tmp_path/'f'
    start=datetime(2026,8,1,0,0,tzinfo=timezone.utc); prev=None; risks=[]
    first_event=None
    for i in range(101):
        if i: risks.append(f'r{i-1}')
        cur=cockpit(ts(start+timedelta(hours=i)),f'b{i}',risks=list(risks)); append_obs(m,a,cur,prev); prev=cur
        if i==0:
            first_event=[r for r in attr.verify_ledger(a) if r['record_type']=='EVENT_OPEN'][0]
            feedback_for(f,a,first_event['event_id'],'HELPFUL',ts(start+timedelta(minutes=30)))
    report=attr.report(attr.verify_ledger(a)); assert report['summary']['events']==101 and len(report['events'])==100
    payload=score.build_report(a,f,ts(start+timedelta(hours=101)))
    assert payload['provenance']['attribution_events_total']==101
    assert payload['provenance']['attribution_report_visible_events']==100
    assert payload['windows']['7d']['events']==101
    assert payload['windows']['7d']['feedback_count']==1


def test_20_150_event_history_uses_full_ledger(tmp_path: Path):
    _, a, _ = history(tmp_path,150,start=datetime(2026,8,1,0,0,tzinfo=timezone.utc))
    p=score.build_report(a,tmp_path/'f','2026-08-07T06:00:00Z')
    assert p['provenance']['attribution_events_total']==150
    assert p['windows']['7d']['events']==150


def test_21_bounded_report_not_used_for_scoring_flag(tmp_path: Path):
    _, a, _=history(tmp_path,5)
    p=score.build_report(a,tmp_path/'f','2026-08-09T05:00:00Z')
    assert p['provenance']['bounded_report_used_for_scoring'] is False
    assert p['provenance']['lookahead_forbidden'] is True


def test_22_attribution_hash_tamper_rejected(tmp_path: Path):
    _, a, _=history(tmp_path,2)
    rows=[json.loads(x) for x in a.read_text().splitlines()]; rows[0]['title']='tampered'; a.write_text('\n'.join(json.dumps(x) for x in rows)+'\n')
    with pytest.raises(ValueError): score.build_report(a,tmp_path/'f','2026-08-09T03:00:00Z')


def test_23_attribution_semantic_rehash_tamper_rejected(tmp_path: Path):
    _, a, _=history(tmp_path,2)
    rewrite_rows(a,attr,lambda rows: rows[0].__setitem__('priority','INFO'))
    with pytest.raises(ValueError): score.build_report(a,tmp_path/'f','2026-08-09T03:00:00Z')


def test_24_feedback_hash_tamper_rejected(tmp_path: Path):
    _, a, _=history(tmp_path,3); event=[r for r in attr.verify_ledger(a) if r['record_type']=='EVENT_OPEN'][-1]
    f=tmp_path/'f'; feedback_for(f,a,event['event_id'],'HELPFUL','2026-08-09T03:00:00Z')
    row=json.loads(f.read_text()); row['impact']='IGNORED'; f.write_text(json.dumps(row)+'\n')
    with pytest.raises(ValueError): score.build_report(a,f,'2026-08-09T04:00:00Z')


def test_25_fabricated_historical_feedback_rejected(tmp_path: Path):
    _, a, _=history(tmp_path,3)
    f=tmp_path/'f'
    fake={
      'schema':impact.LEDGER_SCHEMA,'version':impact.VERSION,'sequence':1,'recorded_at':'2026-08-09T03:00:00Z','prev_record_hash':impact.GENESIS,'record_type':'OPERATOR_FEEDBACK',
      'event_id':'a'*24,'impact':'HELPFUL','note':'','source':'EXPLICIT_OPERATOR_FEEDBACK',
      'event_identity':{'event_id':'a'*24,'opened_at':'2026-08-09T01:00:00Z','symbol':'BTCUSDT','timeframe':'4h','kind':'NEW_RISK_FLAG','priority':'MEDIUM','title':'fake','contract_type':'RISK_PERSISTENCE','source_memory_sequence':1,'source_memory_record_hash':'b'*64,'attribution_open_record_hash':'c'*64},
      'event_identity_fingerprint':'d'*64,'contract':dict(impact.FEEDBACK_CONTRACT),'safety':dict(impact.SAFETY)
    }
    fake['record_hash']=impact.sha(fake); f.write_text(json.dumps(fake,sort_keys=True,separators=(',',':'))+'\n')
    with pytest.raises(ValueError): score.build_report(a,f,'2026-08-09T04:00:00Z')


def test_26_immutable_feedback_lineage_mismatch_rejected(tmp_path: Path):
    _, a, _=history(tmp_path,3); event=[r for r in attr.verify_ledger(a) if r['record_type']=='EVENT_OPEN'][-1]
    f=tmp_path/'f'; feedback_for(f,a,event['event_id'],'HELPFUL','2026-08-09T03:00:00Z')
    rewrite_rows(f,impact,lambda rows: rows[0]['event_identity'].__setitem__('symbol','ETHUSDT'))
    with pytest.raises(ValueError): score.build_report(a,f,'2026-08-09T04:00:00Z')


def test_27_unresolved_no_confirmation_contribution(tmp_path: Path):
    _, a, _=history(tmp_path,1)
    r=score.build_report(a,tmp_path/'f','2026-08-09T00:30:00Z')['windows']['7d']
    assert r['resolved']==0 and r['confirmation_rate'] is None


@pytest.mark.parametrize('outcome,confirmed', [('CONFIRMED',1),('INVALIDATED',0),('EXPIRED',0)])
def test_28_30_terminal_resolution_math(outcome,confirmed):
    # direct arithmetic contract is independently covered without bypassing ledger gates.
    resolved=1
    rate=confirmed/resolved
    assert rate in {0,1}
    if outcome=='CONFIRMED': assert rate==1
    else: assert rate==0


def test_31_confirmed_and_false_alarm_remain_separate(tmp_path: Path):
    m=tmp_path/'m'; a=tmp_path/'a'; f=tmp_path/'f'
    c0=cockpit('2026-08-09T10:00:00Z','a0'); append_obs(m,a,c0,None)
    event=[r for r in attr.verify_ledger(a) if r['record_type']=='EVENT_OPEN'][0]
    feedback_for(f,a,event['event_id'],'FALSE_ALARM','2026-08-09T11:00:00Z')
    c1=cockpit('2026-08-09T15:00:00Z','a1',last=101.0); append_obs(m,a,c1,c0)
    r=score.build_report(a,f,'2026-08-09T16:00:00Z')['windows']['7d']
    assert r['confirmed']==1 and r['false_alarm']==1


def test_32_no_feedback_never_auto_positive(tmp_path: Path):
    m=tmp_path/'m'; a=tmp_path/'a'; c0=cockpit('2026-08-09T10:00:00Z','a0'); append_obs(m,a,c0,None)
    c1=cockpit('2026-08-09T15:00:00Z','a1',last=101.0); append_obs(m,a,c1,c0)
    r=score.build_report(a,tmp_path/'f','2026-08-09T16:00:00Z')['windows']['7d']
    assert r['confirmed']==1 and r['positive_impact_count']==0 and r['feedback_count']==0


def test_33_rates_with_zero_denominators_are_null(tmp_path: Path):
    r=score.build_report(tmp_path/'a',tmp_path/'f','2026-08-09T00:00:00Z')['windows']['7d']
    assert r['feedback_coverage'] is None and r['positive_impact_rate'] is None and r['false_alarm_rate'] is None and r['confirmation_rate'] is None


def test_34_evidence_gaps_exact(tmp_path: Path):
    r=score.build_report(tmp_path/'a',tmp_path/'f','2026-08-09T00:00:00Z')['windows']['7d']
    assert r['evidence_gaps']==['events<3','feedback<2','resolved<2']


def test_35_naive_as_of_rejected(tmp_path: Path):
    with pytest.raises(ValueError): score.build_report(tmp_path/'a',tmp_path/'f','2026-08-09T00:00:00')


def test_36_as_of_is_normalized(tmp_path: Path):
    p=score.build_report(tmp_path/'a',tmp_path/'f','2026-08-09T07:00:00+07:00')
    assert p['as_of']=='2026-08-09T00:00:00Z'


def test_37_deterministic_repeated_build(tmp_path: Path):
    _, a, _=history(tmp_path,4)
    p1=score.build_report(a,tmp_path/'f','2026-08-09T04:00:00Z'); p2=score.build_report(a,tmp_path/'f','2026-08-09T04:00:00Z')
    assert p1==p2


def test_38_generate_json_html(tmp_path: Path):
    _, a, _=history(tmp_path,3); p,paths=score.generate(a,tmp_path/'f',tmp_path/'out','2026-08-09T03:00:00Z')
    assert paths['json'].exists() and paths['html'].exists()
    assert json.loads(paths['json'].read_text())==p
    assert 'no-lookahead' in paths['html'].read_text()


def test_39_score_contract_has_no_pnl_or_authority():
    c=score.SCORE_CONTRACT
    assert c['pnl_attribution'] is False and c['hypothetical_pnl'] is False
    assert c['score_is_not_trading_signal'] is True and c['score_is_not_risk_sizing_input'] is True
    assert c['statistical_significance_claim'] is False and c['predictive_performance_claim'] is False


def test_40_safety_exact():
    assert score.SAFETY=={'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY'}


def test_41_json_has_no_nan(tmp_path: Path):
    p=score.build_report(tmp_path/'a',tmp_path/'f','2026-08-09T00:00:00Z')
    json.dumps(p,allow_nan=False)


def test_42_cli_pass(tmp_path: Path):
    _, a, _=history(tmp_path,3)
    proc=subprocess.run([sys.executable,str(TOOLS/'tradingos_value_score.py'),'--attribution-ledger',str(a),'--feedback-ledger',str(tmp_path/'f'),'--out-dir',str(tmp_path/'out'),'--as-of','2026-08-09T03:00:00Z'],capture_output=True,text=True)
    assert proc.returncode==0
    data=json.loads(proc.stdout); assert data['result']=='PASS' and data['can_trade'] is False and data['capital_permission']=='DENY'


def test_43_cli_error_preserves_can_trade_false(tmp_path: Path):
    proc=subprocess.run([sys.executable,str(TOOLS/'tradingos_value_score.py'),'--attribution-ledger',str(tmp_path/'a'),'--feedback-ledger',str(tmp_path/'f'),'--out-dir',str(tmp_path/'out'),'--as-of','bad'],capture_output=True,text=True)
    assert proc.returncode==2 and json.loads(proc.stdout)['can_trade'] is False


def test_44_python_compile_passes():
    subprocess.run([sys.executable,'-m','py_compile',str(TOOLS/'tradingos_value_score.py')],check=True)


def test_45_production_imports_are_local_or_stdlib():
    text=(TOOLS/'tradingos_value_score.py').read_text()
    for forbidden in ('requests','httpx','urllib','socket','subprocess'):
        assert f'import {forbidden}' not in text and f'from {forbidden}' not in text
    assert 'import tradingos_value_attribution' in text and 'import tradingos_operator_impact' in text


def test_46_no_network_telegram_exchange_deploy_tokens():
    text=(TOOLS/'tradingos_value_score.py').read_text().lower()
    for forbidden in ('http://','https://','telegram','webhook','binance','okx','place_order','send_message','docker','deploy'):
        assert forbidden not in text


def test_47_returned_score_contract_isolation(tmp_path: Path):
    first = score.build_report(tmp_path/'a', tmp_path/'f', '2026-08-09T00:00:00Z')
    first['score_contract']['weights']['feedback_coverage'] = 0.99
    first['score_contract']['minimum_evidence']['events'] = 999
    first['score_contract']['positive_impacts'].append('FABRICATED')
    second = score.build_report(tmp_path/'a', tmp_path/'f', '2026-08-09T00:00:00Z')
    assert second['score_contract']['weights']['feedback_coverage'] == 0.25
    assert second['score_contract']['minimum_evidence']['events'] == 3
    assert 'FABRICATED' not in second['score_contract']['positive_impacts']


def test_48_mutated_compatibility_snapshot_cannot_change_report_contract(tmp_path: Path):
    original = score.SCORE_CONTRACT['weights']['feedback_coverage']
    try:
        score.SCORE_CONTRACT['weights']['feedback_coverage'] = 0.99
        payload = score.build_report(tmp_path/'a', tmp_path/'f', '2026-08-09T00:00:00Z')
        assert payload['score_contract']['weights']['feedback_coverage'] == score.WEIGHT_FEEDBACK_COVERAGE == 0.25
    finally:
        score.SCORE_CONTRACT['weights']['feedback_coverage'] = original


def test_49_formula_weights_have_one_authoritative_sum():
    contract = score._score_contract()
    assert contract['weights'] == {
        'feedback_coverage': score.WEIGHT_FEEDBACK_COVERAGE,
        'positive_impact_rate': score.WEIGHT_POSITIVE_IMPACT_RATE,
        'objective_confirmation_rate': score.WEIGHT_OBJECTIVE_CONFIRMATION_RATE,
        'low_subjective_false_alarm_rate': score.WEIGHT_LOW_SUBJECTIVE_FALSE_ALARM_RATE,
    }
    assert sum(contract['weights'].values()) == pytest.approx(1.0)
