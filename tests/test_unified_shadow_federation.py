import unittest

from tools.tradingos_shadow_integration import (
    ShadowIntegrationError,
    build_trade_case,
    build_trade_decision_packet,
    build_trade_outcome_receipt,
    build_trade_thesis,
    build_triaxis_trade_audit_request,
    normalize_triaxis_adjudication,
    sha256_obj,
)
from tools.unified_shadow_federation import (
    ACTIVE_TRADING_SPINE,
    EXTENDED_ENTITIES,
    PORTFOLIO_44,
    SYSTEM_IDS,
    build_default_shadow_contributions,
    build_system_contribution,
    build_unified_shadow_receipt,
    system_registry,
)


class UnifiedShadowFederationTests(unittest.TestCase):
    def setUp(self):
        self.case = build_trade_case(
            case_id="trade-unified-001",
            frozen_at="2026-08-19T16:00:00Z",
            symbol="BTCUSDT",
            venue="Binance",
            timeframe="1h",
            scenario="BTC trades into resistance after an upside liquidity sweep.",
            snapshot_ref={
                "source_id": "snapshot:unified-001",
                "sha256": "a" * 64,
                "schema": "tradingos.market_snapshot.v1",
            },
            vision_ref={
                "source_id": "market:shadow-req-001",
                "sha256": "b" * 64,
                "schema": "tradingos.visual_market_evidence.v1",
            },
            options=("LONG", "SHORT", "WAIT"),
        )
        self.thesis = build_trade_thesis(
            self.case,
            {
                "schema": "tradingos.decision_brief.v2",
                "status": "READY",
                "stance": "WATCH_LONG",
                "blockers": [],
                "missing_data": [],
                "conflicts": [],
            },
        )
        self.audit_request = build_triaxis_trade_audit_request(self.case, self.thesis)
        self.adjudication = normalize_triaxis_adjudication(
            case_id=self.case["case_id"],
            verdict="HOLD",
            strongest_case=["HTF trend and spot flow support continuation."],
            falsifiers=["Resistance rejection and liquidity trap remain live."],
            surviving_claims=["Trend bias survives; immediate entry does not."],
            evidence_refs=["snapshot:unified-001", "market:shadow-req-001"],
        )
        self.twin = {
            "schema": "sct.prediction/v3",
            "case_id": self.case["case_id"],
            "arm": "sct",
            "options": tuple(self.case["options"]),
            "option_probabilities": {"LONG": 0.68, "SHORT": 0.07, "WAIT": 0.25},
            "predicted_choice": "LONG",
            "confidence": 0.68,
            "reasons": (),
            "change_conditions": (),
            "would_escalate": False,
            "committed_at": 1_787_155_300.0,
            "execution_authority": "NONE",
            "can_execute": False,
        }
        self.twin["prediction_id"] = sha256_obj(self.twin)
        self.packet = build_trade_decision_packet(
            self.case,
            self.thesis,
            self.twin,
            self.adjudication,
            {
                "veto": False,
                "reasons": [],
                "can_trade": False,
                "capital_permission": "DENY",
            },
        )

    def _contributions(self):
        return build_default_shadow_contributions(
            case_id=self.case["case_id"],
            case_sha256=self.case["case_sha256"],
            packet_sha256=self.packet["packet_sha256"],
        )

    def _receipt(self, contributions=None):
        return build_unified_shadow_receipt(
            self.case,
            self.packet,
            contributions if contributions is not None else self._contributions(),
            generated_at="2026-08-19T16:05:00Z",
        )

    def test_full_offline_e2e_accounts_for_every_registered_node(self):
        receipt = self._receipt()
        self.assertEqual(receipt["portfolio_44_count"], 44)
        self.assertEqual(receipt["extended_entity_count"], len(EXTENDED_ENTITIES))
        self.assertEqual(receipt["registry_count"], len(SYSTEM_IDS))
        self.assertEqual(len(receipt["contribution_hashes"]), len(SYSTEM_IDS))
        self.assertEqual(tuple(receipt["portfolio_44_exact_action_order"]), tuple(range(1, 45)))
        self.assertTrue(receipt["all_registered_systems_accounted_for"])
        self.assertEqual(set(receipt["active_trading_spine"]), set(ACTIVE_TRADING_SPINE))
        self.assertEqual(receipt["safety"]["execution_authority"], "NONE")
        self.assertFalse(receipt["safety"]["can_trade"])
        self.assertEqual(receipt["safety"]["capital_permission"], "DENY")
        self.assertTrue(receipt["semantic_boundaries"]["one_federation_does_not_collapse_subsystem_ownership"])
        self.assertTrue(receipt["semantic_boundaries"]["portfolio_44_is_planning_candidate_not_authority"])

        # SCT predicts the human action while TRIAXIS independently blocks the thesis.
        self.assertEqual(self.packet["twin"]["predicted_choice"], "LONG")
        self.assertEqual(self.packet["twin"]["schema"], "sct.prediction/v3")
        self.assertEqual(self.packet["system_recommendation"], "WAIT")
        self.assertTrue(self.packet["divergence"])
        self.assertEqual(self.audit_request["execution_authority"], "NONE")
        self.assertFalse(self.audit_request["constraints"]["countermodel_default"])

        outcome = build_trade_outcome_receipt(
            self.packet,
            actual_choice="LONG",
            decided_at="2026-08-19T16:06:00Z",
            market_outcome={"pnl_r": -1.0, "quality_label": "LOSS"},
        )
        self.assertTrue(outcome["twin_fidelity_match"])
        self.assertFalse(outcome["advisor_agreement"])
        self.assertEqual(outcome["market_outcome"]["pnl_r"], -1.0)

    def test_exact_44_portfolio_is_preserved_without_alias_flattening(self):
        self.assertEqual(len(PORTFOLIO_44), 44)
        self.assertEqual(tuple(line.action_order for line in PORTFOLIO_44), tuple(range(1, 45)))
        self.assertEqual(len({line.project_id for line in PORTFOLIO_44}), 44)
        ids = {line.project_id for line in PORTFOLIO_44}
        for required in (
            "control-canter", "anti-amnesia-gate", "return-plane-v2", "continuityos",
            "archiveos-core", "archive-tooling", "state-authority-plane", "unified-dashboard",
            "knowledge-lab", "work-cockpit", "hanri", "bitevo-runtime", "reflex-layer",
            "tradingos", "parasite-killer", "sovereign-api-core-bot", "arb-radar",
            "sovereign-arena", "grid-os", "delist-drs", "maworld", "bitevo-ai-portal",
            "crypto-guides", "inner-circle", "openclaw", "arbiter-content-engine", "dtaap",
            "sovereign-agent-core", "gpts-core-sdk", "edge-research-lab", "visionassist",
            "claude-bitunix", "parasite-hunter-game", "operator-decision-sprint",
            "ai-agent-reliability-audit", "ai-client-hunter", "blockchain-forensics-osint",
            "btcusdt-binance-bot", "confluence-trading-bot", "max-bitevo-pack",
            "fable-observer", "amora", "amora-token", "rtf-starcoin",
        ):
            self.assertIn(required, ids)

    def test_extended_universe_keeps_nonportfolio_entities_separate(self):
        rows = {row["node_id"]: row for row in system_registry()}
        for required in (
            "entity:universe_hub",
            "entity:core_v6_3",
            "entity:return_broker",
            "entity:lifeos",
            "entity:pandora_spatial_runtime",
            "entity:sim_os_pandora_predecessor",
            "entity:forge_foundry",
            "entity:mind",
            "entity:pfi_brain_fabric",
            "entity:knowledge_foundry",
            "entity:executor_network",
            "entity:physical_ai_cosmos",
            "entity:durable_memory_kernel",
            "entity:system_universe_registry",
            "entity:sct",
            "entity:triaxis",
            "entity:archive_to_core_engine",
            "entity:typed_operational_memory",
            "entity:human_coevolution_layer",
        ):
            self.assertIn(required, rows)
        self.assertIn("portfolio:return-plane-v2", rows)
        self.assertIn("entity:return_broker", rows)
        self.assertNotEqual("portfolio:return-plane-v2", "entity:return_broker")

    def test_missing_any_registry_node_fails_closed(self):
        contributions = list(self._contributions())
        contributions.pop()
        with self.assertRaisesRegex(ShadowIntegrationError, "federation_registry_coverage_mismatch"):
            self._receipt(contributions)

    def test_empty_registry_does_not_silently_fall_back_to_defaults(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "federation_registry_coverage_mismatch"):
            self._receipt([])

    def test_active_core_node_cannot_be_silently_downgraded(self):
        contributions = list(self._contributions())
        node_id = "entity:triaxis"
        index = next(i for i, row in enumerate(contributions) if row["system_id"] == node_id)
        contributions[index] = build_system_contribution(
            system_id=node_id,
            case_id=self.case["case_id"],
            participation="REGISTERED_ONLY",
            summary="silently downgraded fixture",
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "federation_active_spine_missing"):
            self._receipt(contributions)

    def test_nonactive_lines_are_accounted_without_false_liveness(self):
        receipt = self._receipt()
        self.assertIn("portfolio:crypto-guides", receipt["registered_only"])
        self.assertIn("portfolio:openclaw", receipt["registered_only"])
        self.assertIn("entity:forge_foundry", receipt["unresolved_families"])
        self.assertIn("portfolio:amora", receipt["parked"])
        self.assertIn("portfolio:parasite-killer", receipt["not_applicable_to_this_trade_case"])
        self.assertTrue(receipt["semantic_boundaries"]["registered_does_not_mean_live"])
        self.assertTrue(receipt["semantic_boundaries"]["active_shadow_does_not_mean_external_runtime_called"])


if __name__ == "__main__":
    unittest.main()
