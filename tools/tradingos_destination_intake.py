#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, html, importlib.util, json, os, re
from pathlib import Path
from typing import Any

V="1.0.0"
SCHEMA="tradingos.delivery.destination_intake.v1"
CHAT_ID=re.compile(r"^-?[1-9][0-9]{0,19}$")

ROOT=Path(__file__).resolve().parent
_spec=importlib.util.spec_from_file_location("tradingos_binding_package",ROOT/"tradingos_binding_package.py")
if not _spec or not _spec.loader: raise RuntimeError("binding package module unavailable")
binding=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(binding)

def canon(v:Any)->str: return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def sha_text(v:str)->str: return hashlib.sha256(v.encode()).hexdigest()
def file_sha(p:Path)->str: return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def normalize_chat_id(v:Any)->str:
    if isinstance(v,bool): raise ValueError("Telegram chat id must be numeric")
    if isinstance(v,int): s=str(v)
    elif isinstance(v,str): s=v.strip()
    else: raise ValueError("Telegram chat id must be integer/string")
    if not CHAT_ID.fullmatch(s): raise ValueError("invalid Telegram chat id")
    n=int(s)
    if n==0 or abs(n) > 10**20-1: raise ValueError("Telegram chat id out of range")
    return str(n)

def _chat_id_from_chat(obj:Any,out:set[str])->None:
    if isinstance(obj,dict) and "id" in obj:
        try: out.add(normalize_chat_id(obj["id"]))
        except ValueError: pass

def extract_update_chat_ids(payload:Any)->list[str]:
    out:set[str]=set()
    def visit_update(u:Any)->None:
        if not isinstance(u,dict): return
        for key in ("message","edited_message","channel_post","edited_channel_post","my_chat_member","chat_member","chat_join_request"):
            row=u.get(key)
            if isinstance(row,dict): _chat_id_from_chat(row.get("chat"),out)
        cb=u.get("callback_query")
        if isinstance(cb,dict):
            msg=cb.get("message")
            if isinstance(msg,dict): _chat_id_from_chat(msg.get("chat"),out)
    if isinstance(payload,dict) and isinstance(payload.get("result"),list):
        for u in payload["result"]: visit_update(u)
    elif isinstance(payload,list):
        for u in payload: visit_update(u)
    else: visit_update(payload)
    return sorted(out,key=lambda s:int(s))

def destination_from_update(path:Path)->tuple[str,dict[str,Any]]:
    p=Path(path)
    payload=json.loads(p.read_text(encoding="utf-8-sig"))
    ids=extract_update_chat_ids(payload)
    if not ids: raise ValueError("no Telegram chat id found in update JSON")
    if len(ids)!=1: raise ValueError(f"ambiguous Telegram update JSON: {len(ids)} distinct chat ids")
    raw=ids[0]
    meta={"source":"TELEGRAM_UPDATE_JSON","source_sha256":file_sha(p),"candidate_count":1,"raw_destination_persisted":False}
    return raw,meta

def destination_from_env(env_name:str,environ:dict[str,str]|None=None)->tuple[str,dict[str,Any]]:
    env_name=binding.envname(env_name,"destination-value source")
    env=os.environ if environ is None else environ
    raw=env.get(env_name)
    if not raw: raise ValueError(f"destination value env {env_name} is not set")
    raw=normalize_chat_id(raw)
    return raw,{"source":"ENV","source_env":env_name,"candidate_count":1,"raw_destination_persisted":False}

def _contains_exact(v:Any,needle:str)->bool:
    if isinstance(v,str): return v==needle
    if isinstance(v,dict): return any(_contains_exact(k,needle) or _contains_exact(x,needle) for k,x in v.items())
    if isinstance(v,list): return any(_contains_exact(x,needle) for x in v)
    return False


def build_request(cert:dict[str,Any],cfg:dict[str,Any],alias:str,destination_env:str)->dict[str,Any]:
    binding.validate_cert(cert); aid,token_env,hmac_env=binding.validate_cfg(cfg)
    if not isinstance(alias,str) or not binding.ALIAS.fullmatch(alias): raise ValueError("invalid destination alias")
    destination_env=binding.envname(destination_env,"destination")
    if destination_env in {token_env,hmac_env}: raise ValueError("destination env collides with credential env")
    seed={"cert":binding.fp(cert),"config":binding.fp(cfg),"adapter":aid,"alias":alias,"destination_env":destination_env}
    return {
        "schema":"tradingos.delivery.destination_intake_request.v1","version":V,
        "request_id":sha_text(canon(seed))[:32],"status":"AWAITING_DESTINATION_INPUT",
        "adapter_id":aid,"destination_alias":alias,"destination_env":destination_env,"destination_sha256":None,
        "accepted_sources":[
            {"mode":"ENV","instruction":"Provide the Telegram chat id transiently through a local environment variable; the variable name itself is chosen at invocation time."},
            {"mode":"TELEGRAM_UPDATE_JSON","instruction":"Provide a local Telegram Bot API update JSON export containing exactly one distinct chat id."}
        ],
        "required_runtime_env_names_after_separate_binding":[destination_env,token_env,hmac_env],
        "contract":{"input_required":True,"destination_invented":False,"raw_destination_persisted":False,"secrets_accepted":False,"binding_apply_performed":False,"security_config_modified":False,"network_call":False,"deployment_performed":False},
        "safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"},
    }

