#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os
from pathlib import Path
from typing import Any

V='1.0.0'
APPLY_SCHEMA='tradingos.delivery.deploy_permission_apply_receipt.v1'
ROLLBACK_SCHEMA='tradingos.delivery.deploy_permission_rollback_receipt.v1'
PLAN_SCHEMA='tradingos.delivery.deploy_permission_apply_plan.v1'
ROOT=Path(__file__).resolve().parent
_spec=importlib.util.spec_from_file_location('deploy_permission_review',ROOT/'tradingos_deploy_permission_review.py')
if not _spec or not _spec.loader: raise RuntimeError('deploy permission review module unavailable')
review_mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(review_mod)

def canon(v:Any)->str: return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha_bytes(v:bytes)->str: return hashlib.sha256(v).hexdigest()
def sha_obj(v:Any)->str: return sha_bytes(canon(v).encode())
def readj(p:Path)->dict[str,Any]: return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def _contract(applied:bool=False,modified:bool=False,changed:bool=False)->dict[str,Any]:
    return {'deploy_permission_apply_performed':applied,'security_config_modified':modified,'deploy_permission_changed':changed,'credential_values_persisted':False,'network_call':False,'delivery_send_authorized':False,'deployment_performed':False}
def _safety(permission:str='DENY')->dict[str,Any]: return {'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY','deploy_permission':permission}

def _validate_patch(review:dict[str,Any])->None:
    patch=review.get('config_patch_preview')
    if not isinstance(patch,dict) or patch.get('apply') is not False: raise ValueError('invalid deploy permission patch preview')
    if patch.get('precondition_config_sha256')!=review.get('config_precondition_sha256'): raise ValueError('deploy permission patch precondition mismatch')
    if patch.get('set')!={'deploy_permission':'ALLOW'}: raise ValueError('deploy permission patch scope invalid')
    post=patch.get('postcondition')
    if not isinstance(post,dict) or post.get('mode')!='ENABLED' or post.get('deploy_permission')!='ALLOW' or post.get('network_call') is not False or post.get('delivery_send_authorized') is not False or post.get('authenticated_preflight_required') is not True: raise ValueError('deploy permission patch postcondition invalid')

def evaluate(cfg:dict[str,Any],environ:dict[str,str]|None=None,authorization:dict[str,Any]|None=None,review:dict[str,Any]|None=None)->dict[str,Any]:
    current=review_mod.review(cfg,environ) if review is None else review
    status=current.get('status')
    if status in {'BLOCKED_ACTIVATION_REQUIRED','RUNTIME_INPUTS_MISSING'}:
        if authorization is not None: raise ValueError('deploy permission authorization forbidden while prerequisites are blocked')
        return {'schema':PLAN_SCHEMA,'version':V,'status':status,'review_id':current.get('review_id'),'authorization_validated':False,'blockers':list(current.get('blockers',[])),'contract':_contract(),'safety':_safety()}
    if status!='AWAITING_OPERATOR_AUTHORIZATION': raise ValueError('deploy permission review is not apply-eligible')
    _validate_patch(current)
    if current.get('config_precondition_sha256')!=sha_obj(cfg): raise ValueError('deploy permission config precondition mismatch')
    if current.get('runtime',{}).get('all_present') is not True: raise ValueError('runtime prerequisites no longer present')
    if authorization is None:
        return {'schema':PLAN_SCHEMA,'version':V,'status':'AWAITING_OPERATOR_AUTHORIZATION','review_id':current['review_id'],'authorization_validated':False,'blockers':['EXPLICIT_DEPLOY_PERMISSION_AUTHORIZATION_REQUIRED'],'contract':_contract(),'safety':_safety()}
    ar=review_mod.validate_authorization(current,authorization,cfg)
    return {'schema':PLAN_SCHEMA,'version':V,'status':'AUTHORIZED_FOR_DEPLOY_PERMISSION_APPLY_ONLY','review_id':current['review_id'],'authorization_validated':True,'authorized_scope':ar['scope'],'blockers':[],'contract':_contract(),'safety':_safety()}

