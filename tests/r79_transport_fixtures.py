from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODULE_PATH = ROOT / "tools" / "tradingos_model_transport_contract.py"
R78_TEST_PATH = ROOT / "tests" / "test_tradingos_ai_analyst_contract.py"
TPOLICY_PATH = ROOT / "configs" / "TRADINGOS_MODEL_TRANSPORT_POLICY_V1.json"

SPEC = importlib.util.spec_from_file_location("r79", MODULE_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

R78_SPEC = importlib.util.spec_from_file_location("r78_fixtures", R78_TEST_PATH)
assert R78_SPEC and R78_SPEC.loader
r78f = importlib.util.module_from_spec(R78_SPEC)
R78_SPEC.loader.exec_module(r78f)


def tpolicy():
    return json.loads(TPOLICY_PATH.read_text(encoding="utf-8"))


def inputs():
    brief = r78f.brief()
    policy = r78f.policy()
    request = r78f.m.build_request(brief, policy)
    prompt = r78f.m.render_model_prompt(request, policy)
    return brief, policy, request, prompt


def safe_response(request):
    ref = request["evidence_catalog"][0]["evidence_id"]
    return {
        "schema": r78f.m.RESPONSE_SCHEMA,
        "request_id": request["request_id"],
        "brief_sha256": request["brief"]["brief_sha256"],
        "analysis_mode": request["analysis_mode"],
        "claims": [{
            "claim_id": "C1",
            "kind": "BLIND_SPOT",
            "text": "Referenced evidence may underweight conflicting structure.",
            "evidence_refs": [ref],
            "claim_scope": "INTERPRETATION_OF_REFERENCED_EVIDENCE",
            "novel_market_fact": False,
        }],
        "operator_disposition": "REVIEW_BRIEF",
        "questions": [{
            "question_id": "Q1",
            "text": "Should the referenced conflict be reviewed?",
            "evidence_refs": [ref],
        }],
        "external_sources_used": False,
        "probability_claimed": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }


class StaticAdapter:
    transport_mode = "MOCK_LOCAL"
    network_call_performed = False
    credentials_used = False

    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.seen = None

    def invoke(self, envelope):
        self.calls += 1
        self.seen = envelope
        return copy.deepcopy(self.response)


def envelope():
    brief, rp, req, prompt = inputs()
    return brief, rp, req, prompt, m.build_transport_envelope(
        req, prompt, tpolicy(), provider_id="mock", model_id="local-fixture-v1"
    )
