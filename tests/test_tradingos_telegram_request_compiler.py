from __future__ import annotations
import copy, importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('compiler',ROOT/'tools'/'tradingos_telegram_request_compiler.py'); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def cfg():
    raw={"schema":m.guard.CFG,"version":1,"mode":"ENABLED","deploy_permission":"ALLOW","adapter_id":"telegram-primary","credentials":{"telegram_bot_token_env":"TRADINGOS_TELEGRAM_BOT_TOKEN","callback_hmac_secret_env":"TRADINGOS_CALLBACK_HMAC_SECRET"},"destination_bindings":{"ops_primary":{"transport":"telegram","destination_env":"TRADINGOS_TELEGRAM_CHAT_ID","destination_sha256":"a"*64}},"rate_limits":{"delivery_attempts_per_minute":3,"callbacks_per_minute":10},"callback_max_age_seconds":300}
    return raw

def manifest(legacy=True):
    req={"text":"BTCUSDT · HIGH · WATCH_LONG\nRead-only decision support · no signal · no order","reply_markup":{"inline_keyboard":[[{"text":"Helpful","callback_data":"oi1:event:H:12345678"},{"text":"Ignored","callback_data":"oi1:event:I:12345678"}]]}}
    if legacy:req["disable_web_page_preview"]=True
    else:req["link_preview_options"]={"is_disabled":True}
    return {"schema":m.MANIFEST_SCHEMA,"version":"1.0.0","transport":"telegram_bot_api","mode":"DRY_RUN","method":"sendMessage","request":req,"contract":{"network_call":False,"bot_token_present":False,"chat_id_present":False}}

def auth(man=None, conf=None):
    man=man or manifest(); conf=conf or cfg()
    return {"schema":m.AUTH_SCHEMA,"version":"1.0.0","status":"AUTHORIZED_ONE_SEND_NO_EXECUTION","authorization_id":"1"*32,"authorized_at":"2026-08-10T10:20:00Z","expires_at":"2026-08-10T10:25:00Z","review_id":"2"*32,"scope":m.SCOPE,"target":{"source_receipt_sha256":"3"*64,"source_request_id":"delivery:r23:source001","destination_alias":"ops_primary","manifest_sha256":m.sha(man),"config_semantic_sha256":m.guard.sha(m.guard.validate(conf)),"guard_audit_record_hash":"4"*64},"contract":{"send_execution_authorized":True,"single_use_required":True,"consumption_ledger_required":True,"send_performed":False,"network_call":False,"deployment_authorized":False,"webhook_registration_authorized":False,"separate_executor_required":True,"executor_must_revalidate_fresh_state":True},"safety":{"can_trade":False,"capital_permission":"DENY"}}

def test_valid_legacy_manifest_compiles_current_no_network_shape():
    man=manifest(True); p=m.compile_request(auth(man),man,cfg(),"2026-08-10T10:21:00Z")
    assert p["status"]=="REQUEST_TEMPLATE_READY_NO_NETWORK"
    assert p["http_template"]["method"]=="POST" and p["http_template"]["host"]=="api.telegram.org"
    assert p["http_template"]["body_template"]["chat_id"]=="${TRADINGOS_TELEGRAM_CHAT_ID}"
    assert p["http_template"]["body_template"]["link_preview_options"]=={"is_disabled":True}
    assert "disable_web_page_preview" not in p["http_template"]["body_template"]
    assert p["normalization"]["normalized_to_link_preview_options"] is True
    assert p["contract"]["network_call"] is False and p["contract"]["send_performed"] is False

def test_modern_link_preview_manifest_compiles_without_legacy_normalization():
    man=manifest(False); p=m.compile_request(auth(man),man,cfg(),"2026-08-10T10:21:00Z")
    assert p["http_template"]["body_template"]["link_preview_options"]=={"is_disabled":True}
    assert p["normalization"]["normalized_to_link_preview_options"] is False

def test_minimal_old_test_manifest_is_blocked_for_missing_payload():
    man={"schema":m.MANIFEST_SCHEMA,"mode":"DRY_RUN","contract":{"network_call":False},"method":"sendMessage"}
    try:m.compile_request(auth(man),man,cfg(),"2026-08-10T10:21:00Z")
    except ValueError as e: assert "BLOCKED_MESSAGE_PAYLOAD_REQUIRED" in str(e)
    else: raise AssertionError("payload-less manifest accepted")

