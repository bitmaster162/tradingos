import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("transport",ROOT/"tools"/"tradingos_telegram_transport.py")
assert spec and spec.loader
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
CHAT='123456789'; TOKEN='123456:TEST'; SECRET='s'*40

def cfg():
    return {'schema':m.guard.CFG,'version':1,'mode':'ENABLED','deploy_permission':'ALLOW','adapter_id':'telegram-primary','credentials':{'telegram_bot_token_env':'TRADINGOS_TELEGRAM_BOT_TOKEN','callback_hmac_secret_env':'TRADINGOS_CALLBACK_HMAC_SECRET'},'destination_bindings':{'ops_primary':{'transport':'telegram','destination_env':'TRADINGOS_TELEGRAM_CHAT_ID','destination_sha256':m.guard.sha_text(CHAT)}},'rate_limits':{'delivery_attempts_per_minute':3,'callbacks_per_minute':10},'callback_max_age_seconds':300}

def env():
    return {'TRADINGOS_LIVE_SEND_ENABLED':'1','TRADINGOS_TELEGRAM_CHAT_ID':CHAT,'TRADINGOS_TELEGRAM_BOT_TOKEN':TOKEN,'TRADINGOS_CALLBACK_HMAC_SECRET':SECRET}

def manifest():
    return {'schema':m.compiler.MANIFEST_SCHEMA,'version':'1.0.0','transport':'telegram_bot_api','mode':'DRY_RUN','method':'sendMessage','request':{'text':'PROD TEST','link_preview_options':{'is_disabled':True}},'contract':{'network_call':False,'bot_token_present':False,'chat_id_present':False}}

def setup():
    c=cfg(); man=manifest()
    a={'schema':m.gate.AUTH_SCHEMA,'status':'AUTHORIZED_ONE_SEND_NO_EXECUTION','authorization_id':'1'*32,'authorized_at':'2026-08-10T11:00:00Z','expires_at':'2026-08-10T11:10:00Z','review_id':'2'*32,'scope':m.gate.SCOPE,'target':{'source_receipt_sha256':'3'*64,'source_request_id':'delivery:prod:source0001','destination_alias':'ops_primary','manifest_sha256':m.compiler.sha(man),'config_semantic_sha256':m.guard.sha(m.guard.validate(c)),'guard_audit_record_hash':'4'*64},'contract':{'send_execution_authorized':True,'single_use_required':True,'consumption_ledger_required':True,'send_performed':False,'network_call':False,'deployment_authorized':False,'webhook_registration_authorized':False,'executor_must_revalidate_fresh_state':True},'safety':{'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY'}}
    pf={'schema':m.gate.PREFLIGHT_SCHEMA,'status':'ALLOW_READY_NO_SEND','attempted_at':'2026-08-10T11:01:00Z','request_id':'delivery:prod:fresh0002','destination_alias':'ops_primary','guard_decision':'ALLOW_READY','guard_reason':'PREFLIGHT_READY','guard_runtime':{'destination_bound':True,'bot_present':True,'secret_present':True},'runtime':{'all_present':True,'values_persisted':False,'values_hashed_by_bridge':False},'fingerprints':{'manifest_sha256':a['target']['manifest_sha256'],'config_semantic_sha256':a['target']['config_semantic_sha256'],'guard_audit_record_hash':'5'*64},'contract':{'preflight_only':True,'network_call':False,'allow_ready_is_not_delivery':True,'delivery_send_authorized':False,'deployment_authorized':False,'webhook_registration_authorized':False},'safety':{'source_deploy_permission':'ALLOW'}}
    rp=m.compiler.compile_request(a,man,c,'2026-08-10T11:01:05Z')
    return c,man,a,pf,rp

def test_success_then_outcome_append_failure_is_truthful_and_no_retry(tmp_path, monkeypatch):
    c,man,a,pf,rp=setup(); real_append=m.append; calls={'n':0}
    def flaky(ledger,payload):
        calls['n']+=1
        if calls['n']==2: raise OSError('disk full after confirmed send')
        return real_append(ledger,payload)
    monkeypatch.setattr(m,'append',flaky); sent=[]
    def sender(*args):
        sent.append(args); return {'http_status':200,'ok':True,'message_id':77,'error_code':None}
    ledger=tmp_path/'ledger'; r=m.execute(a,pf,man,c,rp,ledger,'2026-08-10T11:01:10Z',m.MODE,env(),sender=sender)
    assert len(sent)==1 and r['status']=='DELIVERED_LEDGER_UNCERTAIN_NO_RETRY' and r['send_performed'] is True
    assert r['manual_reconciliation_required'] is True and r['automatic_retry'] is False and r['outcome_persisted'] is False
    assert m.claim_path(ledger,a['authorization_id']).exists()
    try: m.execute(a,pf,man,c,rp,ledger,'2026-08-10T11:01:11Z',m.MODE,env(),sender=sender)
    except ValueError as exc: assert 'already claimed' in str(exc)
    else: raise AssertionError('authorization retried after uncertain ledger outcome')
    assert len(sent)==1
