from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("guard", ROOT / "tools" / "tradingos_delivery_guard.py")
assert spec and spec.loader
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

CHAT = "-1001234567890"
SECRET = "q" * 40

def cfg(enabled=True, perm="ALLOW", dm=2):
    return {
        "schema": g.CFG,
        "version": 1,
        "mode": "ENABLED" if enabled else "DISABLED",
        "deploy_permission": perm,
        "adapter_id": "telegram-primary",
        "credentials": {
            "telegram_bot_token_env": "TRADINGOS_TELEGRAM_BOT_TOKEN",
            "callback_hmac_secret_env": "TRADINGOS_CALLBACK_HMAC_SECRET",
        },
        "destination_bindings": {
            "ops_primary": {
                "transport": "telegram",
                "destination_env": "TRADINGOS_TELEGRAM_CHAT_ID",
                "destination_sha256": g.sha_text(CHAT),
            }
        } if enabled else {},
        "rate_limits": {"delivery_attempts_per_minute": dm, "callbacks_per_minute": 10},
        "callback_max_age_seconds": 300,
    }

def env(bot=True):
    out = {
        "TRADINGOS_TELEGRAM_CHAT_ID": CHAT,
        "TRADINGOS_CALLBACK_HMAC_SECRET": SECRET,
    }
    if bot:
        out["TRADINGOS_TELEGRAM_BOT_TOKEN"] = "synthetic-token"
    return out

def manifest():
    return {"schema": "tradingos.delivery.telegram.v1", "mode": "DRY_RUN", "contract": {"network_call": False}}

def test_no_product_feedback_imports():
    text = (ROOT / "tools" / "tradingos_delivery_guard.py").read_text()
    assert "tradingos_feedback_actions" not in text
    assert "tradingos_feedback_callback" not in text
    assert "authenticated_feedback" not in text

def test_inline_secret_and_destination_mismatch_rejected():
    c = cfg()
    c["credentials"]["bot_token"] = "bad"
    try:
        g.validate(c)
    except ValueError:
        pass
    else:
        raise AssertionError
    try:
        g.runtime(
            cfg(),
            "ops_primary",
            {
                "TRADINGOS_TELEGRAM_CHAT_ID": "wrong",
                "TRADINGOS_TELEGRAM_BOT_TOKEN": "x",
                "TRADINGOS_CALLBACK_HMAC_SECRET": SECRET,
            },
        )
    except ValueError:
        pass
    else:
        raise AssertionError

def test_disabled_preflight_denies_and_audits(tmp_path):
    audit = tmp_path / "audit"
    result = g.preflight(
        manifest(), cfg(False, "DENY"), audit, "ops_primary",
        "delivery:req:disabled", "2026-08-09T19:00:00Z", {}
    )
    assert result["decision"] == "DENY"
    assert result["reason"] == "CONFIG_DISABLED"
    assert result["contract"]["network_call"] is False
    assert len(g.audit_rows(audit)) == 1

def test_deploy_deny_replay_rate_limit_and_secret_redaction(tmp_path):
    audit = tmp_path / "audit"
    denied = g.preflight(
        manifest(), cfg(True, "DENY"), audit, "ops_primary",
        "delivery:req:deny001", "2026-08-09T18:00:00Z", env()
    )
    assert denied["reason"] == "DEPLOY_PERMISSION_DENY"

    audit2 = tmp_path / "audit2"
    c = cfg(dm=1)
    first = g.preflight(manifest(), c, audit2, "ops_primary", "delivery:req:ready01", "2026-08-09T18:00:00Z", env())
    replay = g.preflight(manifest(), c, audit2, "ops_primary", "delivery:req:ready01", "2026-08-09T18:00:01Z", env())
    limited = g.preflight(manifest(), c, audit2, "ops_primary", "delivery:req:ready02", "2026-08-09T18:00:02Z", env())
    assert first["decision"] == "ALLOW_READY"
    assert replay["reason"] == "REPLAY_REQUEST_ID"
    assert limited["reason"] == "RATE_LIMIT"
    assert "synthetic-token" not in str(first)
    assert CHAT not in str(first)

def test_audit_tamper_detected(tmp_path):
    audit = tmp_path / "audit"
    g.write_audit(
        audit, "2026-08-09T18:00:00Z", "delivery:req:tamper1",
        "OUTBOUND", "DENY", "X", "telegram-primary", None
    )
    audit.write_text(audit.read_text().replace('"reason":"X"', '"reason":"Y"'))
    try:
        g.audit_rows(audit)
    except ValueError:
        pass
    else:
        raise AssertionError

def test_outbound_guard_rejects_callback_audit_rows(tmp_path):
    audit = tmp_path / "audit"
    try:
        g.write_audit(
            audit, "2026-08-09T18:00:00Z", "callback:req:0001",
            "CALLBACK", "DENY", "X", "telegram-primary", "ops_primary"
        )
    except ValueError:
        pass
    else:
        raise AssertionError
