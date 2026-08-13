from __future__ import annotations
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_readiness", ROOT / "tools" / "tradingos_release_readiness.py")
assert spec and spec.loader
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def sim(ack=1.0):
    return {"schema":m.SIM_SCHEMA,"version":"1.0.0","evidence_class":"SIMULATION_ONLY","windows":{"7d":{"deliveries":1,"ack_rate":ack},"30d":{"deliveries":1,"ack_rate":ack}},"contract":{"network_call":False,"transport_outcomes_injected":True,"production_reliability_claim_allowed":False,"real_delivery_metrics":False},"safety":{"can_trade":False,"deploy_permission":"DENY"}}

def real(records=None):
    return {"schema":m.REAL_SCHEMA,"version":1,"evidence_class":"REAL_ONLY","records":records or [],"contract":{"simulation_records_allowed":False}}

def preflight(decision="DENY",reason="CONFIG_DISABLED",bound=False,bot=False,secret=False):
    return {"schema":m.PREFLIGHT_SCHEMA,"result":"PASS","decision":decision,"reason":reason,"runtime":{"destination_bound":bound,"bot_present":bot,"secret_present":secret},"contract":{"network_call":False},"safety":{"deploy_permission":"DENY"}}

def cfg(mode="DISABLED",perm="DENY",bindings=None):
    return {"schema":m.SECURITY_SCHEMA,"version":1,"mode":mode,"deploy_permission":perm,"adapter_id":"telegram-primary","credentials":{"telegram_bot_token_env":"TRADINGOS_TELEGRAM_BOT_TOKEN","callback_hmac_secret_env":"TRADINGOS_CALLBACK_HMAC_SECRET"},"destination_bindings":bindings or {},"rate_limits":{"delivery_attempts_per_minute":3,"callbacks_per_minute":10},"callback_max_age_seconds":300}

def make_test_evidence(product=110,broad=147,failed=1):
    known=["R43_EXTERNAL_EVIDENCE_SEPARATION"] if failed else []
    return {"schema":"tradingos.release.test_evidence.v1","product":{"passed":product,"total":product},"broad":{"passed":broad,"failed":failed,"known_failures":known}}

def certify(tmp_path, **kw):
    sr=tmp_path/"SIMULATION_EVIDENCE"/"reliability.json"; rr=tmp_path/"REAL_EVIDENCE"/"manifest.json"
    sr.parent.mkdir(); rr.parent.mkdir()
    s=kw.get("simulation",sim()); r=kw.get("real_manifest",real()); p=kw.get("preflight",preflight()); c=kw.get("config",cfg()); t=kw.get("test_evidence",make_test_evidence())
    return m.certify(s,r,p,c,t,sr,rr,["KNOWN_R43_CONDITION"])


def test_current_state_ready_for_binding_not_go_live(tmp_path):
    cert=certify(tmp_path)
    assert cert["verdict"]=="READY_FOR_BINDING"
    assert cert["go_live_ready"] is False
    assert cert["production_reliability_claim_allowed"] is False
    assert cert["evidence_separation"]["simulation_ack_rate_7d"]==1.0
    assert cert["evidence_separation"]["real_deliveries"]==0
    assert cert["evidence_separation"]["real_ack_rate"] is None
    assert "REAL_DELIVERIES_LT_3" in cert["go_live_blockers"]


def test_simulation_metrics_never_promote_to_real(tmp_path):
    cert=certify(tmp_path, simulation=sim(1.0))
    assert cert["evidence_separation"]["simulation_metrics_promoted_to_real"] is False
    assert cert["real_evidence"]["real_deliveries"]==0
    assert cert["real_evidence"]["real_acks"]==0


def test_real_manifest_rejects_simulation_record():
    bad=real([{"receipt_id":"receipt:0000001","evidence_class":"SIMULATION_ONLY","network_call":True}])
    try:m.validate_real_manifest(bad)
    except ValueError:pass
    else:raise AssertionError


def test_real_record_requires_network_attempt():
    bad=real([{"receipt_id":"receipt:0000002","evidence_class":"REAL_DELIVERY","network_call":False}])
    try:m.validate_real_manifest(bad)
    except ValueError:pass
    else:raise AssertionError


def test_roots_must_be_physically_separate(tmp_path):
    root=tmp_path/"EVIDENCE"; root.mkdir(); sr=root/"sim.json"; rr=root/"real.json"
    try:m.certify(sim(),real(),preflight(),cfg(),make_test_evidence(),sr,rr,[])
    except ValueError as e: assert "physically separate" in str(e)
    else:raise AssertionError


def test_product_regression_must_be_green():
    bad=make_test_evidence(); bad["product"]={"passed":109,"total":110}
    try:m.validate_test_evidence(bad)
    except ValueError:pass
    else:raise AssertionError


def test_broad_failure_count_must_match_known_list():
    bad=make_test_evidence(); bad["broad"]["failed"]=2
    try:m.validate_test_evidence(bad)
    except ValueError:pass
    else:raise AssertionError


def test_inline_credentials_rejected():
    bad=cfg(); bad["credentials"]["bot_token"]="secret"
    try:m.validate_security_config(bad)
    except ValueError:pass
    else:raise AssertionError


def test_simulation_report_cannot_allow_production_claim():
    bad=sim(); bad["contract"]["production_reliability_claim_allowed"]=True
    try:m.validate_simulation(bad)
    except ValueError:pass
    else:raise AssertionError


def test_non_safe_binding_state_is_ready_with_conditions(tmp_path):
    bindings={"ops":{"transport":"telegram","destination_env":"CHAT","destination_sha256":"0"*64}}
    cert=certify(tmp_path, config=cfg(mode="ENABLED",perm="DENY",bindings=bindings), preflight=preflight(reason="DEPLOY_PERMISSION_DENY",bound=True,bot=True,secret=True))
    assert cert["verdict"]=="READY_WITH_CONDITIONS"
    assert cert["go_live_ready"] is False


def test_real_evidence_does_not_override_deploy_gate(tmp_path):
    records=[]
    for i in range(3):
        records.append({"receipt_id":f"delivery:receipt:{i:02d}","evidence_class":"REAL_DELIVERY","network_call":True})
        records.append({"receipt_id":f"ack:receipt:{i:02d}","evidence_class":"REAL_ACK","network_call":True})
    cert=certify(tmp_path, real_manifest=real(records))
    assert cert["real_evidence"]["real_ack_rate"]==1.0
    assert cert["go_live_ready"] is False
    assert "DEPLOY_PERMISSION_NOT_ALLOWED" in cert["go_live_blockers"]
