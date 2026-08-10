from __future__ import annotations
import copy, importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('sendreview',ROOT/'tools'/'tradingos_send_authorization_review.py'); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def src(status='ALLOW_READY_NO_SEND', attempted='2026-08-10T09:50:00Z'):
    allow=status=='ALLOW_READY_NO_SEND'
    return {
      'schema':m.SOURCE_SCHEMA,'version':'1.0.0','status':status,'guard_decision':'ALLOW_READY' if allow else 'DENY','guard_reason':'PREFLIGHT_READY' if allow else 'CONFIG_DISABLED',
      'request_id':'delivery:r21:source0001','attempted_at':attempted,'destination_alias':'ops_primary',
      'fingerprints':{'guard_source_sha256':'1'*64,'config_semantic_sha256':'2'*64,'manifest_sha256':'3'*64,'guard_audit_record_hash':'4'*64},
      'runtime':{'env_names':{'destination':'TRADINGOS_TELEGRAM_CHAT_ID' if allow else None,'bot_token':'TRADINGOS_TELEGRAM_BOT_TOKEN','callback_hmac_secret':'TRADINGOS_CALLBACK_HMAC_SECRET'},'present':{'destination':allow,'bot_token':allow,'callback_hmac_secret':allow},'all_present':allow,'values_persisted':False,'values_hashed_by_bridge':False},
      'guard_runtime':{'destination_bound':allow,'bot_present':allow,'secret_present':allow},
      'contract':{'preflight_only':True,'network_call':False,'allow_ready_is_not_delivery':True,'delivery_send_authorized':False,'deployment_authorized':False,'webhook_registration_authorized':False,'raw_destination_persisted':False,'runtime_secret_values_persisted':False,'runtime_secret_values_hashed_by_bridge':False,'separate_send_authorization_required':True},
      'safety':{'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY','source_deploy_permission':'ALLOW' if allow else 'DENY'} }

def review(source=None, reviewed='2026-08-10T09:52:00Z'):
    return m.build_review(source or src(),reviewed)

def test_real_deny_blocks_without_approval_code():
    r=review(src('DENY')); assert r['status']=='BLOCKED_PREFLIGHT_REQUIRED' and r['operator_authorization']['approval_code'] is None

def test_fresh_allow_ready_creates_review_bound_code():
    r=review(); assert r['status']=='AWAITING_OPERATOR_AUTHORIZATION' and r['operator_authorization']['approval_code']==f"APPROVE_SEND:{r['review_id']}"

def test_stale_preflight_expires():
    r=m.build_review(src(attempted='2026-08-10T09:40:00Z'),'2026-08-10T09:52:00Z'); assert r['status']=='PREFLIGHT_EXPIRED' and not r['operator_authorization']['possible_now']

def test_review_before_attempt_rejected():
    try:m.build_review(src(),'2026-08-10T09:49:00Z')
    except ValueError as e: assert 'precedes' in str(e)
    else: raise AssertionError('future preflight accepted')

def test_source_network_contract_rejected():
    x=src(); x['contract']['network_call']=True
    try:review(x)
    except ValueError as e: assert 'no-network' in str(e)
    else: raise AssertionError('network source accepted')

def test_source_already_send_authorized_rejected():
    x=src(); x['contract']['delivery_send_authorized']=True
    try:review(x)
    except ValueError as e: assert 'send boundary' in str(e)
    else: raise AssertionError('already-authorized source accepted')

def test_allow_ready_requires_source_deploy_allow():
    x=src(); x['safety']['source_deploy_permission']='DENY'
    try:review(x)
    except ValueError as e: assert 'inconsistent' in str(e)
    else: raise AssertionError('inconsistent allow receipt accepted')

def test_allow_ready_requires_complete_runtime():
    x=src(); x['runtime']['all_present']=False
    try:review(x)
    except ValueError as e: assert 'inconsistent' in str(e)
    else: raise AssertionError('incomplete runtime accepted')

def test_invalid_fingerprint_rejected():
    x=src(); x['fingerprints']['manifest_sha256']='bad'
    try:review(x)
    except ValueError as e: assert 'fingerprint' in str(e)
    else: raise AssertionError('bad fingerprint accepted')

def test_review_id_is_deterministic():
    assert review()['review_id']==review()['review_id']

def test_generic_go_is_not_send_authorization():
    r=review()
    try:m.authorize(r,'го','2026-08-10T09:52:10Z')
    except ValueError as e: assert 'exact review-bound' in str(e)
    else: raise AssertionError('generic go authorized send')

def test_wrong_review_code_rejected():
    r=review()
    try:m.authorize(r,'APPROVE_SEND:'+'0'*32,'2026-08-10T09:52:10Z')
    except ValueError as e: assert 'exact review-bound' in str(e)
    else: raise AssertionError('wrong review code accepted')

def test_exact_code_authorizes_one_send_without_execution():
    r=review(); a=m.authorize(r,r['operator_authorization']['approval_code'],'2026-08-10T09:52:10Z')
    assert a['status']=='AUTHORIZED_ONE_SEND_NO_EXECUTION' and a['contract']['send_execution_authorized'] is True
    assert a['contract']['send_performed'] is False and a['contract']['network_call'] is False and a['contract']['separate_executor_required'] is True

def test_authorization_expiry_rejected():
    r=m.build_review(src(),'2026-08-10T09:52:00Z',300,30)
    try:m.authorize(r,r['operator_authorization']['approval_code'],'2026-08-10T09:53:00Z')
    except ValueError as e: assert 'expired' in str(e)
    else: raise AssertionError('expired authorization accepted')

def test_authorization_binds_manifest_config_audit_and_destination():
    r=review(); a=m.authorize(r,r['operator_authorization']['approval_code'],'2026-08-10T09:52:10Z')
    t=a['target']; assert t['manifest_sha256']=='3'*64 and t['config_semantic_sha256']=='2'*64 and t['guard_audit_record_hash']=='4'*64 and t['destination_alias']=='ops_primary'

def test_review_and_auth_never_authorize_deploy_or_webhook():
    r=review(); a=m.authorize(r,r['operator_authorization']['approval_code'],'2026-08-10T09:52:10Z')
    assert r['contract']['deployment_authorized'] is False and r['contract']['webhook_registration_authorized'] is False
    assert a['contract']['deployment_authorized'] is False and a['contract']['webhook_registration_authorized'] is False
