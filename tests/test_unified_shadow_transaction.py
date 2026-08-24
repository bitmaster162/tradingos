import unittest

from tools.tradingos_shadow_integration import (
    ShadowIntegrationError,
    build_trade_case,
    build_trade_decision_packet,
    build_trade_thesis,
    normalize_triaxis_adjudication,
    sha256_obj,
)
from tools.unified_shadow_control_plane import build_shadow_control_plane_receipt
from tools.unified_shadow_federation import build_default_shadow_contributions, build_unified_shadow_receipt
from tools.unified_shadow_router import build_trade_case_route
from tools.unified_shadow_transaction import build_unified_shadow_transaction


class UnifiedShadowTransactionTests(unittest.TestCase):
    def setUp(self):
        self.case = build_trade_case(
            case_id="trade-tx-001",
            frozen_at="2026-08-19T16:30:00Z",
            symbol="BTCUSDT",
            venue="Binance",
            timeframe="1h",
            scenario="Offline unified transaction fixture.",
            snapshot_ref={"source_id": "snapshot:tx", "sha256": "a" * 64, "schema": "tradingos.market_snapshot.v1"},
            vision_ref={"source_id": "vision:tx", "sha256": "b" * 64, "schema": "tradingos.visual_market_evidence.v1"},
        )
        thesis = build_trade_thesis(
            self.case,
            {"schema": "tradingos.decision_brief.v2", "status": "READY", "stance": "WATCH_LONG"},
        )
        adjudication = normalize_triaxis_adjudication(
            case_id=self.case["case_id"],
            verdict="HOLD",
            strongest_case=["support"],
            falsifiers=["entry timing"],
            surviving_claims=["trend only"],
            evidence_refs=["snapshot:tx", "vision:tx"],
        )
        twin = {
            "schema": "sct.prediction/v3",
            "case_id": self.case["case_id"],
            "arm": "sct",
            "options": tuple(self.case["options"]),
            "option_probabilities": {"LONG": 0.7, "SHORT": 0.1, "WAIT": 0.2},
            "predicted_choice": "LONG",
            "confidence": 0.7,
            "reasons": (),
            "change_conditions": (),
            "would_escalate": False,
            "committed_at": 1_787_157_100.0,
            "execution_authority": "NONE",
            "can_execute": False,
        }
        twin["prediction_id"] = sha256_obj(twin)
        self.packet = build_trade_decision_packet(
            self.case,
            thesis,
            twin,
            adjudication,
            {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
        )
        contributions = build_default_shadow_contributions(
            case_id=self.case["case_id"],
            case_sha256=self.case["case_sha256"],
            packet_sha256=self.packet["packet_sha256"],
        )
        self.federation = build_unified_shadow_receipt(
            self.case,
            self.packet,
            contributions,
            generated_at="2026-08-19T16:31:00Z",
        )
        self.route = build_trade_case_route(
            case_id=self.case["case_id"],
            case_sha256=self.case["case_sha256"],
        )
        self.control = build_shadow_control_plane_receipt(
            self.case,
            self.packet,
            control_center_ref={
                "repo": "bitmaster162/control-center",
                "pr_number": 30,
                "head_sha": "9c3f3642211501867b8f089decb3b9b6166de350",
                "state": "OPEN_DRAFT_UNMERGED",
                "draft": True,
                "merged": False,
            },
            continuityos_source_ref={
                "repo": "bitmaster162/continuityos",
                "branch": "master",
                "head_sha": "9dfb9e5b847a27113ca7c709a0adee900e3ff63f",
            },
            sct_adapter_ref={
                "repo": "bitmaster162/continuityos",
                "pr_number": 91,
                "head_sha": "a0a244d40f0a2aa500df45b1f846f0d863a77749",
                "state": "OPEN_DRAFT_UNMERGED",
                "draft": True,
                "merged": False,
            },
            provider_capture_at="2026-08-12T04:59:00+07:00",
            lease_expires_at="2026-08-12T10:59:00+07:00",
            evaluated_at="2026-08-19T23:50:00+07:00",
            generated_at="2026-08-19T23:50:00+07:00",
            anti_amnesia_context_sha256="c" * 64,
        )

    def build_tx(self, *, packet=None, federation=None, route=None, control=None):
        return build_unified_shadow_transaction(
            self.case,
            self.packet if packet is None else packet,
            self.federation if federation is None else federation,
            self.route if route is None else route,
            self.control if control is None else control,
            frozen_at="2026-08-19T16:32:00Z",
        )

    def test_transaction_binds_case_packet_route_federation_and_control_plane(self):
        tx = self.build_tx()
        self.assertEqual(tx["schema"], "bitevo.unified_shadow_transaction.v2")
        self.assertEqual(tx["registered_node_count"], 63)
        self.assertEqual(tx["system_recommendation"], "WAIT")
        self.assertEqual(tx["control_gate"], "HOLD")
        self.assertEqual(tx["control_plane_action"], "WAIT")
        self.assertEqual(tx["hanri_freshness"], "STALE")
        self.assertTrue(tx["hanri_attention_required"])
        self.assertEqual(tx["control_plane_sha256"], self.control["control_plane_sha256"])
        self.assertEqual(tx["twin_prediction_status"], "UNIQUE")
        self.assertTrue(tx["divergence"])
        self.assertFalse(tx["effect_boundary"]["executor_enabled"])
        self.assertFalse(tx["effect_boundary"]["current_truth_apply"])
        self.assertFalse(tx["effect_boundary"]["continuity_write"])
        self.assertFalse(tx["effect_boundary"]["exchange_call"])
        self.assertFalse(tx["effect_boundary"]["order"])
        self.assertEqual(tx["safety"]["execution_authority"], "NONE")
        self.assertEqual(tx["safety"]["capital_permission"], "DENY")

    def test_control_plane_preserves_source_separation_inside_transaction_binding(self):
        modern = self.control["source_refs"]["continuityos_modern_source"]
        sct = self.control["source_refs"]["sct_trader_twin_adapter"]
        self.assertEqual(modern["claim_ceiling"], "MODERN_GITHUB_SOURCE_ONLY")
        self.assertFalse(modern["proves_live_runtime"])
        self.assertFalse(sct["is_continuityos_source_authority"])
        self.assertNotEqual(modern["head_sha"], sct["head_sha"])

    def test_tampered_packet_is_rejected_at_transaction_boundary(self):
        packet = dict(self.packet)
        packet["system_recommendation"] = "LONG"
        with self.assertRaisesRegex(ShadowIntegrationError, "transaction_packet_hash_mismatch"):
            self.build_tx(packet=packet)

    def test_tampered_route_is_rejected(self):
        route = dict(self.route)
        route["registered_node_count"] = 62
        with self.assertRaisesRegex(ShadowIntegrationError, "transaction_route_hash_mismatch"):
            self.build_tx(route=route)

    def test_executor_cannot_be_enabled_even_with_rehashed_route(self):
        route = dict(self.route)
        route["executor_boundary"] = dict(route["executor_boundary"])
        route["executor_boundary"]["enabled"] = True
        route["route_sha256"] = sha256_obj({k: v for k, v in route.items() if k != "route_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "transaction_executor_must_be_disabled"):
            self.build_tx(route=route)

    def test_tampered_control_receipt_is_rejected(self):
        control = dict(self.control)
        control["control_gate"] = "PASS_SHADOW"
        with self.assertRaisesRegex(ShadowIntegrationError, "transaction_control_plane_hash_mismatch"):
            self.build_tx(control=control)

    def test_control_plane_apply_is_forbidden_even_if_rehashed(self):
        control = dict(self.control)
        control["control_center_projection"] = dict(control["control_center_projection"])
        control["control_center_projection"]["apply"] = True
        control["control_plane_sha256"] = sha256_obj(
            {k: v for k, v in control.items() if k != "control_plane_sha256"}
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "transaction_control_plane_apply_forbidden"):
            self.build_tx(control=control)


if __name__ == "__main__":
    unittest.main()
