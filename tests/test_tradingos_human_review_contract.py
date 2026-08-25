from __future__ import annotations

import copy
import json
from pathlib import Path
import pytest

from r82_human_review_fixtures import m, policy, report, review_input, attestation, ROOT


def test_valid_r81_report_is_accepted():
    m.validate_r81_report_for_review(report())


@pytest.mark.parametrize(
    "disposition,reasons",
    [
        ("ACKNOWLEDGED", ["INTEGRITY_CONFIRMED"]),
        ("ACKNOWLEDGED", ["COUNT_REVIEWED"]),
        ("ACKNOWLEDGED", ["INTEGRITY_CONFIRMED", "COUNT_REVIEWED"]),
        ("DISPUTED", ["SOURCE_BINDING_CONCERN"]),
        ("DISPUTED", ["POLICY_BINDING_CONCERN"]),
        ("DISPUTED", ["SOURCE_BINDING_CONCERN", "POLICY_BINDING_CONCERN"]),
        ("FOLLOWUP_REQUIRED", ["INSUFFICIENT_CONTEXT"]),
        ("FOLLOWUP_REQUIRED", ["SOURCE_BINDING_CONCERN", "INSUFFICIENT_CONTEXT"]),
        ("FOLLOWUP_REQUIRED", ["POLICY_BINDING_CONCERN", "INSUFFICIENT_CONTEXT"]),
    ],
)
def test_valid_review_variants(disposition, reasons):
    a = m.build_human_review_attestation(report(), review_input(disposition, reasons), policy())
    m.validate_human_review_attestation(a, report(), policy())


def test_attestation_is_deterministic():
    a = m.build_human_review_attestation(report(), review_input(), policy())
    b = m.build_human_review_attestation(report(), review_input(), policy())
    assert a == b


def test_report_binding_is_full_payload_sha():
    r = report(); a = m.build_human_review_attestation(r, review_input(), policy())
    assert a["shadow_report_sha256"] == m.stable_sha256(r)


def test_review_policy_binding_is_full_payload_sha():
    p = policy(); a = m.build_human_review_attestation(report(), review_input(), p)
    assert a["review_policy_sha256"] == m.stable_sha256(p)


def test_reason_order_is_preserved_as_canonical():
    a = attestation()
    assert a["reason_codes"] == ["INTEGRITY_CONFIRMED", "COUNT_REVIEWED"]


def test_exact_authority_ceiling():
    a = attestation()
    expected = {
        "shadow_only": True,
        "human_review_only": True,
        "report_consumption_authority": "NONE",
        "memory_write_authority": "NONE",
        "policy_update_allowed": False,
        "live_decision_feedback_allowed": False,
        "live_decision_use_allowed": False,
        "model_selection_use_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    for k, v in expected.items():
        assert a[k] == v


def test_review_origin_does_not_claim_identity():
    assert attestation()["review_origin"] == "UNVERIFIED_HUMAN_INPUT"


def test_schema_json_parses_and_denies_additional_properties():
    schema = json.loads((ROOT / "schemas" / "TRADINGOS_HUMAN_REVIEW_ATTESTATION_V1.schema.json").read_text())
    assert schema["additionalProperties"] is False
    assert schema["properties"]["review_origin"]["const"] == "UNVERIFIED_HUMAN_INPUT"


def test_policy_json_parses_and_has_no_authority():
    p = policy()
    m.validate_review_policy(p)
    assert p["report_consumption_authority"] == "NONE"
    assert p["output_permissions"]["execution_authority"] == "NONE"
    assert p["output_permissions"]["can_trade"] is False
    assert p["output_permissions"]["capital_permission"] == "DENY"
