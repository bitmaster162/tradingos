from __future__ import annotations
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

web=load('web_delivery_test',ROOT/'tools'/'tradingos_web_delivery.py')

def envelope():
    return {"schema":"tradingos.delivery.envelope.v1","delivery_id":"d:e","headline":"BTCUSDT · HIGH · WATCH_LONG","body_lines":["Wait for 4h close"],"feedback_actions":[{"label":"Helpful","impact":"HELPFUL","action_token":"oi1:e06b58fec2365666d555f0ad:H:676672c4"}]}

def test_web_preview_is_disabled_and_network_free():
    manifest=web.build_manifest(envelope()); html=web.render_html(manifest)
    assert manifest['contract']['endpoint_implemented'] is False
    assert manifest['contract']['network_call'] is False
    assert manifest['post_contract']['authentication'].startswith('REQUIRED')
    assert 'disabled' in html
    assert 'fetch(' not in html and 'XMLHttpRequest' not in html
    assert 'oi1:e06b58fec2365666d555f0ad:H:676672c4' in html
