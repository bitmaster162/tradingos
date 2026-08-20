import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_research_closure import build_unified_shadow_research_closure


class UnifiedShadowResearchClosureTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v3",
            "closed_at": "2026-08-20T00:30:00+07:00",
            "case_id": "trade-research-closure-001",
            "transaction_sha256": "a" * 64,
            "continuity_receipt_sha256": "b" * 64,
            "return_intake_sha256": "c" * 64,
            "control_projection_sha256": "d" * 64,
            "hanri_receipt_sha256": "e" * 64,
            "registered_node_count": 63,
            "upstream_control_gate": "HOLD",
            "upstream_control_action": "WAIT",
            "effective_gate": "HOLD",
            "effective_action": "WAIT",
            "status": "P0_SHADOW_CLOSED_NO_EFFECT",
            "planes": {
                "composition": "BOUND",
                "continuity": "BOUND_READ_ONLY",
                "return_transport": "BOUND_READ_ONLY_PHYSICAL",
                "authority_projection": "BOUND_NON_AUTHORITY",
                "hanri_evidence_governor": "BOUND_NON_AUTHORITY_FAIL_CLOSED",
                "archiveos": "BOUND_EVIDENCE_STATUS_ONLY",
                "knowledge_memory": "BOUND_NO_ADMISSION_NO_WRITE",
                "executor": "DISABLED",
            },
            "effect_summary": {
                "merge": False,
                "deploy": False,
                "runtime_activation": False,
                "current_truth_apply": False,
                "knowledge_or_memory_write": False,
                "memory_or_checkpoint_write": False,
                "return_or_archive_write": False,
                "external_model_call": False,
                "exchange_call": False,
                "signal": False,
                "order": False,
                "capital_effect": False,
            },
            "semantics": {"closure_is_evidence_not_authority": True},
            "safety": dict(SHADOW_SAFETY),
        }
        self.base["closure_sha256"] = sha256_obj(self.base)
        self.research = {
            "schema": "bitevo.shadow_research_simulation_receipt.v1",
            "generated_at": "2026-08-20T00:31:00+07:00",
            "source_transaction_sha256": self.base["transaction_sha256"],
            "case_id": self.base["case_id"],
            "decision_dependency": "NON_BLOCKING_SIDE_PLANE",
            "trading_voter": False,
            "can_change_decision": False,
            "surfaces": {
                "maworld": {
                    "role": "ISOLATED_REPRODUCIBLE_EXPERIMENT_CHAMBER_CANDIDATE",
                    "source_status": "SOURCE_UNBOUND",
                    "runtime_status": "RUNTIME_UNBOUND",
                    "source_identity_bound": False,
                    "runtime_invoked": False,
                    "claim_ceiling": "ROLE_AND_RESEARCH_HYPOTHESIS_ONLY",
                },
                "pandora": {
                    "role": "VISUAL_PROGRAMMABLE_RUNTIME_AND_SIMULATION_CANDIDATE",
                    "source_status": "SOURCE_UNBOUND",
                    "runtime_status": "RUNTIME_UNBOUND",
                    "source_identity_bound": False,
                    "runtime_invoked": False,
                    "claim_ceiling": "ROLE_AND_RESEARCH_HYPOTHESIS_ONLY",
                },
                "sovereign_arena": {
                    "repo": "bitmaster162/sovereign-arena-site",
                    "branch": "main",
                    "head_sha": "f070fe0587a4222b993b7e8fc9b8f2726ca414d9",
                    "source_identity_bound": True,
                    "deployment_proven": False,
                    "runtime_proven": False,
                    "role": "RESEARCH_EVIDENCE_PRODUCT_SURFACE",
                    "runtime_invoked": False,
                    "trading_execution_surface": False,
                    "claim_ceiling": "SOURCE_IDENTITY_ONLY",
                },
            },
            "research_contract": {
                "preserve_failed_stopped_degraded_experiments": "DESIGN_REQUIREMENT_ONLY",
                "provenance_required": True,
                "replay_status_required": True,
                "all_trial_denominator_required": True,
                "no_signal_service": True,
                "no_live_trading": True,
                "publication_is_not_authority": True,
            },
            "effects": {
                "runtime_invocation": False,
                "experiment_launch": False,
                "artifact_publication": False,
                "deployment": False,
                "external_message": False,
                "current_truth_apply": False,
                "signal": False,
                "order": False,
                "capital_effect": False,
            },
            "semantics": {"source_identity_is_not_deployment": True},
            "safety": dict(SHADOW_SAFETY),
        }
        self.research["research_plane_sha256"] = sha256_obj(self.research)

    def build(self, base=None, research=None):
        return build_unified_shadow_research_closure(
            self.base if base is None else base,
            self.research if research is None else research,
            closed_at="2026-08-20T00:32:00+07:00",
        )

    def test_v4_closure_preserves_hold_and_adds_non_blocking_research_plane(self):
        closure = self.build()
        self.assertEqual(closure["schema"], "bitevo.unified_shadow_closure.v4")
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertEqual(closure["planes"]["research_simulation"], "BOUND_NON_BLOCKING_NO_RUNTIME")
        self.assertEqual(closure["research_surface_status"]["maworld"], "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND")
        self.assertEqual(closure["research_surface_status"]["pandora"], "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND")
        self.assertEqual(closure["research_surface_status"]["sovereign_arena"], "SOURCE_IDENTITY_BOUND_DEPLOY_RUNTIME_UNPROVEN")
        self.assertTrue(all(value is False for value in closure["effect_summary"].values()))
        self.assertEqual(closure["safety"]["capital_permission"], "DENY")

    def test_research_plane_cannot_vote(self):
        receipt = copy.deepcopy(self.research)
        receipt["trading_voter"] = True
        receipt["research_plane_sha256"] = sha256_obj({k: v for k, v in receipt.items() if k != "research_plane_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "research_closure_voting_authority_breached"):
            self.build(research=receipt)

    def test_maworld_unbound_state_cannot_be_laundered(self):
        receipt = copy.deepcopy(self.research)
        receipt["surfaces"]["maworld"]["source_identity_bound"] = True
        receipt["research_plane_sha256"] = sha256_obj({k: v for k, v in receipt.items() if k != "research_plane_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "research_closure_maworld_overclaim"):
            self.build(research=receipt)

    def test_arena_source_identity_cannot_be_called_runtime(self):
        receipt = copy.deepcopy(self.research)
        receipt["surfaces"]["sovereign_arena"]["runtime_proven"] = True
        receipt["research_plane_sha256"] = sha256_obj({k: v for k, v in receipt.items() if k != "research_plane_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "research_closure_arena_runtime_overclaim"):
            self.build(research=receipt)

    def test_research_effect_is_rejected(self):
        receipt = copy.deepcopy(self.research)
        receipt["effects"]["experiment_launch"] = True
        receipt["research_plane_sha256"] = sha256_obj({k: v for k, v in receipt.items() if k != "research_plane_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "research_closure_research_effect_boundary_breached"):
            self.build(research=receipt)

    def test_base_effect_is_rejected(self):
        base = copy.deepcopy(self.base)
        base["effect_summary"]["deploy"] = True
        base["closure_sha256"] = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "research_closure_base_effect_boundary_breached"):
            self.build(base=base)

    def test_research_cannot_change_hold(self):
        closure = self.build()
        self.assertEqual(closure["effective_gate"], self.base["effective_gate"])
        self.assertEqual(closure["effective_action"], self.base["effective_action"])

    def test_closure_is_deterministic(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["closure_sha256"], second["closure_sha256"])


if __name__ == "__main__":
    unittest.main()
