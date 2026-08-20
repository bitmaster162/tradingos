import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_product_service import (
    PRODUCT_NODES,
    PRODUCT_SPECS,
    build_default_product_service_evidence,
    build_shadow_product_service_ledger,
    build_unified_shadow_product_service_closure,
)


class UnifiedShadowProductServiceTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v10",
            "closed_at": "2026-08-20T01:30:00+07:00",
            "case_id": "product-plane-001",
            "transaction_sha256": "a" * 64,
            "registered_node_count": 63,
            "effective_gate": "HOLD",
            "effective_action": "WAIT",
            "status": "P0_SHADOW_CLOSED_NO_EFFECT",
            "planes": {"human_interface_plane": "BOUND_3_OF_3_PRESENTATION_ONLY_NO_EFFECT"},
            "effect_summary": {"merge": False, "deploy": False, "signal": False, "order": False, "capital_effect": False},
            "safety": dict(SHADOW_SAFETY),
        }
        self.base["closure_sha256"] = sha256_obj(self.base)

    def build_ledger(self, evidence=None):
        return build_shadow_product_service_ledger(
            self.base,
            build_default_product_service_evidence() if evidence is None else evidence,
            generated_at="2026-08-20T01:31:00+07:00",
        )

    def test_all_eight_nodes_are_typed_but_non_influential(self):
        ledger = self.build_ledger()
        self.assertEqual(ledger["product_node_count"], 8)
        self.assertEqual(tuple(row["node_id"] for row in ledger["receipts"]), PRODUCT_NODES)
        self.assertEqual(ledger["proof_complete_nodes"], ())
        for row in ledger["receipts"]:
            self.assertTrue(row["typed_contract_bound"])
            self.assertFalse(row["case_influence_enabled"])
            self.assertFalse(row["decision_vote"])
            self.assertEqual(row["gate_effect"], "NONE")
            self.assertEqual(row["execution_authority"], "NONE")
            self.assertTrue(all(value is False for value in row["effects"].values()))

    def test_complete_commercial_proof_still_cannot_change_gate(self):
        evidence = build_default_product_service_evidence()
        node = "portfolio:operator-decision-sprint"
        for field in PRODUCT_SPECS[node]["proof_fields"]:
            evidence[node][field] = True
        ledger = self.build_ledger(evidence)
        closure = build_unified_shadow_product_service_closure(
            self.base, ledger, closed_at="2026-08-20T01:32:00+07:00"
        )
        self.assertIn(node, ledger["proof_complete_nodes"])
        self.assertEqual(closure["schema"], "bitevo.unified_shadow_closure.v11")
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertFalse(closure["product_status"]["case_influence_enabled"])
        self.assertTrue(all(value is False for value in closure["effect_summary"].values()))

    def test_external_message_or_payment_mutation_is_rejected(self):
        evidence = build_default_product_service_evidence()
        evidence["portfolio:ai-client-hunter"]["external_message_sent"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "product_service_effect_boundary_breached"):
            self.build_ledger(evidence)
        evidence = build_default_product_service_evidence()
        evidence["portfolio:inner-circle"]["payment_mutated"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "product_service_effect_boundary_breached"):
            self.build_ledger(evidence)

    def test_exact_coverage_and_hash_are_enforced(self):
        evidence = build_default_product_service_evidence()
        del evidence["entity:physical_ai_cosmos"]
        with self.assertRaisesRegex(ShadowIntegrationError, "product_service_coverage_mismatch"):
            self.build_ledger(evidence)
        first = self.build_ledger()
        self.assertEqual(first, self.build_ledger())


if __name__ == "__main__":
    unittest.main()
