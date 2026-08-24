from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

R78_TEST = ROOT / "tests" / "test_tradingos_ai_analyst_contract.py"
R79_FIX = ROOT / "tests" / "r79_transport_fixtures.py"
MEMORY_POLICY = ROOT / "configs" / "TRADINGOS_RETROSPECTIVE_MEMORY_POLICY_V1.json"
R80_CONTRACT = ROOT / "tools" / "tradingos_retrospective_memory_contract.py"
R80_CAL = ROOT / "tools" / "tradingos_retrospective_calibration.py"

s = importlib.util.spec_from_file_location("r78f80", R78_TEST)
assert s and s.loader
r78f = importlib.util.module_from_spec(s)
s.loader.exec_module(r78f)

s2 = importlib.util.spec_from_file_location("r79f80", R79_FIX)
assert s2 and s2.loader
r79f = importlib.util.module_from_spec(s2)
s2.loader.exec_module(r79f)

s3 = importlib.util.spec_from_file_location("r80c", R80_CONTRACT)
assert s3 and s3.loader
m = importlib.util.module_from_spec(s3)
s3.loader.exec_module(m)

s4 = importlib.util.spec_from_file_location("r80cal", R80_CAL)
assert s4 and s4.loader
cal = importlib.util.module_from_spec(s4)
s4.loader.exec_module(cal)


def memory_policy():
    return json.loads(MEMORY_POLICY.read_text(encoding="utf-8"))


def chain():
    brief, rp, req, prompt = r79f.inputs()
    tp = r79f.tpolicy()
    env = r79f.m.build_transport_envelope(
        req, prompt, tp, provider_id="mock", model_id="local-fixture-v1"
    )
    response = r79f.safe_response(req)
    adapter = r79f.StaticAdapter(response)
    receipt = r79f.m.invoke_mock_transport(
        env, adapter, tp, request=req, prompt=prompt, r78_policy=rp, source_brief=brief
    )
    annotation = {
        "schema": m.ANNOTATION_SCHEMA,
        "request_id": response["request_id"],
        "brief_sha256": response["brief_sha256"],
        "claim_outcomes": [
            {
                "claim_id": response["claims"][0]["claim_id"],
                "outcome": "SUPPORTED",
                "rationale_code": "EVIDENCE_MATCH",
            }
        ],
    }
    return brief, rp, req, prompt, tp, env, receipt, response, annotation


def record(annotation_override=None):
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    if annotation_override is not None:
        annotation = annotation_override
    return m.build_retrospective_record(
        request=req,
        prompt=prompt,
        r78_policy=rp,
        source_brief=brief,
        transport_policy=tp,
        envelope=env,
        transport_receipt=receipt,
        response=response,
        annotation=annotation,
        memory_policy=memory_policy(),
    )
