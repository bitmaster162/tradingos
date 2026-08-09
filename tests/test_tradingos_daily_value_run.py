from __future__ import annotations
import importlib.util,json,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FIXTURE_MOD=ROOT/'tests'/'test_tradingos_binance_public_snapshot.py'
spec=importlib.util.spec_from_file_location('fixture_value',FIXTURE_MOD); assert spec and spec.loader
fixture=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
SCRIPT=ROOT/'tools'/'tradingos_daily_value_run.py'


def test_daily_value_wrapper_adds_actions_and_gated_score(tmp_path: Path):
    capture=tmp_path/'capture.json'; capture.write_text(json.dumps(fixture.fixture()),encoding='utf-8')
    out=tmp_path/'daily'
    cmd=[sys.executable,str(SCRIPT),'--capture',str(capture),'--out-root',str(out),'--now','2026-08-09T16:05:00Z']
    first=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True); assert first.returncode==0,first.stderr+first.stdout
    payload=json.loads(first.stdout)
    assert payload['feedback_action_events']==1
    assert payload['value_score_windows']['7d']['score'] is None
    assert payload['value_score_windows']['30d']['grade']=='INSUFFICIENT_EVIDENCE'
    actions=json.loads((out/'2026-08-09'/'feedback'/'operator_feedback_actions.json').read_text())
    assert len(actions['events'][0]['actions'])==5
    assert all(len(x['action_token'].encode())<=64 for x in actions['events'][0]['actions'])
    assert (out/'2026-08-09'/'value'/'value_score.html').is_file()
    assert not (out/'operator_impact.ndjson').exists()
    second=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True); assert second.returncode==4
    assert json.loads(second.stdout)['result']=='DUPLICATE_DAY_SUPPRESSED'
