from __future__ import annotations
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
s = importlib.util.spec_from_file_location("bridge", ROOT / "tools" / "tradingos_preflight_bridge.py")
assert s and s.loader
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)


def config(mode="DISABLED", perm="DENY", bound=False, dm=3):
    c = {
        "schema": m.guard.CFG,
        "version": 1,
        "mode": mode,
        "deploy_permission": perm,
        "adapter_id": "telegram-primary",
        "credentials": {
            "telegram_bot_token_env": "TRADINGOS_TELEGRAM_BOT_TOKEN",
            "callback_hmac_secret_env": "TRADINGOS_CALLBACK_HMAC_SECRET",
        },
        "destination_bindings": {},
        "rate_limits": {"delivery_attempts_per_minute": dm, "callbacks_per_minute": 10},
        "callback_max_age_seconds": 300,
    }
    if bound:
        c["destination_bindings"] = {
            "ops_primary": {
                "transport": "telegram",
                "destination_env": "TRADINGOS_TELEGRAM_CHAT_ID",
                "destination_sha256": m.guard.sha_text("-1001234567890"),
            }
        }
    return c


def env(destination="-1001234567890", bot="synthetic-token-r20", secret="s" * 40):
    return {
        "TRADINGOS_TELEGRAM_CHAT_ID": destination,
        "TRADINGOS_TELEGRAM_BOT_TOKEN": bot,
        "TRADINGOS_CALLBACK_HMAC_SECRET": secret,
    }


def manifest(network=False):
    return {"schema": "tradingos.delivery.telegram.v1", "mode": "DRY_RUN", "contract": {"network_call": network}}


def run(tmp_path, cfg, rid="delivery:r20:test0001", e=None, man=None, at="2026-08-10T09:00:00Z"):
    return m.build(man or manifest(), cfg, tmp_path / "audit.ndjson", "ops_primary", rid, at, {} if e is None else e)


def test_real_disabled_config_returns_deny_without_runtime_values(tmp_path):
    r = run(tmp_path, config())
    assert r["status"] == "DENY" and r["guard_reason"] == "CONFIG_DISABLED"
    assert r["contract"]["network_call"] is False and r["contract"]["delivery_send_authorized"] is False


def test_enabled_allow_complete_runtime_returns_allow_ready_no_send(tmp_path):
    r = run(tmp_path, config("ENABLED", "ALLOW", True), e=env())
    assert r["status"] == "ALLOW_READY_NO_SEND" and r["guard_decision"] == "ALLOW_READY"
    assert r["contract"]["allow_ready_is_not_delivery"] is True
    assert r["contract"]["delivery_send_authorized"] is False and r["contract"]["deployment_authorized"] is False


def test_deploy_deny_blocks_before_runtime_readiness(tmp_path):
    r = run(tmp_path, config("ENABLED", "DENY", True), e=env())
    assert r["status"] == "DENY" and r["guard_reason"] == "DEPLOY_PERMISSION_DENY"


def test_missing_bot_denies(tmp_path):
    e = env(); e.pop("TRADINGOS_TELEGRAM_BOT_TOKEN")
    r = run(tmp_path, config("ENABLED", "ALLOW", True), e=e)
    assert r["status"] == "DENY" and "bot token missing" in r["guard_reason"]


def test_short_hmac_secret_denies(tmp_path):
    r = run(tmp_path, config("ENABLED", "ALLOW", True), e=env(secret="short"))
    assert r["status"] == "DENY" and "HMAC secret too short" in r["guard_reason"]


def test_destination_hash_mismatch_denies(tmp_path):
    r = run(tmp_path, config("ENABLED", "ALLOW", True), e=env(destination="-1009999999999"))
    assert r["status"] == "DENY" and "destination binding mismatch" in r["guard_reason"]


def test_runtime_values_are_not_persisted_or_hashed_by_bridge(tmp_path):
    e = env(destination="-1001234567890", bot="UNIQUE_R20_BOT_VALUE", secret="UNIQUE_R20_SECRET_VALUE_abcdefghijklmnopqrstuvwxyz")
    r = run(tmp_path, config("ENABLED", "ALLOW", True), e=e)
    text = m.canonical(r)
    assert all(value not in text for value in e.values())
    assert r["runtime"]["values_persisted"] is False and r["runtime"]["values_hashed_by_bridge"] is False


