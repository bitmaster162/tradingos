#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, html, json, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

V='1.0.0'
SOURCE_SCHEMA='tradingos.delivery.preflight_bridge_receipt.v1'
REVIEW_SCHEMA='tradingos.delivery.send_review.v1'
AUTH_SCHEMA='tradingos.delivery.send_authorization.v1'
H64=re.compile(r'^[0-9a-f]{64}$')
RID=re.compile(r'^[A-Za-z0-9._:-]{12,128}$')
ALIAS=re.compile(r'^[A-Za-z0-9._-]{1,80}$')
SCOPE='ONE_TELEGRAM_SEND_ONLY'


def canonical(v:Any)->str:
    return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def sha(v:Any)->str:
    return hashlib.sha256(canonical(v).encode()).hexdigest()

def read_json(p:Path)->dict[str,Any]:
    v=json.loads(Path(p).read_text(encoding='utf-8-sig'))
    if not isinstance(v,dict): raise ValueError(f'{p} must contain a JSON object')
    return v

def ts(v:str)->datetime:
    s=str(v).strip(); s=s[:-1]+'+00:00' if s.endswith('Z') else s
    d=datetime.fromisoformat(s)
    if d.tzinfo is None: raise ValueError('timestamp must include timezone')
    return d.astimezone(timezone.utc)

def ttext(d:datetime)->str:
    return d.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

def _source_contract(src:dict[str,Any])->None:
    if src.get('schema')!=SOURCE_SCHEMA: raise ValueError('unsupported preflight bridge receipt')
    c=src.get('contract'); s=src.get('safety'); fp=src.get('fingerprints'); rt=src.get('runtime'); gr=src.get('guard_runtime')
    if not all(isinstance(x,dict) for x in (c,s,fp,rt,gr)): raise ValueError('malformed preflight bridge receipt')
    if c.get('preflight_only') is not True or c.get('network_call') is not False: raise ValueError('source is not no-network preflight')
    if c.get('allow_ready_is_not_delivery') is not True or c.get('delivery_send_authorized') is not False: raise ValueError('source already crosses send boundary')
    if c.get('deployment_authorized') is not False or c.get('webhook_registration_authorized') is not False: raise ValueError('source contains forbidden authorization')
    if c.get('separate_send_authorization_required') is not True: raise ValueError('source does not require separate send authorization')
    if rt.get('values_persisted') is not False or rt.get('values_hashed_by_bridge') is not False: raise ValueError('source runtime-value contract unsafe')
    if not isinstance(src.get('request_id'),str) or not RID.fullmatch(src['request_id']): raise ValueError('invalid source request_id')
    if not isinstance(src.get('destination_alias'),str) or not ALIAS.fullmatch(src['destination_alias']): raise ValueError('invalid destination alias')
    ts(str(src.get('attempted_at')))
    for k in ('guard_source_sha256','config_semantic_sha256','manifest_sha256','guard_audit_record_hash'):
        if not isinstance(fp.get(k),str) or not H64.fullmatch(fp[k]): raise ValueError(f'invalid source fingerprint {k}')