def test_manifest_sha_drift_rejected():
    man=manifest(); a=auth(man); changed=copy.deepcopy(man); changed["request"]["text"]+=" x"
    try:m.compile_request(a,changed,cfg(),"2026-08-10T10:21:00Z")
    except ValueError as e: assert "manifest changed" in str(e)
    else: raise AssertionError("manifest drift accepted")

def test_config_sha_drift_rejected():
    man=manifest(); c=cfg(); a=auth(man,c); c["rate_limits"]["delivery_attempts_per_minute"]=4
    try:m.compile_request(a,man,c,"2026-08-10T10:21:00Z")
    except ValueError as e: assert "config changed" in str(e)
    else: raise AssertionError("config drift accepted")

def test_requires_enabled_allow():
    man=manifest(); c=cfg(); a=auth(man,c); c2=copy.deepcopy(c); c2["deploy_permission"]="DENY"; a["target"]["config_semantic_sha256"]=m.guard.sha(m.guard.validate(c2))
    try:m.compile_request(a,man,c2,"2026-08-10T10:21:00Z")
    except ValueError as e: assert "ENABLED/ALLOW" in str(e)
    else: raise AssertionError("DENY config accepted")

def test_authorization_expiry_rejected():
    man=manifest()
    try:m.compile_request(auth(man),man,cfg(),"2026-08-10T10:26:00Z")
    except ValueError as e: assert "expired" in str(e)
    else: raise AssertionError("expired authorization accepted")

def test_unknown_telegram_field_rejected():
    man=manifest(); man["request"]["parse_mode"]="HTML"; a=auth(man)
    try:m.compile_request(a,man,cfg(),"2026-08-10T10:21:00Z")
    except ValueError as e: assert "unsupported Telegram request fields" in str(e)
    else: raise AssertionError("unknown field accepted")

def test_unsafe_keyboard_button_type_rejected():
    man=manifest(); man["request"]["reply_markup"]={"inline_keyboard":[[{"text":"open","url":"https://example.com"}]]}; a=auth(man)
    try:m.compile_request(a,man,cfg(),"2026-08-10T10:21:00Z")
    except ValueError as e: assert "text+callback_data" in str(e)
    else: raise AssertionError("URL button accepted")

def test_callback_data_limit_enforced():
    man=manifest(); man["request"]["reply_markup"]={"inline_keyboard":[[{"text":"x","callback_data":"x"*65}]]}; a=auth(man)
    try:m.compile_request(a,man,cfg(),"2026-08-10T10:21:00Z")
    except ValueError as e: assert "callback_data" in str(e)
    else: raise AssertionError("oversized callback accepted")

def test_compiler_never_reads_or_persists_runtime_values():
    man=manifest(); p=m.compile_request(auth(man),man,cfg(),"2026-08-10T10:21:00Z"); text=m.canonical(p)
    assert "TRADINGOS_TELEGRAM_BOT_TOKEN" in text and "TRADINGOS_TELEGRAM_CHAT_ID" in text
    assert p["credential_reference"]["bot_token_value_persisted"] is False
    assert p["destination"]["raw_chat_id_persisted"] is False
    assert p["contract"]["compiler_reads_runtime_env_values"] is False

def test_authorization_not_consumed_by_compiler():
    man=manifest(); p=m.compile_request(auth(man),man,cfg(),"2026-08-10T10:21:00Z")
    assert p["authorization"]["consumed_by_compiler"] is False and p["contract"]["authorization_consumed"] is False

def test_blocked_real_review_has_no_http_template():
    r={"schema":m.REVIEW_SCHEMA,"status":"BLOCKED_PREFLIGHT_REQUIRED","review_id":"5"*32}
    p=m.blocked_from_review(r,"2026-08-10T10:21:00Z")
    assert p["status"]=="BLOCKED_AUTHORIZATION_REQUIRED" and p["http_template"] is None
    assert p["contract"]["network_call"] is False

def test_legacy_and_modern_preview_fields_cannot_coexist():
    man=manifest(); man["request"]["link_preview_options"]={"is_disabled":True}; a=auth(man)
    try:m.compile_request(a,man,cfg(),"2026-08-10T10:21:00Z")
    except ValueError as e: assert "cannot coexist" in str(e)
    else: raise AssertionError("conflicting preview fields accepted")

def test_no_http_client_dependency_in_compiler_source():
    src=(ROOT/'tools'/'tradingos_telegram_request_compiler.py').read_text()
    for forbidden in ('requests','httpx','urllib.request','socket','aiohttp'):
        assert forbidden not in src
