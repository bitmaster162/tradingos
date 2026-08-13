#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os
from pathlib import Path
from typing import Any

V='1.0.0'
APPLY_SCHEMA='tradingos.delivery.binding_apply_receipt.v1'
ROLLBACK_SCHEMA='tradingos.delivery.binding_rollback_receipt.v1'
PLAN_SCHEMA='tradingos.delivery.binding_apply_plan.v1'
ROOT=Path(__file__).resolve().parent
_spec=importlib.util.spec_from_file_location('binding_authorization',ROOT/'tradingos_binding_authorization.py')
if not _spec or not _spec.loader: raise RuntimeError('binding authorization module unavailable')
authz=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(authz)

def canon(v:Any)->str: return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha_bytes(v:bytes)->str: return hashlib.sha256(v).hexdigest()
def sha_obj(v:Any)->str: return sha_bytes(canon(v).encode())
def readj(p:Path)->dict[str,Any]: return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def _contract(applied:bool=False,modified:bool=False)->dict[str,Any]:
    return {'binding_apply_performed':applied,'security_config_modified':modified,'deploy_permission_changed':False,'network_call':False,'deployment_performed':False,'credentials_modified':False}
def _safety()->dict[str,Any]: return {'signals_allowed':False,'orders_allowed':False,'can_trade':False,'capital_permission':'DENY','deploy_permission':'DENY'}

def evaluate(review:dict[str,Any],cfg:dict[str,Any],authorization:dict[str,Any]|None=None)->dict[str,Any]:
    authz.validate_cfg(cfg)
    status=review.get('status')
    if review.get('schema')!=authz.REVIEW_SCHEMA: raise ValueError('unsupported binding review')
    if status=='BLOCKED_INPUT_REQUIRED':
        if authorization is not None: raise ValueError('authorization forbidden while input is blocked')
        return {'schema':PLAN_SCHEMA,'version':V,'status':'BLOCKED_INPUT_REQUIRED','review_id':review.get('review_id'),'binding_package_id':None,'authorization_validated':False,'blockers':list(review.get('blockers',[])),'contract':_contract(),'safety':_safety()}
    if status!='AWAITING_OPERATOR_AUTHORIZATION': raise ValueError('review is not apply-eligible')
    patch=review.get('config_patch_preview')
    if not isinstance(patch,dict) or patch.get('apply') is not False: raise ValueError('invalid patch preview')
    if patch.get('precondition_config_sha256')!=sha_obj(cfg): raise ValueError('config precondition mismatch')
    sets=patch.get('set')
    if not isinstance(sets,dict) or set(sets)!={'destination_bindings','mode','deploy_permission'}: raise ValueError('patch scope exceeds destination binding')
    if sets.get('mode')!='DISABLED' or sets.get('deploy_permission')!='DENY': raise ValueError('patch would enable deployment')
    bindings=sets.get('destination_bindings')
    if not isinstance(bindings,dict) or len(bindings)!=1: raise ValueError('exactly one destination binding required')
    alias=review.get('destination_alias')
    if set(bindings)!={alias}: raise ValueError('patch alias mismatch')
    row=bindings[alias]
    if not isinstance(row,dict) or set(row)!={'transport','destination_env','destination_sha256'}: raise ValueError('unexpected destination binding fields')
    if row.get('transport')!='telegram' or row.get('destination_env')!=review.get('destination_env') or row.get('destination_sha256')!=review.get('destination_sha256'): raise ValueError('patch target mismatch')
    if authorization is None:
        return {'schema':PLAN_SCHEMA,'version':V,'status':'AWAITING_OPERATOR_AUTHORIZATION','review_id':review['review_id'],'binding_package_id':review['binding_package_id'],'destination_sha256':review['destination_sha256'],'authorization_validated':False,'blockers':['EXPLICIT_OPERATOR_AUTHORIZATION_REQUIRED'],'contract':_contract(),'safety':_safety()}
    ar=authz.validate_authorization(review,authorization)
    if ar.get('status')!='AUTHORIZED_FOR_BINDING_APPLY_ONLY' or ar.get('binding_apply_performed') is not False: raise ValueError('authorization receipt contract invalid')
    return {'schema':PLAN_SCHEMA,'version':V,'status':'AUTHORIZED_FOR_BINDING_APPLY_ONLY','review_id':review['review_id'],'binding_package_id':review['binding_package_id'],'destination_sha256':review['destination_sha256'],'authorization_validated':True,'blockers':[],'authorized_scope':ar['scope'],'contract':_contract(),'safety':_safety()}

def _candidate(cfg:dict[str,Any],review:dict[str,Any])->dict[str,Any]:
    out=json.loads(json.dumps(cfg))
    patch=review['config_patch_preview']['set']
    out['destination_bindings']=json.loads(json.dumps(patch['destination_bindings']))
    out['mode']='DISABLED'; out['deploy_permission']='DENY'
    if out.get('credentials')!=cfg.get('credentials'): raise ValueError('credentials mutation forbidden')
    if out.get('mode')!='DISABLED' or out.get('deploy_permission')!='DENY': raise ValueError('postcondition violated')
    return out

