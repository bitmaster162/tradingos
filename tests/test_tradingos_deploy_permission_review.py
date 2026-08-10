from __future__ import annotations
import importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('deploy_review',ROOT/'tools'/'tradingos_deploy_permission_review.py'); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def cfg(mode='ENABLED',bound=True):
    c={'schema':m.CONFIG_SCHEMA,'version':1,'mode':mode,'deploy_permission':'DENY','adapter_id':'telegram-primary','credentials':{'telegram_bot_token_env':'TRADINGOS_TELEGRAM_BOT_TOKEN','callback_hmac_secret_env':'TRADINGOS_CALLBACK_HMAC_SECRET'},'destination_bindings':{}}
    if bound:c['destination_bindings']={'ops_primary':{'transport':'telegram','destination_env':'TRADINGOS_TELEGRAM_CHAT_ID','destination_sha256':'a'*64}}
    return c

def env_all(): return {'TRADINGOS_TELEGRAM_CHAT_ID':'synthetic-destination','TRADINGOS_TELEGRAM_BOT_TOKEN':'synthetic-token','TRADINGOS_CALLBACK_HMAC_SECRET':'synthetic-secret'}
def auth(r): return {'schema':m.AUTH_SCHEMA,'version':m.V,'review_id':r['review_id'],'destination_sha256':r['destination_sha256'],'approval_code':r['operator_authorization']['approval_code'],'authorized':True,'scope':m.SCOPE}

def test_real_unbound_disabled_state_is_blocked():
    r=m.review(cfg('DISABLED',False),{}); assert r['status']=='BLOCKED_ACTIVATION_REQUIRED' and set(r['blockers'])=={'DESTINATION_BINDING_REQUIRED','ACTIVATION_REQUIRED'} and r['operator_authorization']['approval_code'] is None

def test_bound_but_disabled_is_blocked():
    r=m.review(cfg('DISABLED',True),env_all()); assert r['status']=='BLOCKED_ACTIVATION_REQUIRED' and r['blockers']==['ACTIVATION_REQUIRED']

def test_enabled_bound_missing_runtime_is_blocked():
    r=m.review(cfg(),{}); assert r['status']=='RUNTIME_INPUTS_MISSING' and len(r['blockers'])==3 and r['authorization_possible_now'] is False

def test_enabled_bound_ready_produces_review_not_apply():
    r=m.review(cfg(),env_all()); assert r['status']=='AWAITING_OPERATOR_AUTHORIZATION' and r['authorization_possible_now'] is True
    assert r['contract']['deploy_permission_apply_performed'] is False and r['contract']['delivery_send_authorized'] is False

def test_runtime_values_never_persisted():
    e=env_all(); r=m.review(cfg(),e); text=m.canon(r)+m.render(r)
    for value in e.values(): assert value not in text

def test_patch_scope_is_deploy_permission_only_and_no_send():
    r=m.review(cfg(),env_all()); p=r['config_patch_preview']
    assert p['apply'] is False and p['set']=={'deploy_permission':'ALLOW'}
    assert p['postcondition']['mode']=='ENABLED' and p['postcondition']['delivery_send_authorized'] is False and p['postcondition']['authenticated_preflight_required'] is True

def test_generic_go_is_not_deploy_permission_authorization():
    c=cfg(); r=m.review(c,env_all()); bad=auth(r); bad['approval_code']='го'
    try:m.validate_authorization(r,bad,c)
    except ValueError:pass
    else:raise AssertionError

def test_exact_authorization_receipt_still_does_not_apply_or_send():
    c=cfg(); r=m.review(c,env_all()); receipt=m.validate_authorization(r,auth(r),c)
    assert receipt['status']=='AUTHORIZED_FOR_DEPLOY_PERMISSION_APPLY_ONLY'
    assert receipt['deploy_permission_apply_performed'] is False and receipt['delivery_send_authorized'] is False and receipt['network_call'] is False and receipt['deploy_permission']=='DENY'

def test_stale_config_rejected():
    c=cfg(); r=m.review(c,env_all()); changed=cfg(); changed['notes']=['drift']
    try:m.validate_authorization(r,auth(r),changed)
    except ValueError as e: assert 'precondition' in str(e)
    else:raise AssertionError

def test_inline_credentials_and_already_allowed_baseline_rejected():
    c=cfg(); c['credentials']['bot_token']='secret'
    try:m.review(c,env_all())
    except ValueError:pass
    else:raise AssertionError
    c=cfg(); c['deploy_permission']='ALLOW'
    try:m.review(c,env_all())
    except ValueError:pass
    else:raise AssertionError

def test_generate_persists_names_and_presence_only(tmp_path):
    cp=tmp_path/'c.json'; cp.write_text(json.dumps(cfg())); e=env_all(); r,j,h=m.generate(cp,tmp_path/'out',e); text=j.read_text()+h.read_text()
    assert r['status']=='AWAITING_OPERATOR_AUTHORIZATION'
    for value in e.values(): assert value not in text