def build_review(src:dict[str,Any], reviewed_at:str, max_preflight_age_seconds:int=300, authorization_ttl_seconds:int=300)->dict[str,Any]:
    _source_contract(src)
    if not isinstance(max_preflight_age_seconds,int) or not 30<=max_preflight_age_seconds<=1800: raise ValueError('invalid preflight max age')
    if not isinstance(authorization_ttl_seconds,int) or not 30<=authorization_ttl_seconds<=900: raise ValueError('invalid authorization ttl')
    attempted=ts(src['attempted_at']); reviewed=ts(reviewed_at); age=(reviewed-attempted).total_seconds()
    if age < 0: raise ValueError('reviewed_at precedes preflight attempted_at')
    eligible=(
        src.get('status')=='ALLOW_READY_NO_SEND' and src.get('guard_decision')=='ALLOW_READY' and src.get('guard_reason')=='PREFLIGHT_READY'
        and src['safety'].get('source_deploy_permission')=='ALLOW'
        and src['runtime'].get('all_present') is True
        and all(src['guard_runtime'].get(k) is True for k in ('destination_bound','bot_present','secret_present'))
    )
    if src.get('status')=='ALLOW_READY_NO_SEND' and not eligible: raise ValueError('inconsistent ALLOW_READY_NO_SEND source receipt')
    if not eligible:
        status='BLOCKED_PREFLIGHT_REQUIRED'; blocker='ALLOW_READY_NO_SEND_REQUIRED'
    elif age > max_preflight_age_seconds:
        status='PREFLIGHT_EXPIRED'; blocker='FRESH_PREFLIGHT_REQUIRED'
    else:
        status='AWAITING_OPERATOR_AUTHORIZATION'; blocker=None
    core={
        'source_receipt_sha256':sha(src), 'source_request_id':src['request_id'], 'source_attempted_at':src['attempted_at'],
        'destination_alias':src['destination_alias'], 'manifest_sha256':src['fingerprints']['manifest_sha256'],
        'config_semantic_sha256':src['fingerprints']['config_semantic_sha256'], 'guard_audit_record_hash':src['fingerprints']['guard_audit_record_hash'],
        'reviewed_at':reviewed_at, 'scope':SCOPE,
    }
    review_id=sha(core)[:32]
    approval=f'APPROVE_SEND:{review_id}' if status=='AWAITING_OPERATOR_AUTHORIZATION' else None
    expires=ttext(reviewed+timedelta(seconds=authorization_ttl_seconds)) if approval else None
    return {
        'schema':REVIEW_SCHEMA,'version':V,'status':status,'review_id':review_id,'reviewed_at':reviewed_at,
        'source':{
            'receipt_sha256':core['source_receipt_sha256'],'request_id':src['request_id'],'attempted_at':src['attempted_at'],
            'age_seconds':int(age),'max_age_seconds':max_preflight_age_seconds,'guard_decision':src.get('guard_decision'),
            'guard_reason':src.get('guard_reason'),'destination_alias':src['destination_alias'],
            'manifest_sha256':core['manifest_sha256'],'config_semantic_sha256':core['config_semantic_sha256'],
            'guard_audit_record_hash':core['guard_audit_record_hash'],
        },
        'blocker':blocker,
        'operator_authorization':{
            'possible_now':approval is not None,'approval_code':approval,'scope':SCOPE,'expires_at':expires,
            'generic_go_is_authorization':False,'single_use_required':True,'consumption_ledger_required':True,
        },
        'contract':{
            'review_only':True,'network_call':False,'send_performed':False,'delivery_send_authorized':False,
            'deployment_authorized':False,'webhook_registration_authorized':False,'raw_runtime_values_required':False,
            'separate_executor_required':True,
        },
        'safety':{'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY'},
    }


def authorize(review:dict[str,Any], approval_code:str, authorized_at:str)->dict[str,Any]:
    if review.get('schema')!=REVIEW_SCHEMA or review.get('status')!='AWAITING_OPERATOR_AUTHORIZATION': raise ValueError('review is not authorization-eligible')
    op=review.get('operator_authorization'); src=review.get('source')
    if not isinstance(op,dict) or not isinstance(src,dict): raise ValueError('malformed send review')
    expected=op.get('approval_code')
    if not isinstance(expected,str) or approval_code.strip()!=expected: raise ValueError('exact review-bound send approval code required')
    at=ts(authorized_at); reviewed=ts(review['reviewed_at']); expires=ts(op['expires_at'])
    if at < reviewed: raise ValueError('authorized_at precedes review')
    if at > expires: raise ValueError('send authorization expired')
    target={
        'review_id':review['review_id'],'source_receipt_sha256':src['receipt_sha256'],'source_request_id':src['request_id'],
        'destination_alias':src['destination_alias'],'manifest_sha256':src['manifest_sha256'],
        'config_semantic_sha256':src['config_semantic_sha256'],'guard_audit_record_hash':src['guard_audit_record_hash'],
        'authorized_at':authorized_at,'scope':SCOPE,
    }
    return {
        'schema':AUTH_SCHEMA,'version':V,'status':'AUTHORIZED_ONE_SEND_NO_EXECUTION','authorization_id':sha(target)[:32],
        'authorized_at':authorized_at,'expires_at':op['expires_at'],'review_id':review['review_id'],'scope':SCOPE,
        'target':{
            'source_receipt_sha256':src['receipt_sha256'],'source_request_id':src['request_id'],'destination_alias':src['destination_alias'],
            'manifest_sha256':src['manifest_sha256'],'config_semantic_sha256':src['config_semantic_sha256'],
            'guard_audit_record_hash':src['guard_audit_record_hash'],
        },
        'contract':{
            'send_execution_authorized':True,'single_use_required':True,'consumption_ledger_required':True,
            'send_performed':False,'network_call':False,'deployment_authorized':False,'webhook_registration_authorized':False,
            'separate_executor_required':True,'executor_must_revalidate_fresh_state':True,
        },
        'safety':{'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY'},
    }


