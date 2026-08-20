import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_capability_bus import build_shadow_capability_influence_ledger
from tools.unified_shadow_capability_closure import build_unified_shadow_capability_closure


class UnifiedShadowCapabilityClosureTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v5",
            "closed_at": "2026-08-20T00:34:00+07:00",
            "case_id": "trade-capability-closure-001",
            "transaction_sha256": "a" * 64,
            "base_closure_sha256": "b" * 64,
            "knowledge_memory_sha256": "c" * 64,
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
                "research_simulation": "BOUND_NON_BLOCKING_NO_RUNTIME",
                "knowledge_candidate": "BOUND_UNADMITTED_NO_WRITE",
                "durable_memory_candidate": "BOUND_PROPOSAL_ONLY_NO_WRITE",
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
                "claim_admission": False,
                "project_canon_write": False,
                "durable_memory_write": False,
            },
            "knowledge_status": {
                "claim_candidates": 3,
                "admitted_claims": 0,
                "memory_write": False,
                "source_runtime_bound": False,
            },
            "semantics": {"memory_is_not_permission": True},
            "safety": dict(SHADOW_SAFETY),
        }
        self.base["closure_sha256"] = sha256_obj(self.base)
        self.ledger = build_shadow_capability_influence_ledger(
            self.base,
            generated_at="2026-08-20T00:35:00+07:00",
        )

    def build(self, base=None, ledger=None):
        return build_unified_shadow_capability_closure(
            self.base if base is None else base,
            self.ledger if ledger is None else ledger,
            closed_at="2026-08-20T00:36:00+07:00",
        )

    def test_v6_closure_binds_63_of_63_without_authority(self):
        closure = self.build()
        self.assertEqual(closure["schema"], "bitevo.unified_shadow_closure.v6")
        self.assertEqual(closure["registered_node_count"], 63)
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertEqual(closure["planes"]["capability_influence_bus"], "BOUND_63_OF_63_NO_EFFECT_AUTHORITY")
        self.assertEqual(closure["capability_status"]["nodes_partitioned"], 63)
        self.assertFalse(closure["capability_status"]["majority_vote"])
        self.assertFalse(closure["capability_status"]["runtime_invoked"])
        self.assertFalse(closure["capability_status"]["effect_authority_granted"])
        self.assertFalse(closure["capability_status"]["gate_widening_allowed"])
        self.assertTrue(all(value is False for value in closure["effect_summary"].values()))
        self.assertEqual(closure["safety"]["execution_authority"], "NONE")
        self.assertEqual(closure["safety"]["capital_permission"], "DENY")

    def test_ledger_cannot_widen_gate(self):
        ledger = copy.deepcopy(self.ledger)
        rows = [dict(row) for row in ledger["nodes"]]
        rows[0]["may_widen_gate"] = True
        ledger["nodes"] = tuple(rows)
        ledger["capability_ledger_sha256"] = sha256_obj({k: v for k, v in ledger.items() if k != "capability_ledger_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "capability_closure_gate_widening_detected"):
            self.build(ledger=ledger)

    def test_ledger_cannot_create_trading_vote(self):
        ledger = copy.deepcopy(self.ledger)
        rows = [dict(row) for row in ledger["nodes"]]
        rows[0]["trading_vote"] = True
        ledger["nodes"] = tuple(rows)
        ledger["capability_ledger_sha256"] = sha256_obj({k: v for k, v in ledger.items() if k != "capability_ledger_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "capability_closure_trading_vote_detected"):
            self.build(ledger=ledger)

    def test_ledger_cannot_claim_effect_authority(self):
        ledger = copy.deepcopy(self.ledger)
        rows = [dict(row) for row in ledger["nodes"]]
        rows[0]["effect_authority"] = "TRADE"
        ledger["nodes"] = tuple(rows)
        ledger["capability_ledger_sha256"] = sha256_obj({k: v for k, v in ledger.items() if k != "capability_ledger_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "capability_closure_effect_authority_detected"):
            self.build(ledger=ledger)

    def test_ledger_cannot_claim_runtime_invocation(self):
        ledger = copy.deepcopy(self.ledger)
        rows = [dict(row) for row in ledger["nodes"]]
        rows[0]["external_runtime_invoked"] = True
        ledger["nodes"] = tuple(rows)
        ledger["capability_ledger_sha256"] = sha256_obj({k: v for k, v in ledger.items() if k != "capability_ledger_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "capability_closure_runtime_invocation_detected"):
            self.build(ledger=ledger)

    def test_effectful_base_is_rejected(self):
        base = copy.deepcopy(self.base)
        base["effect_summary"]["signal"] = True
        base["closure_sha256"] = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "capability_closure_base_effect_boundary_breached"):
            self.build(base=base)

    def test_closure_is_deterministic(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["closure_sha256"], second["closure_sha256"])


if __name__ == "__main__":
    unittest.main()