def apply_permission(config_path:Path,review:dict[str,Any],authorization:dict[str,Any],out_dir:Path,environ:dict[str,str]|None=None)->tuple[dict[str,Any],Path,Path]:
    config_path=Path(config_path); original=config_path.read_bytes(); cfg=json.loads(original.decode('utf-8-sig'))
    current=review_mod.review(cfg,environ)
    if current.get('review_id')!=review.get('review_id') or current.get('status')!='AWAITING_OPERATOR_AUTHORIZATION': raise ValueError('deploy permission review no longer current')
    if current.get('runtime',{}).get('all_present') is not True: raise ValueError('runtime prerequisites no longer present')
    plan=evaluate(cfg,environ,authorization,review)
    if plan['status']!='AUTHORIZED_FOR_DEPLOY_PERMISSION_APPLY_ONLY': raise ValueError('deploy permission apply not authorized')
    before_obj_sha=sha_obj(cfg); before_file_sha=sha_bytes(original)
    candidate=json.loads(json.dumps(cfg)); candidate['deploy_permission']='ALLOW'
    if candidate.get('mode')!='ENABLED': raise ValueError('deploy permission apply requires mode=ENABLED')
    for key in cfg:
        if key!='deploy_permission' and candidate.get(key)!=cfg.get(key): raise ValueError('deploy permission candidate changed preserved field')
    if candidate.get('credentials')!=cfg.get('credentials') or candidate.get('destination_bindings')!=cfg.get('destination_bindings'): raise ValueError('deploy permission candidate changed protected fields')
    candidate_bytes=(json.dumps(candidate,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    after_obj_sha=sha_obj(candidate); after_file_sha=sha_bytes(candidate_bytes)
    if after_obj_sha==before_obj_sha: raise ValueError('deploy permission apply produced no semantic change')
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    backup=out/'security_config.before_deploy_permission.json'; backup.write_bytes(original)
    if sha_bytes(backup.read_bytes())!=before_file_sha: raise ValueError('deploy permission backup verification failed')
    tmp=config_path.with_name(config_path.name+'.tradingos-deploy-permission-tmp')
    try:
        with open(tmp,'wb') as f:
            f.write(candidate_bytes); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,config_path)
    finally:
        if tmp.exists(): tmp.unlink()
    readback=config_path.read_bytes(); parsed=json.loads(readback.decode('utf-8'))
    if sha_bytes(readback)!=after_file_sha or sha_obj(parsed)!=after_obj_sha: raise ValueError('deploy permission post-write hash mismatch')
    if parsed.get('mode')!='ENABLED' or parsed.get('deploy_permission')!='ALLOW': raise ValueError('deploy permission postcondition violated')
    for key in cfg:
        if key!='deploy_permission' and parsed.get(key)!=cfg.get(key): raise ValueError('deploy permission protected field changed')
    receipt={
        'schema':APPLY_SCHEMA,'version':V,'status':'DEPLOY_PERMISSION_APPLIED_ALLOW_NO_SEND','review_id':review['review_id'],'destination_alias':review['destination_alias'],'destination_sha256':review['destination_sha256'],'authorized_scope':plan['authorized_scope'],
        'before_config_object_sha256':before_obj_sha,'before_config_file_sha256':before_file_sha,'after_config_object_sha256':after_obj_sha,'after_config_file_sha256':after_file_sha,'backup_file':backup.name,
        'postcondition':{'mode':'ENABLED','deploy_permission':'ALLOW','authenticated_preflight_required':True,'delivery_send_authorized':False,'network_call':False},'contract':_contract(True,True,True),'safety':_safety('ALLOW'),
    }
    rp=out/'deploy_permission_apply_receipt.json'; rp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return receipt,rp,backup

def rollback_permission(config_path:Path,apply_receipt:dict[str,Any],backup_path:Path,out_dir:Path)->tuple[dict[str,Any],Path]:
    if apply_receipt.get('schema')!=APPLY_SCHEMA or apply_receipt.get('status')!='DEPLOY_PERMISSION_APPLIED_ALLOW_NO_SEND': raise ValueError('invalid deploy permission apply receipt')
    config_path=Path(config_path); current=config_path.read_bytes(); backup=Path(backup_path).read_bytes()
    if sha_bytes(current)!=apply_receipt.get('after_config_file_sha256'): raise ValueError('deploy permission rollback current-state mismatch')
    if sha_bytes(backup)!=apply_receipt.get('before_config_file_sha256'): raise ValueError('deploy permission rollback backup mismatch')
    tmp=config_path.with_name(config_path.name+'.tradingos-deploy-permission-rollback-tmp')
    try:
        with open(tmp,'wb') as f:
            f.write(backup); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,config_path)
    finally:
        if tmp.exists(): tmp.unlink()
    restored_bytes=config_path.read_bytes()
    if sha_bytes(restored_bytes)!=apply_receipt['before_config_file_sha256']: raise ValueError('deploy permission rollback verification failed')
    restored=json.loads(restored_bytes.decode('utf-8-sig')); meta=review_mod.inspect_cfg(restored)
    if meta.get('mode')!='ENABLED' or not meta.get('bound') or restored.get('deploy_permission')!='DENY': raise ValueError('deploy permission rollback restored invalid baseline')
    receipt={'schema':ROLLBACK_SCHEMA,'version':V,'status':'DEPLOY_PERMISSION_ROLLED_BACK_DENY','apply_review_id':apply_receipt['review_id'],'restored_config_file_sha256':apply_receipt['before_config_file_sha256'],'restored_config_object_sha256':sha_obj(restored),'deploy_permission_apply_performed':False,'rollback_performed':True,'network_call':False,'delivery_send_authorized':False,'deploy_permission':'DENY'}
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); rp=out/'deploy_permission_rollback_receipt.json'; rp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return receipt,rp

def main()->int:
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('plan'); p.add_argument('--config',type=Path,required=True)
    a=sub.add_parser('apply'); a.add_argument('--config',type=Path,required=True); a.add_argument('--review',type=Path,required=True); a.add_argument('--authorization',type=Path,required=True); a.add_argument('--out-dir',type=Path,required=True)
    r=sub.add_parser('rollback'); r.add_argument('--config',type=Path,required=True); r.add_argument('--apply-receipt',type=Path,required=True); r.add_argument('--backup',type=Path,required=True); r.add_argument('--out-dir',type=Path,required=True)
    x=ap.parse_args()
    try:
        if x.cmd=='plan': out=evaluate(readj(x.config))
        elif x.cmd=='apply': out,_,_=apply_permission(x.config,readj(x.review),readj(x.authorization),x.out_dir)
        else: out,_=rollback_permission(x.config,readj(x.apply_receipt),x.backup,x.out_dir)
    except Exception as exc:
        print(json.dumps({'result':'ERROR','error':str(exc),'can_trade':False,'deploy_permission':'DENY','delivery_send_authorized':False},indent=2)); return 2
    print(json.dumps({'result':'PASS',**out},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
