#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, html, json, os, re
from pathlib import Path
from typing import Any

V='1.0.0'
REVIEW_SCHEMA='tradingos.delivery.deploy_permission_review.v1'
AUTH_SCHEMA='tradingos.delivery.deploy_permission_authorization.v1'
CONFIG_SCHEMA='tradingos.delivery.security_config.v1'
ENV=re.compile(r'^[A-Z][A-Z0-9_]{2,127}$')
ALIAS=re.compile(r'^[A-Za-z0-9._-]{1,80}$')
H64=re.compile(r'^[0-9a-f]{64}$')
SCOPE='GRANT_DEPLOY_PERMISSION_ONLY_NO_SEND'

def canon(v:Any)->str: return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha_text(v:str)->str: return hashlib.sha256(v.encode()).hexdigest()
def fp(v:Any)->str: return sha_text(canon(v))
def readj(p:Path)->dict[str,Any]: return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def _contract()->dict[str,Any]: return {'deploy_permission_apply_performed':False,'security_config_modified':False,'credential_values_persisted':False,'network_call':False,'delivery_send_authorized':False,'deployment_performed':False}
def _safety()->dict[str,Any]: return {'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY','deploy_permission':'DENY'}

def inspect_cfg(cfg:dict[str,Any])->dict[str,Any]:
    if cfg.get('schema')!=CONFIG_SCHEMA or cfg.get('version')!=1: raise ValueError('unsupported security config')
    if cfg.get('deploy_permission')!='DENY': raise ValueError('review requires deploy_permission=DENY baseline')
    cr=cfg.get('credentials')
    if not isinstance(cr,dict): raise ValueError('invalid credentials contract')
    if {'bot_token','telegram_bot_token','secret','callback_hmac_secret','password','api_key'} & set(cr): raise ValueError('inline credentials forbidden')
    token_env=cr.get('telegram_bot_token_env'); hmac_env=cr.get('callback_hmac_secret_env')
    for name in (token_env,hmac_env):
        if not isinstance(name,str) or not ENV.fullmatch(name): raise ValueError('invalid credential env reference')
    if token_env==hmac_env: raise ValueError('credential env references must be distinct')
    bindings=cfg.get('destination_bindings')
    if bindings in ({},None):
        return {'mode':cfg.get('mode'),'bound':False,'token_env':token_env,'hmac_env':hmac_env,'binding':None}
    if not isinstance(bindings,dict) or len(bindings)!=1: raise ValueError('exactly one destination binding required')
    alias,row=next(iter(bindings.items()))
    if not isinstance(alias,str) or not ALIAS.fullmatch(alias): raise ValueError('invalid destination alias')
    if not isinstance(row,dict) or set(row)!={'transport','destination_env','destination_sha256'}: raise ValueError('invalid destination binding contract')
    if row.get('transport')!='telegram': raise ValueError('unsupported transport')
    denv=row.get('destination_env'); hx=row.get('destination_sha256')
    if not isinstance(denv,str) or not ENV.fullmatch(denv): raise ValueError('invalid destination env reference')
    if denv in {token_env,hmac_env}: raise ValueError('destination env collides with credential env')
    if not isinstance(hx,str) or not H64.fullmatch(hx): raise ValueError('invalid destination hash')
    return {'mode':cfg.get('mode'),'bound':True,'token_env':token_env,'hmac_env':hmac_env,'binding':{'alias':alias,'destination_env':denv,'destination_sha256':hx}}

def presence(meta:dict[str,Any],environ:dict[str,str]|None=None)->dict[str,Any]:
    env=os.environ if environ is None else environ
    names=[]
    if meta.get('binding'): names.append(meta['binding']['destination_env'])
    names.extend([meta['token_env'],meta['hmac_env']])
    rows=[{'env_name':n,'present':bool(env.get(n))} for n in names]
    return {'required_env_names':names,'presence':rows,'all_present':all(x['present'] for x in rows),'values_persisted':False}

