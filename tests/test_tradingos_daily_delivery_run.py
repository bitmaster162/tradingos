from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CAPTURE=Path('/mnt/data/TRADINGOS_DECISION_BRIEF_LIVE_2026-08-09/BINANCE_PUBLIC_CAPTURE.json')

def test_daily_delivery_wrapper_renders_only_and_never_creates_feedback(tmp_path):
    assert CAPTURE.is_file()
    out=tmp_path/'run'
    env=dict(os.environ); env['PYTHONPATH']=str(ROOT)
    p=subprocess.run([sys.executable,str(ROOT/'tools'/'tradingos_daily_delivery_run.py'),'--capture',str(CAPTURE),'--out-root',str(out),'--now','2026-08-09T16:05:00Z'],cwd=ROOT,text=True,capture_output=True,env=env)
    assert p.returncode==0, p.stdout+p.stderr
    receipt=json.loads(p.stdout); day=out/receipt['bangkok_day']
    assert receipt['delivery']['mode']=='DRY_RUN'
    assert receipt['delivery']['telegram_buttons']==5
    assert receipt['delivery']['web_buttons']==5
    assert receipt['delivery']['network_call'] is False
    assert not (out/'operator_impact.ndjson').exists()
    tg=json.loads((day/receipt['outputs']['telegram_payload']).read_text())
    assert tg['contract']['bot_token_present'] is False
    web=(day/receipt['outputs']['web_delivery_html']).read_text()
    assert 'buttons disabled' in web
