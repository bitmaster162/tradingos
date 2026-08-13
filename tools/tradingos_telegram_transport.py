#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,http.client,json,os,ssl,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import tradingos_delivery_guard as guard
import tradingos_send_executor_state as gate
import tradingos_telegram_request_compiler as compiler
V='1.0.0'; MODE='LIVE_NETWORK_SEND'; SCHEMA='tradingos.delivery.live_send_ledger.v1'; GENESIS='GENESIS'; KILL='TRADINGOS_LIVE_SEND_ENABLED'
def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(v): return hashlib.sha256(canon(v).encode()).hexdigest()
def readj(p):
 v=json.loads(Path(p).read_text(encoding='utf-8-sig'))
 if not isinstance(v,dict): raise ValueError('JSON object required')
 return v
def verify(a,pf,m,c,rp,at,e,max_age=60):
 gate.verify_authorization(a,at); gate.verify_fresh_preflight(a,pf,at,max_age)
 if rp.get('schema')!=compiler.PLAN_SCHEMA or rp.get('status')!='REQUEST_TEMPLATE_READY_NO_NETWORK': raise ValueError('R23 request plan required')
 expected=compiler.compile_request(a,m,c,rp.get('compiled_at'))
 if canon(expected)!=canon(rp): raise ValueError('R23 request plan mismatch')
 cfg=guard.validate(c); alias=a['target']['destination_alias']; b=cfg['destination_bindings'].get(alias)
 if not isinstance(b,dict): raise ValueError('destination binding missing')
 rt=guard.runtime(cfg,alias,e,True); te=cfg['credentials']['telegram_bot_token_env']; ce=b['destination_env']; token=e.get(te,''); chat=e.get(ce,'')
 if not token or not chat: raise ValueError('Telegram runtime missing')
 if rp['credential_reference']['bot_token_env']!=te or rp['destination']['chat_id_env']!=ce: raise ValueError('runtime reference drift')
 body=json.loads(json.dumps(rp['http_template']['body_template']))
 if body.get('chat_id')!=f'${{{ce}}}': raise ValueError('chat placeholder mismatch')
 body['chat_id']=chat
 if e.get(KILL,'0')!='1': raise ValueError('runtime kill switch closed')
 return expected,token,body,rt['destination_hash']
def rows(p):
 p=Path(p)
 if not p.exists(): return []
 out=[]; prev=GENESIS
 for n,line in enumerate(p.read_text(encoding='utf-8-sig').splitlines(),1):
  if not line.strip(): continue
  r=json.loads(line); b=dict(r); got=b.pop('record_hash',None)
  if r.get('schema')!=SCHEMA or r.get('sequence')!=len(out)+1 or r.get('prev_record_hash')!=prev or sha(b)!=got: raise ValueError(f'ledger chain invalid at {n}')
  if r.get('raw_destination_persisted') is not False or r.get('secret_persisted') is not False: raise ValueError('unsafe ledger record')
  out.append(r); prev=got
 return out
