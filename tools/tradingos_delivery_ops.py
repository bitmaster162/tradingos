#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, html, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

V="1.0.0"; REC="tradingos.delivery.operations.record.v1"; REPORT="tradingos.delivery.reliability.report.v1"; GEN="GENESIS"
BACKOFF=(30,120,300); MAX_ATTEMPTS=4
OUTCOMES={"ACKED","RETRYABLE_FAILURE","NO_ACK","PERMANENT_FAILURE"}

def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def sha(v): return hashlib.sha256(canon(v).encode()).hexdigest()
def parse_ts(v):
 s=v.strip(); s=s[:-1]+"+00:00" if s.endswith("Z") else s; d=datetime.fromisoformat(s)
 if d.tzinfo is None: raise ValueError("timestamp must include timezone")
 return d.astimezone(timezone.utc)
def txt(d): return d.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
def load_json(p):
 v=json.loads(Path(p).read_text(encoding="utf-8-sig"))
 if not isinstance(v,dict): raise ValueError("JSON input must be an object")
 return v

def validate_inputs(envelope,manifest):
 if envelope.get("schema")!="tradingos.delivery.envelope.v1": raise ValueError("unsupported delivery envelope")
 if manifest.get("schema")!="tradingos.delivery.telegram.v1" or manifest.get("mode")!="DRY_RUN": raise ValueError("unsupported telegram manifest")
 if envelope.get("contract",{}).get("network_call") is not False or manifest.get("contract",{}).get("network_call") is not False: raise ValueError("network_call must be false")
 if envelope.get("safety",{}).get("deploy_permission")!="DENY" or manifest.get("safety",{}).get("deploy_permission")!="DENY": raise ValueError("deploy_permission must remain DENY")
 did=envelope.get("delivery_id")
 if not isinstance(did,str) or not did: raise ValueError("delivery_id missing")
 return did, sha(manifest.get("request"))

def rows(path):
 p=Path(path)
 if not p.exists(): return []
 out=[]; prev=GEN
 for n,line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(),1):
  if not line.strip(): continue
  r=json.loads(line); body=dict(r); got=body.pop("record_hash",None)
  if r.get("schema")!=REC or r.get("sequence")!=len(out)+1 or r.get("prev_record_hash")!=prev or not isinstance(got,str) or sha(body)!=got: raise ValueError(f"operations ledger line {n} invalid")
  parse_ts(str(r.get("occurred_at")))
  out.append(r); prev=got
 return out

def append(path,at,request_id,delivery_key,event,data):
 if not isinstance(request_id,str) or len(request_id)<12: raise ValueError("operation request id too short")
 rs=rows(path)
 for r in rs:
  if r.get("operation_request_id")==request_id:
   return "DUPLICATE_SUPPRESSED",r
 body={"schema":REC,"version":V,"sequence":len(rs)+1,"occurred_at":at,"prev_record_hash":rs[-1]["record_hash"] if rs else GEN,"operation_request_id":request_id,"delivery_key":delivery_key,"event":event,"data":data,"contract":{"simulation_only":True,"network_call":False,"production_reliability_claim_allowed":False},"safety":{"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"}}
 parse_ts(at); rec={**body,"record_hash":sha(body)}; p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); p.open("a",encoding="utf-8",newline="\n").write(canon(rec)+"\n"); return "APPENDED",rec

def delivery_rows(rs,key): return [r for r in rs if r.get("delivery_key")==key]
def state(rs,key):
 ds=delivery_rows(rs,key)
 if not ds: return None
 attempts=[r for r in ds if r.get("event")=="ATTEMPT"]
 last=ds[-1]; st=last.get("data",{}).get("state")
 return {"state":st,"attempt_count":len(attempts),"next_attempt_at":last.get("data",{}).get("next_attempt_at"),"delivery_key":key,"last_record_hash":last.get("record_hash")}

def enqueue(ledger,envelope,manifest,at,request_id):
 did,pf=validate_inputs(envelope,manifest); key=sha({"delivery_id":did,"payload_fingerprint":pf})[:32]; rs=rows(ledger); current=state(rs,key)
 if current: return "ALREADY_QUEUED",current
 status,_=append(ledger,at,request_id,key,"QUEUED",{"state":"QUEUED","delivery_id":did,"payload_fingerprint":pf,"attempt_count":0,"next_attempt_at":at})
 return status,state(rows(ledger),key)

