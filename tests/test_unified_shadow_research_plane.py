import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_research_plane import build_shadow_research_simulation_receipt


class UnifiedShadowResearchPlaneTests(unittest.TestCase):
    def setUp(self):
        self.transaction = {
            "schema": "bitevo.unified_shadow_transaction.v2",
            "frozen_at": "2026-08-20T00:25:00+07:00",
            "case_id": "trade-research-001",
            "trade_case_sha256": "a" * 64,
            "decision_packet_sha256": "b" * 64,
            "federation_sha256": "c" * 64,
            "route_sha256": "d" * 64,
            "control_plane_sha256": "e" * 64,
            "registered_node_count": 63,
            "system_recommendation": "WAIT",
            "control_gate": "HOLD",
            "control_plane_action": "WAIT",
            "hanri_freshness": "STALE",
            "hanri_attention_required": True,
            "twin_prediction_status": "UNIQUE",
            "divergence": False,
            "effect_boundary": {
                "executor_enabled": False,
                "current_truth_apply": False,
                "continuity_write": False,
                "runtime_registration": False,
                "external_model_call": False,
                "exchange_call": False,
                "signal": False,
                "order": False,
                "credential_mutation": False,
                "merge": False,
                "deploy": False,
            },
            "semantics": {"one_transaction_one_case": True},
            "safety": dict(SHADOW_SAFETY),
        }
        self.transaction["transaction_sha256"] = sha256_obj(self.transaction)
        self.arena_ref = {
            "repo": "bitmaster162/sovereign-arena-site",
            "branch": "main",
            "head_sha": "f070fe0587a4222b993b7e8fc9b8f2726ca414d9",
        }

    def build(self, transaction=None, **kwargs):
        return build_shadow_research_simulation_receipt(
            self.transaction if transaction is None else transaction,
            sovereign_arena_ref=kwargs.pop("sovereign_arena_ref", self.arena_ref),
            maworld_source_bound=kwargs.pop("maworld_source_bound", False),
            maworld_runtime_bound=kwargs.pop("maworld_runtime_bound", False),
            pandora_source_bound=kwargs.pop("pandora_source_bound", False),
            pandora_runtime_bound=kwargs.pop("pandora_runtime_bound", False),
            generated_at=kwargs.pop("generated_at", "2026-08-20T00:31:00+07:00"),
        )

    def test_valid_side_plane_is_non_blocking_non_voting_and_no_effect(self):
        receipt = self.build()
        self.assertEqual(receipt["schema"], "bitevo.shadow_research_simulation_receipt.v1")
        self.assertEqual(receipt["decision_dependency"], "NON_BLOCKING_SIDE_PLANE")
        self.assertFalse(receipt["trading_voter"])
        self.assertFalse(receipt["can_change_decision"])
        self.assertFalse(receipt["surfaces"]["maworld"]["source_identity_bound"])
        self.assertFalse(receipt["surfaces"]["maworld"]["runtime_invoked"])
        self.assertFalse(receipt["surfaces"]["pandora"]["source_identity_bound"])
        self.assertFalse(receipt["surfaces"]["pandora"]["runtime_invoked"])
        self.assertTrue(receipt["surfaces"]["sovereign_arena"]["source_identity_bound"])
        self.assertFalse(receipt["surfaces"]["sovereign_arena"]["deployment_proven"])
        self.assertFalse(receipt["surfaces"]["sovereign_arena"]["runtime_proven"])
        self.assertFalse(receipt["surfaces"]["sovereign_arena"]["runtime_invoked"])
        self.assertTrue(all(value is False for value in receipt["effects"].values()))
        self.assertEqual(receipt["safety"]["execution_authority"], "NONE")
        self.assertEqual(receipt["safety"]["capital_permission"], "DENY")

    def test_maworld_source_or_runtime_cannot_be_silently_promoted(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "maworld_current_source_or_runtime_not_bound"):
            self.build(maworld_source_bound=True)
        with self.assertRaisesRegex(ShadowIntegrationError, "maworld_current_source_or_runtime_not_bound"):
            self.build(maworld_runtime_bound=True)

    def test_pandora_source_or_runtime_cannot_be_silently_promoted(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "pandora_current_source_or_runtime_not_bound"):
            self.build(pandora_source_bound=True)
        with self.assertRaisesRegex(ShadowIntegrationError, "pandora_current_source_or_runtime_not_bound"):
            self.build(pandora_runtime_bound=True)

    def test_wrong_arena_head_is_rejected(self):
        ref = dict(self.arena_ref)
        ref["head_sha"] = "0" * 40
        with self.assertRaisesRegex(ShadowIntegrationError, "research_arena_head_mismatch"):
            self.build(sovereign_arena_ref=ref)

    def test_transaction_tamper_is_rejected(self):
        tx = copy.deepcopy(self.transaction)
        tx["system_recommendation"] = "LONG"
        with self.assertRaisesRegex(ShadowIntegrationError, "research_transaction_hash_mismatch"):
            self.build(transaction=tx)

    def test_effectful_transaction_is_rejected_even_if_rehashed(self):
        tx = copy.deepcopy(self.transaction)
        tx["effect_boundary"]["external_model_call"] = True
        tx["transaction_sha256"] = sha256_obj({k: v for k, v in tx.items() if k != "transaction_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "research_transaction_effect_boundary_breached"):
            self.build(transaction=tx)

    def test_receipt_is_deterministic(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["research_plane_sha256"], second["research_plane_sha256"])


if __name__ == "__main__":
    unittest.main()
