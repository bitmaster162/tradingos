import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_knowledge_memory import build_shadow_knowledge_memory_candidate


class UnifiedShadowKnowledgeMemoryTests(unittest.TestCase):
    def setUp(self):
        self.transaction = {
            "schema": "bitevo.unified_shadow_transaction.v2",
            "frozen_at": "2026-08-20T00:25:00+07:00",
            "case_id": "trade-knowledge-001",
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
        self.hanri = {
            "schema": "hanri.shadow-evidence-governor.receipt/v1",
            "generated_at": "2026-08-20T00:26:00+07:00",
            "source_transaction_sha256": self.transaction["transaction_sha256"],
            "case_id": self.transaction["case_id"],
            "human_sovereign": True,
            "authority_reference": {"generation": "R64"},
            "hanri_source": {
                "repo": "bitmaster162/control-center",
                "branch": "hanri/r37-product-pilot-accepted",
                "head_sha": "ef5c504179de8ae8c16bd70c168b14b79bd2f466",
                "role": "BOUNDED_RUNTIME_ATTENTION_GOVERNOR_PROJECTION",
                "authority_root": False,
                "can_promote_self": False,
            },
            "archiveos": {
                "status": "BLOCKED_REVERIFY",
                "freshness": "STALE",
                "current_claim_allowed": False,
                "promotion_eligible": False,
                "proof_gap": ["fresh full archive-integrity receipt is missing"],
                "role": "NON_AUTHORITATIVE_EVIDENCE_VAULT",
                "canonical_root": "C:\\PROJECTS\\archiveos_api",
                "drive_role": "MIRROR_EVIDENCE_ONLY",
            },
            "archive_tooling": {
                "role": "ARTIFACT_COMPILER_NOT_ARCHIVE_ENGINE",
                "authoritative_archive_engine": False,
                "semantic_acceptance_authority": False,
            },
            "knowledge_memory": {
                "claim_admission": "NOT_PERFORMED",
                "durable_memory_write": False,
                "project_canon_write": False,
                "current_truth_write": False,
                "memory_is_permission": False,
            },
            "governor": {
                "gate": "HOLD",
                "action": "WAIT",
                "hold_reasons": ["UPSTREAM_CONTROL_GATE_HOLD", "ARCHIVEOS_BLOCKED_REVERIFY"],
                "attention_required": True,
                "promotion_eligible": False,
                "auto_promotion": False,
            },
            "source_precedence": ["HUMAN_SOVEREIGN", "R64_CONTROL_CENTER_AUTHORITY"],
            "effects": {
                "github_write": False,
                "drive_write": False,
                "archiveos_write": False,
                "knowledge_write": False,
                "memory_write": False,
                "current_truth_apply": False,
                "runtime_write": False,
                "scheduler_write": False,
                "external_message": False,
                "signal": False,
                "order": False,
                "capital_effect": False,
            },
            "semantics": {"hanri_is_not_second_authority_root": True},
            "safety": dict(SHADOW_SAFETY),
        }
        self.hanri["hanri_receipt_sha256"] = sha256_obj(self.hanri)

    def build(self, transaction=None, hanri=None):
        return build_shadow_knowledge_memory_candidate(
            self.transaction if transaction is None else transaction,
            self.hanri if hanri is None else hanri,
            generated_at="2026-08-20T00:33:00+07:00",
        )

    def test_candidate_is_unadmitted_non_voting_and_no_write(self):
        receipt = self.build()
        self.assertEqual(receipt["schema"], "bitevo.shadow_knowledge_memory_candidate.v1")
        self.assertEqual(receipt["admission"]["status"], "NOT_PERFORMED")
        self.assertEqual(receipt["admission"]["admitted_claim_count"], 0)
        self.assertFalse(receipt["can_change_decision"])
        self.assertEqual(receipt["decision_dependency"], "NON_VOTING_EVIDENCE_DERIVATIVE")
        self.assertFalse(receipt["knowledge_foundry"]["source_identity_bound"])
        self.assertFalse(receipt["knowledge_foundry"]["runtime_bound"])
        self.assertFalse(receipt["durable_memory"]["source_identity_bound"])
        self.assertFalse(receipt["durable_memory"]["runtime_bound"])
        self.assertFalse(receipt["memory_proposal"]["write_allowed"])
        self.assertFalse(receipt["memory_proposal"]["project_canon_write"])
        self.assertFalse(receipt["memory_proposal"]["current_truth_write"])
        self.assertTrue(all(row["admission_status"] == "UNADMITTED" for row in receipt["claim_candidates"]))
        self.assertTrue(all(value is False for value in receipt["effects"].values()))
        self.assertEqual(receipt["safety"]["capital_permission"], "DENY")

    def test_archiveos_block_creates_unadmitted_proof_gap_claim(self):
        receipt = self.build()
        ids = {row["claim_id"] for row in receipt["claim_candidates"]}
        self.assertIn("claim:p0-archiveos-proof-gap", ids)
        row = next(row for row in receipt["claim_candidates"] if row["claim_id"] == "claim:p0-archiveos-proof-gap")
        self.assertEqual(row["truth_class"], "SOURCE_BOUND_DERIVATIVE")
        self.assertEqual(row["admission_status"], "UNADMITTED")
        self.assertFalse(row["can_be_current_truth"])

    def test_upstream_semantic_admission_overclaim_is_rejected(self):
        hanri = copy.deepcopy(self.hanri)
        hanri["knowledge_memory"]["claim_admission"] = "PASS"
        hanri["hanri_receipt_sha256"] = sha256_obj({k: v for k, v in hanri.items() if k != "hanri_receipt_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "knowledge_upstream_admission_already_claimed"):
            self.build(hanri=hanri)

    def test_upstream_memory_write_is_rejected(self):
        hanri = copy.deepcopy(self.hanri)
        hanri["knowledge_memory"]["durable_memory_write"] = True
        hanri["hanri_receipt_sha256"] = sha256_obj({k: v for k, v in hanri.items() if k != "hanri_receipt_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "knowledge_upstream_write_breached:durable_memory_write"):
            self.build(hanri=hanri)

    def test_transaction_tamper_is_rejected(self):
        tx = copy.deepcopy(self.transaction)
        tx["system_recommendation"] = "LONG"
        with self.assertRaisesRegex(ShadowIntegrationError, "knowledge_transaction_hash_mismatch"):
            self.build(transaction=tx)

    def test_hanri_tamper_is_rejected(self):
        hanri = copy.deepcopy(self.hanri)
        hanri["archiveos"]["freshness"] = "CURRENT"
        with self.assertRaisesRegex(ShadowIntegrationError, "knowledge_hanri_hash_mismatch"):
            self.build(hanri=hanri)

    def test_memory_proposal_hash_is_bound(self):
        receipt = self.build()
        proposal = receipt["memory_proposal"]
        expected = sha256_obj({k: v for k, v in proposal.items() if k != "proposal_sha256"})
        self.assertEqual(proposal["proposal_sha256"], expected)

    def test_receipt_is_deterministic(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["knowledge_memory_sha256"], second["knowledge_memory_sha256"])


if __name__ == "__main__":
    unittest.main()
