#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, html, json, os, re
from pathlib import Path
from typing import Any

V='1.0.0'
REVIEW_SCHEMA='tradingos.delivery.activation_review.v1'
AUTH_SCHEMA='tradingos.delivery.activation_authorization.v1'
CONFIG_SCHEMA='tradingos.delivery.security_config.v1'
ENV=re.compile(r'^[A-Z][A-Z0-9_]{2,127}$')
ALIAS=re.compile(r'^[A-Za-z0-9._-]{1,80}$')
H64=re.compile(r'^[0-9a-f]{64}$')
SCOPE='ENABLE_DELIVERY_ADAPTER_KEEP_DEPLOY_DENY'

def canon(v:Any)->str: return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha_text(v:str)->str: return hashlib.sha256(v.encode()).hexdigest()
def fp(v:Any)->str: return sha_text(canon(v))
def readj(p:Path)->dict[str,Any]: return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def _contract()->dict[str,Any]:
    return {'activation_apply_performed':False,'security_config_modified':False,'deploy_permission_changed':False,'credential_values_read_for_output':False,'credential_values_persisted':False,'network_call':False,'deployment_performed':False}
def _safety()->dict[str,Any]: return {'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY','deploy_permission':'DENY'}

def validate_cfg(cfg:dict[str,Any])->dict[str,Any]:
    if cfg.get('schema')!=CONFIG_SCHEMA or cfg.get('version')!=1: raise ValueError('unsupported security config')
    if cfg.get('mode')!='DISABLED' or cfg.get('deploy_permission')!='DENY': raise ValueError('activation review requires DISABLED/DENY baseline')
    cr=cfg.get('credentials')
    if not isinstance(cr,dict): raise ValueError('invalid credentials contract')
    if {'bot_token','telegram_bot_token','secret','callback_hmac_secret','password','api_key'} & set(cr): raise ValueError('inline credentials forbidden')
    token_env=cr.get('telegram_bot_token_env'); hmac_env=cr.get('callback_hmac_secret_env')
    for name in (token_env,hmac_env):
        if not isinstance(name,str) or not ENV.fullmatch(name): raise ValueError('invalid credential env reference')
    if token_env==hmac_env: raise ValueError('credential env references must be distinct')
    bindings=cfg.get('destination_bindings')
    if bindings in ({},None):
        return {'bound':False,'adapter_id':cfg.get('adapter_id'),'token_env':token_env,'hmac_env':hmac_env,'binding':None}
    if not isinstance(bindings,dict) or len(bindings)!=1: raise ValueError('exactly one destination binding required for activation review')
    alias,row=next(iter(bindings.items()))
    if not isinstance(alias,str) or not ALIAS.fullmatch(alias): raise ValueError('invalid destination alias')
    if not isinstance(row,dict) or set(row)!={'transport','destination_env','destination_sha256'}: raise ValueError('invalid destination binding contract')
    if row.get('transport')!='telegram': raise ValueError('unsupported destination transport')
    denv=row.get('destination_env'); hx=row.get('destination_sha256')
    if not isinstance(denv,str) or not ENV.fullmatch(denv): raise ValueError('invalid destination env reference')
    if denv in {token_env,hmac_env}: raise ValueError('destination env collides with credential env')
    if not isinstance(hx,str) or not H64.fullmatch(hx): raise ValueError('invalid destination hash')
    return {'bound':True,'adapter_id':cfg.get('adapter_id'),'token_env':token_env,'hmac_env':hmac_env,'binding':{'alias':alias,'transport':'telegram','destination_env':denv,'destination_sha256':hx}}

def runtime_presence(meta:dict[str,Any],environ:dict[str,str]|None=None)->dict[str,Any]:
    env=os.environ if environ is None else environ
    names=[]
    if meta.get('binding'): names.append(meta['binding']['destination_env'])
    names.extend([meta['token_env'],meta['hmac_env']])
    rows=[]
    for name in names:
        value=env.get(name)
        rows.append({'env_name':name,'present':bool(value)})
    return {'required_env_names':names,'presence':rows,'all_present':all(x['present'] for x in rows)}

