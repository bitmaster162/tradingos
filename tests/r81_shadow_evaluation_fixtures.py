from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
R80_FIX = ROOT / "tests" / "r80_retrospective_fixtures.py"
SHADOW_POLICY = ROOT / "configs" / "TRADINGOS_SHADOW_EVALUATION_POLICY_V1.json"
R81_CONTRACT = ROOT / "tools" / "tradingos_shadow_evaluation_contract.py"

s = importlib.util.spec_from_file_location("r80f81", R80_FIX)
assert s and s.loader
r80f = importlib.util.module_from_spec(s)
s.loader.exec_module(r80f)

s2 = importlib.util.spec_from_file_location("r81c", R81_CONTRACT)
assert s2 and s2.loader
m = importlib.util.module_from_spec(s2)
s2.loader.exec_module(m)


def memory_policy():
    return r80f.memory_policy()


def shadow_policy():
    return json.loads(SHADOW_POLICY.read_text(encoding="utf-8"))


def record_for(outcome: str = "SUPPORTED", rationale_code: str = "EVIDENCE_MATCH"):
    brief, rp, req, prompt, tp, env, receipt, response, annotation = r80f.chain()
    annotation = copy.deepcopy(annotation)
    annotation["claim_outcomes"][0]["outcome"] = outcome
    annotation["claim_outcomes"][0]["rationale_code"] = rationale_code
    return r80f.m.build_retrospective_record(
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


def records():
    return [
        record_for("SUPPORTED", "EVIDENCE_MATCH"),
        record_for("CONTRADICTED", "EVIDENCE_CONFLICT"),
        record_for("UNRESOLVED", "INSUFFICIENT_EVIDENCE"),
        record_for("NOT_EVALUABLE", "NOT_APPLICABLE"),
    ]


def declaration(source_records=None):
    rs = records() if source_records is None else source_records
    return m.build_frozen_set_declaration(rs, memory_policy(), shadow_policy())


def report(source_records=None, source_declaration=None):
    rs = records() if source_records is None else source_records
    dec = declaration(rs) if source_declaration is None else source_declaration
    return m.build_shadow_report(rs, dec, memory_policy(), shadow_policy())
