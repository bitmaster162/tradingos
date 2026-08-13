#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,html,json,re
from pathlib import Path
from typing import Any
V="1.0.0"; CERT_SCHEMA="tradingos.release_readiness.certificate.v1"; REAL_SCHEMA="tradingos.delivery.real_evidence_manifest.v1"; SIM_SCHEMA="tradingos.delivery.reliability.report.v1"; PREFLIGHT_SCHEMA="tradingos.delivery.preflight_receipt.v1"; SECURITY_SCHEMA="tradingos.delivery.security_config.v1"
VERDICTS={"NOT_READY","READY_WITH_CONDITIONS","READY_FOR_BINDING"}; REAL_CLASSES={"REAL_DELIVERY","REAL_ACK","REAL_FAILURE","REAL_DEAD_LETTER"}
def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def fp(v): return hashlib.sha256(canon(v).encode()).hexdigest()
def readj(p):
 v=json.loads(Path(p).read_text(encoding="utf-8-sig"))
 if not isinstance(v,dict): raise ValueError(f"{p} must contain a JSON object")
 return v
def under(p,r):
 try: Path(p).resolve().relative_to(Path(r).resolve()); return True
 except ValueError: return False

def test_evidence(v):
 if v.get("schema")!="tradingos.release.test_evidence.v1": raise ValueError("unsupported test evidence schema")
 p,b=v.get("product"),v.get("broad")
 if not isinstance(p,dict) or not isinstance(b,dict): raise ValueError("test evidence missing product/broad blocks")
 pp,pt,bp,bf=p.get("passed"),p.get("total"),b.get("passed"),b.get("failed")
 if not all(isinstance(x,int) and x>=0 for x in (pp,pt,bp,bf)): raise ValueError("invalid test counters")
 if pp!=pt or pt<100: raise ValueError("product regression is not fully passing")
 known=b.get("known_failures",[])
 if not isinstance(known,list) or any(not isinstance(x,str) or not x for x in known) or bf!=len(known): raise ValueError("invalid known broad failures")
 return {"product_passed":pp,"product_total":pt,"broad_passed":bp,"broad_failed":bf,"known_failures":known}

def simulation(v):
 if v.get("schema")!=SIM_SCHEMA or v.get("evidence_class")!="SIMULATION_ONLY": raise ValueError("simulation report must be SIMULATION_ONLY")
 c,s,w=v.get("contract"),v.get("safety"),v.get("windows")
 if not isinstance(c,dict) or not isinstance(s,dict) or not isinstance(w,dict) or not all(isinstance(w.get(x),dict) for x in ("7d","30d")): raise ValueError("invalid simulation report")
 if c.get("transport_outcomes_injected") is not True or c.get("production_reliability_claim_allowed") is not False or c.get("real_delivery_metrics") is not False or c.get("network_call") is not False: raise ValueError("simulation report crossed production boundary")
 if s.get("deploy_permission")!="DENY" or s.get("can_trade") is not False: raise ValueError("simulation report violates safety")
 return {"evidence_class":"SIMULATION_ONLY","deliveries_7d":int(w["7d"].get("deliveries") or 0),"ack_rate_7d":w["7d"].get("ack_rate"),"deliveries_30d":int(w["30d"].get("deliveries") or 0),"ack_rate_30d":w["30d"].get("ack_rate")}

def real_manifest(v):
 if v.get("schema")!=REAL_SCHEMA or v.get("version")!=1 or v.get("evidence_class")!="REAL_ONLY": raise ValueError("real evidence manifest must be REAL_ONLY")
 c,rs=v.get("contract"),v.get("records")
 if not isinstance(c,dict) or not isinstance(rs,list) or c.get("simulation_records_allowed") is not False: raise ValueError("invalid real evidence manifest")
 seen=set(); d=a=f=q=0
 for i,r in enumerate(rs,1):
  if not isinstance(r,dict) or r.get("evidence_class") not in REAL_CLASSES: raise ValueError(f"real evidence record {i} is not REAL evidence")
  rid=r.get("receipt_id")
  if not isinstance(rid,str) or len(rid)<12 or rid in seen: raise ValueError(f"real evidence record {i} has invalid/duplicate receipt_id")
  seen.add(rid)
  if r.get("network_call") is not True: raise ValueError(f"real evidence record {i} does not prove a network attempt")
  cls=r["evidence_class"]; d+=cls=="REAL_DELIVERY"; a+=cls=="REAL_ACK"; f+=cls=="REAL_FAILURE"; q+=cls=="REAL_DEAD_LETTER"
 return {"records":len(rs),"real_deliveries":d,"real_acks":a,"real_failures":f,"real_dead_letters":q,"real_ack_rate":round(a/d,4) if d else None}