def apply_binding(config_path:Path,review:dict[str,Any],authorization:dict[str,Any],out_dir:Path)->tuple[dict[str,Any],Path,Path]:
    config_path=Path(config_path); original=config_path.read_bytes(); cfg=json.loads(original.decode('utf-8-sig'))
    plan=evaluate(review,cfg,authorization)
    if plan['status']!='AUTHORIZED_FOR_BINDING_APPLY_ONLY': raise ValueError('binding apply not authorized')
    before_obj_sha=sha_obj(cfg); before_file_sha=sha_bytes(original)
    if review['config_patch_preview']['precondition_config_sha256']!=before_obj_sha: raise ValueError('stale config precondition')
    candidate=_candidate(cfg,review)
    candidate_bytes=(json.dumps(candidate,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    after_obj_sha=sha_obj(candidate); after_file_sha=sha_bytes(candidate_bytes)
    if after_obj_sha==before_obj_sha: raise ValueError('binding apply produced no semantic change')
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    backup=out/'security_config.before.json'; backup.write_bytes(original)
    if sha_bytes(backup.read_bytes())!=before_file_sha: raise ValueError('backup verification failed')
    tmp=config_path.with_name(config_path.name+'.tradingos-binding-tmp')
    try:
        with open(tmp,'wb') as f:
            f.write(candidate_bytes); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,config_path)
    finally:
        if tmp.exists(): tmp.unlink()
    readback=config_path.read_bytes()
    if sha_bytes(readback)!=after_file_sha: raise ValueError('post-write file hash mismatch')
    parsed=json.loads(readback.decode('utf-8'))
    if sha_obj(parsed)!=after_obj_sha or parsed.get('mode')!='DISABLED' or parsed.get('deploy_permission')!='DENY' or parsed.get('credentials')!=cfg.get('credentials'): raise ValueError('post-write semantic verification failed')
    receipt={
        'schema':APPLY_SCHEMA,'version':V,'status':'BINDING_APPLIED_DISABLED_DENY',
        'review_id':review['review_id'],'binding_package_id':review['binding_package_id'],'destination_alias':review['destination_alias'],'destination_sha256':review['destination_sha256'],
        'authorized_scope':plan['authorized_scope'],'before_config_object_sha256':before_obj_sha,'before_config_file_sha256':before_file_sha,'after_config_object_sha256':after_obj_sha,'after_config_file_sha256':after_file_sha,
        'backup_file':backup.name,'rollback_required_for_reversal':True,
        'contract':_contract(True,True),'safety':_safety(),
    }
    rp=out/'binding_apply_receipt.json'; rp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return receipt,rp,backup

def rollback_binding(config_path:Path,apply_receipt:dict[str,Any],backup_path:Path,out_dir:Path)->tuple[dict[str,Any],Path]:
    if apply_receipt.get('schema')!=APPLY_SCHEMA or apply_receipt.get('status')!='BINDING_APPLIED_DISABLED_DENY': raise ValueError('invalid apply receipt')
    config_path=Path(config_path); current=config_path.read_bytes(); backup=Path(backup_path).read_bytes()
    if sha_bytes(current)!=apply_receipt.get('after_config_file_sha256'): raise ValueError('rollback current-state mismatch')
    if sha_bytes(backup)!=apply_receipt.get('before_config_file_sha256'): raise ValueError('rollback backup mismatch')
    tmp=config_path.with_name(config_path.name+'.tradingos-rollback-tmp')
    try:
        with open(tmp,'wb') as f:
            f.write(backup); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,config_path)
    finally:
        if tmp.exists(): tmp.unlink()
    if sha_bytes(config_path.read_bytes())!=apply_receipt['before_config_file_sha256']: raise ValueError('rollback verification failed')
    restored=json.loads(config_path.read_text(encoding='utf-8-sig'))
    authz.validate_cfg(restored)
    receipt={'schema':ROLLBACK_SCHEMA,'version':V,'status':'BINDING_ROLLED_BACK','apply_review_id':apply_receipt['review_id'],'restored_config_file_sha256':apply_receipt['before_config_file_sha256'],'restored_config_object_sha256':sha_obj(restored),'binding_apply_performed':False,'rollback_performed':True,'network_call':False,'deploy_permission':'DENY'}
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); rp=out/'binding_rollback_receipt.json'; rp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return receipt,rp

def main()->int:
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('plan'); p.add_argument('--config',type=Path,required=True); p.add_argument('--review',type=Path,required=True); p.add_argument('--authorization',type=Path)
    a=sub.add_parser('apply'); a.add_argument('--config',type=Path,required=True); a.add_argument('--review',type=Path,required=True); a.add_argument('--authorization',type=Path,required=True); a.add_argument('--out-dir',type=Path,required=True)
    r=sub.add_parser('rollback'); r.add_argument('--config',type=Path,required=True); r.add_argument('--apply-receipt',type=Path,required=True); r.add_argument('--backup',type=Path,required=True); r.add_argument('--out-dir',type=Path,required=True)
    x=ap.parse_args()
    try:
        if x.cmd=='plan': out=evaluate(readj(x.review),readj(x.config),readj(x.authorization) if x.authorization else None)
        elif x.cmd=='apply': out,_,_=apply_binding(x.config,readj(x.review),readj(x.authorization),x.out_dir)
        else: out,_=rollback_binding(x.config,readj(x.apply_receipt),x.backup,x.out_dir)
    except Exception as exc:
        print(json.dumps({'result':'ERROR','error':str(exc),'can_trade':False,'deploy_permission':'DENY'},indent=2)); return 2
    print(json.dumps({'result':'PASS',**out},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