def claim_path(ledger,aid): return Path(str(Path(ledger))+f'.claims/{aid}.claim')
def claim(ledger,a,at):
 p=claim_path(ledger,a['authorization_id']); p.parent.mkdir(parents=True,exist_ok=True)
 try: fd=os.open(str(p),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 except FileExistsError as exc: raise ValueError('authorization already claimed') from exc
 with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(canon({'authorization_id':a['authorization_id'],'review_id':a['review_id'],'claimed_at':at,'mode':MODE})+'\n'); f.flush(); os.fsync(f.fileno())
 return p
def append(ledger,payload):
 p=Path(ledger); p.parent.mkdir(parents=True,exist_ok=True); lock=Path(str(p)+'.lock')
 try: fd=os.open(str(lock),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 except FileExistsError as exc: raise ValueError('ledger locked') from exc
 try:
  os.close(fd); rs=rows(p); b={'schema':SCHEMA,'version':V,'sequence':len(rs)+1,'prev_record_hash':rs[-1]['record_hash'] if rs else GENESIS,**payload,'raw_destination_persisted':False,'secret_persisted':False}; r={**b,'record_hash':sha(b)}
  with p.open('a',encoding='utf-8') as f: f.write(canon(r)+'\n'); f.flush(); os.fsync(f.fileno())
  return r
 finally:
  try: lock.unlink()
  except FileNotFoundError: pass
def http_send(host,path,body,timeout):
 if host!='api.telegram.org' or not path.startswith('/bot') or not path.endswith('/sendMessage'): raise ValueError('fixed Telegram endpoint required')
 conn=http.client.HTTPSConnection(host,timeout=timeout,context=ssl.create_default_context())
 try: conn.request('POST',path,body=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode(),headers={'Content-Type':'application/json'}); r=conn.getresponse(); raw=r.read(1048576); status=r.status
 finally: conn.close()
 try: d=json.loads(raw.decode()) if raw else {}
 except Exception: d={}
 res=d.get('result') if isinstance(d,dict) and isinstance(d.get('result'),dict) else {}
 return {'http_status':status,'ok':isinstance(d,dict) and d.get('ok') is True,'message_id':res.get('message_id') if isinstance(res.get('message_id'),int) else None,'error_code':d.get('error_code') if isinstance(d,dict) and isinstance(d.get('error_code'),int) else None}
def inspect(a,pf,m,c,rp,at,e,max_age=60):
 try: verify(a,pf,m,c,rp,at,e,max_age)
 except ValueError as exc: return {'status':'BLOCKED','blocker':str(exc),'network_call':False,'send_performed':False}
 return {'status':'READY_FOR_EXPLICIT_LIVE_SEND','blocker':None,'network_call':False,'send_performed':False}
def execute(a,pf,m,c,rp,ledger,at,mode,e,max_age=60,timeout=15,sender=http_send):
 if mode!=MODE: raise ValueError('explicit LIVE_NETWORK_SEND required')
 plan,token,body,dhash=verify(a,pf,m,c,rp,at,e,max_age)
 if not 1<=timeout<=60: raise ValueError('invalid timeout')
 rows(ledger); cp=claim(ledger,a,at); common={'authorization_id':a['authorization_id'],'review_id':a['review_id'],'destination_alias':a['target']['destination_alias'],'destination_sha256':dhash,'manifest_sha256':a['target']['manifest_sha256'],'config_semantic_sha256':a['target']['config_semantic_sha256'],'request_plan_id':plan['plan_id'],'executed_at':at,'execution_mode':MODE}
 pre=append(ledger,{**common,'record_type':'CLAIMED_BEFORE_NETWORK','state':'CLAIMED_BEFORE_NETWORK','network_call':False,'send_performed':False,'transport_attempted':False})
 try:
  o=sender('api.telegram.org',f'/bot{token}/sendMessage',body,timeout); ok=o.get('http_status')==200 and o.get('ok') is True; st='DELIVERED' if ok else 'FAILED_NO_RETRY'; post=append(ledger,{**common,'record_type':'NETWORK_OUTCOME','state':st,'network_call':True,'send_performed':ok,'transport_attempted':True,'http_status':o.get('http_status'),'telegram_ok':o.get('ok') is True,'telegram_message_id':o.get('message_id'),'telegram_error_code':o.get('error_code')})
  return {'status':st,'claim_file':cp.name,'prepared_record_hash':pre['record_hash'],'outcome_record_hash':post['record_hash'],'network_call':True,'send_performed':ok,'transport_attempted':True,'telegram_message_id':o.get('message_id') if ok else None,'automatic_retry':False,'can_trade':False}
 except Exception as exc:
  try: post=append(ledger,{**common,'record_type':'NETWORK_EXCEPTION','state':'FAILED_OR_UNCERTAIN_NO_RETRY','network_call':True,'send_performed':False,'transport_attempted':True,'error_type':type(exc).__name__})
  except Exception: post=None
  return {'status':'FAILED_OR_UNCERTAIN_NO_RETRY','claim_file':cp.name,'prepared_record_hash':pre['record_hash'],'outcome_record_hash':post.get('record_hash') if isinstance(post,dict) else None,'network_call':True,'send_performed':False,'transport_attempted':True,'manual_reconciliation_required':True,'automatic_retry':False,'can_trade':False}
def main():
 p=argparse.ArgumentParser(); p.add_argument('cmd',choices=['plan','send']);
 for n in ('send-authorization','fresh-preflight','telegram-manifest','config','request-plan'): p.add_argument('--'+n,type=Path,required=True)
 p.add_argument('--executed-at',required=True); p.add_argument('--ledger',type=Path); p.add_argument('--execution-mode'); a=p.parse_args(); e=dict(os.environ)
 try:
  A=readj(a.send_authorization); P=readj(a.fresh_preflight); M=readj(a.telegram_manifest); C=readj(a.config); R=readj(a.request_plan); out=inspect(A,P,M,C,R,a.executed_at,e) if a.cmd=='plan' else execute(A,P,M,C,R,a.ledger,a.executed_at,a.execution_mode,e)
 except Exception as exc: print(json.dumps({'result':'ERROR','error':str(exc),'send_performed':False,'can_trade':False},indent=2)); return 2
 print(json.dumps({'result':'PASS',**out},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