def attempt(ledger,key,at,request_id,outcome):
 if outcome not in OUTCOMES: raise ValueError("unsupported simulated outcome")
 rs=rows(ledger)
 if any(r.get("operation_request_id")==request_id for r in rs): return "DUPLICATE_SUPPRESSED",state(rs,key)
 cur=state(rs,key)
 if not cur: raise ValueError("delivery not queued")
 if cur["state"] in {"ACKED","DEAD_LETTER"}: raise ValueError("delivery is terminal")
 now=parse_ts(at); due=parse_ts(cur["next_attempt_at"])
 if now<due: raise ValueError("retry attempted before next_attempt_at")
 no=cur["attempt_count"]+1
 if no>MAX_ATTEMPTS: raise ValueError("maximum attempts exceeded")
 aid=sha({"delivery_key":key,"attempt_no":no})[:24]
 next_at=None; reason=None
 if outcome=="ACKED": st="ACKED"
 elif outcome=="PERMANENT_FAILURE": st="DEAD_LETTER"; reason="PERMANENT_FAILURE"
 elif no>=MAX_ATTEMPTS: st="DEAD_LETTER"; reason="RETRY_EXHAUSTED"
 else:
  st="RETRY_WAIT"; next_at=txt(now+timedelta(seconds=BACKOFF[no-1])); reason=outcome
 data={"state":st,"attempt_no":no,"attempt_id":aid,"simulated_outcome":outcome,"ack_source":"SIMULATED_TRANSPORT_OUTCOME" if outcome=="ACKED" else None,"failure_reason":reason,"next_attempt_at":next_at,"max_attempts":MAX_ATTEMPTS}
 status,_=append(ledger,at,request_id,key,"ATTEMPT",data); return status,state(rows(ledger),key)

def delivery_summary(rs,key):
 ds=delivery_rows(rs,key); q=next((r for r in ds if r.get("event")=="QUEUED"),None); ats=[r for r in ds if r.get("event")=="ATTEMPT"]
 if not q: return None
 st=ds[-1]["data"]["state"]; ack=next((r for r in ats if r["data"].get("state")=="ACKED"),None)
 return {"delivery_key":key,"queued_at":q["occurred_at"],"state":st,"attempts":len(ats),"retried":len(ats)>1,"acked":st=="ACKED","dead_lettered":st=="DEAD_LETTER","first_attempt_acked":bool(ats and ats[0]["data"].get("state")=="ACKED"),"acked_at":ack["occurred_at"] if ack else None}

def report(ledger,now):
 rs=rows(ledger); n=parse_ts(now); keys=[]
 for r in rs:
  if r.get("event")=="QUEUED" and r.get("delivery_key") not in keys: keys.append(r["delivery_key"])
 sums=[delivery_summary(rs,k) for k in keys]; sums=[x for x in sums if x]
 wins={}
 for label,days in (("7d",7),("30d",30)):
  cur=[x for x in sums if timedelta(0)<=n-parse_ts(x["queued_at"])<=timedelta(days=days)]; total=len(cur); ack=sum(x["acked"] for x in cur); dlq=sum(x["dead_lettered"] for x in cur); retried=sum(x["retried"] for x in cur); fa=sum(x["first_attempt_acked"] for x in cur); attempts=sum(x["attempts"] for x in cur); ack_attempts=[x["attempts"] for x in cur if x["acked"]]
  div=lambda a,b: round(a/b,4) if b else None
  wins[label]={"deliveries":total,"acked":ack,"dead_lettered":dlq,"pending":total-ack-dlq,"attempts":attempts,"retried_deliveries":retried,"ack_rate":div(ack,total),"first_attempt_ack_rate":div(fa,total),"retry_rate":div(retried,total),"dead_letter_rate":div(dlq,total),"avg_attempts_to_ack":round(sum(ack_attempts)/len(ack_attempts),4) if ack_attempts else None}
 return {"schema":REPORT,"version":V,"generated_at":now,"evidence_class":"SIMULATION_ONLY","windows":wins,"contract":{"network_call":False,"transport_outcomes_injected":True,"production_reliability_claim_allowed":False,"real_delivery_metrics":False},"safety":{"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"}}

