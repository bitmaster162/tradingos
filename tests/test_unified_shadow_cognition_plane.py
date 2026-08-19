import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_cognition_plane import (
    COGNITION_NODES,
    COGNITION_SPECS,
    build_default_cognition_evidence,
    build_shadow_cognition_proposal_ledger,
)


class UnifiedShadowCognitionPlaneTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v8",
            "closed_at": "2026-08-20T01:16:00+07:00",
            "case_id": "trade-cognition-001",
            "transaction_sha256": "a" * 64,
            "registered_node_count": 63,
            "effective_gate": "HOLD",
            "effective_action": "WAIT",
            "status": "P0_SHADOW_CLOSED_NO_EFFECT",
            "planes": {"trading_advisory_contracts": "BOUND_9_OF_9_RELEVANCE_GATED_NARROW_ONLY"},
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
        return build_shadow_cognition_proposal_ledger(
            self.base,
            build_default_cognition_evidence() if evidence is None else evidence,
            generated_at="2026-08-20T01:17:00+07:00",
        )

    def fully_prove(self, evidence, node):
        for field in COGNITION_SPECS[node]["proof_fields"]:
            evidence[node][field] = True

    def test_all_eleven_cognition_nodes_are_typed_and_proposal_only(self):
        ledger = self.build()
        self.assertEqual(ledger["schema"], "bitevo.shadow_cognition_proposal_ledger.v1")
        self.assertEqual(ledger["cognition_node_count"], 11)
        self.assertEqual(tuple(row["node_id"] for row in ledger["receipts"]), COGNITION_NODES)
        self.assertEqual(ledger["proof_complete_nodes"], ())
        self.assertTrue(ledger["all_cognition_nodes_typed"])
        for row in ledger["receipts"]:
            self.assertTrue(row["typed_contract_bound"])
            self.assertTrue(row["proposal_only"])
            self.assertFalse(row["case_influence_enabled"])
            self.assertFalse(row["decision_vote"])
            self.assertEqual(row["gate_effect"], "NONE")
            self.assertFalse(row["may_widen_gate"])
            self.assertEqual(row["current_truth_authority"], "NONE")
            self.assertEqual(row["memory_authority"], "NONE")
            self.assertEqual(row["execution_authority"], "NONE")
            self.assertFalse(row["external_runtime_invoked"])
            self.assertTrue(all(value is False for value in row["effects"].values()))
        self.assertTrue(all(value is False for value in ledger["effects"].values()))
        self.assertEqual(ledger["safety"]["capital_permission"], "DENY")

    def test_exact_eleven_node_coverage_is_required(self):
        evidence = build_default_cognition_evidence()
        del evidence["entity:mind"]
        with self.assertRaisesRegex(ShadowIntegrationError, "cognition_coverage_mismatch"):
            self.build(evidence)

    def test_runtime_claim_requires_source_identity(self):
        evidence = build_default_cognition_evidence()
        evidence["portfolio:openclaw"]["runtime_verified"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "cognition_runtime_without_source_identity"):
            self.build(evidence)

    def test_tool_or_model_call_is_rejected(self):
        evidence = build_default_cognition_evidence()
        evidence["portfolio:bitevo-runtime"]["tool_call_performed"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "cognition_effect_boundary_breached"):
            self.build(evidence)
        evidence = build_default_cognition_evidence()
        evidence["portfolio:arbiter-content-engine"]["model_call_performed"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "cognition_effect_boundary_breached"):
            self.build(evidence)

    def test_memory_or_truth_write_is_rejected(self):
        evidence = build_default_cognition_evidence()
        evidence["entity:lifeos"]["memory_write_performed"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "cognition_effect_boundary_breached"):
            self.build(evidence)
        evidence = build_default_cognition_evidence()
        evidence["entity:mind"]["current_truth_write_performed"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "cognition_effect_boundary_breached"):
            self.build(evidence)

    def test_full_proof_does_not_enable_case_influence(self):
        evidence = build_default_cognition_evidence()
        node = "portfolio:bitevo-runtime"
        self.fully_prove(evidence, node)
        evidence[node]["source_identity_verified"] = True
        ledger = self.build(evidence)
        receipt = ledger["receipts"][0]
        self.assertTrue(receipt["proof_complete"])
        self.assertIn(node, ledger["proof_complete_nodes"])
        self.assertFalse(receipt["case_influence_enabled"])
        self.assertEqual(receipt["gate_effect"], "NONE")
        self.assertEqual(receipt["execution_authority"], "NONE")

    def test_sovereign_agent_core_is_pattern_library_not_second_authority(self):
        ledger = self.build()
        receipt = next(row for row in ledger["receipts"] if row["node_id"] == "portfolio:sovereign-agent-core")
        self.assertEqual(receipt["role"], "AGENT_TRUST_PATTERN_LIBRARY_MERGE_CONCEPTS_ONLY")
        self.assertIn("second_authority_root", receipt["forbidden_ownership"])

    def test_pfi_family_cannot_own_truth_bytes_or_effects(self):
        ledger = self.build()
        receipt = next(row for row in ledger["receipts"] if row["node_id"] == "entity:pfi_brain_fabric")
        self.assertIn("exact_source_bytes", receipt["forbidden_ownership"])
        self.assertIn("accepted_truth", receipt["forbidden_ownership"])
        self.assertIn("effect_authority", receipt["forbidden_ownership"])

    def test_human_coevolution_is_update_proposal_not_self_development(self):
        ledger = self.build()
        receipt = next(row for row in ledger["receipts"] if row["node_id"] == "entity:human_coevolution_layer")
        self.assertEqual(receipt["role"], "HUMAN_AGENT_ENVIRONMENT_UPDATE_PROPOSAL_PROTOCOL")
        self.assertIn("self_approval", receipt["forbidden_ownership"])
        self.assertIn("autonomous_self_development", receipt["forbidden_ownership"])
        self.assertFalse(receipt["effects"]["human_approval"])
        self.assertFalse(receipt["effects"]["canary"])

    def test_hash_is_deterministic(self):
        self.assertEqual(self.build(), self.build())
        self.assertEqual(self.build()["cognition_ledger_sha256"], self.build()["cognition_ledger_sha256"])


if __name__ == "__main__":
    unittest.main()
