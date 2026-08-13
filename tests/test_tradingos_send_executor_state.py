from __future__ import annotations
import copy, importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('executor',ROOT/'tools'/'tradingos_send_executor_state.py'); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)


def auth(authorized='2026-08-10T10:05:00Z', expires='2026-08-10T10:10:00Z'):
    return {
        'schema':m.AUTH_SCHEMA,'version':'1.0.0','status':'AUTHORIZED_ONE_SEND_NO_EXECUTION',
        'authorization_id':'a'*32,'authorized_at':authorized,'expires_at':expires,'review_id':'b'*32,'scope':m.SCOPE,
        'target':{
            'source_receipt_sha256':'1'*64,'source_request_id':'delivery:r20:old0001','destination_alias':'ops_primary',
            'manifest_sha256':'2'*64,'config_semantic_sha256':'3'*64,'guard_audit_record_hash':'4'*64,
        },
        'contract':{
            'send_execution_authorized':True,'single_use_required':True,'consumption_ledger_required':True,
            'send_performed':False,'network_call':False,'deployment_authorized':False,'webhook_registration_authorized':False,
            'separate_executor_required':True,'executor_must_revalidate_fresh_state':True,
        },
        'safety':{'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY'},
    }


def preflight(attempted='2026-08-10T10:05:30Z', request_id='delivery:r20:fresh001'):
    return {
        'schema':m.PREFLIGHT_SCHEMA,'version':'1.0.0','status':'ALLOW_READY_NO_SEND','guard_decision':'ALLOW_READY','guard_reason':'PREFLIGHT_READY',
        'request_id':request_id,'attempted_at':attempted,'destination_alias':'ops_primary',
        'fingerprints':{'guard_source_sha256':'5'*64,'config_semantic_sha256':'3'*64,'manifest_sha256':'2'*64,'guard_audit_record_hash':'6'*64},
        'runtime':{'env_names':{'destination':'D','bot_token':'B','callback_hmac_secret':'H'},'present':{'destination':True,'bot_token':True,'callback_hmac_secret':True},'all_present':True,'values_persisted':False,'values_hashed_by_bridge':False},
        'guard_runtime':{'destination_bound':True,'bot_present':True,'secret_present':True},
        'contract':{'preflight_only':True,'network_call':False,'allow_ready_is_not_delivery':True,'delivery_send_authorized':False,'deployment_authorized':False,'webhook_registration_authorized':False,'raw_destination_persisted':False,'runtime_secret_values_persisted':False,'runtime_secret_values_hashed_by_bridge':False,'separate_send_authorization_required':True},
        'safety':{'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY','source_deploy_permission':'ALLOW'},
    }


def test_plan_without_authorization_is_blocked():
    p=m.plan(None,None,'2026-08-10T10:06:00Z')
    assert p['status']=='BLOCKED_AUTHORIZATION_REQUIRED' and p['contract']['network_call'] is False


def test_plan_authorized_without_fresh_preflight_is_blocked():
    p=m.plan(auth(),None,'2026-08-10T10:06:00Z')
    assert p['status']=='BLOCKED_FRESH_PREFLIGHT_REQUIRED'


def test_ready_plan_requires_new_fresh_preflight():
    p=m.plan(auth(),preflight(),'2026-08-10T10:06:00Z')
    assert p['status']=='READY_NO_NETWORK_TEST_MODE' and p['contract']['production_send_authorized_by_plan'] is False


def test_execute_consumes_once_without_network(tmp_path):
    r=m.execute_no_network(auth(),preflight(),tmp_path/'ledger.ndjson','2026-08-10T10:06:00Z')
    assert r['status']=='AUTHORIZED_CONSUMED_NO_NETWORK'
    assert r['send_performed'] is False and r['network_call'] is False and r['transport_attempted'] is False
    rows=m.ledger_rows(tmp_path/'ledger.ndjson'); assert len(rows)==1 and rows[0]['authorization_id']=='a'*32


def test_replay_same_authorization_rejected(tmp_path):
    a=auth(); p=preflight(); ledger=tmp_path/'ledger.ndjson'
    m.execute_no_network(a,p,ledger,'2026-08-10T10:06:00Z')
    try:m.execute_no_network(a,preflight(request_id='delivery:r20:fresh002'),ledger,'2026-08-10T10:06:10Z')
    except ValueError as exc: assert 'already consumed' in str(exc)
    else: raise AssertionError('replay accepted')


def test_network_execution_mode_rejected(tmp_path):
    try:m.execute_no_network(auth(),preflight(),tmp_path/'l','2026-08-10T10:06:00Z','NETWORK')
    except ValueError as exc: assert 'NO_NETWORK_TEST_MODE' in str(exc)
    else: raise AssertionError('network mode accepted')


def test_expired_authorization_rejected(tmp_path):
    try:m.execute_no_network(auth(expires='2026-08-10T10:05:59Z'),preflight(),tmp_path/'l','2026-08-10T10:06:00Z')
    except ValueError as exc: assert 'expired' in str(exc)
    else: raise AssertionError('expired auth accepted')


def test_old_preflight_rejected(tmp_path):
    try:m.execute_no_network(auth(),preflight(attempted='2026-08-10T10:04:00Z'),tmp_path/'l','2026-08-10T10:06:00Z',max_age_seconds=60)
    except ValueError as exc: assert 'preflight expired' in str(exc)
    else: raise AssertionError('stale preflight accepted')


