import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_human_interface import build_default_interface_evidence, build_shadow_human_interface_ledger
from tools.unified_shadow_human_interface_closure import build_unified_shadow_human_interface_closure


class UnifiedShadowHumanInterfaceClosureTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v9",
            "closed_at": "2026-08-20T01:24:00+07:00",
            "case_id": "trade-interface-close-001",
            "transaction_sha256": "d" * 64,
            "registered_node_count": 63,
            "effective_gate": "HOLD",
            "effective_action": "WAIT",
            "status": "P0_SHADOW_CLOSED_NO_EFFECT",
            "planes": {"cognition_proposal_plane": "BOUND_11_OF_11_PROPOSAL_ONLY_NO_CASE_INFLUENCE"},
            "effect_summary": {
                "merge": False,
                "deploy": False,
                "runtime_activation": False,
                "signal": False,
                "order": False,
                "capital_effect": False,
            },
            "safety": dict(SHADOW_SAFETY),
        }
        self.base["closure_sha256"] = sha256_obj(self.base)
        self.ledger = build_shadow_human_interface_ledger(
            self.base,
            build_default_interface_evidence(),
            generated_at="2026-08-20T01:25:00+07:00",
        )

    def close(self, *, base=None, ledger=None):
        return build_unified_shadow_human_interface_closure(
            self.base if base is None else base,
            self.ledger if ledger is None else ledger,
            closed_at="2026-08-20T01:26:00+07:00",
        )

    def test_v10_preserves_hold_wait_and_binds_three_interfaces(self):
        closure = self.close()
        self.assertEqual(closure["schema"], "bitevo.unified_shadow_closure.v10")
        self.assertEqual(closure["typed_interface_node_count"], 3)
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertEqual(closure["planes"]["human_interface_plane"], "BOUND_3_OF_3_PRESENTATION_ONLY_NO_EFFECT")
        self.assertFalse(closure["interface_status"]["source_of_truth"])
        self.assertFalse(closure["interface_status"]["approval_authority"])
        self.assertFalse(closure["interface_status"]["external_message_authority"])
        self.assertFalse(closure["interface_status"]["effect_authority"])
        self.assertTrue(all(value is False for value in closure["effect_summary"].values()))

    def test_pass_shadow_is_preserved_exactly(self):
        base = copy.deepcopy(self.base)
        base["effective_gate"] = "PASS_SHADOW"
        base["effective_action"] = "REVIEW"
        base["closure_sha256"] = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
        ledger = build_shadow_human_interface_ledger(
            base,
            build_default_interface_evidence(),
            generated_at="2026-08-20T01:27:00+07:00",
        )
        closure = self.close(base=base, ledger=ledger)
        self.assertEqual(closure["effective_gate"], "PASS_SHADOW")
        self.assertEqual(closure["effective_action"], "REVIEW")

    def test_tampered_external_message_capability_is_rejected(self):
        ledger = copy.deepcopy(self.ledger)
        row = ledger["receipts"][1]
        row["may_send_external_message"] = True
        row["interface_receipt_sha256"] = sha256_obj({k: v for k, v in row.items() if k != "interface_receipt_sha256"})
        ledger["interface_ledger_sha256"] = sha256_obj({k: v for k, v in ledger.items() if k != "interface_ledger_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "human_interface_closure_effect_capability_detected"):
            self.close(ledger=ledger)

    def test_tampered_source_of_truth_is_rejected(self):
        ledger = copy.deepcopy(self.ledger)
        row = ledger["receipts"][2]
        row["source_of_truth"] = True
        row["interface_receipt_sha256"] = sha256_obj({k: v for k, v in row.items() if k != "interface_receipt_sha256"})
        ledger["interface_ledger_sha256"] = sha256_obj({k: v for k, v in ledger.items() if k != "interface_ledger_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "human_interface_closure_decision_or_truth_influence"):
            self.close(ledger=ledger)

    def test_closure_is_deterministic(self):
        self.assertEqual(self.close(), self.close())


if __name__ == "__main__":
    unittest.main()