def render(review:dict[str,Any])->str:
    e=html.escape; op=review['operator_authorization']; src=review['source']
    code=e(str(op['approval_code'] or '—'))
    return '<!doctype html><html><head><meta charset="utf-8"><title>TradingOS Send Review</title><style>body{background:#071019;color:#eef6fa;font:14px system-ui}main{max-width:900px;margin:auto;padding:32px}article{background:#0d1823;border:1px solid #263746;border-radius:14px;padding:18px;margin:12px 0}code{color:#d9eff9}</style></head><body><main>'+f'<h1>{e(review["status"])}</h1><article><b>Review</b><p><code>{e(review["review_id"])}</code></p><p>source {e(src["request_id"])} · age {src["age_seconds"]}s</p></article><article><b>Approval code</b><p><code>{code}</code></p><p>review only · send performed=false · network=false</p></article></main></body></html>'


def generate(src_path:Path,reviewed_at:str,out_dir:Path,max_age:int=300,ttl:int=300)->tuple[dict[str,Any],Path,Path]:
    r=build_review(read_json(src_path),reviewed_at,max_age,ttl); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    jp=out/'send_review.json'; hp=out/'send_review.html'; jp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n'); hp.write_text(render(r),encoding='utf-8',newline='\n'); return r,jp,hp


def main()->int:
    p=argparse.ArgumentParser(description='Create or authorize a TradingOS one-send review without executing a network send')
    p.add_argument('--preflight-receipt',type=Path,required=True); p.add_argument('--reviewed-at',required=True); p.add_argument('--out-dir',type=Path,required=True)
    p.add_argument('--max-preflight-age-seconds',type=int,default=300); p.add_argument('--authorization-ttl-seconds',type=int,default=300)
    p.add_argument('--approval-code'); p.add_argument('--authorized-at')
    a=p.parse_args()
    try:
        review,jp,hp=generate(a.preflight_receipt.resolve(),a.reviewed_at,a.out_dir.resolve(),a.max_preflight_age_seconds,a.authorization_ttl_seconds)
        auth_path=None; auth=None
        if a.approval_code or a.authorized_at:
            if not a.approval_code or not a.authorized_at: raise ValueError('approval-code and authorized-at are required together')
            auth=authorize(review,a.approval_code,a.authorized_at); auth_path=a.out_dir.resolve()/'send_authorization.json'; auth_path.write_text(json.dumps(auth,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    except Exception as exc:
        print(json.dumps({'result':'ERROR','error':str(exc),'can_trade':False,'capital_permission':'DENY'},indent=2)); return 2
    print(json.dumps({'result':'PASS','review_status':review['status'],'review_id':review['review_id'],'authorization_status':auth.get('status') if auth else None,'send_performed':False,'network_call':False,'review_json':str(jp),'review_html':str(hp),'authorization_json':str(auth_path) if auth_path else None,'can_trade':False,'capital_permission':'DENY'},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
