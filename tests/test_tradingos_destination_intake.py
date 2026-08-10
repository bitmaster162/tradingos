from __future__ import annotations
import importlib.util, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location("destination_intake",ROOT/"tools"/"tradingos_destination_intake.py"); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def cert():
    return {"schema":m.binding.CERT_SCHEMA,"verdict":"READY_FOR_BINDING","go_live_ready":False,"binding":{"ready":True},"contract":{"network_call":False,"deployment_performed":False},"safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"}}
def cfg():
    return {"schema":m.binding.CONFIG_SCHEMA,"version":1,"mode":"DISABLED","deploy_permission":"DENY","adapter_id":"telegram-primary","credentials":{"telegram_bot_token_env":"TRADINGOS_TELEGRAM_BOT_TOKEN","callback_hmac_secret_env":"TRADINGOS_CALLBACK_HMAC_SECRET"},"destination_bindings":{}}

def test_normalizes_positive_and_negative_chat_ids():
    assert m.normalize_chat_id(" 12345 ")=="12345"
    assert m.normalize_chat_id(-1001234567890)=="-1001234567890"

def test_invalid_chat_ids_fail_closed():
    for v in (0,"0","+123","abc","12.3",True,"-0",""):
        try:m.normalize_chat_id(v)
        except ValueError:pass
        else:raise AssertionError(v)

def test_extracts_common_telegram_update_shapes():
    cid=-1001234567890
    payload={"ok":True,"result":[{"message":{"chat":{"id":cid}}},{"callback_query":{"message":{"chat":{"id":cid}}}},{"my_chat_member":{"chat":{"id":cid}}}]}
    assert m.extract_update_chat_ids(payload)==[str(cid)]

def test_ambiguous_update_json_rejected(tmp_path):
    p=tmp_path/"updates.json"; p.write_text(json.dumps({"result":[{"message":{"chat":{"id":1}}},{"message":{"chat":{"id":2}}}]}))
    try:m.destination_from_update(p)
    except ValueError as e:assert "ambiguous" in str(e)
    else:raise AssertionError

def test_no_chat_id_update_rejected(tmp_path):
    p=tmp_path/"updates.json"; p.write_text(json.dumps({"ok":True,"result":[{"update_id":1}]}))
    try:m.destination_from_update(p)
    except ValueError as e:assert "no Telegram chat id" in str(e)
    else:raise AssertionError

def test_env_source_normalizes_without_persisting_value():
    raw="-1001234567890"; v,meta=m.destination_from_env("TEMP_CHAT",{"TEMP_CHAT":raw})
    assert v==raw and raw not in m.canon(meta) and meta["source"]=="ENV"

def test_build_hash_ready_matches_r11_and_hides_raw():
    raw="-1001234567890"; receipt,pkg=m.build(cert(),cfg(),"ops_primary","TRADINGOS_TELEGRAM_CHAT_ID",raw,{"source":"ENV","source_env":"TEMP_CHAT","candidate_count":1,"raw_destination_persisted":False})
    assert receipt["status"]=="HASH_READY" and pkg["status"]=="HASH_READY"
    assert receipt["destination_sha256"]==m.sha_text(raw)==pkg["binding_request"]["destination_sha256"]
    assert raw not in m.canon(receipt) and raw not in m.canon(pkg)

def test_generate_from_update_writes_only_hash(tmp_path):
    raw="-1001234567890"; cp=tmp_path/"cert.json"; sp=tmp_path/"cfg.json"; up=tmp_path/"updates.json"
    cp.write_text(json.dumps(cert())); sp.write_text(json.dumps(cfg())); up.write_text(json.dumps({"result":[{"channel_post":{"chat":{"id":int(raw)}}}]}))
    r,p,rp,jp,hp=m.generate(cp,sp,"ops_primary","TRADINGOS_TELEGRAM_CHAT_ID",tmp_path/"out",telegram_update_json=up)
    assert r["status"]=="HASH_READY" and r["source"]["source"]=="TELEGRAM_UPDATE_JSON"
    assert raw not in rp.read_text() and raw not in jp.read_text() and raw not in hp.read_text()

def test_generate_requires_exactly_one_source(tmp_path):
    cp=tmp_path/"c"; sp=tmp_path/"s"; cp.write_text(json.dumps(cert())); sp.write_text(json.dumps(cfg()))
    for kwargs in ({},{"destination_value_env":"A","telegram_update_json":tmp_path/"u"}):
        try:m.generate(cp,sp,"ops","TRADINGOS_CHAT",tmp_path/"out",**kwargs)
        except ValueError as e:assert "exactly one" in str(e)
        else:raise AssertionError

def test_intake_does_not_bypass_r11_safety():
    bad=cfg(); bad["deploy_permission"]="ALLOW"
    try:m.build(cert(),bad,"ops_primary","TRADINGOS_TELEGRAM_CHAT_ID","123",{"source":"ENV"})
    except ValueError:pass
    else:raise AssertionError

def test_contract_never_applies_binding_or_network():
    r,p=m.build(cert(),cfg(),"ops_primary","TRADINGOS_TELEGRAM_CHAT_ID","123",{"source":"ENV","source_env":"TEMP","candidate_count":1,"raw_destination_persisted":False})
    assert r["contract"]=={"raw_destination_persisted":False,"secrets_accepted":False,"binding_apply_performed":False,"security_config_modified":False,"network_call":False,"deployment_performed":False}
    assert r["safety"]["deploy_permission"]=="DENY" and p["contract"]["binding_apply_performed"] is False

def test_request_artifact_is_input_required_and_non_mutating():
    r=m.build_request(cert(),cfg(),"ops_primary","TRADINGOS_TELEGRAM_CHAT_ID")
    assert r["status"]=="AWAITING_DESTINATION_INPUT" and r["destination_sha256"] is None
    assert [x["mode"] for x in r["accepted_sources"]]==["ENV","TELEGRAM_UPDATE_JSON"]
    assert r["contract"]["destination_invented"] is False and r["contract"]["network_call"] is False

def test_generate_request_writes_no_destination_or_secret_values(tmp_path):
    cp=tmp_path/"cert.json"; sp=tmp_path/"cfg.json"; cp.write_text(json.dumps(cert())); sp.write_text(json.dumps(cfg()))
    r,j,h=m.generate_request(cp,sp,"ops_primary","TRADINGOS_TELEGRAM_CHAT_ID",tmp_path/"out")
    text=j.read_text()+h.read_text()
    assert r["status"]=="AWAITING_DESTINATION_INPUT" and "synthetic-token" not in text
    assert "destination_sha256" in j.read_text() and j.exists() and h.exists()
