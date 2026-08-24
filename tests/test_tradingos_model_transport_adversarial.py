from __future__ import annotations

import ast
import copy
import pytest

from r79_transport_fixtures import (
    m, tpolicy, inputs, safe_response, StaticAdapter, envelope, MODULE_PATH, ROOT
)


@pytest.mark.parametrize("field", [
    "live_provider_calls_allowed",
    "credentials_allowed",
    "environment_secret_reads_allowed",
    "external_sources_allowed",
    "provider_tools_allowed",
    "streaming_allowed",
    "async_dispatch_allowed",
])
def test_policy_rejects_unsafe_boolean_widening(field):
    p = tpolicy()
    p[field] = True
    with pytest.raises(ValueError):
        m.validate_transport_policy(p)


@pytest.mark.parametrize("field", [
    "transport_boundary_enabled",
    "mock_local_allowed",
    "request_binding_required",
    "prompt_binding_required",
    "post_model_validation_required",
])
def test_policy_rejects_required_guard_disable(field):
    p = tpolicy()
    p[field] = False
    with pytest.raises(ValueError):
        m.validate_transport_policy(p)


@pytest.mark.parametrize("field,value", [
    ("allowed_adapter_modes", ["HTTP"]),
    ("allowed_adapter_modes", ["MOCK_LOCAL", "HTTP"]),
    ("retries", 1),
    ("retries", True),
    ("timeout_seconds", 1),
    ("max_prompt_chars", 49999),
])
def test_policy_rejects_contract_drift(field, value):
    p = tpolicy()
    p[field] = value
    with pytest.raises(ValueError):
        m.validate_transport_policy(p)


def test_policy_rejects_permission_widening():
    p = tpolicy()
    p["output_permissions"]["can_trade"] = True
    with pytest.raises(ValueError):
        m.validate_transport_policy(p)


@pytest.mark.parametrize("provider_id,model_id", [
    ("mock provider", "x"),
    ("mock", "model with spaces"),
    ("", "x"),
    ("mock", ""),
])
def test_envelope_rejects_bad_labels(provider_id, model_id):
    _, _, req, prompt = inputs()
    with pytest.raises(ValueError):
        m.build_transport_envelope(req, prompt, tpolicy(), provider_id=provider_id, model_id=model_id)


def test_envelope_rejects_live_mode():
    _, _, req, prompt = inputs()
    with pytest.raises(ValueError):
        m.build_transport_envelope(
            req, prompt, tpolicy(), provider_id="mock", model_id="x", transport_mode="HTTP"
        )


def test_envelope_rejects_oversize_prompt():
    _, _, req, _ = inputs()
    with pytest.raises(ValueError):
        m.build_transport_envelope(
            req, "x" * 50001, tpolicy(), provider_id="mock", model_id="x"
        )


@pytest.mark.parametrize("field", [
    "request_id",
    "brief_sha256",
    "request_sha256",
    "prompt_sha256",
    "transport_policy_sha256",
    "envelope_id",
])
def test_envelope_rejects_binding_mutation(field):
    _, _, req, prompt, env = envelope()
    env[field] = "0" * len(env[field])
    with pytest.raises(ValueError):
        m.validate_transport_envelope(env, req, prompt, tpolicy())


def test_envelope_rejects_prompt_mutation():
    _, _, req, prompt, env = envelope()
    env["prompt"] += " drift"
    with pytest.raises(ValueError):
        m.validate_transport_envelope(env, req, prompt, tpolicy())


def test_envelope_rejects_provider_mode_mutation():
    _, _, req, prompt, env = envelope()
    env["provider"]["transport_mode"] = "HTTP"
    with pytest.raises(ValueError):
        m.validate_transport_envelope(env, req, prompt, tpolicy())


def test_envelope_rejects_safety_widening():
    _, _, req, prompt, env = envelope()
    env["safety"]["network_call_authorized"] = True
    with pytest.raises(ValueError):
        m.validate_transport_envelope(env, req, prompt, tpolicy())


