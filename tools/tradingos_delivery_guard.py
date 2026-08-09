#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,hmac,json,os,re,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import tradingos_feedback_actions as actions
import tradingos_feedback_callback as callback

V="1.0.0"; CFG="tradingos.delivery.security_config.v1"; AUD="tradingos.delivery.security_audit.v1"; CB="tradingos.delivery.callback_request.v1"; GEN="GENESIS"
ENV=re.compile(r"^[A-Z][A-Z0-9_]{2,127}$"); RID=re.compile(r"^[A-Za-z0-9._:-]{12,128}$"); H64=re.compile(r"^[0-9a-f]{64}$")
def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def sha_text(v): return hashlib.sha256(v.encode()).hexdigest()
def sha(v): return sha_text(canon(v))
def ts(v):
 s=v.strip(); s=s[:-1]+"+00:00" if s.endswith("Z") else s; d=datetime.fromisoformat(s)
 if d.tzinfo is None: raise ValueError("timestamp must include timezone")
 return d.astimezone(timezone.utc)
def env_name(v):
 if not isinstance(v,str) or not ENV.fullmatch(v): raise ValueError("invalid environment-variable name")
 return v

def validate(c):
 if c.get("schema")!=CFG or c.get("version")!=1: raise ValueError("unsupported security config")
 mode=c.get("mode"); perm=c.get("deploy_permission"); aid=c.get("adapter_id"); cr=c.get("credentials"); lim=c.get("rate_limits"); binds=c.get("destination_bindings")
 if mode not in {"DISABLED","ENABLED"} or perm not in {"DENY","ALLOW"} or not isinstance(aid,str) or not aid: raise ValueError("invalid security config")
 if not all(isinstance(x,dict) for x in (cr,lim,binds)): raise ValueError("invalid security config objects")
 if {"bot_token","telegram_bot_token","secret","callback_hmac_secret","password","api_key"}&set(cr): raise ValueError("inline credentials forbidden")
 token_env=env_name(cr.get("telegram_bot_token_env")); secret_env=env_name(cr.get("callback_hmac_secret_env")); dm=lim.get("delivery_attempts_per_minute"); cm=lim.get("callbacks_per_minute"); age=c.get("callback_max_age_seconds")
 if not isinstance(dm,int) or not 1<=dm<=60 or not isinstance(cm,int) or not 1<=cm<=120 or not isinstance(age,int) or not 30<=age<=3600: raise ValueError("invalid limits")
 out={}
 for alias,b in binds.items():
  hx=b.get("destination_sha256") if isinstance(b,dict) else None
  if not isinstance(alias,str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}",alias) or not isinstance(b,dict) or b.get("transport")!="telegram": raise ValueError("invalid destination binding")
  ev=env_name(b.get("destination_env")); hx=hx or ""
  if mode=="ENABLED" and not H64.fullmatch(hx): raise ValueError("enabled destination requires sha256 binding")
  if hx and not H64.fullmatch(hx): raise ValueError("invalid destination hash")
  out[alias]={"transport":"telegram","destination_env":ev,"destination_sha256":hx}
 if mode=="ENABLED" and not out: raise ValueError("enabled mode requires destination")
 return {"schema":CFG,"version":1,"mode":mode,"deploy_permission":perm,"adapter_id":aid,"credentials":{"telegram_bot_token_env":token_env,"callback_hmac_secret_env":secret_env},"destination_bindings":out,"rate_limits":{"delivery_attempts_per_minute":dm,"callbacks_per_minute":cm},"callback_max_age_seconds":age}
def load(p):
 v=json.loads(Path(p).read_text(encoding="utf-8-sig"));
 if not isinstance(v,dict): raise ValueError("config must be object")
 return validate(v)
