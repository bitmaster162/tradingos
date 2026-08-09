#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,html,json,os,re
from pathlib import Path
V="1.0.0"; SCHEMA="tradingos.delivery.binding_package.v1"; CERT="tradingos.release_readiness.certificate.v1"; CFG="tradingos.delivery.security_config.v1"; CERT_SCHEMA=CERT; CONFIG_SCHEMA=CFG
ENV=re.compile(r"^[A-Z][A-Z0-9_]{2,127}$"); ALIAS=re.compile(r"^[A-Za-z0-9._-]{1,80}$")
def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def sha(v): return hashlib.sha256(v.encode()).hexdigest()
def fp(v): return sha(canon(v))
canonical=canon; sha256_text=sha
def readj(p):
 v=json.loads(Path(p).read_text(encoding="utf-8-sig"))
 if not isinstance(v,dict): raise ValueError("JSON object required")
 return v
def envname(v,label):
 if not isinstance(v,str) or not ENV.fullmatch(v): raise ValueError(f"invalid {label} env name")
 return v
def exact(v,n):
 if isinstance(v,str): return v==n
 if isinstance(v,dict): return any(exact(k,n) or exact(x,n) for k,x in v.items())
 if isinstance(v,list): return any(exact(x,n) for x in v)
 return False
def validate_cert(c):
 b=c.get("binding"); ct=c.get("contract"); s=c.get("safety")
 if c.get("schema")!=CERT or c.get("verdict")!="READY_FOR_BINDING" or not isinstance(b,dict) or b.get("ready") is not True: raise ValueError("certificate is not READY_FOR_BINDING")
 if not isinstance(ct,dict) or ct.get("network_call") is not False or ct.get("deployment_performed") is not False: raise ValueError("certificate violates no-deployment contract")
 if not isinstance(s,dict) or s.get("can_trade") is not False or s.get("capital_permission")!="DENY" or s.get("deploy_permission")!="DENY": raise ValueError("certificate violates safety boundary")
def validate_cfg(c):
 if c.get("schema")!=CFG or c.get("version")!=1: raise ValueError("unsupported security config")
 if c.get("mode")!="DISABLED" or c.get("deploy_permission")!="DENY": raise ValueError("source config must be DISABLED with deploy_permission=DENY")
 if c.get("destination_bindings") not in ({},None): raise ValueError("source config already has destination binding")
 cr=c.get("credentials")
 if not isinstance(cr,dict): raise ValueError("credentials contract missing")
 if {"bot_token","telegram_bot_token","secret","callback_hmac_secret","password","api_key"}&set(cr): raise ValueError("inline credentials forbidden")
 aid=c.get("adapter_id"); token=envname(cr.get("telegram_bot_token_env"),"bot-token"); hmac=envname(cr.get("callback_hmac_secret_env"),"callback-HMAC")
 if not isinstance(aid,str) or not aid: raise ValueError("adapter_id missing")
 return aid,token,hmac
