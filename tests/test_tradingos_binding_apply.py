from __future__ import annotations
import importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('apply',ROOT/'tools'/'tradingos_binding_apply.py'); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def cfg(): return {'schema':m.authz.CONFIG_SCHEMA,'version':1,'mode':'DISABLED','deploy_permission':'DENY','adapter_id':'telegram-primary','credentials':{'telegram_bot_token_env':'TRADINGOS_TELEGRAM_BOT_TOKEN','callback_hmac_secret_env':'TRADINGOS_CALLBACK_HMAC_SECRET'},'destination_bindings':{}}
def req(): return {'schema':m.authz.REQUEST_SCHEMA,'status':'AWAITING_DESTINATION_INPUT','destination_alias':'ops_primary','destination_env':'TRADINGOS_TELEGRAM_CHAT_ID','destination_sha256':None}
def pkg(): return {'schema':m.authz.PACKAGE_SCHEMA,'status':'HASH_READY','package_id':'pkg123','binding_request':{'destination_alias':'ops_primary','destination_env':'TRADINGOS_TELEGRAM_CHAT_ID','destination_sha256':'a'*64},'contract':{'binding_apply_performed':False,'network_call':False},'safety':{'deploy_permission':'DENY'}}
def review(): return m.authz.review_hash_ready(pkg(),cfg())
def authorization(r=None):
    r=r or review(); return {'schema':m.authz.AUTH_SCHEMA,'version':m.authz.V,'review_id':r['review_id'],'binding_package_id':r['binding_package_id'],'destination_sha256':r['destination_sha256'],'approval_code':r['operator_authorization']['approval_code'],'authorized':True,'scope':'BIND_DESTINATION_HASH_ONLY_KEEP_DISABLED_DENY'}

def test_real_blocked_review_cannot_apply_or_accept_authorization():
    r=m.authz.blocked_from_request(req(),cfg()); p=m.evaluate(r,cfg()); assert p['status']=='BLOCKED_INPUT_REQUIRED' and p['contract']['binding_apply_performed'] is False
    try:m.evaluate(r,cfg(),{'anything':True})
    except ValueError:pass
    else:raise AssertionError

def test_hash_ready_without_authorization_is_still_waiting():
    p=m.evaluate(review(),cfg()); assert p['status']=='AWAITING_OPERATOR_AUTHORIZATION' and p['contract']['security_config_modified'] is False

def test_generic_go_is_not_authorization():
    r=review(); bad=authorization(r); bad['approval_code']='го'
    try:m.evaluate(r,cfg(),bad)
    except ValueError:pass
    else:raise AssertionError

def test_authorized_plan_does_not_apply():
    r=review(); p=m.evaluate(r,cfg(),authorization(r)); assert p['status']=='AUTHORIZED_FOR_BINDING_APPLY_ONLY' and p['authorization_validated'] is True and p['contract']['binding_apply_performed'] is False

def test_patch_scope_rejects_extra_changes():
    r=review(); r['config_patch_preview']['set']['credentials']={'x':'y'}
    try:m.evaluate(r,cfg(),authorization(r))
    except ValueError:pass
    else:raise AssertionError

def test_stale_config_precondition_rejected():
    r=review(); c=cfg(); c['notes']=['changed']
    try:m.evaluate(r,c,authorization(r))
    except ValueError as e: assert 'precondition' in str(e)
    else:raise AssertionError

def test_apply_mutates_only_binding_and_keeps_disabled_deny(tmp_path):
    r=review(); c=cfg(); cp=tmp_path/'security.json'; original=(json.dumps(c,indent=2)+'\n').encode(); cp.write_bytes(original)
    receipt,rp,bp=m.apply_binding(cp,r,authorization(r),tmp_path/'out'); after=json.loads(cp.read_text())
    assert receipt['status']=='BINDING_APPLIED_DISABLED_DENY' and rp.exists() and bp.exists()
    assert after['mode']=='DISABLED' and after['deploy_permission']=='DENY' and after['credentials']==c['credentials']
    assert after['destination_bindings']['ops_primary']['destination_sha256']=='a'*64
    assert bp.read_bytes()==original and receipt['contract']['network_call'] is False

def test_apply_requires_exact_authorization(tmp_path):
    r=review(); bad=authorization(r); bad['binding_package_id']='other'; cp=tmp_path/'c.json'; cp.write_text(json.dumps(cfg()))
    try:m.apply_binding(cp,r,bad,tmp_path/'out')
    except ValueError:pass
    else:raise AssertionError
    assert json.loads(cp.read_text())['destination_bindings']=={}

def test_apply_receipt_contains_hashes_not_credentials(tmp_path):
    r=review(); cp=tmp_path/'c.json'; cp.write_text(json.dumps(cfg()))
    receipt,rp,_=m.apply_binding(cp,r,authorization(r),tmp_path/'out'); text=rp.read_text()
    assert len(receipt['before_config_file_sha256'])==64 and len(receipt['after_config_file_sha256'])==64
    assert 'TRADINGOS_TELEGRAM_BOT_TOKEN' not in text and 'TRADINGOS_CALLBACK_HMAC_SECRET' not in text

def test_rollback_restores_exact_original_bytes(tmp_path):
    r=review(); cp=tmp_path/'c.json'; original=b'{\n  "schema": "tradingos.delivery.security_config.v1",\n  "version": 1,\n  "mode": "DISABLED",\n  "deploy_permission": "DENY",\n  "adapter_id": "telegram-primary",\n  "credentials": {"telegram_bot_token_env":"TRADINGOS_TELEGRAM_BOT_TOKEN","callback_hmac_secret_env":"TRADINGOS_CALLBACK_HMAC_SECRET"},\n  "destination_bindings": {}\n}\n'; cp.write_bytes(original)
    receipt,_,bp=m.apply_binding(cp,r,authorization(r),tmp_path/'apply'); rr,_=m.rollback_binding(cp,receipt,bp,tmp_path/'rollback')
    assert rr['status']=='BINDING_ROLLED_BACK' and cp.read_bytes()==original

def test_rollback_rejects_post_apply_drift(tmp_path):
    r=review(); cp=tmp_path/'c.json'; cp.write_text(json.dumps(cfg()))
    receipt,_,bp=m.apply_binding(cp,r,authorization(r),tmp_path/'apply'); cp.write_text(cp.read_text()+' ')
    try:m.rollback_binding(cp,receipt,bp,tmp_path/'rollback')
    except ValueError as e: assert 'current-state mismatch' in str(e)
    else:raise AssertionError

def test_apply_and_rollback_never_network_or_deploy(tmp_path):
    r=review(); cp=tmp_path/'c.json'; cp.write_text(json.dumps(cfg()))
    receipt,_,bp=m.apply_binding(cp,r,authorization(r),tmp_path/'apply'); rr,_=m.rollback_binding(cp,receipt,bp,tmp_path/'rollback')
    assert receipt['contract']['network_call'] is False and receipt['safety']['deploy_permission']=='DENY'
    assert rr['network_call'] is False and rr['deploy_permission']=='DENY'