def preflight(v):
 if v.get("schema")!=PREFLIGHT_SCHEMA or v.get("result")!="PASS": raise ValueError("invalid preflight receipt")
 c,s,r=v.get("contract"),v.get("safety"),v.get("runtime")
 if not all(isinstance(x,dict) for x in (c,s,r)) or c.get("network_call") is not False or s.get("deploy_permission")!="DENY": raise ValueError("preflight receipt crossed deployment boundary")
 return {"decision":v.get("decision"),"reason":v.get("reason"),"destination_bound":r.get("destination_bound") is True,"bot_present":r.get("bot_present") is True,"secret_present":r.get("secret_present") is True,"network_call":False}

def security(v):
 if v.get("schema")!=SECURITY_SCHEMA or v.get("version")!=1: raise ValueError("unsupported security config")
 cr,b=v.get("credentials"),v.get("destination_bindings")
 if not isinstance(cr,dict) or not isinstance(b,dict): raise ValueError("security config missing credentials/bindings")
 if {"bot_token","telegram_bot_token","secret","callback_hmac_secret","password","api_key"}&set(cr): raise ValueError("inline credential fields forbidden")
 er=re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
 if any(not isinstance(cr.get(k),str) or not er.fullmatch(cr[k]) for k in ("telegram_bot_token_env","callback_hmac_secret_env")): raise ValueError("credentials must be environment-variable references")
 mode,perm=v.get("mode"),v.get("deploy_permission")
 if mode not in {"DISABLED","ENABLED"} or perm not in {"DENY","ALLOW"}: raise ValueError("invalid security mode/permission")
 return {"mode":mode,"deploy_permission":perm,"destination_bindings":len(b),"inline_credentials":False}

validate_test_evidence=test_evidence
validate_simulation=simulation
validate_real_manifest=real_manifest
validate_preflight=preflight
validate_security_config=security

def certify(sim_v,real_v,pre_v,sec_v,test_v,sim_path,real_path,known=None):
 sim,real,pre,sec,t=simulation(sim_v),real_manifest(real_v),preflight(pre_v),security(sec_v),test_evidence(test_v); known=list(known or [])
 sr,rr=Path(sim_path).resolve().parent,Path(real_path).resolve().parent
 if sr==rr or under(sr,rr) or under(rr,sr): raise ValueError("REAL and SIMULATION evidence roots must be physically separate")
 blockers=[]; conditions=known
 bind={"simulation_separated":True,"real_evidence_separated":True,"product_regression_green":t["product_passed"]==t["product_total"],"safe_config_disabled":sec["mode"]=="DISABLED","deploy_permission_denied":sec["deploy_permission"]=="DENY","destination_unbound":sec["destination_bindings"]==0 and not pre["destination_bound"],"credentials_unbound":not pre["bot_present"] and not pre["secret_present"],"preflight_fail_closed":pre["decision"]=="DENY" and pre["reason"]=="CONFIG_DISABLED"}
 if blockers: verdict="NOT_READY"
 elif all(bind.values()): verdict="READY_FOR_BINDING"
 else:
  verdict="READY_WITH_CONDITIONS"; conditions += [f"BINDING_PREREQ:{k}=false" for k,v in bind.items() if not v]
 gl=[]
 if sec["mode"]!="ENABLED": gl.append("CONFIG_NOT_ENABLED")
 if sec["deploy_permission"]!="ALLOW": gl.append("DEPLOY_PERMISSION_NOT_ALLOWED")
 if sec["destination_bindings"]<1: gl.append("NO_ALLOWLISTED_DESTINATION")
 if not pre["destination_bound"]: gl.append("RUNTIME_DESTINATION_NOT_BOUND")
 if not pre["bot_present"]: gl.append("BOT_CREDENTIAL_NOT_BOUND")
 if not pre["secret_present"]: gl.append("CALLBACK_AUTH_SECRET_NOT_BOUND")
 if pre["decision"]!="ALLOW_READY": gl.append("LATEST_PREFLIGHT_NOT_ALLOW_READY")
 if real["real_deliveries"]<3: gl.append("REAL_DELIVERIES_LT_3")
 if real["real_acks"]<3: gl.append("REAL_ACKS_LT_3")
 if real["real_ack_rate"] is None or real["real_ack_rate"]<.95: gl.append("REAL_ACK_RATE_LT_0_95")
 if real["real_dead_letters"]>0: gl.append("REAL_DEAD_LETTERS_PRESENT")
 live=not gl
 cert={"schema":CERT_SCHEMA,"version":V,"verdict":verdict,"go_live_ready":live,"production_reliability_claim_allowed":live,"binding":{"ready":verdict=="READY_FOR_BINDING","prerequisites":bind,"meaning":"READY_FOR_BINDING authorizes only a separate binding procedure; it is not deployment or go-live approval."},"evidence_separation":{"simulation_root":str(sr),"real_root":str(rr),"roots_separate":True,"simulation_evidence_class":"SIMULATION_ONLY","real_evidence_class":"REAL_ONLY","simulation_metrics_promoted_to_real":False,"simulation_ack_rate_7d":sim["ack_rate_7d"],"real_ack_rate":real["real_ack_rate"],"real_deliveries":real["real_deliveries"],"real_acks":real["real_acks"]},"tests":t,"preflight":pre,"security_config":sec,"real_evidence":real,"blockers":blockers,"conditions":conditions,"go_live_blockers":gl,"next_action":"Run separately approved destination/credential binding and repeat authenticated preflight; do not deploy from this certificate.","sources":{"simulation_report_sha256":fp(sim_v),"real_manifest_sha256":fp(real_v),"preflight_receipt_sha256":fp(pre_v),"security_config_sha256":fp(sec_v),"test_evidence_sha256":fp(test_v)},"contract":{"simulation_can_satisfy_real_delivery_gate":False,"binding_is_deployment":False,"network_call":False,"credentials_required":False,"deployment_performed":False},"safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"}}
 if verdict not in VERDICTS: raise ValueError("invalid verdict")
 return cert

