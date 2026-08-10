from __future__ import annotations
import importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('activation',ROOT/'tools'/'tradingos_activation_readiness.py'); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def cfg(bound=False):
    c={'schema':m.CONFIG_SCHEMA,'version':1,'mode':'DISABLED','deploy_permission':'DENY','adapter_id':'telegram-primary','credentials':{'telegram_bot_token_env':'TRADINGOS_TELEGRAM_BOT_TOKEN','callback_hmac_secret_env':'TRADINGOS_CALLBACK_HMAC_SECRET'},'destination_bindings':{}}
    if bound:c['destination_bindings']={'ops_primary':{'transport':'telegram','destination_env':'TRADINGOS_TELEGRAM_CHAT_ID','destination_sha256':'a'*64}}
    return c

def env_all(): return {'TRADINGOS_TELEGRAM_CHAT_ID':'synthetic-destination','TRADINGOS_TELEGRAM_BOT_TOKEN':'synthetic-token','TRADINGOS_CALLBACK_HMAC_SECRET':'synthetic-secret-do-not-persist'}
def auth(r): return {'schema':m.AUTH_SCHEMA,'version':m.V,'review_id':r['review_id'],'destination_sha256':r['destination_sha256'],'approval_code':r['operator_authorization']['approval_code'],'authorized':True,'scope':m.SCOPE}

def test_unbound_config_is_blocked_before_runtime_check():
    r=m.review(cfg(False),env_all()); assert r['status']=='BLOCKED_BINDING_REQUIRED' and r['authorization_possible_now'] is False and r['config_patch_preview'] is None

def test_bound_config_reports_missing_runtime_envs_by_name_only():
    r=m.review(cfg(True),{}); assert r['status']=='RUNTIME_INPUTS_MISSING' and len(r['blockers'])==3
    text=m.canon(r); assert 'synthetic-token' not in text and r['runtime']['values_persisted'] is False

def test_partial_runtime_presence_is_fail_closed():
    r=m.review(cfg(True),{'TRADINGOS_TELEGRAM_CHAT_ID':'x'}); assert r['status']=='RUNTIME_INPUTS_MISSING'
    assert [x['present'] for x in r['runtime']['presence']]==[True,False,False]

def test_all_runtime_inputs_present_yields_activation_review_not_apply():
    r=m.review(cfg(True),env_all()); assert r['status']=='AWAITING_OPERATOR_AUTHORIZATION' and r['authorization_possible_now'] is True
    assert r['contract']['activation_apply_performed'] is False and r['contract']['network_call'] is False

def test_secret_values_never_persisted_or_hashed_into_output():
    e=env_all(); r=m.review(cfg(True),e); text=m.canon(r)+m.render(r)
    for value in e.values(): assert value not in text
    assert r['runtime']['secret_policy_validation']=='DELEGATED_TO_AUTHENTICATED_PREFLIGHT'

def test_activation_patch_enables_mode_but_keeps_deploy_deny_and_no_network():
    r=m.review(cfg(True),env_all()); p=r['config_patch_preview']
    assert p['apply'] is False and p['set']=={'mode':'ENABLED','deploy_permission':'DENY'}
    assert p['postcondition']['allow_ready_possible'] is False and p['postcondition']['network_call'] is False

def test_activation_approval_code_is_deterministic_and_review_bound():
    a=m.review(cfg(True),env_all()); b=m.review(cfg(True),env_all())
    assert a['review_id']==b['review_id'] and a['operator_authorization']['approval_code']==f"APPROVE_ACTIVATION:{a['review_id']}"

def test_generic_go_is_not_activation_authorization():
    r=m.review(cfg(True),env_all()); bad=auth(r); bad['approval_code']='го'
    try:m.validate_authorization(r,bad,cfg(True))
    except ValueError:pass
    else:raise AssertionError

def test_exact_activation_authorization_receipt_still_does_not_apply():
    c=cfg(True); r=m.review(c,env_all()); receipt=m.validate_authorization(r,auth(r),c)
    assert receipt['status']=='AUTHORIZED_FOR_ACTIVATION_APPLY_ONLY' and receipt['activation_apply_performed'] is False
    assert receipt['network_call'] is False and receipt['deploy_permission']=='DENY'

def test_stale_config_rejected_at_authorization():
    c=cfg(True); r=m.review(c,env_all()); changed=cfg(True); changed['rate_limits']={'delivery_attempts_per_minute':3}
    try:m.validate_authorization(r,auth(r),changed)
    except ValueError as e: assert 'precondition' in str(e)
    else:raise AssertionError

def test_inline_credentials_and_enabled_baseline_rejected():
    for c in (cfg(True),cfg(True)):
        if 'bot_token' not in c['credentials']: c['credentials']['bot_token']='x'
        try:m.review(c,env_all())
        except ValueError:pass
        else:raise AssertionError
    c=cfg(True); c['mode']='ENABLED'
    try:m.review(c,env_all())
    except ValueError:pass
    else:raise AssertionError

def test_invalid_or_ambiguous_binding_contract_rejected():
    cases=[]
    c=cfg(True); c['destination_bindings']['ops_primary']['destination_sha256']='bad'; cases.append(c)
    c=cfg(True); c['destination_bindings']['two']=dict(c['destination_bindings']['ops_primary']); cases.append(c)
    c=cfg(True); c['destination_bindings']['ops_primary']['destination_env']='TRADINGOS_TELEGRAM_BOT_TOKEN'; cases.append(c)
    for c in cases:
        try:m.review(c,env_all())
        except ValueError:pass
        else:raise AssertionError

def test_generate_outputs_only_presence_metadata(tmp_path):
    cp=tmp_path/'config.json'; cp.write_text(json.dumps(cfg(True)))
    e=env_all(); r,j,h=m.generate(cp,tmp_path/'out',e); text=j.read_text()+h.read_text()
    assert r['status']=='AWAITING_OPERATOR_AUTHORIZATION' and j.exists() and h.exists()
    for value in e.values(): assert value not in text