def test_same_source_request_id_is_not_fresh(tmp_path):
    try:m.execute_no_network(auth(),preflight(request_id='delivery:r20:old0001'),tmp_path/'l','2026-08-10T10:06:00Z')
    except ValueError as exc: assert 'new request_id' in str(exc)
    else: raise AssertionError('old preflight reused')


def test_manifest_change_rejected(tmp_path):
    p=preflight(); p['fingerprints']['manifest_sha256']='9'*64
    try:m.execute_no_network(auth(),p,tmp_path/'l','2026-08-10T10:06:00Z')
    except ValueError as exc: assert 'manifest changed' in str(exc)
    else: raise AssertionError('manifest drift accepted')


def test_config_change_rejected(tmp_path):
    p=preflight(); p['fingerprints']['config_semantic_sha256']='9'*64
    try:m.execute_no_network(auth(),p,tmp_path/'l','2026-08-10T10:06:00Z')
    except ValueError as exc: assert 'config changed' in str(exc)
    else: raise AssertionError('config drift accepted')


def test_alias_change_rejected(tmp_path):
    p=preflight(); p['destination_alias']='other'
    try:m.execute_no_network(auth(),p,tmp_path/'l','2026-08-10T10:06:00Z')
    except ValueError as exc: assert 'alias changed' in str(exc)
    else: raise AssertionError('alias drift accepted')


def test_non_allow_ready_preflight_rejected(tmp_path):
    p=preflight(); p['status']='DENY'; p['guard_decision']='DENY'; p['guard_reason']='RATE_LIMIT'
    try:m.execute_no_network(auth(),p,tmp_path/'l','2026-08-10T10:06:00Z')
    except ValueError as exc: assert 'ALLOW_READY_NO_SEND' in str(exc)
    else: raise AssertionError('deny preflight accepted')


def test_tampered_authorization_contract_rejected(tmp_path):
    a=auth(); a['contract']['network_call']=True
    try:m.execute_no_network(a,preflight(),tmp_path/'l','2026-08-10T10:06:00Z')
    except ValueError as exc: assert 'execution boundary' in str(exc)
    else: raise AssertionError('unsafe auth accepted')


def test_ledger_hash_chain_tamper_rejected(tmp_path):
    ledger=tmp_path/'ledger.ndjson'; m.execute_no_network(auth(),preflight(),ledger,'2026-08-10T10:06:00Z')
    text=ledger.read_text(); ledger.write_text(text.replace('AUTHORIZED_CONSUMED_NO_NETWORK','TAMPERED'))
    try:m.ledger_rows(ledger)
    except ValueError as exc: assert 'record_hash mismatch' in str(exc) or 'unsafe' in str(exc)
    else: raise AssertionError('tampered ledger accepted')


def test_consumption_record_contains_no_runtime_values(tmp_path):
    r=m.execute_no_network(auth(),preflight(),tmp_path/'ledger.ndjson','2026-08-10T10:06:00Z')
    text=(tmp_path/'ledger.ndjson').read_text()+m.canonical(r)
    assert 'BOT_SECRET_VALUE' not in text and 'CHAT_RAW_VALUE' not in text
    row=m.ledger_rows(tmp_path/'ledger.ndjson')[0]
    assert row['contract']['production_send_consumed'] is False and row['contract']['real_transport_executor_not_present'] is True


def test_authorization_target_hash_format_required(tmp_path):
    a=auth(); a['target']['manifest_sha256']='bad'
    try:m.execute_no_network(a,preflight(),tmp_path/'l','2026-08-10T10:06:00Z')
    except ValueError as exc: assert 'manifest_sha256' in str(exc)
    else: raise AssertionError('bad target hash accepted')


def test_fresh_preflight_runtime_must_be_complete(tmp_path):
    p=preflight(); p['guard_runtime']['bot_present']=False
    try:m.execute_no_network(auth(),p,tmp_path/'l','2026-08-10T10:06:00Z')
    except ValueError as exc: assert 'runtime incomplete' in str(exc)
    else: raise AssertionError('incomplete runtime accepted')

def test_atomic_claim_blocks_reuse_even_without_ledger_row(tmp_path):
    ledger=tmp_path/'ledger.ndjson'; a=auth()
    claim=m.claim_authorization(ledger,a,'2026-08-10T10:06:00Z')
    assert claim.exists()
    try:m.claim_authorization(ledger,a,'2026-08-10T10:06:01Z')
    except ValueError as exc: assert 'already claimed' in str(exc)
    else: raise AssertionError('duplicate atomic claim accepted')


def test_existing_global_ledger_lock_fails_closed_and_keeps_claim(tmp_path):
    ledger=tmp_path/'ledger.ndjson'; lock=m._ledger_lock_path(ledger); lock.write_text('held')
    try:m.execute_no_network(auth(),preflight(),ledger,'2026-08-10T10:06:00Z')
    except ValueError as exc: assert 'ledger is locked' in str(exc)
    else: raise AssertionError('locked ledger accepted')
    assert m._claim_path(ledger,'a'*32).exists()
    assert not ledger.exists()


def test_success_releases_global_lock_but_keeps_single_use_claim(tmp_path):
    ledger=tmp_path/'ledger.ndjson'
    m.execute_no_network(auth(),preflight(),ledger,'2026-08-10T10:06:00Z')
    assert not m._ledger_lock_path(ledger).exists()
    assert m._claim_path(ledger,'a'*32).exists()