def review(cfg:dict[str,Any],environ:dict[str,str]|None=None)->dict[str,Any]:
    meta=validate_cfg(cfg); cfg_sha=fp(cfg)
    if not meta['bound']:
        seed={'config':cfg_sha,'status':'BLOCKED_BINDING_REQUIRED'}
        return {
            'schema':REVIEW_SCHEMA,'version':V,'review_id':sha_text(canon(seed))[:32],
            'status':'BLOCKED_BINDING_REQUIRED','authorization_possible_now':False,'blockers':['DESTINATION_BINDING_REQUIRED'],
            'config_precondition_sha256':cfg_sha,'destination_alias':None,'destination_sha256':None,
            'runtime':{'required_env_names':[meta['token_env'],meta['hmac_env']],'presence':[],'all_present':False,'values_persisted':False},
            'config_patch_preview':None,'operator_authorization':{'approval_code':None,'scope':SCOPE,'status':'NOT_AVAILABLE'},
            'contract':_contract(),'safety':_safety(),
        }
    runtime=runtime_presence(meta,environ)
    b=meta['binding']
    seed={'config':cfg_sha,'alias':b['alias'],'destination_sha256':b['destination_sha256'],'runtime_presence':runtime['presence']}
    review_id=sha_text(canon(seed))[:32]
    base={
        'schema':REVIEW_SCHEMA,'version':V,'review_id':review_id,'config_precondition_sha256':cfg_sha,
        'destination_alias':b['alias'],'destination_sha256':b['destination_sha256'],'destination_env':b['destination_env'],
        'runtime':{**runtime,'values_persisted':False,'secret_policy_validation':'DELEGATED_TO_AUTHENTICATED_PREFLIGHT'},
        'contract':_contract(),'safety':_safety(),
    }
    missing=[x['env_name'] for x in runtime['presence'] if not x['present']]
    if missing:
        base.update({'status':'RUNTIME_INPUTS_MISSING','authorization_possible_now':False,'blockers':[f'ENV_MISSING:{x}' for x in missing],'config_patch_preview':None,'operator_authorization':{'approval_code':None,'scope':SCOPE,'status':'NOT_AVAILABLE'}})
        return base
    code=f'APPROVE_ACTIVATION:{review_id}'
    patch={
        'schema':'tradingos.delivery.activation_config_patch_preview.v1','apply':False,
        'precondition_config_sha256':cfg_sha,
        'set':{'mode':'ENABLED','deploy_permission':'DENY'},
        'preserve':['credentials','destination_bindings','adapter_id','rate_limits','callback_max_age_seconds'],
        'forbidden_changes':['credentials','destination_bindings','deploy_permission=ALLOW','network','webhook','deployment'],
        'postcondition':{'mode':'ENABLED','deploy_permission':'DENY','network_call':False,'allow_ready_possible':False},
    }
    base.update({
        'status':'AWAITING_OPERATOR_AUTHORIZATION','authorization_possible_now':True,'blockers':['EXPLICIT_ACTIVATION_AUTHORIZATION_REQUIRED'],
        'config_patch_preview':patch,
        'operator_authorization':{'status':'AWAITING','approval_code':code,'scope':SCOPE,'does_not_authorize':['deploy_permission=ALLOW','network_delivery','deployment','credential_changes']},
    })
    return base

def validate_authorization(r:dict[str,Any],auth:dict[str,Any],cfg:dict[str,Any])->dict[str,Any]:
    if r.get('schema')!=REVIEW_SCHEMA or r.get('status')!='AWAITING_OPERATOR_AUTHORIZATION': raise ValueError('activation review is not awaiting authorization')
    validate_cfg(cfg)
    if fp(cfg)!=r.get('config_precondition_sha256'): raise ValueError('activation config precondition mismatch')
    if auth.get('schema')!=AUTH_SCHEMA or auth.get('version')!=V: raise ValueError('unsupported activation authorization')
    if auth.get('review_id')!=r.get('review_id') or auth.get('destination_sha256')!=r.get('destination_sha256'): raise ValueError('activation authorization target mismatch')
    if auth.get('approval_code')!=r['operator_authorization']['approval_code'] or auth.get('authorized') is not True: raise ValueError('explicit activation authorization missing')
    if auth.get('scope')!=SCOPE: raise ValueError('activation authorization scope mismatch')
    return {
        'schema':'tradingos.delivery.activation_authorization_receipt.v1','version':V,'status':'AUTHORIZED_FOR_ACTIVATION_APPLY_ONLY',
        'review_id':r['review_id'],'destination_alias':r['destination_alias'],'destination_sha256':r['destination_sha256'],'scope':SCOPE,
        'activation_apply_performed':False,'security_config_modified':False,'network_call':False,'deploy_permission':'DENY',
        'does_not_authorize':['deploy_permission=ALLOW','network_delivery','deployment','credential_changes'],
    }

def render(r:dict[str,Any])->str:
    e=html.escape
    blockers=''.join(f'<li><code>{e(x)}</code></li>' for x in r.get('blockers',[]))
    presence=''.join(f'<li><code>{e(x["env_name"])}</code>: {"present" if x["present"] else "missing"}</li>' for x in r.get('runtime',{}).get('presence',[]))
    code=r.get('operator_authorization',{}).get('approval_code') or 'NOT_AVAILABLE'
    return f'<!doctype html><html><head><meta charset="utf-8"><title>TradingOS Activation Readiness</title><style>body{{background:#071019;color:#eef6fb;font:14px system-ui}}main{{max-width:960px;margin:auto;padding:32px}}article{{background:#0d1823;border:1px solid #263746;border-radius:16px;padding:18px;margin:12px}}p,li{{color:#a8bac7}}code{{color:#e7f3fa}}</style></head><body><main><h1>Delivery Activation Readiness</h1><article><h2>{e(r["status"])}</h2><ul>{blockers}</ul><h3>Runtime env presence</h3><ul>{presence}</ul><p>Approval code: <code>{e(code)}</code></p></article><p>apply=false · network=false · deployment=false · deploy_permission=DENY</p></main></body></html>'

def generate(config:Path,out_dir:Path,environ:dict[str,str]|None=None)->tuple[dict[str,Any],Path,Path]:
    r=review(readj(config),environ); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    jp=out/'activation_review.json'; hp=out/'activation_review.html'
    jp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n'); hp.write_text(render(r),encoding='utf-8')
    return r,jp,hp

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--config',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    try:r,j,h=generate(a.config,a.out_dir)
    except Exception as exc:
        print(json.dumps({'result':'ERROR','error':str(exc),'can_trade':False,'deploy_permission':'DENY'},indent=2)); return 2
    print(json.dumps({'result':'PASS','status':r['status'],'review_id':r['review_id'],'authorization_possible_now':r['authorization_possible_now'],'activation_apply_performed':False,'network_call':False,'deploy_permission':'DENY','json':str(j),'html':str(h)},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
