from __future__ import annotations
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

tg=load('telegram_delivery_test',ROOT/'tools'/'tradingos_telegram_delivery.py')

def envelope():
    return {"schema":"tradingos.delivery.envelope.v1","headline":"BTCUSDT · HIGH · WATCH_LONG","body_lines":["Long trigger close","Read-only decision support · no signal · no order"],"feedback_actions":[{"label":"Helpful","action_token":"oi1:e06b58fec2365666d555f0ad:H:676672c4"},{"label":"Ignored","action_token":"oi1:e06b58fec2365666d555f0ad:I:4196675c"}],"safety":{"can_trade":False,"deploy_permission":"DENY"}}

def test_telegram_payload_has_inline_callbacks_but_no_destination_or_token():
    payload=tg.build(envelope())
    assert payload['mode']=='DRY_RUN'
    assert payload['method']=='sendMessage'
    flat=[b for row in payload['request']['reply_markup']['inline_keyboard'] for b in row]
    assert len(flat)==2
    assert all(len(x['callback_data'].encode())<=64 for x in flat)
    text=str(payload).lower()
    assert 'bot_token' not in payload['request']
    assert 'chat_id' not in payload['request']
    assert payload['contract']['network_call'] is False
    assert payload['contract']['bot_token_present'] is False

def test_telegram_rejects_oversized_callback():
    row=envelope(); row['feedback_actions']=[{"label":"X","action_token":"x"*65}]
    try: tg.build(row)
    except ValueError as exc: assert 'callback_data' in str(exc)
    else: raise AssertionError('expected token size rejection')
