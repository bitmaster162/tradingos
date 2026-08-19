import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_human_interface import (
    INTERFACE_NODES,
    INTERFACE_SPECS,
    build_default_interface_evidence,
    build_shadow_human_interface_ledger,
)


class UnifiedShadowHumanInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v9",
            "closed_at": "2026-08-20T01:22:00+07:00",
            "case_id": "trade-interface-001",
            "transaction_sha256": "c" * 64,
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

    def build(self, evidence=None):
        return build_shadow_human_interface_ledger(
            self.base,
            build_default_interface_evidence() if evidence is None else evidence,
            generated_at="2026-08-20T01:23:00+07:00",
        )

    def test_all_three_interfaces_are_presentation_only(self):
        ledger = self.build()
        self.assertEqual(ledger["schema"], "bitevo.shadow_human_interface_ledger.v1")
        self.assertEqual(ledger["interface_node_count"], 3)
        self.assertEqual(tuple(row["node_id"] for row in ledger["receipts"]), INTERFACE_NODES)
        for row in ledger["receipts"]:
            self.assertTrue(row["presentation_only"])
            self.assertFalse(row["source_of_truth"])
            self.assertFalse(row["decision_vote"])
            self.assertEqual(row["gate_effect"], "NONE")
            self.assertFalse(row["may_grant_approval"])
            self.assertFalse(row["may_send_external_message"])
            self.assertFalse(row["may_execute_action"])
            self.assertEqual(row["execution_authority"], "NONE")
            self.assertTrue(all(value is False for value in row["effects"].values()))

    def test_work_cockpit_cannot_send_message(self):
        evidence = build_default_interface_evidence()
        evidence["portfolio:work-cockpit"]["external_message_sent"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "human_interface_effect_boundary_breached"):
            self.build(evidence)

    def test_hub_cannot_perform_runtime_action(self):
        evidence = build_default_interface_evidence()
        evidence["entity:universe_hub"]["runtime_action_performed"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "human_interface_effect_boundary_breached"):
            self.build(evidence)

    def test_dashboard_cannot_write_current_truth(self):
        evidence = build_default_interface_evidence()
        evidence["portfolio:unified-dashboard"]["current_truth_written"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "human_interface_effect_boundary_breached"):
            self.build(evidence)

    def test_proof_complete_does_not_add_authority(self):
        evidence = build_default_interface_evidence()
        node = "entity:universe_hub"
        for field in INTERFACE_SPECS[node]["proof_fields"]:
            evidence[node][field] = True
        ledger = self.build(evidence)
        receipt = ledger["receipts"][2]
        self.assertTrue(receipt["proof_complete"])
        self.assertFalse(receipt["source_of_truth"])
        self.assertFalse(receipt["may_execute_action"])
        self.assertEqual(receipt["execution_authority"], "NONE")

    def test_exact_three_node_coverage_required(self):
        evidence = build_default_interface_evidence()
        del evidence["portfolio:work-cockpit"]
        with self.assertRaisesRegex(ShadowIntegrationError, "human_interface_coverage_mismatch"):
            self.build(evidence)

    def test_hash_is_deterministic(self):
        self.assertEqual(self.build(), self.build())


if __name__ == "__main__":
    unittest.main()