def render(c):
 e=html.escape; s=c["evidence_separation"]; gl="".join(f"<li>{e(str(x))}</li>" for x in c["go_live_blockers"]); co="".join(f"<li>{e(str(x))}</li>" for x in c["conditions"]) or "<li>None</li>"
 return f'<!doctype html><html><head><meta charset="utf-8"><title>TradingOS Release Readiness</title><style>body{{margin:0;background:#071019;color:#eef6fb;font:14px system-ui}}main{{max-width:980px;margin:auto;padding:32px}}article{{background:#0d1823;border:1px solid #263746;border-radius:16px;padding:18px;margin:12px 0}}small,p,li{{color:#a8bac7}}code{{color:#e7f3fa}}.v{{font-size:30px;font-weight:800}}</style></head><body><main><h1>TradingOS Release Readiness</h1><article><small>Binding verdict</small><div class="v">{e(c["verdict"])}</div><p>Go-live ready: {str(c["go_live_ready"]).lower()}</p></article><article><h2>Evidence separation</h2><p>Simulation ACK rate 7d: <code>{e(str(s["simulation_ack_rate_7d"]))}</code></p><p>Real deliveries: <code>{s["real_deliveries"]}</code> · real ACKs: <code>{s["real_acks"]}</code> · real ACK rate: <code>{e(str(s["real_ack_rate"]))}</code></p><p>Simulation metrics promoted to real: <code>false</code></p></article><article><h2>Go-live blockers</h2><ul>{gl}</ul></article><article><h2>Conditions</h2><ul>{co}</ul></article><p>network_call=false · deployment_performed=false · can_trade=false · capital_permission=DENY · deploy_permission=DENY</p></main></body></html>'
def main():
 p=argparse.ArgumentParser(); p.add_argument("--simulation-report",type=Path,required=True); p.add_argument("--real-manifest",type=Path,required=True); p.add_argument("--preflight-receipt",type=Path,required=True); p.add_argument("--security-config",type=Path,required=True); p.add_argument("--test-evidence",type=Path,required=True); p.add_argument("--known-condition",action="append",default=[]); p.add_argument("--out-dir",type=Path,required=True); a=p.parse_args()
 try:
  c=certify(readj(a.simulation_report),readj(a.real_manifest),readj(a.preflight_receipt),readj(a.security_config),readj(a.test_evidence),a.simulation_report,a.real_manifest,a.known_condition); o=a.out_dir.resolve(); o.mkdir(parents=True,exist_ok=True); (o/"release_readiness.json").write_text(json.dumps(c,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); (o/"release_readiness.html").write_text(render(c),encoding="utf-8")
 except Exception as x: print(json.dumps({"result":"ERROR","error":str(x),"can_trade":False},indent=2)); return 2
 print(json.dumps({"result":"PASS","verdict":c["verdict"],"go_live_ready":c["go_live_ready"],"real_deliveries":c["real_evidence"]["real_deliveries"],"network_call":False,"deploy_permission":"DENY"},indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
