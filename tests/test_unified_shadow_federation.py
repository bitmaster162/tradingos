import unittest

from tools.tradingos_shadow_integration import (
    ShadowIntegrationError,
    build_trade_case,
    build_trade_decision_packet,
    build_trade_outcome_receipt,
    build_trade_thesis,
    build_triaxis_trade_audit_request,
    normalize_triaxis_adjudication,
)
from tools.unified_shadow_federation import (
    ACTIVE_TRADING_SPINE,
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
            "schema": "sct.prediction/v2",
            "prediction_id": "pred-unified-001",
            "option_probabilities": {"LONG": 0.68, "SHORT": 0.07, "WAIT": 0.25},
            "execution_authority": "NONE",
            "can_execute": False,
        }
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

    def test_full_offline_e2e_accounts_for_every_registered_system(self):
        contributions = build_default_shadow_contributions(
            case_id=self.case["case_id"],
            case_sha256=self.case["case_sha256"],
            packet_sha256=self.packet["packet_sha256"],
        )
        receipt = build_unified_shadow_receipt(
            self.case,
            self.packet,
            contributions,
            generated_at="2026-08-19T16:05:00Z",
        )

        self.assertEqual(receipt["registry_count"], len(SYSTEM_IDS))
        self.assertEqual(len(receipt["contribution_hashes"]), len(SYSTEM_IDS))
        self.assertTrue(receipt["all_registered_systems_accounted_for"])
        self.assertEqual(set(receipt["active_trading_spine"]), set(ACTIVE_TRADING_SPINE))
        self.assertEqual(receipt["safety"]["execution_authority"], "NONE")
        self.assertFalse(receipt["safety"]["can_trade"])
        self.assertEqual(receipt["safety"]["capital_permission"], "DENY")
        self.assertTrue(receipt["semantic_boundaries"]["one_federation_does_not_collapse_subsystem_ownership"])

        # The case demonstrates the intended separation: SCT predicts LONG, while
        # TRIAXIS HOLD causes the shadow advisor to WAIT.
        self.assertEqual(self.packet["twin"]["predicted_choice"], "LONG")
        self.assertEqual(self.packet["system_recommendation"], "WAIT")
        self.assertTrue(self.packet["divergence"])
        self.assertEqual(self.audit_request["execution_authority"], "NONE")

        outcome = build_trade_outcome_receipt(
            self.packet,
            actual_choice="LONG",
            decided_at="2026-08-19T16:06:00Z",
            market_outcome={"pnl_r": -1.0, "quality_label": "LOSS"},
        )
        self.assertTrue(outcome["twin_fidelity_match"])
        self.assertFalse(outcome["advisor_agreement"])
        self.assertEqual(outcome["market_outcome"]["pnl_r"], -1.0)

    def test_registry_contains_control_continuity_cognition_trading_and_commercial_planes(self):
        by_id = {row["system_id"]: row for row in system_registry()}
        for required in (
            "universe_hub",
            "control_center",
            "hanri",
            "anti_amnesia_gate",
            "core_v6_3",
            "continuityos",
            "archiveos",
            "return_broker",
            "lifeos",
            "bitevo_runtime",
            "maworld",
            "mind",
            "pfi_brain_fabric",
            "executor_network",
            "pandora",
            "visionassist",
            "sct",
            "triaxis",
            "tradingos",
            "grid_os",
            "arb_radar",
            "delist_drs",
            "sovereign_arena",
            "ai_skill_lab",
            "crypto_guides",
            "inner_circle_vip",
            "okx_nft_bot",
        ):
            self.assertIn(required, by_id)

    def test_missing_any_registry_node_fails_closed(self):
        contributions = list(build_default_shadow_contributions(
            case_id=self.case["case_id"],
            case_sha256=self.case["case_sha256"],
            packet_sha256=self.packet["packet_sha256"],
        ))
        contributions.pop()
        with self.assertRaisesRegex(ShadowIntegrationError, "federation_registry_coverage_mismatch"):
            build_unified_shadow_receipt(
                self.case,
                self.packet,
                contributions,
                generated_at="2026-08-19T16:05:00Z",
            )

    def test_active_core_node_cannot_be_silently_downgraded(self):
        contributions = list(build_default_shadow_contributions(
            case_id=self.case["case_id"],
            case_sha256=self.case["case_sha256"],
            packet_sha256=self.packet["packet_sha256"],
        ))
        index = next(i for i, row in enumerate(contributions) if row["system_id"] == "triaxis")
        contributions[index] = build_system_contribution(
            system_id="triaxis",
            case_id=self.case["case_id"],
            participation="REGISTERED_ONLY",
            summary="silently downgraded fixture",
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "federation_active_spine_missing"):
            build_unified_shadow_receipt(
                self.case,
                self.packet,
                contributions,
                generated_at="2026-08-19T16:05:00Z",
            )

    def test_registered_only_is_not_claimed_live(self):
        contributions = build_default_shadow_contributions(
            case_id=self.case["case_id"],
            case_sha256=self.case["case_sha256"],
            packet_sha256=self.packet["packet_sha256"],
        )
        receipt = build_unified_shadow_receipt(
            self.case,
            self.packet,
            contributions,
            generated_at="2026-08-19T16:05:00Z",
        )
        self.assertIn("ai_skill_lab", receipt["not_applicable_to_this_trade_case"])
        self.assertIn("crypto_guides", receipt["registered_only"])
        self.assertIn("forge_foundry", receipt["unresolved_families"])
        self.assertIn("amora", receipt["parked"])
        self.assertTrue(receipt["semantic_boundaries"]["registered_does_not_mean_live"])


if __name__ == "__main__":
    unittest.main()
