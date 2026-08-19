import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_capability_bus import (
    DECISION_BOUND,
    EVIDENCE_GATE,
    EXECUTOR_DISABLED,
    INFLUENCE_CLASSES,
    build_shadow_capability_influence_ledger,
)
from tools.unified_shadow_federation import SYSTEM_IDS


class UnifiedShadowCapabilityBusTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v5",
            "closed_at": "2026-08-20T00:34:00+07:00",
            "case_id": "trade-capability-001",
            "transaction_sha256": "a" * 64,
            "base_closure_sha256": "b" * 64,
            "knowledge_memory_sha256": "c" * 64,
            "registered_node_count": 63,
            "effective_gate": "HOLD",
            "effective_action": "WAIT",
            "status": "P0_SHADOW_CLOSED_NO_EFFECT",
            "planes": {
                "composition": "BOUND",
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

    def build(self, base=None):
        return build_shadow_capability_influence_ledger(
            self.base if base is None else base,
            generated_at="2026-08-20T00:35:00+07:00",
        )

    def test_all_63_nodes_are_partitioned_exactly_once(self):
        receipt = self.build()
        self.assertEqual(receipt["registered_node_count"], 63)
        self.assertTrue(receipt["all_nodes_assigned_exactly_once"])
        ids = [row["node_id"] for row in receipt["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(SYSTEM_IDS))
        self.assertEqual(sum(receipt["class_counts"].values()), 63)

    def test_static_influence_classes_cover_universe_without_overlap(self):
        flattened = [node for members in INFLUENCE_CLASSES.values() for node in members]
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(set(flattened), set(SYSTEM_IDS))

    def test_decision_bound_nodes_are_non_executing(self):
        receipt = self.build()
        by_id = {row["node_id"]: row for row in receipt["nodes"]}
        for node_id in DECISION_BOUND:
            self.assertTrue(by_id[node_id]["may_influence_typed_decision"])
            self.assertFalse(by_id[node_id]["may_widen_gate"])
            self.assertFalse(by_id[node_id]["trading_vote"])
            self.assertEqual(by_id[node_id]["effect_authority"], "NONE")
            self.assertFalse(by_id[node_id]["external_runtime_invoked"])

    def test_evidence_gate_nodes_can_only_narrow_with_typed_contract(self):
        receipt = self.build()
        by_id = {row["node_id"]: row for row in receipt["nodes"]}
        for node_id in EVIDENCE_GATE:
            self.assertFalse(by_id[node_id]["may_influence_typed_decision"])
            self.assertTrue(by_id[node_id]["may_narrow_gate_if_typed_contract_allows"])
            self.assertFalse(by_id[node_id]["may_widen_gate"])
            self.assertEqual(by_id[node_id]["effect_authority"], "NONE")

    def test_trading_advisory_nodes_are_not_votes_or_execution_authority(self):
        receipt = self.build()
        advisory = [row for row in receipt["nodes"] if row["influence_class"] == "TRADING_ADVISORY_ACCOUNTED"]
        self.assertTrue(advisory)
        for row in advisory:
            self.assertFalse(row["may_influence_typed_decision"])
            self.assertFalse(row["may_narrow_gate_if_typed_contract_allows"])
            self.assertFalse(row["trading_vote"])
            self.assertEqual(row["effect_authority"], "NONE")

    def test_product_and_research_nodes_cannot_vote(self):
        receipt = self.build()
        for row in receipt["nodes"]:
            if row["influence_class"] in {"PRODUCT_SERVICE_ACCOUNTED", "RESEARCH_SIDE_NON_VOTING"}:
                self.assertFalse(row["may_influence_typed_decision"])
                self.assertFalse(row["trading_vote"])
                self.assertFalse(row["may_widen_gate"])

    def test_executor_is_single_disabled_boundary(self):
        self.assertEqual(EXECUTOR_DISABLED, {"entity:executor_network"})
        receipt = self.build()
        row = next(row for row in receipt["nodes"] if row["node_id"] == "entity:executor_network")
        self.assertEqual(row["influence_class"], "EXECUTOR_DISABLED")
        self.assertEqual(row["effect_authority"], "NONE")
        self.assertFalse(row["external_runtime_invoked"])

    def test_ledger_does_not_claim_source_or_runtime_proof(self):
        receipt = self.build()
        self.assertTrue(all(row["source_identity_proven_by_ledger"] is False for row in receipt["nodes"]))
        self.assertTrue(all(row["runtime_proven_by_ledger"] is False for row in receipt["nodes"]))
        self.assertTrue(all(row["external_runtime_invoked"] is False for row in receipt["nodes"]))

    def test_effectful_base_closure_is_rejected(self):
        base = copy.deepcopy(self.base)
        base["effect_summary"]["order"] = True
        base["closure_sha256"] = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "capability_bus_base_effect_boundary_breached"):
            self.build(base=base)

    def test_base_tamper_is_rejected(self):
        base = copy.deepcopy(self.base)
        base["knowledge_status"]["claim_candidates"] = 99
        with self.assertRaisesRegex(ShadowIntegrationError, "capability_bus_base_hash_mismatch"):
            self.build(base=base)

    def test_ledger_is_deterministic(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["capability_ledger_sha256"], second["capability_ledger_sha256"])


if __name__ == "__main__":
    unittest.main()
