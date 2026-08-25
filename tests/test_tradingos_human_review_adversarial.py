from __future__ import annotations

import ast
import copy
from pathlib import Path
import pytest

from r82_human_review_fixtures import m, policy, report, review_input, attestation, ROOT


@pytest.mark.parametrize("extra_key", [
    "free_text", "notes", "recommendation", "recommended_action", "probability", "rate",
    "confidence", "pnl", "price", "return", "model_rank", "provider_rank", "model_choice",
    "provider_choice", "policy_update", "prompt_update", "weight_update", "signal", "order",
    "wallet", "position_size", "entry", "stop", "take_profit", "execute", "credential",
])
def test_review_input_rejects_extra_fields(extra_key):
    x = review_input(); x[extra_key] = "forbidden"
    with pytest.raises(ValueError, match="review input key set mismatch"):
        m.build_human_review_attestation(report(), x, policy())


@pytest.mark.parametrize("bad", [None, "ACK", 7, True, "APPROVE", "REJECT", "MERGE", "TRADE"])
def test_disposition_rejected(bad):
    x = review_input(); x["disposition"] = bad
    with pytest.raises(ValueError):
        m.build_human_review_attestation(report(), x, policy())


@pytest.mark.parametrize("bad_reasons", [
    [],
    ["INTEGRITY_CONFIRMED", "INTEGRITY_CONFIRMED"],
    ["COUNT_REVIEWED", "INTEGRITY_CONFIRMED"],
    ["UNKNOWN"],
    ["INTEGRITY_CONFIRMED", "COUNT_REVIEWED", "INSUFFICIENT_CONTEXT"],
    "INTEGRITY_CONFIRMED",
    None,
])
def test_reason_codes_rejected(bad_reasons):
    x = review_input(); x["reason_codes"] = bad_reasons
    with pytest.raises(ValueError):
        m.build_human_review_attestation(report(), x, policy())


@pytest.mark.parametrize("disposition,reasons", [
    ("ACKNOWLEDGED", ["SOURCE_BINDING_CONCERN"]),
    ("ACKNOWLEDGED", ["INSUFFICIENT_CONTEXT"]),
    ("DISPUTED", ["INTEGRITY_CONFIRMED"]),
    ("DISPUTED", ["INSUFFICIENT_CONTEXT"]),
    ("FOLLOWUP_REQUIRED", ["SOURCE_BINDING_CONCERN"]),
    ("FOLLOWUP_REQUIRED", ["POLICY_BINDING_CONCERN"]),
])
def test_disposition_reason_mismatch_rejected(disposition, reasons):
    with pytest.raises(ValueError):
        m.build_human_review_attestation(report(), review_input(disposition, reasons), policy())


@pytest.mark.parametrize("field,new_value", [
    ("record_count", 5),
    ("claim_count", 8),
    ("report_mode", "RATES"),
    ("shadow_only", False),
    ("memory_write_authority", "WRITE"),
    ("auto_learning_allowed", True),
    ("live_decision_feedback_allowed", True),
    ("live_decision_use_allowed", True),
    ("model_selection_use_allowed", True),
    ("execution_authority", "LIVE"),
    ("can_trade", True),
    ("capital_permission", "ALLOW"),
    ("confers_authority", True),
])
def test_r81_report_tamper_rejected(field, new_value):
    r = report(); r[field] = new_value
    with pytest.raises(ValueError):
        m.build_human_review_attestation(r, review_input(), policy())


@pytest.mark.parametrize("integrity_key", [
    "all_records_valid", "duplicate_record_ids_absent", "frozen_set_exact",
    "mixed_memory_policy_absent", "record_payloads_bound",
])
def test_r81_integrity_tamper_rejected(integrity_key):
    r = report(); r["integrity"][integrity_key] = False
    with pytest.raises(ValueError):
        m.build_human_review_attestation(r, review_input(), policy())