def render_html(r):
 cards=[]
 for k in ("7d","30d"):
  w=r["windows"][k]; cards.append(f'<article><small>{k}</small><h2>{w["deliveries"]} deliveries</h2><p>ACK {w["ack_rate"]} · retry {w["retry_rate"]} · DLQ {w["dead_letter_rate"]}</p></article>')
 return '<!doctype html><html><head><meta charset="utf-8"><title>TradingOS Delivery Reliability</title><style>body{background:#071019;color:#f4f8fb;font:14px system-ui;margin:0}main{max-width:900px;margin:auto;padding:30px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}article{background:#0d1823;border:1px solid #263746;border-radius:16px;padding:18px}small,p{color:#9cb0bf}.warn{padding:12px;border:1px solid #5a4a2b;border-radius:12px;margin:16px 0}@media(max-width:700px){.grid{grid-template-columns:1fr}}</style></head><body><main><h1>Delivery Reliability Simulation</h1><div class="warn">SIMULATION_ONLY · injected transport outcomes · not production reliability.</div><div class="grid">'+''.join(cards)+'</div><p>network_call=false · deploy_permission=DENY</p></main></body></html>'

def simulate(envelope,manifest,ledger,out_dir,start,scenario):
 env=load_json(envelope); man=load_json(manifest); t=parse_ts(start); s,_=enqueue(ledger,env,man,txt(t),"ops:enqueue:"+sha({"start":start,"scenario":scenario})[:20]); key=state(rows(ledger),sha({"delivery_id":env["delivery_id"],"payload_fingerprint":sha(man.get("request"))})[:32])["delivery_key"]
 if scenario=="retry_then_ack":
  attempt(ledger,key,txt(t+timedelta(seconds=1)),"ops:attempt1:"+key[:16],"RETRYABLE_FAILURE"); due=parse_ts(state(rows(ledger),key)["next_attempt_at"]); attempt(ledger,key,txt(due),"ops:attempt2:"+key[:16],"ACKED")
 elif scenario=="first_ack": attempt(ledger,key,txt(t+timedelta(seconds=1)),"ops:attempt1:"+key[:16],"ACKED")
 elif scenario=="dead_letter": attempt(ledger,key,txt(t+timedelta(seconds=1)),"ops:attempt1:"+key[:16],"PERMANENT_FAILURE")
 else: raise ValueError("unknown scenario")
 final=state(rows(ledger),key); rep=report(ledger,txt(t+timedelta(minutes=10))); o=Path(out_dir); o.mkdir(parents=True,exist_ok=True); (o/"delivery_reliability.json").write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); (o/"delivery_reliability.html").write_text(render_html(rep),encoding="utf-8"); receipt={"schema":"tradingos.delivery.operations.simulation_receipt.v1","version":V,"scenario":scenario,"delivery_key":key,"final_state":final,"ledger_rows":len(rows(ledger)),"report":rep,"contract":{"simulation_only":True,"network_call":False,"deploy_permission":"DENY"}}; (o/"simulation_receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); return receipt

def main():
 p=argparse.ArgumentParser(); p.add_argument("--envelope",type=Path,required=True); p.add_argument("--telegram-manifest",type=Path,required=True); p.add_argument("--ledger",type=Path,required=True); p.add_argument("--out-dir",type=Path,required=True); p.add_argument("--start-at",required=True); p.add_argument("--scenario",choices=["retry_then_ack","first_ack","dead_letter"],default="retry_then_ack"); a=p.parse_args()
 try:r=simulate(a.envelope,a.telegram_manifest,a.ledger,a.out_dir,a.start_at,a.scenario)
 except Exception as e: print(json.dumps({"result":"ERROR","error":str(e),"can_trade":False},indent=2)); return 2
 print(json.dumps({"result":"PASS","scenario":r["scenario"],"state":r["final_state"]["state"],"attempts":r["final_state"]["attempt_count"],"ledger_rows":r["ledger_rows"],"network_call":False,"deploy_permission":"DENY"},indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
