#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, html, json, re
from pathlib import Path
from typing import Any

V='1.0.0'
REVIEW_SCHEMA='tradingos.delivery.binding_review.v1'
AUTH_SCHEMA='tradingos.delivery.binding_authorization.v1'
REQUEST_SCHEMA='tradingos.delivery.destination_intake_request.v1'
PACKAGE_SCHEMA='tradingos.delivery.binding_package.v1'
CONFIG_SCHEMA='tradingos.delivery.security_config.v1'
H64=re.compile(r'^[0-9a-f]{64}$')
ENV=re.compile(r'^[A-Z][A-Z0-9_]{2,127}$')
ALIAS=re.compile(r'^[A-Za-z0-9._-]{1,80}$')

def canon(v:Any)->str: return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha_text(v:str)->str: return hashlib.sha256(v.encode()).hexdigest()
def fp(v:Any)->str: return sha_text(canon(v))
def readj(p:Path)->dict[str,Any]: return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def validate_cfg(c:dict[str,Any])->None:
    if c.get('schema')!=CONFIG_SCHEMA or c.get('version')!=1: raise ValueError('unsupported security config')
    if c.get('mode')!='DISABLED' or c.get('deploy_permission')!='DENY': raise ValueError('source config must remain DISABLED/DENY')
    if c.get('destination_bindings') not in ({},None): raise ValueError('source config already has destination binding')
    cr=c.get('credentials')
    if not isinstance(cr,dict): raise ValueError('invalid credentials contract')
    if {'bot_token','telegram_bot_token','secret','callback_hmac_secret','password','api_key'} & set(cr): raise ValueError('inline credentials forbidden')
    for k in ('telegram_bot_token_env','callback_hmac_secret_env'):
        if not isinstance(cr.get(k),str) or not ENV.fullmatch(cr[k]): raise ValueError('invalid credential env reference')

def _contract()->dict[str,Any]:
    return {'binding_apply_performed':False,'security_config_modified':False,'deploy_permission_changed':False,'network_call':False,'deployment_performed':False,'credentials_required':False}

def _safety()->dict[str,Any]:
    return {'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY','deploy_permission':'DENY'}

def blocked_from_request(req:dict[str,Any],cfg:dict[str,Any])->dict[str,Any]:
    validate_cfg(cfg)
    if req.get('schema')!=REQUEST_SCHEMA or req.get('status')!='AWAITING_DESTINATION_INPUT': raise ValueError('expected destination intake request')
    alias=req.get('destination_alias'); env=req.get('destination_env')
    if not isinstance(alias,str) or not ALIAS.fullmatch(alias): raise ValueError('invalid destination alias')
    if not isinstance(env,str) or not ENV.fullmatch(env): raise ValueError('invalid destination env')
    if req.get('destination_sha256') is not None: raise ValueError('input-required request unexpectedly has hash')
    seed={'request':fp(req),'config':fp(cfg),'alias':alias,'destination_env':env}
    return {
        'schema':REVIEW_SCHEMA,'version':V,'review_id':sha_text(canon(seed))[:32],
        'status':'BLOCKED_INPUT_REQUIRED','binding_package_id':None,'destination_alias':alias,'destination_env':env,'destination_sha256':None,
        'authorization_required':True,'authorization_possible_now':False,
        'blockers':['DESTINATION_VALUE_REQUIRED','HASH_READY_PACKAGE_REQUIRED'],
        'config_patch_preview':None,
        'operator_authorization':{'status':'NOT_AVAILABLE_UNTIL_HASH_READY','approval_code':None,'scope':'BIND_DESTINATION_HASH_ONLY_KEEP_DISABLED_DENY'},
        'contract':_contract(),'safety':_safety(),
    }

def review_hash_ready(pkg:dict[str,Any],cfg:dict[str,Any])->dict[str,Any]:
    validate_cfg(cfg)
    if pkg.get('schema')!=PACKAGE_SCHEMA or pkg.get('status')!='HASH_READY': raise ValueError('expected HASH_READY binding package')
    br=pkg.get('binding_request')
    if not isinstance(br,dict): raise ValueError('missing binding request')
    alias=br.get('destination_alias'); env=br.get('destination_env'); hx=br.get('destination_sha256')
    if not isinstance(alias,str) or not ALIAS.fullmatch(alias): raise ValueError('invalid destination alias')
    if not isinstance(env,str) or not ENV.fullmatch(env): raise ValueError('invalid destination env')
    if not isinstance(hx,str) or not H64.fullmatch(hx): raise ValueError('invalid destination hash')
    if pkg.get('contract',{}).get('binding_apply_performed') is not False or pkg.get('contract',{}).get('network_call') is not False: raise ValueError('unsafe binding package contract')
    seed={'package_id':pkg.get('package_id'),'package':fp(pkg),'config':fp(cfg),'hash':hx}
    review_id=sha_text(canon(seed))[:32]
    approval_code=f'APPROVE_BINDING:{review_id}'
    patch={
        'schema':'tradingos.delivery.binding_config_patch_preview.v1','apply':False,
        'precondition_config_sha256':fp(cfg),
        'set':{
            'destination_bindings':{
                alias:{'transport':'telegram','destination_env':env,'destination_sha256':hx}
            },
            'mode':'DISABLED','deploy_permission':'DENY'
        },
        'forbidden_changes':['credentials','mode=ENABLED','deploy_permission=ALLOW','webhook','network'],
        'postcondition':{'mode':'DISABLED','deploy_permission':'DENY','network_call':False}
    }
    return {
        'schema':REVIEW_SCHEMA,'version':V,'review_id':review_id,
        'status':'AWAITING_OPERATOR_AUTHORIZATION','binding_package_id':pkg.get('package_id'),'destination_alias':alias,'destination_env':env,'destination_sha256':hx,
        'authorization_required':True,'authorization_possible_now':True,'blockers':['EXPLICIT_OPERATOR_AUTHORIZATION_REQUIRED'],
        'config_patch_preview':patch,
        'operator_authorization':{
            'status':'AWAITING','approval_code':approval_code,
            'scope':'BIND_DESTINATION_HASH_ONLY_KEEP_DISABLED_DENY',
            'does_not_authorize':['credentials','deploy_permission=ALLOW','mode=ENABLED','network_delivery','deployment']
        },
        'rollback_preview':{'action':'REMOVE_DESTINATION_BINDING','destination_alias':alias,'restore_mode':'DISABLED','restore_deploy_permission':'DENY','performed':False},
        'contract':_contract(),'safety':_safety(),
    }