def runtime(c,alias,environ=None,need_bot=True):
 c=validate(c); e=os.environ if environ is None else environ; b=c["destination_bindings"].get(alias)
 if not b: raise ValueError("destination not allowlisted")
 dest=e.get(b["destination_env"],""); hx=sha_text(dest) if dest else ""
 if not dest or not hmac.compare_digest(hx,b["destination_sha256"]): raise ValueError("destination binding mismatch")
 bot=e.get(c["credentials"]["telegram_bot_token_env"],""); secret=e.get(c["credentials"]["callback_hmac_secret_env"],"")
 if need_bot and not bot: raise ValueError("bot token missing")
 if len(secret.encode())<32: raise ValueError("HMAC secret too short")
 return {"destination_hash":hx,"bot_present":bool(bot),"secret_present":True,"_secret":secret}

def audit_rows(p):
 p=Path(p)
 if not p.exists(): return []
 rows=[]; prev=GEN
 for n,line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(),1):
  if not line.strip(): continue
  r=json.loads(line); body=dict(r); got=body.pop("record_hash",None)
  if r.get("schema")!=AUD or r.get("sequence")!=len(rows)+1 or r.get("prev_record_hash")!=prev or not isinstance(got,str) or not hmac.compare_digest(sha(body),got): raise ValueError(f"audit line {n} invalid")
  ts(str(r.get("occurred_at"))); rid=r.get("request_id")
  if not isinstance(rid,str) or not RID.fullmatch(rid): raise ValueError("invalid audit request id")
  rows.append(r); prev=got
 return rows
def write_audit(p,at,rid,direction,decision,reason,aid,alias,hx=None,meta=None):
 ts(at)
 if not RID.fullmatch(rid) or direction not in {"OUTBOUND","CALLBACK"} or decision not in {"ALLOW_READY","ALLOW_CALLBACK","DENY"}: raise ValueError("invalid audit record")
 rows=audit_rows(p); body={"schema":AUD,"version":V,"sequence":len(rows)+1,"occurred_at":at,"prev_record_hash":rows[-1]["record_hash"] if rows else GEN,"request_id":rid,"direction":direction,"decision":decision,"reason":reason,"adapter_id":aid,"destination_alias":alias,"destination_hash_prefix":hx[:16] if hx else None,"metadata":meta or {},"contract":{"append_only":True,"raw_destination_persisted":False,"secrets_persisted":False,"network_call":False}}
 row={**body,"record_hash":sha(body)}; p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.open("a",encoding="utf-8",newline="\n").write(canon(row)+"\n"); return row
def seen(rows,rid,direction): return any(r.get("request_id")==rid and r.get("direction")==direction for r in rows)
def recent(rows,now,direction): return sum(1 for r in rows if r.get("direction")==direction and r.get("decision") in {"ALLOW_READY","ALLOW_CALLBACK"} and 0<=(now-ts(r["occurred_at"])).total_seconds()<60)

def cb_body(r):
 if r.get("schema")!=CB: raise ValueError("unsupported callback schema")
 b={k:r.get(k) for k in ("schema","request_id","received_at","adapter_id","destination_alias","action_token")}; rid=b["request_id"]
 if not isinstance(rid,str) or not RID.fullmatch(rid): raise ValueError("invalid callback request id")
 ts(str(b["received_at"]))
 if any(not isinstance(b[k],str) or not b[k] for k in ("adapter_id","destination_alias","action_token")): raise ValueError("invalid callback fields")
 return b
def sign(r,secret):
 if len(secret.encode())<32: raise ValueError("HMAC secret too short")
 return hmac.new(secret.encode(),canon(cb_body(r)).encode(),hashlib.sha256).hexdigest()
def verify_sig(r,secret):
 got=r.get("signature")
 if not isinstance(got,str) or not H64.fullmatch(got) or not hmac.compare_digest(sign(r,secret),got): raise ValueError("callback signature mismatch")

