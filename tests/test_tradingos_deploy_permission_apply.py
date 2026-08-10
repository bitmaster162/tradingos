from __future__ import annotations
import importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('deploy_apply',ROOT/'tools'/'tradingos_deploy_permission_apply.py'); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def cfg(bound=True):
    c={'schema':m.review_mod.CONFIG_SCHEMA,'version':1,'mode':'ENABLED','deploy_permission':'DENY','adapter_id':'telegram-primary','credentials':{'telegram_bot_token_env':'TRADINGOS_TELEGRAM_BOT_TOKEN','callback_hmac_secret_env':'TRADINGOS_CALLBACK_HMAC_SECRET'},'destination_bindings':{}}
    if bound:c['destination_bindings']={'ops_primary':{'transport':'telegram','destination_env':'TRADINGOS_TELEGRAM_CHAT_ID','destination_sha256':'a'*64}}
    return c

def env_all(): return {'TRADINGOS_TELEGRAM_CHAT_ID':'synthetic-destination','TRADINGOS_TELEGRAM_BOT_TOKEN':'synthetic-token','TRADINGOS_CALLBACK_HMAC_SECRET':'synthetic-secret'}
def review(c=None,e=None): return m.review_mod.review(c or cfg(),e or env_all())
def auth(r): return {'schema':m.review_mod.AUTH_SCHEMA,'version':m.review_mod.V,'review_id':r['review_id'],'destination_sha256':r['destination_sha256'],'approval_code':r['operator_authorization']['approval_code'],'authorized':True,'scope':m.review_mod.SCOPE}

def test_real_unbound_plan_is_blocked():
    p=m.evaluate(cfg(False),{}); assert p['status']=='BLOCKED_ACTIVATION_REQUIRED' and p['contract']['deploy_permission_apply_performed'] is False

def test_missing_runtime_plan_is_blocked_and_cannot_accept_auth():
    c=cfg(); r=m.review_mod.review(c,{})
    p=m.evaluate(c,{},review=r); assert p['status']=='RUNTIME_INPUTS_MISSING'
    try:m.evaluate(c,{},authorization={'x':1},review=r)
    except ValueError:pass
    else:raise AssertionError

def test_ready_without_authorization_stays_waiting():
    c=cfg(); r=review(c); p=m.evaluate(c,env_all(),review=r); assert p['status']=='AWAITING_OPERATOR_AUTHORIZATION' and p['contract']['security_config_modified'] is False

def test_generic_go_is_not_deploy_permission_authorization():
    c=cfg(); r=review(c); bad=auth(r); bad['approval_code']='го'
    try:m.evaluate(c,env_all(),bad,r)
    except ValueError:pass
    else:raise AssertionError

def test_authorized_plan_still_does_not_apply_or_send():
    c=cfg(); r=review(c); p=m.evaluate(c,env_all(),auth(r),r)
    assert p['status']=='AUTHORIZED_FOR_DEPLOY_PERMISSION_APPLY_ONLY' and p['contract']['deploy_permission_apply_performed'] is False and p['contract']['delivery_send_authorized'] is False

def test_apply_rejects_runtime_disappearing_after_review(tmp_path):
    c=cfg(); r=review(c); cp=tmp_path/'c.json'; cp.write_text(json.dumps(c))
    try:m.apply_permission(cp,r,auth(r),tmp_path/'out',{})
    except ValueError as e: assert 'review no longer current' in str(e) or 'runtime prerequisites' in str(e)
    else:raise AssertionError

def test_tampered_patch_scope_rejected():
    c=cfg(); r=review(c); r['config_patch_preview']['set']['mode']='DISABLED'
    try:m.evaluate(c,env_all(),auth(r),r)
    except ValueError:pass
    else:raise AssertionError

def test_deploy_permission_apply_changes_only_permission_and_does_not_send(tmp_path):
    c=cfg(); r=review(c); cp=tmp_path/'c.json'; original=(json.dumps(c,indent=2)+'\n').encode(); cp.write_bytes(original)
    rec,rp,bp=m.apply_permission(cp,r,auth(r),tmp_path/'out',env_all()); after=json.loads(cp.read_text())
    assert rec['status']=='DEPLOY_PERMISSION_APPLIED_ALLOW_NO_SEND' and rp.exists() and bp.exists()
    assert after['mode']=='ENABLED' and after['deploy_permission']=='ALLOW'
    assert after['credentials']==c['credentials'] and after['destination_bindings']==c['destination_bindings']
    assert bp.read_bytes()==original and rec['postcondition']['delivery_send_authorized'] is False and rec['postcondition']['authenticated_preflight_required'] is True

def test_apply_rejects_stale_config(tmp_path):
    c=cfg(); r=review(c); changed=cfg(); changed['notes']=['drift']; cp=tmp_path/'c.json'; cp.write_text(json.dumps(changed))
    try:m.apply_permission(cp,r,auth(r),tmp_path/'out',env_all())
    except ValueError:pass
    else:raise AssertionError

def test_receipt_contains_no_runtime_values(tmp_path):
    c=cfg(); e=env_all(); r=review(c,e); cp=tmp_path/'c.json'; cp.write_text(json.dumps(c))
    rec,rp,_=m.apply_permission(cp,r,auth(r),tmp_path/'out',e); text=rp.read_text()
    for value in e.values(): assert value not in text
    assert rec['contract']['network_call'] is False and rec['contract']['delivery_send_authorized'] is False and rec['safety']['deploy_permission']=='ALLOW'

def test_rollback_restores_exact_original_bytes(tmp_path):
    c=cfg(); e=env_all(); r=review(c,e); cp=tmp_path/'c.json'; original=(json.dumps(c,separators=(',',':'))+'\n').encode(); cp.write_bytes(original)
    rec,_,bp=m.apply_permission(cp,r,auth(r),tmp_path/'apply',e); rr,_=m.rollback_permission(cp,rec,bp,tmp_path/'rollback')
    assert rr['status']=='DEPLOY_PERMISSION_ROLLED_BACK_DENY' and cp.read_bytes()==original and rr['delivery_send_authorized'] is False

def test_rollback_rejects_post_apply_drift(tmp_path):
    c=cfg(); e=env_all(); r=review(c,e); cp=tmp_path/'c.json'; cp.write_text(json.dumps(c))
    rec,_,bp=m.apply_permission(cp,r,auth(r),tmp_path/'apply',e); cp.write_text(cp.read_text()+' ')
    try:m.rollback_permission(cp,rec,bp,tmp_path/'rollback')
    except ValueError as ex: assert 'current-state mismatch' in str(ex)
    else:raise AssertionError

def test_already_allowed_config_cannot_be_reapplied(tmp_path):
    c=cfg(); e=env_all(); r=review(c,e); cp=tmp_path/'c.json'; cp.write_text(json.dumps(c)); m.apply_permission(cp,r,auth(r),tmp_path/'apply',e)
    try:m.apply_permission(cp,r,auth(r),tmp_path/'again',e)
    except ValueError:pass
    else:raise AssertionError
