from __future__ import annotations
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path); assert s and s.loader; m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
g=load("guard",ROOT/"tools"/"tradingos_delivery_guard.py"); act=load("actions_guard",ROOT/"tools"/"tradingos_feedback_actions.py")
def cfg(enabled=True,perm="ALLOW",dm=2,cm=2):
 d="-1001234567890"; return {"schema":g.CFG,"version":1,"mode":"ENABLED" if enabled else "DISABLED","deploy_permission":perm,"adapter_id":"telegram-primary","credentials":{"telegram_bot_token_env":"TRADINGOS_TELEGRAM_BOT_TOKEN","callback_hmac_secret_env":"TRADINGOS_CALLBACK_HMAC_SECRET"},"destination_bindings":{"ops_primary":{"transport":"telegram","destination_env":"TRADINGOS_TELEGRAM_CHAT_ID","destination_sha256":g.sha_text(d)}} if enabled else {},"rate_limits":{"delivery_attempts_per_minute":dm,"callbacks_per_minute":cm},"callback_max_age_seconds":300}
def env(bot=True):
 e={"TRADINGOS_TELEGRAM_CHAT_ID":"-1001234567890","TRADINGOS_CALLBACK_HMAC_SECRET":"q"*40};
 if bot:e["TRADINGOS_TELEGRAM_BOT_TOKEN"]="synthetic-token"
 return e
def manifest(): return {"schema":"tradingos.delivery.telegram.v1","mode":"DRY_RUN","contract":{"network_call":False}}
def attr(events=None): return {"schema":"tradingos.value_attribution.report.v1","events":events or [{"event_id":"e06b58fec2365666d555f0ad","symbol":"BTCUSDT","kind":"LEVEL_PROXIMITY","priority":"HIGH","outcome":"UNRESOLVED","opened_at":"2026-08-09T16:01:36Z"}]}
def req(rid="callback:req:0001",event="e06b58fec2365666d555f0ad",impact="CAUSED_REVIEW",at="2026-08-09T18:00:00Z"):
 r={"schema":g.CB,"request_id":rid,"received_at":at,"adapter_id":"telegram-primary","destination_alias":"ops_primary","action_token":act.make_token(event,impact)}; r["signature"]=g.sign(r,"q"*40); return r

def test_inline_secret_and_destination_mismatch_rejected():
 c=cfg(); c["credentials"]["bot_token"]="bad"
 try:g.validate(c)
 except ValueError:pass
 else:raise AssertionError
 try:g.runtime(cfg(),"ops_primary",{"TRADINGOS_TELEGRAM_CHAT_ID":"wrong","TRADINGOS_TELEGRAM_BOT_TOKEN":"x","TRADINGOS_CALLBACK_HMAC_SECRET":"q"*40})
 except ValueError:pass
 else:raise AssertionError
def test_current_disabled_preflight_denies_and_audits(tmp_path):
 a=tmp_path/"audit"; r=g.preflight(manifest(),cfg(False,"DENY"),a,"ops_primary","delivery:req:disabled","2026-08-09T19:00:00Z",{})
 assert r["decision"]=="DENY" and r["reason"]=="CONFIG_DISABLED" and r["contract"]["network_call"] is False and len(g.audit_rows(a))==1
def test_preflight_deploy_replay_rate_limit_and_secret_redaction(tmp_path):
 a=tmp_path/"audit"; c=cfg(True,"DENY"); r=g.preflight(manifest(),c,a,"ops_primary","delivery:req:deny001","2026-08-09T18:00:00Z",env()); assert r["reason"]=="DEPLOY_PERMISSION_DENY"
 a=tmp_path/"audit2"; c=cfg(dm=1); x=g.preflight(manifest(),c,a,"ops_primary","delivery:req:ready01","2026-08-09T18:00:00Z",env()); y=g.preflight(manifest(),c,a,"ops_primary","delivery:req:ready01","2026-08-09T18:00:01Z",env()); z=g.preflight(manifest(),c,a,"ops_primary","delivery:req:ready02","2026-08-09T18:00:02Z",env()); assert x["decision"]=="ALLOW_READY" and y["reason"]=="REPLAY_REQUEST_ID" and z["reason"]=="RATE_LIMIT" and "synthetic-token" not in str(x)
def test_audit_tamper_detected(tmp_path):
 p=tmp_path/"a"; g.write_audit(p,"2026-08-09T18:00:00Z","delivery:req:tamper1","OUTBOUND","DENY","X","a",None); p.write_text(p.read_text().replace('"reason":"X"','"reason":"Y"'))
 try:g.audit_rows(p)
 except ValueError:pass
 else:raise AssertionError
def test_hmac_tamper_and_stale_callback_denied(tmp_path):
 r=req(); r["destination_alias"]="other"
 try:g.verify_sig(r,"q"*40)
 except ValueError:pass
 else:raise AssertionError
 stale=g.authenticated_feedback(attr(),tmp_path/"f",tmp_path/"a",cfg(),req(rid="callback:req:stale01",at="2026-08-09T17:00:00Z"),"2026-08-09T18:00:01Z",environ=env(False)); assert stale["reason"]=="STALE_CALLBACK" and not (tmp_path/"f").exists()
def test_authenticated_callback_writes_then_replay_denies(tmp_path):
 f=tmp_path/"f"; a=tmp_path/"a"; r=req(); x=g.authenticated_feedback(attr(),f,a,cfg(),r,"2026-08-09T18:00:01Z",environ=env(False)); y=g.authenticated_feedback(attr(),f,a,cfg(),r,"2026-08-09T18:00:02Z",environ=env(False)); assert x["decision"]=="ALLOW_CALLBACK" and x["feedback_written"] and y["reason"]=="REPLAY_REQUEST_ID"
def test_signed_corrupt_action_token_denies_without_feedback(tmp_path):
 r=req(); r["action_token"]=r["action_token"][:-1]+("0" if r["action_token"][-1]!="0" else "1"); r["signature"]=g.sign(r,"q"*40); x=g.authenticated_feedback(attr(),tmp_path/"f",tmp_path/"a",cfg(),r,"2026-08-09T18:00:01Z",environ=env(False)); assert x["decision"]=="DENY" and "checksum" in x["reason"] and not (tmp_path/"f").exists()