def test_envelope_rejects_cross_request_replay():
    _, _, req1, prompt1, env = envelope()
    _, _, req2, prompt2 = inputs()
    req2 = copy.deepcopy(req2)
    req2["request_id"] = "0" * 24
    with pytest.raises(ValueError):
        m.validate_transport_envelope(env, req2, prompt2, tpolicy())


@pytest.mark.parametrize("mode,network,creds", [
    ("HTTP", False, False),
    ("MOCK_LOCAL", True, False),
    ("MOCK_LOCAL", False, True),
])
def test_adapter_denied_before_call(mode, network, creds):
    brief, rp, req, prompt, env = envelope()
    response = safe_response(req)

    class BadAdapter(StaticAdapter):
        transport_mode = mode
        network_call_performed = network
        credentials_used = creds

    adapter = BadAdapter(response)
    with pytest.raises(ValueError):
        m.invoke_mock_transport(
            env, adapter, tpolicy(), request=req, prompt=prompt, r78_policy=rp, source_brief=brief
        )
    assert adapter.calls == 0


@pytest.mark.parametrize("field,value", [
    ("can_trade", True),
    ("execution_authority", "LIVE"),
    ("capital_permission", "ALLOW"),
    ("signals_allowed", True),
    ("orders_allowed", True),
    ("external_sources_used", True),
    ("probability_claimed", True),
])
def test_r78_validator_rejects_authority_or_source_widening(field, value):
    brief, rp, req, prompt, env = envelope()
    response = safe_response(req)
    response[field] = value
    adapter = StaticAdapter(response)
    with pytest.raises(ValueError):
        m.invoke_mock_transport(
            env, adapter, tpolicy(), request=req, prompt=prompt, r78_policy=rp, source_brief=brief
        )


def test_r78_validator_rejects_unknown_evidence_ref():
    brief, rp, req, prompt, env = envelope()
    response = safe_response(req)
    response["claims"][0]["evidence_refs"] = ["UNKNOWN-deadbeef0000"]
    with pytest.raises(ValueError):
        m.invoke_mock_transport(
            env, StaticAdapter(response), tpolicy(),
            request=req, prompt=prompt, r78_policy=rp, source_brief=brief
        )


def test_r78_validator_rejects_new_numeric_literal():
    brief, rp, req, prompt, env = envelope()
    response = safe_response(req)
    response["claims"][0]["text"] = "Referenced evidence implies 999999 risk units."
    with pytest.raises(ValueError):
        m.invoke_mock_transport(
            env, StaticAdapter(response), tpolicy(),
            request=req, prompt=prompt, r78_policy=rp, source_brief=brief
        )


def test_r78_validator_rejects_probability_language():
    brief, rp, req, prompt, env = envelope()
    response = safe_response(req)
    response["claims"][0]["text"] = "There is a probability of reversal."
    with pytest.raises(ValueError):
        m.invoke_mock_transport(
            env, StaticAdapter(response), tpolicy(),
            request=req, prompt=prompt, r78_policy=rp, source_brief=brief
        )


def test_r78_validator_rejects_execution_language():
    brief, rp, req, prompt, env = envelope()
    response = safe_response(req)
    response["claims"][0]["text"] = "Buy the asset now."
    with pytest.raises(ValueError):
        m.invoke_mock_transport(
            env, StaticAdapter(response), tpolicy(),
            request=req, prompt=prompt, r78_policy=rp, source_brief=brief
        )


def test_r79_sources_have_no_network_provider_secret_or_process_imports():
    forbidden = {
        "requests", "urllib", "httpx", "aiohttp", "socket", "subprocess",
        "openai", "anthropic", "google", "cohere", "mistralai", "os"
    }
    for path in sorted((ROOT / "tools").glob("tradingos_model_transport_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden), (path, imported & forbidden)


def test_r79_sources_contain_no_environment_or_secret_read_calls():
    forbidden = ["getenv(", "environ[", "api_key", "bearer ", "authorization:"]
    for path in sorted((ROOT / "tools").glob("tradingos_model_transport_*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, (path, token)
