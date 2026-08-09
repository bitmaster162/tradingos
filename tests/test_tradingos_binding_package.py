from __future__ import annotations
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("binding_package", ROOT / "tools" / "tradingos_binding_package.py")
assert spec and spec.loader
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def cert(verdict="READY_FOR_BINDING", ready=True):
    return {
        "schema": m.CERT_SCHEMA,
        "verdict": verdict,
        "go_live_ready": False,
        "binding": {"ready": ready},
        "contract": {"network_call": False, "deployment_performed": False},
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY", "deploy_permission": "DENY"},
    }


def cfg(mode="DISABLED", perm="DENY", bindings=None):
    return {
        "schema": m.CONFIG_SCHEMA,
        "version": 1,
        "mode": mode,
        "deploy_permission": perm,
        "adapter_id": "telegram-primary",
        "credentials": {"telegram_bot_token_env": "TRADINGOS_TELEGRAM_BOT_TOKEN", "callback_hmac_secret_env": "TRADINGOS_CALLBACK_HMAC_SECRET"},
        "destination_bindings": {} if bindings is None else bindings,
    }


def test_template_only_has_no_hash_and_no_apply():
    p = m.build(cert(), cfg(), "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID")
    assert p["status"] == "TEMPLATE_ONLY"
    assert p["binding_request"]["destination_sha256"] is None
    assert p["contract"]["binding_apply_performed"] is False
    assert p["contract"]["network_call"] is False
    assert p["safety"]["deploy_permission"] == "DENY"


def test_hash_ready_persists_hash_not_raw_destination():
    raw = "-1001234567890"
    p = m.build(cert(), cfg(), "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID", raw)
    text = m.canonical(p) + m.render(p)
    assert p["status"] == "HASH_READY"
    assert p["binding_request"]["destination_sha256"] == m.sha256_text(raw)
    assert raw not in text


def test_generate_reads_destination_transiently_from_env(tmp_path):
    cp = tmp_path / "cert.json"; sp = tmp_path / "cfg.json"
    cp.write_text(json.dumps(cert())); sp.write_text(json.dumps(cfg()))
    p, jp, hp = m.generate(cp, sp, "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID", tmp_path / "out", "TEMP_DEST", {"TEMP_DEST": "chat-value-42"})
    assert p["status"] == "HASH_READY" and jp.exists() and hp.exists()
    assert "chat-value-42" not in jp.read_text() and "chat-value-42" not in hp.read_text()


def test_certificate_must_be_ready_for_binding():
    for c in (cert("READY_WITH_CONDITIONS", False), cert("NOT_READY", False)):
        try: m.build(c, cfg(), "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID")
        except ValueError: pass
        else: raise AssertionError


def test_source_config_must_stay_disabled_and_deploy_denied():
    for c in (cfg("ENABLED", "DENY"), cfg("DISABLED", "ALLOW")):
        try: m.build(cert(), c, "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID")
        except ValueError: pass
        else: raise AssertionError


def test_existing_destination_binding_rejected():
    try: m.build(cert(), cfg(bindings={"old": {"transport": "telegram"}}), "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID")
    except ValueError: pass
    else: raise AssertionError


def test_inline_secret_keys_rejected():
    c = cfg(); c["credentials"]["bot_token"] = "secret"
    try: m.build(cert(), c, "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID")
    except ValueError: pass
    else: raise AssertionError


def test_alias_and_env_names_are_validated():
    for alias, env in (("bad alias", "TRADINGOS_CHAT"), ("ops", "bad-env"), ("ops", "TRADINGOS_TELEGRAM_BOT_TOKEN")):
        try: m.build(cert(), cfg(), alias, env)
        except ValueError: pass
        else: raise AssertionError


def test_required_env_names_only_no_values():
    p = m.build(cert(), cfg(), "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID", "synthetic-destination")
    assert p["binding_request"]["required_env_names"] == ["TRADINGOS_TELEGRAM_CHAT_ID", "TRADINGOS_TELEGRAM_BOT_TOKEN", "TRADINGOS_CALLBACK_HMAC_SECRET"]
    assert p["contract"]["secrets_in_package"] is False
    assert p["contract"]["raw_destination_in_package"] is False


def test_rollback_and_preflight_are_non_mutating_plans():
    p = m.build(cert(), cfg(), "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID")
    assert p["rollback_plan"]["rollback_performed"] is False
    assert p["rollback_plan"]["network_call"] is False
    assert p["post_binding_preflight_plan"]["phase_2_future_go_live_preflight"]["not_authorized_by_this_package"] is True
    assert p["post_binding_preflight_plan"]["phase_2_future_go_live_preflight"]["network_call"] is False


def test_package_id_is_deterministic_and_hash_sensitive():
    a = m.build(cert(), cfg(), "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID", "one")
    b = m.build(cert(), cfg(), "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID", "one")
    c = m.build(cert(), cfg(), "ops_primary", "TRADINGOS_TELEGRAM_CHAT_ID", "two")
    assert a["package_id"] == b["package_id"] and a["package_id"] != c["package_id"]