def validate_authorization(review:dict[str,Any],auth:dict[str,Any])->dict[str,Any]:
    if review.get('schema')!=REVIEW_SCHEMA or review.get('status')!='AWAITING_OPERATOR_AUTHORIZATION': raise ValueError('review is not awaiting authorization')
    if auth.get('schema')!=AUTH_SCHEMA or auth.get('version')!=V: raise ValueError('unsupported authorization')
    if auth.get('review_id')!=review.get('review_id') or auth.get('binding_package_id')!=review.get('binding_package_id'): raise ValueError('authorization target mismatch')
    if auth.get('destination_sha256')!=review.get('destination_sha256'): raise ValueError('authorization hash mismatch')
    expected=review['operator_authorization']['approval_code']
    if auth.get('approval_code')!=expected or auth.get('authorized') is not True: raise ValueError('explicit authorization missing')
    if auth.get('scope')!='BIND_DESTINATION_HASH_ONLY_KEEP_DISABLED_DENY': raise ValueError('authorization scope mismatch')
    return {
        'schema':'tradingos.delivery.binding_authorization_receipt.v1','version':V,'status':'AUTHORIZED_FOR_BINDING_APPLY_ONLY',
        'review_id':review['review_id'],'binding_package_id':review['binding_package_id'],'destination_sha256':review['destination_sha256'],
        'scope':auth['scope'],'binding_apply_performed':False,'network_call':False,'deploy_permission':'DENY',
        'does_not_authorize':['credentials','deploy_permission=ALLOW','mode=ENABLED','network_delivery','deployment']
    }

def render(r:dict[str,Any])->str:
    e=html.escape; blockers=''.join(f'<li><code>{e(x)}</code></li>' for x in r.get('blockers',[]))
    code=r.get('operator_authorization',{}).get('approval_code') or 'NOT_AVAILABLE'
    return f'<!doctype html><html><head><meta charset="utf-8"><title>TradingOS Binding Authorization</title><style>body{{background:#071019;color:#eef6fb;font:14px system-ui}}main{{max-width:960px;margin:auto;padding:32px}}article{{background:#0d1823;border:1px solid #263746;border-radius:16px;padding:18px;margin:12px}}p,li{{color:#a8bac7}}code{{color:#e7f3fa}}</style></head><body><main><h1>Binding Authorization Gate</h1><article><h2>{e(r["status"])}</h2><p>Review <code>{e(r["review_id"])}</code></p><ul>{blockers}</ul><p>Approval code: <code>{e(code)}</code></p></article><p>apply=false · network=false · deployment=false · deploy_permission=DENY</p></main></body></html>'

def generate(source:Path,security_config:Path,out_dir:Path)->tuple[dict[str,Any],Path,Path]:
    src=readj(source); cfg=readj(security_config)
    if src.get('schema')==REQUEST_SCHEMA: r=blocked_from_request(src,cfg)
    elif src.get('schema')==PACKAGE_SCHEMA: r=review_hash_ready(src,cfg)
    else: raise ValueError('unsupported binding authorization source')
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); jp=out/'binding_review.json'; hp=out/'binding_review.html'
    jp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n'); hp.write_text(render(r),encoding='utf-8')
    return r,jp,hp

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--source',type=Path,required=True); ap.add_argument('--security-config',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    try:r,j,h=generate(a.source,a.security_config,a.out_dir)
    except Exception as exc:
        print(json.dumps({'result':'ERROR','error':str(exc),'can_trade':False,'deploy_permission':'DENY'},indent=2)); return 2
    print(json.dumps({'result':'PASS','status':r['status'],'review_id':r['review_id'],'authorization_possible_now':r['authorization_possible_now'],'binding_apply_performed':False,'network_call':False,'deploy_permission':'DENY','json':str(j),'html':str(h)},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