def build(cert,cfg,alias,dest_env,dest=None):
 validate_cert(cert); aid,token_env,hmac_env=validate_cfg(cfg)
 if not isinstance(alias,str) or not ALIAS.fullmatch(alias): raise ValueError("invalid destination alias")
 dest_env=envname(dest_env,"destination")
 if dest_env in {token_env,hmac_env}: raise ValueError("destination env collides with credential env")
 if dest is not None and (not isinstance(dest,str) or not dest): raise ValueError("destination value must be non-empty")
 digest=sha(dest) if dest is not None else None; status="HASH_READY" if digest else "TEMPLATE_ONLY"
 seed={"cert":fp(cert),"config":fp(cfg),"adapter":aid,"alias":alias,"env":dest_env,"sha256":digest,"status":status}; pid=sha(canon(seed))[:32]
 envs=[dest_env,token_env,hmac_env]; bind={"transport":"telegram","destination_env":dest_env,"destination_sha256":digest}
 req={"adapter_id":aid,"destination_alias":alias,"transport":"telegram","destination_env":dest_env,"destination_sha256":digest,"hash_status":"READY" if digest else "INPUT_REQUIRED","required_env_names":envs,"proposed_config_change":{"mode":"ENABLED_AFTER_SEPARATE_BINDING_APPROVAL","deploy_permission":"DENY_UNCHANGED","destination_bindings":{alias:bind}},"apply_performed":False}
 rollback={"steps":[f"Remove destination binding alias {alias} from the separately applied runtime config.","Set delivery mode back to DISABLED.","Keep deploy_permission=DENY.",f"Unset runtime destination environment variable {dest_env} if provisioned.",f"Unset/rotate {token_env} and {hmac_env} if provisioned during the separate binding procedure.","Append rollback audit receipt; do not delete prior audit evidence.","Repeat fail-closed preflight and require DENY/CONFIG_DISABLED."],"network_call":False,"rollback_performed":False}
 plan={"phase_1_binding_validation":{"requires_separate_binding_approval":True,"expected_config":{"mode":"ENABLED","deploy_permission":"DENY"},"checks":["Runtime destination hash must equal destination_sha256.","Alias must resolve only through destination_env.","Credentials must exist only via named environment variables.","No raw credential/destination may be persisted.","Local guard preflight must perform no network I/O."],"network_call":False},"phase_2_future_go_live_preflight":{"not_authorized_by_this_package":True,"requires_separate_deploy_permission_allow":True,"requires_real_runtime_credentials":True,"target_guard_result":"ALLOW_READY","network_call":False,"send_message":False}}
 out={"schema":SCHEMA,"version":V,"package_id":pid,"status":status,"binding_request":req,"rollback_plan":rollback,"post_binding_preflight_plan":plan,"sources":{"release_readiness_certificate_sha256":fp(cert),"security_config_sha256":fp(cfg)},"contract":{"secrets_in_package":False,"raw_destination_in_package":False,"binding_apply_performed":False,"security_config_modified":False,"deploy_permission_changed":False,"webhook_registered":False,"network_call":False,"deployment_performed":False,"ready_for_binding_is_go_live":False},"safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"}}
 if dest is not None and exact(out,dest): raise ValueError("raw destination leaked")
 return out
def render(p):
 e=html.escape; r=p["binding_request"]; envs="".join(f"<li><code>{e(x)}</code></li>" for x in r["required_env_names"]); rb="".join(f"<li>{e(x)}</li>" for x in p["rollback_plan"]["steps"]); d=r["destination_sha256"] or "INPUT_REQUIRED"
 return f'<!doctype html><html><head><meta charset="utf-8"><title>TradingOS Binding Package</title><style>body{{background:#071019;color:#eef6fb;font:14px system-ui}}main{{max-width:960px;margin:auto;padding:32px}}article{{background:#0d1823;border:1px solid #263746;border-radius:16px;padding:18px;margin:12px}}p,li{{color:#a8bac7}}code{{color:#e7f3fa;word-break:break-all}}</style></head><body><main><h1>TradingOS Binding Package</h1><article><h2>{e(p["status"])}</h2><p>Package <code>{e(p["package_id"])}</code></p><p>Alias <code>{e(r["destination_alias"])}</code> · env <code>{e(r["destination_env"])}</code></p><p>SHA-256 <code>{e(d)}</code></p><ul>{envs}</ul></article><article><h2>Rollback</h2><ul>{rb}</ul></article><p>apply=false · network=false · deployment=false · deploy_permission=DENY</p></main></body></html>'
def generate(cp,sp,alias,dest_env,out,dest_value_env=None,environ=None):
 env=os.environ if environ is None else environ; dest=None
 if dest_value_env is not None:
  n=envname(dest_value_env,"destination-value source"); dest=env.get(n)
  if not dest: raise ValueError(f"destination value env {n} is not set")
 p=build(readj(cp),readj(sp),alias,dest_env,dest); out=Path(out); out.mkdir(parents=True,exist_ok=True); jp=out/"binding_package.json"; hp=out/"binding_package.html"; jp.write_text(json.dumps(p,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n"); hp.write_text(render(p),encoding="utf-8"); return p,jp,hp
def main():
 a=argparse.ArgumentParser(); a.add_argument("--certificate",type=Path,required=True); a.add_argument("--security-config",type=Path,required=True); a.add_argument("--destination-alias",required=True); a.add_argument("--destination-env",required=True); a.add_argument("--destination-value-env"); a.add_argument("--out-dir",type=Path,required=True); x=a.parse_args()
 try: p,j,h=generate(x.certificate,x.security_config,x.destination_alias,x.destination_env,x.out_dir,x.destination_value_env)
 except Exception as z: print(json.dumps({"result":"ERROR","error":str(z),"can_trade":False},indent=2)); return 2
 print(json.dumps({"result":"PASS","package_id":p["package_id"],"status":p["status"],"destination_hash_ready":p["binding_request"]["destination_sha256"] is not None,"binding_apply_performed":False,"network_call":False,"deploy_permission":"DENY","json":str(j),"html":str(h)},indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