def preflight(manifest,c,audit,alias,rid,at,environ=None):
 c=validate(c); rows=audit_rows(audit); now=ts(at); decision="DENY"; reason="CONFIG_DISABLED"; rt=None
 if seen(rows,rid,"OUTBOUND"): reason="REPLAY_REQUEST_ID"
 elif c["mode"]!="ENABLED": pass
 elif c["deploy_permission"]!="ALLOW": reason="DEPLOY_PERMISSION_DENY"
 else:
  try: rt=runtime(c,alias,environ,True)
  except ValueError as e: reason="RUNTIME_NOT_READY:"+str(e)
  else:
   if recent(rows,now,"OUTBOUND")>=c["rate_limits"]["delivery_attempts_per_minute"]: reason="RATE_LIMIT"
   else: decision="ALLOW_READY"; reason="PREFLIGHT_READY"
 if manifest.get("schema")!="tradingos.delivery.telegram.v1" or manifest.get("mode")!="DRY_RUN" or manifest.get("contract",{}).get("network_call") is not False: raise ValueError("unsafe delivery manifest")
 a=write_audit(audit,at,rid,"OUTBOUND",decision,reason,c["adapter_id"],alias,rt.get("destination_hash") if rt else None,{"network_call":False})
 return {"schema":"tradingos.delivery.preflight_receipt.v1","version":V,"decision":decision,"reason":reason,"request_id":rid,"runtime":{"destination_bound":bool(rt),"bot_present":bool(rt and rt["bot_present"]),"secret_present":bool(rt and rt["secret_present"])},"audit_record_hash":a["record_hash"],"contract":{"preflight_only":True,"network_call":False,"allow_ready_is_not_delivery":True},"safety":{"can_trade":False,"capital_permission":"DENY","deploy_permission":c["deploy_permission"]}}
def authenticated_feedback(attribution,feedback,audit,c,request,now,note="",environ=None):
 c=validate(c); b=cb_body(request); rows=audit_rows(audit); n=ts(now); rt=None; decision="DENY"; reason="CONFIG_DISABLED"
 if seen(rows,b["request_id"],"CALLBACK"): reason="REPLAY_REQUEST_ID"
 elif c["mode"]!="ENABLED": pass
 elif c["deploy_permission"]!="ALLOW": reason="DEPLOY_PERMISSION_DENY"
 elif b["adapter_id"]!=c["adapter_id"]: reason="ADAPTER_ID_MISMATCH"
 elif abs((n-ts(b["received_at"])).total_seconds())>c["callback_max_age_seconds"]: reason="STALE_CALLBACK"
 else:
  try: actions.parse_token(b["action_token"]); rt=runtime(c,b["destination_alias"],environ,False); verify_sig(request,rt["_secret"])
  except ValueError as e: reason="AUTH_FAILED:"+str(e)
  else:
   if recent(rows,n,"CALLBACK")>=c["rate_limits"]["callbacks_per_minute"]: reason="RATE_LIMIT"
   else: decision="ALLOW_CALLBACK"; reason="AUTHENTICATED"
 a=write_audit(audit,now,b["request_id"],"CALLBACK",decision,reason,c["adapter_id"],b["destination_alias"],rt.get("destination_hash") if rt else None,{"network_call":False})
 if decision!="ALLOW_CALLBACK": return {"decision":"DENY","reason":reason,"feedback_written":False,"audit_record_hash":a["record_hash"]}
 r=callback.consume(attribution,Path(feedback),b["action_token"],b["received_at"],note)
 return {"decision":"ALLOW_CALLBACK","reason":reason,"feedback_written":r.get("record_status")=="APPENDED","feedback":r,"audit_record_hash":a["record_hash"]}

def main():
 p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True); q=sub.add_parser("preflight")
 for x in ("telegram_manifest","config","audit_ledger","destination_alias","request_id","attempted_at"): q.add_argument("--"+x.replace("_","-"),required=True)
 a=p.parse_args()
 try:
  if a.cmd=="preflight": r=preflight(json.loads(Path(a.telegram_manifest).read_text()),load(a.config),Path(a.audit_ledger),a.destination_alias,a.request_id,a.attempted_at)
 except Exception as e: print(json.dumps({"result":"ERROR","error":str(e),"can_trade":False},indent=2)); return 2
 print(json.dumps({"result":"PASS",**r},indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
