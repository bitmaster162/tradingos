import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_knowledge_closure import build_unified_shadow_knowledge_closure


class UnifiedShadowKnowledgeClosureTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v4",
            "closed_at": "2026-08-20T00:32:00+07:00",
            "case_id": "trade-knowledge-closure-001",
            "transaction_sha256": "a" * 64,
            "base_closure_sha256": "b" * 64,
            "research_plane_sha256": "c" * 64,
            "registered_node_count": 63,
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
                "research_simulation": "BOUND_NON_BLOCKING_NO_RUNTIME",
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
                "experiment_launch": False,
                "research_publication": False,
                "simulation_runtime": False,
            },
            "research_surface_status": {
                "maworld": "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND",
                "pandora": "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND",
                "sovereign_arena": "SOURCE_IDENTITY_BOUND_DEPLOY_RUNTIME_UNPROVEN",
            },
            "semantics": {"research_side_plane_does_not_vote": True},
            "safety": dict(SHADOW_SAFETY),
        }
        self.base["closure_sha256"] = sha256_obj(self.base)

        proposal = {
            "proposal_id": "memory:trade-knowledge-closure-001:p0-shadow",
            "memory_class": "EVIDENCE_STATE_CANDIDATE",
            "source_transaction_sha256": self.base["transaction_sha256"],
            "source_hanri_receipt_sha256": "d" * 64,
            "candidate_claim_ids": ("claim:p0-effective-gate", "claim:p0-archiveos-freshness"),
            "write_allowed": False,
            "auto_merge_allowed": False,
            "private_memory_write": False,
            "shared_memory_write": False,
            "project_canon_write": False,
            "current_truth_write": False,
        }
        proposal["proposal_sha256"] = sha256_obj(proposal)
        self.candidate = {
            "schema": "bitevo.shadow_knowledge_memory_candidate.v1",
            "generated_at": "2026-08-20T00:33:00+07:00",
            "case_id": self.base["case_id"],
            "source_transaction_sha256": self.base["transaction_sha256"],
            "source_hanri_receipt_sha256": "d" * 64,
            "knowledge_foundry": {
                "role": "SOURCE_TO_CLAIM_TO_CONTRADICTION_TO_DECISION_GRAPH",
                "source_identity_bound": False,
                "runtime_bound": False,
                "claim_admission_performed": False,
                "semantic_acceptance_authority": False,
                "claim_ceiling": "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND",
            },
            "durable_memory": {
                "role": "POLICY_GOVERNED_DURABLE_MEMORY_TRANSACTION_CANDIDATE",
                "source_identity_bound": False,
                "runtime_bound": False,
                "write_performed": False,
                "permission_source": False,
                "claim_ceiling": "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND",
            },
            "claim_candidates": (
                {
                    "claim_id": "claim:p0-effective-gate",
                    "statement": "P0 effective evidence-governor gate is HOLD",
                    "truth_class": "SOURCE_BOUND_DERIVATIVE",
                    "evidence_refs": ("transaction:" + self.base["transaction_sha256"],),
                    "admission_status": "UNADMITTED",
                    "can_be_current_truth": False,
                },
                {
                    "claim_id": "claim:p0-archiveos-freshness",
                    "statement": "ArchiveOS freshness state in the bound HANRI receipt is STALE",
                    "truth_class": "SOURCE_BOUND_DERIVATIVE",
                    "evidence_refs": ("hanri:" + "d" * 64,),
                    "admission_status": "UNADMITTED",
                    "can_be_current_truth": False,
                },
            ),
            "contradiction_candidates": (),
            "memory_proposal": proposal,
            "admission": {
                "status": "NOT_PERFORMED",
                "admitted_claim_count": 0,
                "rejected_claim_count": 0,
                "human_or_authority_review_required": True,
            },
            "decision_dependency": "NON_VOTING_EVIDENCE_DERIVATIVE",
            "can_change_decision": False,
            "effects": {
                "knowledge_write": False,
                "memory_write": False,
                "project_canon_write": False,
                "current_truth_apply": False,
                "runtime_invocation": False,
                "external_message": False,
                "signal": False,
                "order": False,
                "capital_effect": False,
            },
            "semantics": {"memory_proposal_is_not_memory_write": True},
            "safety": dict(SHADOW_SAFETY),
        }
        self.candidate["knowledge_memory_sha256"] = sha256_obj(self.candidate)

    def build(self, base=None, candidate=None):
        return build_unified_shadow_knowledge_closure(
            self.base if base is None else base,
            self.candidate if candidate is None else candidate,
            closed_at="2026-08-20T00:34:00+07:00",
        )

    def test_v5_closure_preserves_hold_and_no_effect(self):
        closure = self.build()
        self.assertEqual(closure["schema"], "bitevo.unified_shadow_closure.v5")
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertEqual(closure["planes"]["knowledge_candidate"], "BOUND_UNADMITTED_NO_WRITE")
        self.assertEqual(closure["planes"]["durable_memory_candidate"], "BOUND_PROPOSAL_ONLY_NO_WRITE")
        self.assertEqual(closure["knowledge_status"]["admitted_claims"], 0)
        self.assertFalse(closure["knowledge_status"]["memory_write"])
        self.assertTrue(all(value is False for value in closure["effect_summary"].values()))
        self.assertEqual(closure["safety"]["capital_permission"], "DENY")

    def test_candidate_cannot_change_decision(self):
        candidate = copy.deepcopy(self.candidate)
        candidate["can_change_decision"] = True
        candidate["knowledge_memory_sha256"] = sha256_obj({k: v for k, v in candidate.items() if k != "knowledge_memory_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "knowledge_closure_decision_authority_breached"):
            self.build(candidate=candidate)

    def test_claim_cannot_be_pre_admitted(self):
        candidate = copy.deepcopy(self.candidate)
        claims = [dict(row) for row in candidate["claim_candidates"]]
        claims[0]["admission_status"] = "ADMITTED"
        candidate["claim_candidates"] = tuple(claims)
        candidate["knowledge_memory_sha256"] = sha256_obj({k: v for k, v in candidate.items() if k != "knowledge_memory_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "knowledge_closure_claim_candidate_promoted"):
            self.build(candidate=candidate)

    def test_memory_write_cannot_cross_closure(self):
        candidate = copy.deepcopy(self.candidate)
        candidate["memory_proposal"]["write_allowed"] = True
        candidate["memory_proposal"]["proposal_sha256"] = sha256_obj(
            {k: v for k, v in candidate["memory_proposal"].items() if k != "proposal_sha256"}
        )
        candidate["knowledge_memory_sha256"] = sha256_obj({k: v for k, v in candidate.items() if k != "knowledge_memory_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "knowledge_closure_memory_proposal_write_breached:write_allowed"):
            self.build(candidate=candidate)

    def test_foundry_runtime_cannot_be_silently_bound(self):
        candidate = copy.deepcopy(self.candidate)
        candidate["knowledge_foundry"]["runtime_bound"] = True
        candidate["knowledge_memory_sha256"] = sha256_obj({k: v for k, v in candidate.items() if k != "knowledge_memory_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "knowledge_closure_foundry_source_runtime_overclaim"):
            self.build(candidate=candidate)

    def test_base_effect_is_rejected(self):
        base = copy.deepcopy(self.base)
        base["effect_summary"]["simulation_runtime"] = True
        base["closure_sha256"] = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "knowledge_closure_base_effect_boundary_breached"):
            self.build(base=base)

    def test_closure_is_deterministic(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["closure_sha256"], second["closure_sha256"])


if __name__ == "__main__":
    unittest.main()
