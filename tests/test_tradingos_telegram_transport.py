import copy,importlib.util,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; s=importlib.util.spec_from_file_location('t',ROOT/'tools'/'tradingos_telegram_transport.py'); assert s and s.loader; m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
CHAT='123456789'; TOKEN='123456:TEST'; SECRET='s'*40
def cfg(): return {'schema':m.guard.CFG,'version':1,'mode':'ENABLED','deploy_permission':'ALLOW','adapter_id':'telegram-primary','credentials':{'telegram_bot_token_env':'TRADINGOS_TELEGRAM_BOT_TOKEN','callback_hmac_secret_env':'TRADINGOS_CALLBACK_HMAC_SECRET'},'destination_bindings':{'ops_primary':{'transport':'telegram','destination_env':'TRADINGOS_TELEGRAM_CHAT_ID','destination_sha256':m.guard.sha_text(CHAT)}},'rate_limits':{'delivery_attempts_per_minute':3,'callbacks_per_minute':10},'callback_max_age_seconds':300}
def env(live='1',chat=CHAT): return {'TRADINGOS_LIVE_SEND_ENABLED':live,'TRADINGOS_TELEGRAM_CHAT_ID':chat,'TRADINGOS_TELEGRAM_BOT_TOKEN':TOKEN,'TRADINGOS_CALLBACK_HMAC_SECRET':SECRET}
def manifest(): return {'schema':m.compiler.MANIFEST_SCHEMA,'version':'1.0.0','transport':'telegram_bot_api','mode':'DRY_RUN','method':'sendMessage','request':{'text':'PROD TEST','link_preview_options':{'is_disabled':True}},'contract':{'network_call':False,'bot_token_present':False,'chat_id_present':False}}
def setup():
 c=cfg(); man=manifest(); a={'schema':m.gate.AUTH_SCHEMA,'status':'AUTHORIZED_ONE_SEND_NO_EXECUTION','authorization_id':'1'*32,'authorized_at':'2026-08-10T11:00:00Z','expires_at':'2026-08-10T11:10:00Z','review_id':'2'*32,'scope':m.gate.SCOPE,'target':{'source_receipt_sha256':'3'*64,'source_request_id':'delivery:prod:source0001','destination_alias':'ops_primary','manifest_sha256':m.compiler.sha(man),'config_semantic_sha256':m.guard.sha(m.guard.validate(c)),'guard_audit_record_hash':'4'*64},'contract':{'send_execution_authorized':True,'single_use_required':True,'consumption_ledger_required':True,'send_performed':False,'network_call':False,'deployment_authorized':False,'webhook_registration_authorized':False,'executor_must_revalidate_fresh_state':True}}
 pf={'schema':m.gate.PREFLIGHT_SCHEMA,'status':'ALLOW_READY_NO_SEND','attempted_at':'2026-08-10T11:01:00Z','request_id':'delivery:prod:fresh0002','destination_alias':'ops_primary','guard_decision':'ALLOW_READY','guard_reason':'PREFLIGHT_READY','guard_runtime':{'destination_bound':True,'bot_present':True,'secret_present':True},'runtime':{'all_present':True,'values_persisted':False,'values_hashed_by_bridge':False},'fingerprints':{'manifest_sha256':a['target']['manifest_sha256'],'config_semantic_sha256':a['target']['config_semantic_sha256'],'guard_audit_record_hash':'5'*64},'contract':{'preflight_only':True,'network_call':False,'allow_ready_is_not_delivery':True,'delivery_send_authorized':False,'deployment_authorized':False,'webhook_registration_authorized':False},'safety':{'source_deploy_permission':'ALLOW'}}
 rp=m.compiler.compile_request(a,man,c,'2026-08-10T11:01:05Z'); return c,man,a,pf,rp
def test_kill_switch_blocks():
 c,man,a,pf,rp=setup(); r=m.inspect(a,pf,man,c,rp,'2026-08-10T11:01:10Z',env('0')); assert r['status']=='BLOCKED'
def test_tamper_blocks():
 c,man,a,pf,rp=setup(); bad=copy.deepcopy(rp); bad['http_template']['body_template']['text']='X'
 try:m.verify(a,pf,man,c,bad,'2026-08-10T11:01:10Z',env())
 except ValueError: pass
 else: raise AssertionError
def test_destination_drift_blocks():
 c,man,a,pf,rp=setup()
 try:m.verify(a,pf,man,c,rp,'2026-08-10T11:01:10Z',env(chat='9'))
 except ValueError: pass
 else: raise AssertionError
def test_live_success_single_use_no_leak(tmp_path):
 c,man,a,pf,rp=setup(); calls=[]
 def fake(*x): calls.append(x); return {'http_status':200,'ok':True,'message_id':77,'error_code':None}
 led=tmp_path/'l'; r=m.execute(a,pf,man,c,rp,led,'2026-08-10T11:01:10Z',m.MODE,env(),sender=fake); assert r['status']=='DELIVERED' and len(calls)==1; text=led.read_text()+json.dumps(r); assert CHAT not in text and TOKEN not in text and SECRET not in text
 try:m.execute(a,pf,man,c,rp,led,'2026-08-10T11:01:11Z',m.MODE,env(),sender=fake)
 except ValueError: pass
 else: raise AssertionError
 assert len(calls)==1
def test_failure_no_retry(tmp_path):
 c,man,a,pf,rp=setup(); calls=[]
 def fake(*x): calls.append(1); return {'http_status':500,'ok':False}
 led=tmp_path/'l'; r=m.execute(a,pf,man,c,rp,led,'2026-08-10T11:01:10Z',m.MODE,env(),sender=fake); assert r['status']=='FAILED_NO_RETRY'
 try:m.execute(a,pf,man,c,rp,led,'2026-08-10T11:01:11Z',m.MODE,env(),sender=fake)
 except ValueError: pass
 else: raise AssertionError
 assert len(calls)==1
def test_network_exception_fail_closed(tmp_path):
 c,man,a,pf,rp=setup(); led=tmp_path/'l'; r=m.execute(a,pf,man,c,rp,led,'2026-08-10T11:01:10Z',m.MODE,env(),sender=lambda *x:(_ for _ in ()).throw(TimeoutError())); assert r['manual_reconciliation_required'] is True and m.claim_path(led,a['authorization_id']).exists()
def test_wrong_endpoint_rejected_without_network():
 try:m.http_send('example.com','/x',{},1)
 except ValueError: pass
 else: raise AssertionError
