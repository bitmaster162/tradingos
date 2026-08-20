import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_trading_advisory import (
    ADVISORY_NODES,
    ADVISORY_SPECS,
    GLOBAL_INFLUENCE_FIELDS,
    build_default_trading_advisory_evidence,
    build_shadow_trading_advisory_ledger,
)


class UnifiedShadowTradingAdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v6",
            "closed_at": "2026-08-20T01:05:00+07:00",
            "case_id": "trade-advisory-002",
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
            generated_at="2026-08-20T01:06:00+07:00",
        )

    def fully_prove(self, evidence, node):
        evidence[node]["case_relevance_verified"] = True
        evidence[node]["pre_freeze_evidence_verified"] = True
        for field in ADVISORY_SPECS[node]["required_bool_fields"]:
            evidence[node][field] = True

    def test_current_bounded_posture_types_all_nine_but_admits_none(self):
        ledger = self.build()
        self.assertEqual(ledger["schema"], "bitevo.shadow_trading_advisory_ledger.v2")
        self.assertEqual(ledger["advisory_node_count"], 9)
        self.assertEqual(tuple(row["node_id"] for row in ledger["receipts"]), ADVISORY_NODES)
        self.assertEqual(ledger["admitted_nodes"], ())
        self.assertEqual(ledger["not_admitted_nodes"], ADVISORY_NODES)
        self.assertEqual(ledger["narrowing_nodes"], ())
        self.assertEqual(ledger["global_influence_fields"], GLOBAL_INFLUENCE_FIELDS)
        self.assertTrue(all(row["typed_contract_bound"] is True for row in ledger["receipts"]))
        self.assertTrue(all(row["may_widen_gate"] is False for row in ledger["receipts"]))
        self.assertTrue(all(row["execution_authority"] == "NONE" for row in ledger["receipts"]))
        self.assertTrue(all(value is False for value in ledger["effects"].values()))
        self.assertFalse(ledger["safety"]["can_trade"])
        self.assertEqual(ledger["safety"]["capital_permission"], "DENY")

    def test_exact_remaining_four_nodes_are_present(self):
        for node in (
            "portfolio:claude-bitunix",
            "portfolio:btcusdt-binance-bot",
            "portfolio:confluence-trading-bot",
            "portfolio:max-bitevo-pack",
        ):
            self.assertIn(node, ADVISORY_NODES)
            self.assertIn(node, ADVISORY_SPECS)

    def test_fully_verified_delist_risk_can_only_narrow(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:delist-drs"
        self.fully_prove(evidence, node)
        evidence[node]["finding"] = "RISK_FLAG"
        evidence[node]["outcome"] = "REPAIR"
        ledger = self.build(evidence)
        receipt = next(item for item in ledger["receipts"] if item["node_id"] == node)
        self.assertTrue(receipt["admitted_for_narrowing"])
        self.assertEqual(receipt["gate_effect"], "NARROW_TO_HOLD")
        self.assertFalse(receipt["may_widen_gate"])
        self.assertFalse(receipt["trading_vote"])
        self.assertEqual(receipt["execution_authority"], "NONE")
        self.assertEqual(ledger["narrowing_nodes"], (node,))

    def test_case_relevance_is_mandatory_even_with_project_proofs(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:claude-bitunix"
        evidence[node]["pre_freeze_evidence_verified"] = True
        for field in ADVISORY_SPECS[node]["required_bool_fields"]:
            evidence[node][field] = True
        evidence[node]["finding"] = "RISK_FLAG"
        evidence[node]["outcome"] = "FAIL"
        ledger = self.build(evidence)
        receipt = next(item for item in ledger["receipts"] if item["node_id"] == node)
        self.assertFalse(receipt["admitted_for_narrowing"])
        self.assertIn("case_relevance_verified", receipt["missing_proof_fields"])
        self.assertEqual(receipt["gate_effect"], "NONE")

    def test_post_freeze_evidence_cannot_influence_case(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:btcusdt-binance-bot"
        evidence[node]["case_relevance_verified"] = True
        for field in ADVISORY_SPECS[node]["required_bool_fields"]:
            evidence[node][field] = True
        evidence[node]["finding"] = "RISK_FLAG"
        evidence[node]["outcome"] = "RECAPTURE_AND_TEST"
        ledger = self.build(evidence)
        receipt = next(item for item in ledger["receipts"] if item["node_id"] == node)
        self.assertFalse(receipt["admitted_for_narrowing"])
        self.assertIn("pre_freeze_evidence_verified", receipt["missing_proof_fields"])
        self.assertEqual(receipt["gate_effect"], "NONE")

    def test_fully_verified_claude_fail_can_narrow_only_if_relevant_and_pre_freeze(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:claude-bitunix"
        self.fully_prove(evidence, node)
        evidence[node]["finding"] = "NO_OBJECTION"
        evidence[node]["outcome"] = "FAIL"
        ledger = self.build(evidence)
        self.assertEqual(ledger["narrowing_nodes"], (node,))

    def test_legacy_max_toolkit_never_narrows_market_gate(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:max-bitevo-pack"
        self.fully_prove(evidence, node)
        evidence[node]["finding"] = "RISK_FLAG"
        evidence[node]["outcome"] = "KILL"
        ledger = self.build(evidence)
        receipt = next(item for item in ledger["receipts"] if item["node_id"] == node)
        self.assertTrue(receipt["admitted_for_narrowing"])
        self.assertEqual(receipt["gate_effect"], "NONE")
        self.assertEqual(ledger["narrowing_nodes"], ())

    def test_edge_keep_is_evidence_only_even_after_full_admission(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:edge-research-lab"
        self.fully_prove(evidence, node)
        evidence[node]["finding"] = "NO_OBJECTION"
        evidence[node]["outcome"] = "KEEP"
        ledger = self.build(evidence)
        receipt = ledger["receipts"][0]
        self.assertTrue(receipt["admitted_for_narrowing"])
        self.assertEqual(receipt["gate_effect"], "NONE")

    def test_edge_kill_narrows_after_full_admission(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:edge-research-lab"
        self.fully_prove(evidence, node)
        evidence[node]["finding"] = "NO_OBJECTION"
        evidence[node]["outcome"] = "KILL"
        ledger = self.build(evidence)
        self.assertEqual(ledger["narrowing_nodes"], (node,))

    def test_effectful_evidence_is_rejected(self):
        evidence = build_default_trading_advisory_evidence()
        evidence["portfolio:confluence-trading-bot"]["trade_signal_emitted"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "trading_advisory_effect_boundary_breached"):
            self.build(evidence)

    def test_runtime_claim_without_source_identity_is_rejected(self):
        evidence = build_default_trading_advisory_evidence()
        evidence["portfolio:sovereign-api-core-bot"]["runtime_verified"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "trading_advisory_runtime_without_source_identity"):
            self.build(evidence)

    def test_exact_nine_node_coverage_is_required(self):
        evidence = build_default_trading_advisory_evidence()
        del evidence["portfolio:max-bitevo-pack"]
        with self.assertRaisesRegex(ShadowIntegrationError, "trading_advisory_coverage_mismatch"):
            self.build(evidence)

    def test_hash_is_deterministic(self):
        self.assertEqual(self.build(), self.build())
        self.assertEqual(self.build()["advisory_ledger_sha256"], self.build()["advisory_ledger_sha256"])


if __name__ == "__main__":
    unittest.main()