def review(cfg:dict[str,Any],environ:dict[str,str]|None=None)->dict[str,Any]:
    meta=inspect_cfg(cfg); cfg_sha=fp(cfg); runtime=presence(meta,environ)
    blockers=[]
    if not meta['bound']: blockers.append('DESTINATION_BINDING_REQUIRED')
    if meta.get('mode')!='ENABLED': blockers.append('ACTIVATION_REQUIRED')
    if blockers:
        seed={'config':cfg_sha,'blockers':blockers}
        return {'schema':REVIEW_SCHEMA,'version':V,'review_id':sha_text(canon(seed))[:32],'status':'BLOCKED_ACTIVATION_REQUIRED','authorization_possible_now':False,'blockers':blockers,'config_precondition_sha256':cfg_sha,'runtime':runtime,'config_patch_preview':None,'operator_authorization':{'status':'NOT_AVAILABLE','approval_code':None,'scope':SCOPE},'contract':_contract(),'safety':_safety()}
    missing=[x['env_name'] for x in runtime['presence'] if not x['present']]
    b=meta['binding']; seed={'config':cfg_sha,'alias':b['alias'],'destination_sha256':b['destination_sha256'],'runtime_presence':runtime['presence']}
    review_id=sha_text(canon(seed))[:32]
    base={'schema':REVIEW_SCHEMA,'version':V,'review_id':review_id,'config_precondition_sha256':cfg_sha,'destination_alias':b['alias'],'destination_sha256':b['destination_sha256'],'runtime':runtime,'contract':_contract(),'safety':_safety()}
    if missing:
        base.update({'status':'RUNTIME_INPUTS_MISSING','authorization_possible_now':False,'blockers':[f'ENV_MISSING:{x}' for x in missing],'config_patch_preview':None,'operator_authorization':{'status':'NOT_AVAILABLE','approval_code':None,'scope':SCOPE}}); return base
    patch={'schema':'tradingos.delivery.deploy_permission_patch_preview.v1','apply':False,'precondition_config_sha256':cfg_sha,'set':{'deploy_permission':'ALLOW'},'preserve':['mode=ENABLED','credentials','destination_bindings','adapter_id','rate_limits','callback_max_age_seconds'],'forbidden_changes':['credentials','destination_bindings','mode','network','webhook','deployment'],'postcondition':{'mode':'ENABLED','deploy_permission':'ALLOW','network_call':False,'delivery_send_authorized':False,'authenticated_preflight_required':True}}
    base.update({'status':'AWAITING_OPERATOR_AUTHORIZATION','authorization_possible_now':True,'blockers':['EXPLICIT_DEPLOY_PERMISSION_AUTHORIZATION_REQUIRED'],'config_patch_preview':patch,'operator_authorization':{'status':'AWAITING','approval_code':f'APPROVE_DEPLOY_PERMISSION:{review_id}','scope':SCOPE,'does_not_authorize':['network_delivery','message_send','webhook_registration','deployment','credential_changes']}})
    return base

def validate_authorization(r:dict[str,Any],auth:dict[str,Any],cfg:dict[str,Any])->dict[str,Any]:
    if r.get('schema')!=REVIEW_SCHEMA or r.get('status')!='AWAITING_OPERATOR_AUTHORIZATION': raise ValueError('deploy permission review is not awaiting authorization')
    meta=inspect_cfg(cfg)
    if meta.get('mode')!='ENABLED' or not meta.get('bound'): raise ValueError('deploy permission prerequisites changed')
    if fp(cfg)!=r.get('config_precondition_sha256'): raise ValueError('deploy permission config precondition mismatch')
    if auth.get('schema')!=AUTH_SCHEMA or auth.get('version')!=V: raise ValueError('unsupported deploy permission authorization')
    if auth.get('review_id')!=r.get('review_id') or auth.get('destination_sha256')!=r.get('destination_sha256'): raise ValueError('deploy permission authorization target mismatch')
    if auth.get('approval_code')!=r['operator_authorization']['approval_code'] or auth.get('authorized') is not True: raise ValueError('explicit deploy permission authorization missing')
    if auth.get('scope')!=SCOPE: raise ValueError('deploy permission authorization scope mismatch')
    return {'schema':'tradingos.delivery.deploy_permission_authorization_receipt.v1','version':V,'status':'AUTHORIZED_FOR_DEPLOY_PERMISSION_APPLY_ONLY','review_id':r['review_id'],'destination_alias':r['destination_alias'],'destination_sha256':r['destination_sha256'],'scope':SCOPE,'deploy_permission_apply_performed':False,'security_config_modified':False,'network_call':False,'delivery_send_authorized':False,'deploy_permission':'DENY','does_not_authorize':['network_delivery','message_send','webhook_registration','deployment','credential_changes']}

def render(r:dict[str,Any])->str:
    e=html.escape; blockers=''.join(f'<li><code>{e(x)}</code></li>' for x in r.get('blockers',[])); presence_html=''.join(f'<li><code>{e(x["env_name"])}</code>: {"present" if x["present"] else "missing"}</li>' for x in r.get('runtime',{}).get('presence',[])); code=r.get('operator_authorization',{}).get('approval_code') or 'NOT_AVAILABLE'
    return f'<!doctype html><html><head><meta charset="utf-8"><title>TradingOS Deploy Permission Review</title><style>body{{background:#071019;color:#eef6fb;font:14px system-ui}}main{{max-width:960px;margin:auto;padding:32px}}article{{background:#0d1823;border:1px solid #263746;border-radius:16px;padding:18px;margin:12px}}p,li{{color:#a8bac7}}code{{color:#e7f3fa}}</style></head><body><main><h1>Deploy Permission Review</h1><article><h2>{e(r["status"])}</h2><ul>{blockers}</ul><ul>{presence_html}</ul><p>Approval code: <code>{e(code)}</code></p></article><p>apply=false · send=false · network=false · deployment=false · deploy_permission remains DENY</p></main></body></html>'

def generate(config:Path,out_dir:Path,environ:dict[str,str]|None=None)->tuple[dict[str,Any],Path,Path]:
    r=review(readj(config),environ); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); jp=out/'deploy_permission_review.json'; hp=out/'deploy_permission_review.html'; jp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n'); hp.write_text(render(r),encoding='utf-8'); return r,jp,hp

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--config',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    try:r,j,h=generate(a.config,a.out_dir)
    except Exception as exc: print(json.dumps({'result':'ERROR','error':str(exc),'can_trade':False,'deploy_permission':'DENY'},indent=2)); return 2
    print(json.dumps({'result':'PASS','status':r['status'],'review_id':r['review_id'],'authorization_possible_now':r['authorization_possible_now'],'deploy_permission_apply_performed':False,'delivery_send_authorized':False,'network_call':False,'deploy_permission':'DENY','json':str(j),'html':str(h)},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
