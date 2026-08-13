from __future__ import annotations
import importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('auth',ROOT/'tools'/'tradingos_binding_authorization.py'); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def cfg():
    return {'schema':m.CONFIG_SCHEMA,'version':1,'mode':'DISABLED','deploy_permission':'DENY','adapter_id':'telegram-primary','credentials':{'telegram_bot_token_env':'TRADINGOS_TELEGRAM_BOT_TOKEN','callback_hmac_secret_env':'TRADINGOS_CALLBACK_HMAC_SECRET'},'destination_bindings':{}}
def req():
    return {'schema':m.REQUEST_SCHEMA,'status':'AWAITING_DESTINATION_INPUT','destination_alias':'ops_primary','destination_env':'TRADINGOS_TELEGRAM_CHAT_ID','destination_sha256':None}
def pkg(h='a'*64):
    return {'schema':m.PACKAGE_SCHEMA,'status':'HASH_READY','package_id':'pkg123','binding_request':{'destination_alias':'ops_primary','destination_env':'TRADINGOS_TELEGRAM_CHAT_ID','destination_sha256':h},'contract':{'binding_apply_performed':False,'network_call':False},'safety':{'deploy_permission':'DENY'}}

def test_real_input_request_stays_blocked():
    r=m.blocked_from_request(req(),cfg()); assert r['status']=='BLOCKED_INPUT_REQUIRED' and not r['authorization_possible_now'] and r['config_patch_preview'] is None

def test_hash_ready_requires_explicit_authorization():
    r=m.review_hash_ready(pkg(),cfg()); assert r['status']=='AWAITING_OPERATOR_AUTHORIZATION' and r['blockers']==['EXPLICIT_OPERATOR_AUTHORIZATION_REQUIRED']

def test_patch_preview_binds_hash_only_and_keeps_disabled_deny():
    r=m.review_hash_ready(pkg(),cfg()); p=r['config_patch_preview']; b=p['set']['destination_bindings']['ops_primary']; assert b['destination_sha256']=='a'*64 and b['destination_env']=='TRADINGOS_TELEGRAM_CHAT_ID'; assert p['set']['mode']=='DISABLED' and p['set']['deploy_permission']=='DENY' and p['apply'] is False

def test_review_never_applies_or_networks():
    for r in (m.blocked_from_request(req(),cfg()),m.review_hash_ready(pkg(),cfg())):
        assert r['contract']['binding_apply_performed'] is False and r['contract']['security_config_modified'] is False and r['contract']['network_call'] is False and r['safety']['deploy_permission']=='DENY'

def test_invalid_hash_rejected():
    try:m.review_hash_ready(pkg('bad'),cfg())
    except ValueError:pass
    else:raise AssertionError

def test_unsafe_source_config_rejected():
    for change in ({'mode':'ENABLED'},{'deploy_permission':'ALLOW'},{'destination_bindings':{'x':{}}}):
        c=cfg(); c.update(change)
        try:m.review_hash_ready(pkg(),c)
        except ValueError:pass
        else:raise AssertionError

def test_authorization_code_is_review_bound_and_deterministic():
    a=m.review_hash_ready(pkg(),cfg()); b=m.review_hash_ready(pkg(),cfg()); assert a['review_id']==b['review_id']; assert a['operator_authorization']['approval_code']==f"APPROVE_BINDING:{a['review_id']}"

def test_valid_explicit_authorization_only_authorizes_binding_apply():
    r=m.review_hash_ready(pkg(),cfg()); a={'schema':m.AUTH_SCHEMA,'version':m.V,'review_id':r['review_id'],'binding_package_id':r['binding_package_id'],'destination_sha256':r['destination_sha256'],'approval_code':r['operator_authorization']['approval_code'],'authorized':True,'scope':'BIND_DESTINATION_HASH_ONLY_KEEP_DISABLED_DENY'}; out=m.validate_authorization(r,a); assert out['status']=='AUTHORIZED_FOR_BINDING_APPLY_ONLY' and out['binding_apply_performed'] is False and out['network_call'] is False and out['deploy_permission']=='DENY'

def test_wrong_authorization_code_rejected():
    r=m.review_hash_ready(pkg(),cfg()); a={'schema':m.AUTH_SCHEMA,'version':m.V,'review_id':r['review_id'],'binding_package_id':r['binding_package_id'],'destination_sha256':r['destination_sha256'],'approval_code':'APPROVE_BINDING:wrong','authorized':True,'scope':'BIND_DESTINATION_HASH_ONLY_KEEP_DISABLED_DENY'}
    try:m.validate_authorization(r,a)
    except ValueError:pass
    else:raise AssertionError

def test_go_word_is_not_binding_authorization():
    r=m.review_hash_ready(pkg(),cfg()); a={'schema':m.AUTH_SCHEMA,'version':m.V,'review_id':r['review_id'],'binding_package_id':r['binding_package_id'],'destination_sha256':r['destination_sha256'],'approval_code':'го','authorized':True,'scope':'BIND_DESTINATION_HASH_ONLY_KEEP_DISABLED_DENY'}
    try:m.validate_authorization(r,a)
    except ValueError:pass
    else:raise AssertionError

def test_generate_supports_blocked_and_hash_ready_sources(tmp_path):
    cp=tmp_path/'cfg.json'; cp.write_text(json.dumps(cfg()))
    for name,source,status in [('r',req(),'BLOCKED_INPUT_REQUIRED'),('p',pkg(),'AWAITING_OPERATOR_AUTHORIZATION')]:
        sp=tmp_path/f'{name}.json'; sp.write_text(json.dumps(source)); r,j,h=m.generate(sp,cp,tmp_path/name); assert r['status']==status and j.exists() and h.exists()
