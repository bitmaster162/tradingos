import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_parked_nontrading import (
    PARKED_NODES,
    PARKED_SPECS,
    build_default_parked_nontrading_evidence,
    build_shadow_parked_nontrading_ledger,
    build_unified_shadow_parked_nontrading_closure,
)


class UnifiedShadowParkedNontradingTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v11",
            "closed_at": "2026-08-20T01:33:00+07:00",
            "case_id": "parked-plane-001",
            "transaction_sha256": "b" * 64,
            "registered_node_count": 63,
            "effective_gate": "HOLD",
            "effective_action": "WAIT",
            "status": "P0_SHADOW_CLOSED_NO_EFFECT",
            "planes": {"product_service_plane": "BOUND_8_OF_8_ACCOUNTED_NO_DECISION_OR_EFFECT_AUTHORITY"},
            "effect_summary": {"merge": False, "deploy": False, "signal": False, "order": False, "capital_effect": False},
            "safety": dict(SHADOW_SAFETY),
        }
        self.base["closure_sha256"] = sha256_obj(self.base)

    def build_ledger(self, evidence=None):
        return build_shadow_parked_nontrading_ledger(
            self.base,
            build_default_parked_nontrading_evidence() if evidence is None else evidence,
            generated_at="2026-08-20T01:34:00+07:00",
        )

    def test_all_five_nodes_are_typed_without_revival_authority(self):
        ledger = self.build_ledger()
        self.assertEqual(ledger["parked_node_count"], 5)
        self.assertEqual(tuple(row["node_id"] for row in ledger["receipts"]), PARKED_NODES)
        for row in ledger["receipts"]:
            self.assertFalse(row["revival_authority"])
            self.assertFalse(row["scope_mixing_allowed"])
            self.assertFalse(row["case_influence_enabled"])
            self.assertEqual(row["execution_authority"], "NONE")
            self.assertTrue(all(value is False for value in row["effects"].values()))

    def test_proof_completion_does_not_reactivate_project(self):
        evidence = build_default_parked_nontrading_evidence()
        node = "portfolio:parasite-hunter-game"
        for field in PARKED_SPECS[node]["proof_fields"]:
            evidence[node][field] = True
        ledger = self.build_ledger(evidence)
        closure = build_unified_shadow_parked_nontrading_closure(
            self.base, ledger, closed_at="2026-08-20T01:35:00+07:00"
        )
        self.assertIn(node, ledger["proof_complete_nodes"])
        self.assertEqual(closure["schema"], "bitevo.unified_shadow_closure.v12")
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertFalse(closure["parked_status"]["revival_authority"])

    def test_wallet_order_token_and_runtime_effects_are_rejected(self):
        for node, field in (
            ("portfolio:parasite-killer", "wallet_accessed"),
            ("portfolio:parasite-killer", "order_emitted"),
            ("portfolio:amora-token", "token_launched"),
            ("portfolio:amora", "runtime_activated"),
        ):
            evidence = build_default_parked_nontrading_evidence()
            evidence[node][field] = True
            with self.assertRaisesRegex(ShadowIntegrationError, "parked_effect_boundary_breached"):
                self.build_ledger(evidence)

    def test_exact_coverage_is_required(self):
        evidence = build_default_parked_nontrading_evidence()
        del evidence["portfolio:rtf-starcoin"]
        with self.assertRaisesRegex(ShadowIntegrationError, "parked_coverage_mismatch"):
            self.build_ledger(evidence)


if __name__ == "__main__":
    unittest.main()
