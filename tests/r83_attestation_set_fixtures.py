from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from r82_human_review_fixtures import m as r82m, policy as r82_policy, report, review_input

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SET_POLICY = ROOT / "configs" / "TRADINGOS_ATTESTATION_EVIDENCE_SET_POLICY_V1.json"
CONTRACT = ROOT / "tools" / "tradingos_attestation_set_contract.py"

s = importlib.util.spec_from_file_location("r83c", CONTRACT)
assert s and s.loader
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


def set_policy():
    return json.loads(SET_POLICY.read_text(encoding="utf-8"))


def evidence_item(disposition="ACKNOWLEDGED", reasons=None, report_suffix=""):
    p = r82_policy()
    r = report()
    if report_suffix:
        r["frozen_set_declaration_sha256"] = report_suffix[0] * 64
        payload = {k: r[k] for k in r82m.R81_REPORT_KEYS if k != "report_id"}
        import hashlib
        r["report_id"] = hashlib.sha256(
            f"tradingos.shadow_evaluation_report.v1:1.0.0:".encode("utf-8")
            + r82m.r81c.stable_json_bytes(payload)
        ).hexdigest()[:24]
    a = r82m.build_human_review_attestation(r, review_input(disposition, reasons), p)
    return {"attestation": a, "report": r, "review_policy": p}


def evidence_items():
    return [
        evidence_item("ACKNOWLEDGED", ["INTEGRITY_CONFIRMED", "COUNT_REVIEWED"], "e"),
        evidence_item("DISPUTED", ["SOURCE_BINDING_CONCERN"], "f"),
        evidence_item("FOLLOWUP_REQUIRED", ["INSUFFICIENT_CONTEXT"], "1"),
    ]


def same_report_multiple_attestations():
    p = r82_policy()
    r = report()
    return [
        {"attestation": r82m.build_human_review_attestation(r, review_input("ACKNOWLEDGED", ["INTEGRITY_CONFIRMED"]), p), "report": copy.deepcopy(r), "review_policy": copy.deepcopy(p)},
        {"attestation": r82m.build_human_review_attestation(r, review_input("DISPUTED", ["SOURCE_BINDING_CONCERN"]), p), "report": copy.deepcopy(r), "review_policy": copy.deepcopy(p)},
    ]


def manifest(items=None):
    if items is None:
        items = evidence_items()
    return m.build_attestation_evidence_set(items, set_policy())


def clone(value):
    return copy.deepcopy(value)