def render_request(r:dict[str,Any])->str:
    e=html.escape; modes="".join(f"<li><b>{e(x['mode'])}</b> — {e(x['instruction'])}</li>" for x in r["accepted_sources"]); envs="".join(f"<li><code>{e(x)}</code></li>" for x in r["required_runtime_env_names_after_separate_binding"])
    return f'<!doctype html><html><head><meta charset="utf-8"><title>TradingOS Destination Intake Request</title><style>body{{background:#071019;color:#eef6fb;font:14px system-ui}}main{{max-width:960px;margin:auto;padding:32px}}article{{background:#0d1823;border:1px solid #263746;border-radius:16px;padding:18px;margin:12px}}p,li{{color:#a8bac7}}code{{color:#e7f3fa}}</style></head><body><main><h1>Destination Intake</h1><article><h2>{e(r["status"])}</h2><p>Alias <code>{e(r["destination_alias"])}</code> · destination env <code>{e(r["destination_env"])}</code></p><p>SHA-256 <code>INPUT_REQUIRED</code></p><ul>{modes}</ul></article><article><h2>Future runtime env names</h2><ul>{envs}</ul></article><p>apply=false · network=false · deployment=false · deploy_permission=DENY</p></main></body></html>'

def generate_request(certificate:Path,security_config:Path,alias:str,destination_env:str,out_dir:Path)->tuple[dict[str,Any],Path,Path]:
    r=build_request(binding.readj(certificate),binding.readj(security_config),alias,destination_env); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); jp=out/"destination_intake_request.json"; hp=out/"destination_intake_request.html"; jp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n"); hp.write_text(render_request(r),encoding="utf-8"); return r,jp,hp

def build(cert:dict[str,Any],cfg:dict[str,Any],alias:str,destination_env:str,raw_destination:str,source_meta:dict[str,Any])->tuple[dict[str,Any],dict[str,Any]]:
    raw=normalize_chat_id(raw_destination)
    package=binding.build(cert,cfg,alias,destination_env,raw)
    if package.get("status")!="HASH_READY": raise ValueError("binding package did not reach HASH_READY")
    digest=sha_text(raw)
    if package["binding_request"].get("destination_sha256")!=digest: raise ValueError("binding hash mismatch")
    receipt={
        "schema":SCHEMA,"version":V,
        "intake_id":sha_text(canon({"package_id":package["package_id"],"source":source_meta,"destination_sha256":digest}))[:32],
        "status":"HASH_READY",
        "destination_alias":alias,
        "destination_env":destination_env,
        "destination_sha256":digest,
        "binding_package_id":package["package_id"],
        "source":source_meta,
        "contract":{"raw_destination_persisted":False,"secrets_accepted":False,"binding_apply_performed":False,"security_config_modified":False,"network_call":False,"deployment_performed":False},
        "safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"},
    }
    if _contains_exact(receipt,raw) or _contains_exact(package,raw): raise ValueError("raw destination leaked into output")
    return receipt,package

def generate(certificate:Path,security_config:Path,alias:str,destination_env:str,out_dir:Path,*,destination_value_env:str|None=None,telegram_update_json:Path|None=None,environ:dict[str,str]|None=None)->tuple[dict[str,Any],dict[str,Any],Path,Path,Path]:
    if (destination_value_env is None)==(telegram_update_json is None): raise ValueError("select exactly one destination source")
    if destination_value_env is not None: raw,meta=destination_from_env(destination_value_env,environ)
    else: raw,meta=destination_from_update(Path(telegram_update_json))
    receipt,package=build(binding.readj(certificate),binding.readj(security_config),alias,destination_env,raw,meta)
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    rp=out/"destination_intake_receipt.json"; jp=out/"binding_package.json"; hp=out/"binding_package.html"
    rp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    jp.write_text(json.dumps(package,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    hp.write_text(binding.render(package),encoding="utf-8")
    raw_text=raw
    for p in (rp,jp,hp):
        if raw_text in p.read_text(encoding="utf-8"): raise ValueError("raw destination persisted")
    return receipt,package,rp,jp,hp

def main()->int:
    ap=argparse.ArgumentParser(description="Convert a transient Telegram destination into a secrets-free HASH_READY TradingOS binding package")
    ap.add_argument("--certificate",type=Path,required=True); ap.add_argument("--security-config",type=Path,required=True)
    ap.add_argument("--destination-alias",required=True); ap.add_argument("--destination-env",required=True)
    src=ap.add_mutually_exclusive_group(required=True); src.add_argument("--destination-value-env"); src.add_argument("--telegram-update-json",type=Path)
    ap.add_argument("--out-dir",type=Path,required=True); a=ap.parse_args()
    try:
        r,p,rp,jp,hp=generate(a.certificate,a.security_config,a.destination_alias,a.destination_env,a.out_dir,destination_value_env=a.destination_value_env,telegram_update_json=a.telegram_update_json)
    except Exception as exc:
        print(json.dumps({"result":"ERROR","error":str(exc),"can_trade":False,"deploy_permission":"DENY"},indent=2)); return 2
    print(json.dumps({"result":"PASS","status":r["status"],"intake_id":r["intake_id"],"package_id":p["package_id"],"destination_sha256":r["destination_sha256"],"raw_destination_persisted":False,"binding_apply_performed":False,"network_call":False,"deploy_permission":"DENY","receipt":str(rp),"json":str(jp),"html":str(hp)},indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
