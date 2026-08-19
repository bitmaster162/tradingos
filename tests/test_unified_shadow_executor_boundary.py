import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_executor_boundary import (
    PROOF_FIELDS,
    build_default_executor_evidence,
    build_shadow_executor_boundary_receipt,
    build_unified_shadow_executor_closure,
)


class UnifiedShadowExecutorBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v12",
            "closed_at": "2026-08-20T01:36:00+07:00",
            "case_id": "executor-boundary-001",
            "transaction_sha256": "c" * 64,
            "registered_node_count": 63,
            "effective_gate": "HOLD",
            "effective_action": "WAIT",
            "status": "P0_SHADOW_CLOSED_NO_EFFECT",
            "planes": {"parked_nontrading_plane": "BOUND_5_OF_5_NO_REVIVAL_OR_EFFECT_AUTHORITY"},
            "effect_summary": {"merge": False, "deploy": False, "signal": False, "order": False, "capital_effect": False},
            "safety": dict(SHADOW_SAFETY),
        }
        self.base["closure_sha256"] = sha256_obj(self.base)

    def receipt(self, evidence=None):
        return build_shadow_executor_boundary_receipt(
            self.base,
            build_default_executor_evidence() if evidence is None else evidence,
            generated_at="2026-08-20T01:37:00+07:00",
        )

    def test_executor_is_typed_but_disabled(self):
        receipt = self.receipt()
        self.assertFalse(receipt["executor_enabled"])
        self.assertFalse(receipt["dispatch_enabled"])
        self.assertFalse(receipt["arbitrary_command_allowed"])
        self.assertFalse(receipt["caller_chosen_effect_class_allowed"])
        self.assertTrue(receipt["trusted_effect_class_derivation_required"])
        self.assertTrue(receipt["operation_specific_handler_required"])
        self.assertEqual(receipt["execution_authority"], "NONE")
        self.assertTrue(all(value is False for value in receipt["effects"].values()))

    def test_old_arbitrary_command_effect_class_bypass_is_rejected(self):
        for field in ("arbitrary_command_allowed", "caller_chosen_effect_class_allowed", "dispatch_performed"):
            evidence = build_default_executor_evidence()
            evidence[field] = True
            with self.assertRaisesRegex(ShadowIntegrationError, "executor_effect_or_bypass_observed"):
                self.receipt(evidence)

    def test_full_contract_proof_still_does_not_enable_dispatch(self):
        evidence = build_default_executor_evidence()
        for field in PROOF_FIELDS:
            evidence[field] = True
        receipt = self.receipt(evidence)
        closure = build_unified_shadow_executor_closure(
            self.base, receipt, closed_at="2026-08-20T01:38:00+07:00"
        )
        self.assertTrue(receipt["proof_complete"])
        self.assertEqual(closure["schema"], "bitevo.unified_shadow_closure.v13")
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertFalse(closure["executor_status"]["executor_enabled"])
        self.assertFalse(closure["executor_status"]["dispatch_enabled"])
        self.assertFalse(closure["executor_status"]["execution_authority"])

    def test_tampered_receipt_is_rejected(self):
        receipt = copy.deepcopy(self.receipt())
        receipt["may_self_merge"] = True
        receipt["executor_receipt_sha256"] = sha256_obj(
            {k: v for k, v in receipt.items() if k != "executor_receipt_sha256"}
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "executor_closure_disabled_invariant_breached"):
            build_unified_shadow_executor_closure(
                self.base, receipt, closed_at="2026-08-20T01:39:00+07:00"
            )


if __name__ == "__main__":
    unittest.main()
