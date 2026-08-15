from __future__ import annotations
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('runtime',ROOT/'tools'/'tradingos_runtime_service.py'); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
CHAT='123456789'
def cfg(mode='DISABLED',perm='DENY',bound=False):
    c={"schema":m.guard.CFG,"version":1,"mode":mode,"deploy_permission":perm,"adapter_id":"telegram-primary","credentials":{"telegram_bot_token_env":"TRADINGOS_TELEGRAM_BOT_TOKEN","callback_hmac_secret_env":"TRADINGOS_CALLBACK_HMAC_SECRET"},"destination_bindings":{},"rate_limits":{"delivery_attempts_per_minute":3,"callbacks_per_minute":10},"callback_max_age_seconds":300}
    if bound:c['destination_bindings']={'ops_primary':{'transport':'telegram','destination_env':'TRADINGOS_TELEGRAM_CHAT_ID','destination_sha256':m.guard.sha_text(CHAT)}}
    return c

def env():return {'TRADINGOS_TELEGRAM_CHAT_ID':CHAT,'TRADINGOS_TELEGRAM_BOT_TOKEN':'123:test','TRADINGOS_CALLBACK_HMAC_SECRET':'s'*40}
def test_disabled_is_safe_idle_without_secrets():
    r=m.snapshot(cfg(),{}); assert r['status']=='SAFE_IDLE_DISABLED' and r['network_call'] is False
def test_enabled_deny_is_safe_idle():
    r=m.snapshot(cfg('ENABLED','DENY',True),{}); assert r['status']=='SAFE_IDLE_DEPLOY_DENY'
def test_enabled_allow_missing_runtime_is_blocked():
    r=m.snapshot(cfg('ENABLED','ALLOW',True),{}); assert r['status']=='BLOCKED_RUNTIME_INPUTS'
def test_enabled_allow_ready_is_redacted():
    e=env(); r=m.snapshot(cfg('ENABLED','ALLOW',True),e); assert r['status']=='READY_FOR_AUTHORIZED_SEND'
    text=str(r); assert all(v not in text for v in e.values())
