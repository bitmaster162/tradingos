from __future__ import annotations
import importlib.util,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path); assert s and s.loader; m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
m=load("ops",ROOT/"tools"/"tradingos_delivery_ops.py")
def env(): return {"schema":"tradingos.delivery.envelope.v1","delivery_id":"d:test:0001","contract":{"network_call":False},"safety":{"deploy_permission":"DENY"}}
def man(): return {"schema":"tradingos.delivery.telegram.v1","mode":"DRY_RUN","request":{"text":"x"},"contract":{"network_call":False},"safety":{"deploy_permission":"DENY"}}
def queue(tmp):
 l=tmp/"ops.ndjson"; _,s=m.enqueue(l,env(),man(),"2026-08-09T18:00:00Z","req:enqueue:0001"); return l,s["delivery_key"]
def test_retry_backoff_then_ack(tmp_path):
 l,k=queue(tmp_path); _,s=m.attempt(l,k,"2026-08-09T18:00:01Z","req:attempt:0001","RETRYABLE_FAILURE"); assert s["state"]=="RETRY_WAIT" and s["next_attempt_at"]=="2026-08-09T18:00:31Z"; _,s=m.attempt(l,k,s["next_attempt_at"],"req:attempt:0002","ACKED"); assert s["state"]=="ACKED" and s["attempt_count"]==2
def test_early_retry_rejected(tmp_path):
 l,k=queue(tmp_path); m.attempt(l,k,"2026-08-09T18:00:01Z","req:attempt:1001","NO_ACK")
 try:m.attempt(l,k,"2026-08-09T18:00:10Z","req:attempt:1002","ACKED")
 except ValueError as e: assert "before next_attempt_at" in str(e)
 else: raise AssertionError
def test_request_idempotency(tmp_path):
 l,k=queue(tmp_path); x,_=m.attempt(l,k,"2026-08-09T18:00:01Z","req:attempt:2001","RETRYABLE_FAILURE"); y,s=m.attempt(l,k,"2026-08-09T18:00:02Z","req:attempt:2001","ACKED"); assert x=="APPENDED" and y=="DUPLICATE_SUPPRESSED" and s["attempt_count"]==1
def test_permanent_failure_dead_letters(tmp_path):
 l,k=queue(tmp_path); _,s=m.attempt(l,k,"2026-08-09T18:00:01Z","req:attempt:3001","PERMANENT_FAILURE"); assert s["state"]=="DEAD_LETTER"
def test_retry_exhaustion_dead_letters(tmp_path):
 l,k=queue(tmp_path); at="2026-08-09T18:00:01Z"
 for i in range(4):
  _,s=m.attempt(l,k,at,f"req:attempt:4{i:03d}","RETRYABLE_FAILURE")
  if s["state"]=="RETRY_WAIT": at=s["next_attempt_at"]
 assert s["state"]=="DEAD_LETTER" and s["attempt_count"]==4
def test_terminal_delivery_rejects_more_attempts(tmp_path):
 l,k=queue(tmp_path); m.attempt(l,k,"2026-08-09T18:00:01Z","req:attempt:5001","ACKED")
 try:m.attempt(l,k,"2026-08-09T18:01:00Z","req:attempt:5002","ACKED")
 except ValueError as e: assert "terminal" in str(e)
 else: raise AssertionError
def test_tamper_detected(tmp_path):
 l,k=queue(tmp_path); l.write_text(l.read_text().replace('"state":"QUEUED"','"state":"ACKED"'))
 try:m.rows(l)
 except ValueError: pass
 else: raise AssertionError
def test_reliability_report_is_simulation_only(tmp_path):
 l,k=queue(tmp_path); m.attempt(l,k,"2026-08-09T18:00:01Z","req:attempt:6001","RETRYABLE_FAILURE"); m.attempt(l,k,"2026-08-09T18:00:31Z","req:attempt:6002","ACKED"); r=m.report(l,"2026-08-09T19:00:00Z"); w=r["windows"]["7d"]; assert r["evidence_class"]=="SIMULATION_ONLY" and r["contract"]["production_reliability_claim_allowed"] is False and w["acked"]==1 and w["retried_deliveries"]==1 and w["ack_rate"]==1.0