def test_r81_outcome_count_tamper_rejected():
    r = report(); r["counts_by_outcome"]["SUPPORTED"] += 1
    with pytest.raises(ValueError):
        m.build_human_review_attestation(r, review_input(), policy())


def test_r81_claim_kind_count_tamper_rejected():
    r = report(); r["counts_by_claim_kind"]["THESIS"] += 1
    with pytest.raises(ValueError):
        m.build_human_review_attestation(r, review_input(), policy())


def test_r81_report_id_tamper_rejected():
    r = report(); r["report_id"] = "0" * 24
    with pytest.raises(ValueError):
        m.build_human_review_attestation(r, review_input(), policy())


def test_r81_extra_field_rejected():
    r = report(); r["recommendation"] = "BUY"
    with pytest.raises(ValueError, match="R81 report key set mismatch"):
        m.build_human_review_attestation(r, review_input(), policy())


@pytest.mark.parametrize("field,new_value", [
    ("review_origin", "VERIFIED_OWNER"),
    ("disposition", "DISPUTED"),
    ("shadow_only", False),
    ("human_review_only", False),
    ("report_consumption_authority", "LIVE"),
    ("memory_write_authority", "WRITE"),
    ("policy_update_allowed", True),
    ("live_decision_feedback_allowed", True),
    ("live_decision_use_allowed", True),
    ("model_selection_use_allowed", True),
    ("execution_authority", "LIVE"),
    ("can_trade", True),
    ("capital_permission", "ALLOW"),
    ("confers_authority", True),
])
def test_attestation_tamper_rejected(field, new_value):
    a = attestation(); a[field] = new_value
    with pytest.raises(ValueError):
        m.validate_human_review_attestation(a, report(), policy())


def test_attestation_report_binding_rejects_other_valid_report():
    r1 = report(); a = m.build_human_review_attestation(r1, review_input(), policy())
    r2 = report(); r2["counts_by_outcome"] = {"SUPPORTED": 1, "CONTRADICTED": 2, "UNRESOLVED": 3, "NOT_EVALUABLE": 1}
    payload = {k: r2[k] for k in m.R81_REPORT_KEYS if k != "report_id"}
    import hashlib
    r2["report_id"] = hashlib.sha256(
        f"tradingos.shadow_evaluation_report.v1:1.0.0:".encode() + m.r81c.stable_json_bytes(payload)
    ).hexdigest()[:24]
    with pytest.raises(ValueError, match="attestation report"):
        m.validate_human_review_attestation(a, r2, policy())


def test_policy_tamper_rejected():
    p = policy(); p["live_decision_use_allowed"] = True
    with pytest.raises(ValueError):
        m.build_human_review_attestation(report(), review_input(), p)


def test_policy_extra_field_rejected():
    p = policy(); p["reviewer_name"] = "someone"
    with pytest.raises(ValueError, match="review policy key set mismatch"):
        m.build_human_review_attestation(report(), review_input(), p)


def test_core_has_no_unsafe_imports_or_calls():
    forbidden_imports = {"os", "subprocess", "socket", "requests", "urllib", "http", "pathlib", "sqlite3", "asyncio"}
    forbidden_calls = {"open", "exec", "eval", "compile", "__import__", "system", "popen"}
    for rel in ["tools/tradingos_human_review_common.py", "tools/tradingos_human_review_contract.py"]:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in forbidden_imports for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden_imports
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls


def test_core_has_no_persistence_network_provider_or_execution_tokens():
    forbidden = [
        "requests.", "urllib.", "http.client", "socket.", "subprocess.", "os.environ", "open(",
        "sqlite", "postgres", "redis", "api_key", "bearer", "authorization:", "place_order",
        "send_order", "execute_trade", "model_client", "provider_client",
    ]
    text = "\n".join((ROOT / rel).read_text(encoding="utf-8").lower() for rel in [
        "tools/tradingos_human_review_common.py", "tools/tradingos_human_review_contract.py"
    ])
    for token in forbidden:
        assert token not in text
