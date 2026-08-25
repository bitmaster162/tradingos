from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REVIEW_POLICY = ROOT / "configs" / "TRADINGOS_HUMAN_REVIEW_POLICY_V1.json"
CONTRACT = ROOT / "tools" / "tradingos_human_review_contract.py"

s = importlib.util.spec_from_file_location("r82c", CONTRACT)
assert s and s.loader
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


def policy():
    return json.loads(REVIEW_POLICY.read_text(encoding="utf-8"))


def report():
    r = {
        "schema": "tradingos.shadow_evaluation_report.v1",
        "frozen_set_declaration_id": "a" * 24,
        "frozen_set_declaration_sha256": "b" * 64,
        "memory_policy_sha256": "c" * 64,
        "shadow_policy_sha256": "d" * 64,
        "record_count": 4,
        "claim_count": 7,
        "counts_by_outcome": {
            "SUPPORTED": 2,
            "CONTRADICTED": 1,
            "UNRESOLVED": 3,
            "NOT_EVALUABLE": 1,
        },
        "counts_by_claim_kind": {
            "THESIS": 1,
            "COUNTERTHESIS": 1,
            "BLIND_SPOT": 1,
            "PREMORTEM": 1,
            "SCENARIO_READ": 1,
            "INVALIDATION_READ": 1,
            "OPERATOR_QUESTION": 1,
        },
        "integrity": {
            "all_records_valid": True,
            "duplicate_record_ids_absent": True,
            "frozen_set_exact": True,
            "mixed_memory_policy_absent": True,
            "record_payloads_bound": True,
        },
        "report_mode": "COUNT_AND_INTEGRITY_ONLY",
        "shadow_only": True,
        "memory_write_authority": "NONE",
        "auto_learning_allowed": False,
        "live_decision_feedback_allowed": False,
        "live_decision_use_allowed": False,
        "model_selection_use_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    payload = {k: r[k] for k in m.R81_REPORT_KEYS if k != "report_id"}
    r["report_id"] = hashlib.sha256(
        f"tradingos.shadow_evaluation_report.v1:1.0.0:".encode("utf-8")
        + m.r81c.stable_json_bytes(payload)
    ).hexdigest()[:24]
    return r


def review_input(disposition="ACKNOWLEDGED", reasons=None):
    if reasons is None:
        reasons = ["INTEGRITY_CONFIRMED", "COUNT_REVIEWED"]
    return {"disposition": disposition, "reason_codes": reasons}


def attestation(disposition="ACKNOWLEDGED", reasons=None):
    return m.build_human_review_attestation(report(), review_input(disposition, reasons), policy())


def clone(value):
    return copy.deepcopy(value)
