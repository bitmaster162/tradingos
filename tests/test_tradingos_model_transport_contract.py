from __future__ import annotations

import copy
import pytest

from r79_transport_fixtures import m, tpolicy, inputs, safe_response, StaticAdapter, envelope


def test_policy_accepts_exact_v1():
    m.validate_transport_policy(tpolicy())


def test_envelope_exact_positive():
    _, _, req, prompt, env = envelope()
    assert env["schema"] == m.ENVELOPE_SCHEMA
    assert env["request_id"] == req["request_id"]
    assert env["prompt"] == prompt
    assert env["provider"]["transport_mode"] == "MOCK_LOCAL"
    assert env["safety"] == m.SAFETY


def test_mock_invoke_positive_and_receipt_bound():
    brief, rp, req, prompt, env = envelope()
    response = safe_response(req)
    adapter = StaticAdapter(response)
    receipt = m.invoke_mock_transport(
        env, adapter, tpolicy(), request=req, prompt=prompt, r78_policy=rp, source_brief=brief
    )
    assert adapter.calls == 1
    assert receipt["r78_response_validated"] is True
    assert receipt["network_call_performed"] is False
    assert receipt["execution_authority"] == "NONE"
    assert receipt["can_trade"] is False
    assert receipt["capital_permission"] == "DENY"


def test_adapter_receives_deep_copy():
    brief, rp, req, prompt, env = envelope()
    response = safe_response(req)

    class MutatingAdapter(StaticAdapter):
        def invoke(self, supplied):
            self.calls += 1
            supplied["prompt"] = "mutated inside adapter"
            return copy.deepcopy(self.response)

    original = copy.deepcopy(env)
    adapter = MutatingAdapter(response)
    receipt = m.invoke_mock_transport(
        env, adapter, tpolicy(), request=req, prompt=prompt, r78_policy=rp, source_brief=brief
    )
    assert env == original
    assert receipt["r78_response_validated"] is True


def test_non_object_response_rejected():
    brief, rp, req, prompt, env = envelope()
    adapter = StaticAdapter("not-an-object")
    with pytest.raises(ValueError):
        m.invoke_mock_transport(
            env, adapter, tpolicy(), request=req, prompt=prompt, r78_policy=rp, source_brief=brief
        )


def test_receipt_mutation_rejected():
    brief, rp, req, prompt, env = envelope()
    response = safe_response(req)
    adapter = StaticAdapter(response)
    receipt = m.invoke_mock_transport(
        env, adapter, tpolicy(), request=req, prompt=prompt, r78_policy=rp, source_brief=brief
    )
    receipt["can_trade"] = True
    with pytest.raises(ValueError):
        m.validate_transport_receipt(receipt, env, response)
