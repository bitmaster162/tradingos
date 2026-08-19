import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_trading_advisory import (
    ADVISORY_NODES,
    ADVISORY_SPECS,
    build_default_trading_advisory_evidence,
    build_shadow_trading_advisory_ledger,
)


class UnifiedShadowTradingAdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v6",
            "closed_at": "2026-08-20T00:40:00+07:00",
            "case_id": "trade-advisory-001",
            "transaction_sha256": "a" * 64,
            "registered_node_count": 63,
            "effective_gate": "HOLD",
            "effective_action": "WAIT",
            "status": "P0_SHADOW_CLOSED_NO_EFFECT",
            "planes": {"capability_influence_bus": "BOUND_63_OF_63_NO_EFFECT_AUTHORITY"},
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
        return build_shadow_trading_advisory_ledger(
            self.base,
            build_default_trading_advisory_evidence() if evidence is None else evidence,
            generated_at="2026-08-20T00:41:00+07:00",
        )

    def test_current_bounded_posture_types_all_five_but_admits_none(self):
        ledger = self.build()
        self.assertEqual(ledger["schema"], "bitevo.shadow_trading_advisory_ledger.v1")
        self.assertEqual(ledger["advisory_node_count"], 5)
        self.assertEqual(tuple(row["node_id"] for row in ledger["receipts"]), ADVISORY_NODES)
        self.assertEqual(ledger["admitted_nodes"], ())
        self.assertEqual(ledger["not_admitted_nodes"], ADVISORY_NODES)
        self.assertEqual(ledger["narrowing_nodes"], ())
        self.assertTrue(all(row["typed_contract_bound"] is True for row in ledger["receipts"]))
        self.assertTrue(all(row["may_widen_gate"] is False for row in ledger["receipts"]))
        self.assertTrue(all(row["execution_authority"] == "NONE" for row in ledger["receipts"]))
        self.assertTrue(all(value is False for value in ledger["effects"].values()))
        self.assertFalse(ledger["safety"]["can_trade"])
        self.assertEqual(ledger["safety"]["capital_permission"], "DENY")

    def test_fully_verified_delist_risk_can_only_narrow(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:delist-drs"
        row = evidence[node]
        for field in ADVISORY_SPECS[node]["required_bool_fields"]:
            row[field] = True
        row["finding"] = "RISK_FLAG"
        row["outcome"] = "REPAIR"
        ledger = self.build(evidence)
        receipt = next(item for item in ledger["receipts"] if item["node_id"] == node)
        self.assertTrue(receipt["admitted_for_narrowing"])
        self.assertEqual(receipt["gate_effect"], "NARROW_TO_HOLD")
        self.assertFalse(receipt["may_widen_gate"])
        self.assertFalse(receipt["trading_vote"])
        self.assertEqual(receipt["execution_authority"], "NONE")
        self.assertEqual(ledger["narrowing_nodes"], (node,))

    def test_edge_keep_is_evidence_only_even_after_full_admission(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:edge-research-lab"
        row = evidence[node]
        for field in ADVISORY_SPECS[node]["required_bool_fields"]:
            row[field] = True
        row["finding"] = "NO_OBJECTION"
        row["outcome"] = "KEEP"
        ledger = self.build(evidence)
        receipt = ledger["receipts"][0]
        self.assertTrue(receipt["admitted_for_narrowing"])
        self.assertEqual(receipt["gate_effect"], "NONE")
        self.assertEqual(ledger["narrowing_nodes"], ())

    def test_edge_kill_narrows_after_full_admission(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:edge-research-lab"
        row = evidence[node]
        for field in ADVISORY_SPECS[node]["required_bool_fields"]:
            row[field] = True
        row["finding"] = "NO_OBJECTION"
        row["outcome"] = "KILL"
        ledger = self.build(evidence)
        self.assertEqual(ledger["narrowing_nodes"], (node,))

    def test_missing_one_required_proof_means_no_influence(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:arb-radar"
        row = evidence[node]
        for field in ADVISORY_SPECS[node]["required_bool_fields"]:
            row[field] = True
        row["freshness_verified"] = False
        row["finding"] = "RISK_FLAG"
        row["outcome"] = "REPAIR"
        ledger = self.build(evidence)
        receipt = next(item for item in ledger["receipts"] if item["node_id"] == node)
        self.assertFalse(receipt["admitted_for_narrowing"])
        self.assertIn("freshness_verified", receipt["missing_proof_fields"])
        self.assertEqual(receipt["gate_effect"], "NONE")

    def test_effectful_evidence_is_rejected(self):
        evidence = build_default_trading_advisory_evidence()
        evidence["portfolio:grid-os"]["trade_signal_emitted"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "trading_advisory_effect_boundary_breached"):
            self.build(evidence)

    def test_runtime_claim_without_source_identity_is_rejected(self):
        evidence = build_default_trading_advisory_evidence()
        evidence["portfolio:sovereign-api-core-bot"]["runtime_verified"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "trading_advisory_runtime_without_source_identity"):
            self.build(evidence)

    def test_exact_five_node_coverage_is_required(self):
        evidence = build_default_trading_advisory_evidence()
        del evidence["portfolio:grid-os"]
        with self.assertRaisesRegex(ShadowIntegrationError, "trading_advisory_coverage_mismatch"):
            self.build(evidence)

    def test_hash_is_deterministic(self):
        self.assertEqual(self.build(), self.build())
        self.assertEqual(self.build()["advisory_ledger_sha256"], self.build()["advisory_ledger_sha256"])


if __name__ == "__main__":
    unittest.main()
