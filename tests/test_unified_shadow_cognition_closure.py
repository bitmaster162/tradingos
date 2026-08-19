import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_cognition_closure import build_unified_shadow_cognition_closure
from tools.unified_shadow_cognition_plane import (
    COGNITION_NODES,
    COGNITION_SPECS,
    build_default_cognition_evidence,
    build_shadow_cognition_proposal_ledger,
)


class UnifiedShadowCognitionClosureTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v8",
            "closed_at": "2026-08-20T01:18:00+07:00",
            "case_id": "trade-cognition-close-001",
            "transaction_sha256": "b" * 64,
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
        self.ledger = build_shadow_cognition_proposal_ledger(
            self.base,
            build_default_cognition_evidence(),
            generated_at="2026-08-20T01:19:00+07:00",
        )

    def close(self, *, base=None, ledger=None):
        return build_unified_shadow_cognition_closure(
            self.base if base is None else base,
            self.ledger if ledger is None else ledger,
            closed_at="2026-08-20T01:20:00+07:00",
        )

    def test_v9_binds_all_eleven_without_changing_hold_wait(self):
        closure = self.close()
        self.assertEqual(closure["schema"], "bitevo.unified_shadow_closure.v9")
        self.assertEqual(closure["typed_cognition_node_count"], 11)
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertEqual(closure["planes"]["cognition_proposal_plane"], "BOUND_11_OF_11_PROPOSAL_ONLY_NO_CASE_INFLUENCE")
        self.assertFalse(closure["cognition_status"]["case_influence_enabled"])
        self.assertFalse(closure["cognition_status"]["gate_change_allowed"])
        self.assertFalse(closure["cognition_status"]["current_truth_authority_granted"])
        self.assertFalse(closure["cognition_status"]["memory_write_authority_granted"])
        self.assertFalse(closure["cognition_status"]["execution_authority_granted"])
        self.assertTrue(all(value is False for value in closure["effect_summary"].values()))
        self.assertEqual(closure["safety"]["capital_permission"], "DENY")

    def test_pass_shadow_base_is_preserved_not_upgraded_or_downgraded(self):
        base = copy.deepcopy(self.base)
        base["effective_gate"] = "PASS_SHADOW"
        base["effective_action"] = "REVIEW"
        base["closure_sha256"] = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
        ledger = build_shadow_cognition_proposal_ledger(
            base,
            build_default_cognition_evidence(),
            generated_at="2026-08-20T01:21:00+07:00",
        )
        closure = self.close(base=base, ledger=ledger)
        self.assertEqual(closure["effective_gate"], "PASS_SHADOW")
        self.assertEqual(closure["effective_action"], "REVIEW")

    def test_proof_complete_node_still_has_no_case_influence(self):
        evidence = build_default_cognition_evidence()
        node = "entity:human_coevolution_layer"
        for field in COGNITION_SPECS[node]["proof_fields"]:
            evidence[node][field] = True
        ledger = build_shadow_cognition_proposal_ledger(
            self.base,
            evidence,
            generated_at="2026-08-20T01:22:00+07:00",
        )
        closure = self.close(ledger=ledger)
        self.assertIn(node, closure["cognition_status"]["proof_complete_nodes"])
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertFalse(closure["cognition_status"]["case_influence_enabled"])

    def test_tampered_case_influence_is_rejected(self):
        ledger = copy.deepcopy(self.ledger)
        row = ledger["receipts"][0]
        row["case_influence_enabled"] = True
        row["cognition_receipt_sha256"] = sha256_obj({k: v for k, v in row.items() if k != "cognition_receipt_sha256"})
        ledger["cognition_ledger_sha256"] = sha256_obj({k: v for k, v in ledger.items() if k != "cognition_ledger_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "cognition_closure_case_influence_detected"):
            self.close(ledger=ledger)

    def test_tampered_memory_authority_is_rejected(self):
        ledger = copy.deepcopy(self.ledger)
        row = next(item for item in ledger["receipts"] if item["node_id"] == "entity:lifeos")
        row["memory_authority"] = "WRITE"
        row["cognition_receipt_sha256"] = sha256_obj({k: v for k, v in row.items() if k != "cognition_receipt_sha256"})
        ledger["cognition_ledger_sha256"] = sha256_obj({k: v for k, v in ledger.items() if k != "cognition_ledger_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "cognition_closure_memory_authority_detected"):
            self.close(ledger=ledger)

    def test_tampered_effect_is_rejected(self):
        ledger = copy.deepcopy(self.ledger)
        row = next(item for item in ledger["receipts"] if item["node_id"] == "portfolio:openclaw")
        row["effects"]["tool_call"] = True
        row["cognition_receipt_sha256"] = sha256_obj({k: v for k, v in row.items() if k != "cognition_receipt_sha256"})
        ledger["cognition_ledger_sha256"] = sha256_obj({k: v for k, v in ledger.items() if k != "cognition_ledger_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "cognition_closure_receipt_effect_boundary_breached"):
            self.close(ledger=ledger)

    def test_receipt_identity_covers_all_nodes_in_order(self):
        self.assertEqual(tuple(row["node_id"] for row in self.ledger["receipts"]), COGNITION_NODES)
        self.assertEqual(len(COGNITION_NODES), 11)

    def test_closure_is_deterministic(self):
        self.assertEqual(self.close(), self.close())


if __name__ == "__main__":
    unittest.main()