def test_replay_request_id_denied_by_guard(tmp_path):
    c = config("ENABLED", "ALLOW", True); e = env(); audit = tmp_path / "audit.ndjson"
    one = m.build(manifest(), c, audit, "ops_primary", "delivery:r20:replay01", "2026-08-10T09:00:00Z", e)
    two = m.build(manifest(), c, audit, "ops_primary", "delivery:r20:replay01", "2026-08-10T09:00:01Z", e)
    assert one["status"] == "ALLOW_READY_NO_SEND" and two["guard_reason"] == "REPLAY_REQUEST_ID"


def test_rate_limit_denied_by_guard(tmp_path):
    c = config("ENABLED", "ALLOW", True, dm=1); e = env(); audit = tmp_path / "audit.ndjson"
    a = m.build(manifest(), c, audit, "ops_primary", "delivery:r20:rate0001", "2026-08-10T09:00:00Z", e)
    b = m.build(manifest(), c, audit, "ops_primary", "delivery:r20:rate0002", "2026-08-10T09:00:01Z", e)
    assert a["status"] == "ALLOW_READY_NO_SEND" and b["guard_reason"] == "RATE_LIMIT"


def test_unsafe_manifest_rejected(tmp_path):
    try:
        run(tmp_path, config("ENABLED", "ALLOW", True), e=env(), man=manifest(True))
    except ValueError as exc:
        assert "unsafe delivery manifest" in str(exc)
    else:
        raise AssertionError("unsafe network manifest accepted")


def test_bridge_rejects_guard_network_contract_tamper(tmp_path):
    class Fake:
        validate = staticmethod(m.guard.validate)
        sha = staticmethod(m.guard.sha)
        @staticmethod
        def preflight(*args, **kwargs):
            return {"decision": "ALLOW_READY", "reason": "PREFLIGHT_READY", "request_id": "delivery:r20:tamper01", "runtime": {"destination_bound": True, "bot_present": True, "secret_present": True}, "audit_record_hash": "a"*64, "contract": {"preflight_only": True, "network_call": True, "allow_ready_is_not_delivery": True}}
    try:
        m.build(manifest(), config("ENABLED", "ALLOW", True), tmp_path / "a", "ops_primary", "delivery:r20:tamper01", "2026-08-10T09:00:00Z", env(), Fake)
    except ValueError as exc:
        assert "network" in str(exc)
    else:
        raise AssertionError("network-capable guard receipt accepted")


def test_bridge_rejects_allow_ready_without_complete_runtime(tmp_path):
    class Fake:
        validate = staticmethod(m.guard.validate)
        sha = staticmethod(m.guard.sha)
        @staticmethod
        def preflight(*args, **kwargs):
            return {"decision": "ALLOW_READY", "reason": "PREFLIGHT_READY", "request_id": "delivery:r20:badready1", "runtime": {"destination_bound": True, "bot_present": False, "secret_present": True}, "audit_record_hash": "b"*64, "contract": {"preflight_only": True, "network_call": False, "allow_ready_is_not_delivery": True}}
    try:
        m.build(manifest(), config("ENABLED", "ALLOW", True), tmp_path / "a", "ops_primary", "delivery:r20:badready1", "2026-08-10T09:00:00Z", env(), Fake)
    except ValueError as exc:
        assert "complete runtime" in str(exc)
    else:
        raise AssertionError("incomplete ALLOW_READY accepted")


def test_generate_writes_redacted_json_and_html(tmp_path):
    cp = tmp_path / "config.json"; mp = tmp_path / "manifest.json"
    cp.write_text(json.dumps(config("ENABLED", "ALLOW", True))); mp.write_text(json.dumps(manifest()))
    e = env(bot="UNIQUE_GENERATE_BOT", secret="UNIQUE_GENERATE_SECRET_abcdefghijklmnopqrstuvwxyz")
    payload, jp, hp = m.generate(mp, cp, tmp_path / "audit", "ops_primary", "delivery:r20:generate1", "2026-08-10T09:00:00Z", tmp_path / "out", e)
    text = jp.read_text() + hp.read_text()
    assert payload["status"] == "ALLOW_READY_NO_SEND" and jp.exists() and hp.exists()
    assert all(value not in text for value in e.values())
