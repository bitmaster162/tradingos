import unittest

from tools.tradingos_shadow_integration import (
    ShadowIntegrationError,
    build_trade_case,
    build_trade_decision_packet,
    build_trade_thesis,
    normalize_triaxis_adjudication,
)
from tools.unified_shadow_control_plane import build_shadow_control_plane_receipt


class UnifiedShadowControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.case = build_trade_case(
            case_id="trade-control-001",
            frozen_at="2026-08-19T16:30:00Z",
            symbol="BTCUSDT",
            venue="Binance",
            timeframe="1h",
            scenario="Offline control-plane integration fixture.",
            snapshot_ref={
                "source_id": "snapshot:control",
                "sha256": "a" * 64,
                "schema": "tradingos.market_snapshot.v1",
            },
            vision_ref={
                "source_id": "vision:control",
                "sha256": "b" * 64,
                "schema": "tradingos.visual_market_evidence.v1",
            },
        )
        thesis = build_trade_thesis(
            self.case,
            {"schema": "tradingos.decision_brief.v2", "status": "READY", "stance": "WATCH_LONG"},
        )
        adjudication = normalize_triaxis_adjudication(
            case_id=self.case["case_id"],
            verdict="PASS",
            strongest_case=["support"],
            falsifiers=[],
            surviving_claims=["support"],
            evidence_refs=["snapshot:control", "vision:control"],
        )
        twin = {
            "schema": "sct.prediction/v3",
            "prediction_id": "pred-control",
            "predicted_choice": "LONG",
            "confidence": 0.7,
            "option_probabilities": {"LONG": 0.7, "SHORT": 0.1, "WAIT": 0.2},
            "execution_authority": "NONE",
            "can_execute": False,
        }
        self.packet = build_trade_decision_packet(
            self.case,
            thesis,
            twin,
            adjudication,
            {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
        )
        self.control_ref = {
            "repo": "bitmaster162/control-center",
            "pr_number": 30,
            "head_sha": "9c3f3642211501867b8f089decb3b9b6166de350",
            "state": "OPEN_DRAFT_UNMERGED",
            "draft": True,
            "merged": False,
        }
        self.continuity_ref = {
            "repo": "bitmaster162/continuityos",
            "pr_number": 91,
            "head_sha": "a0a244d40f0a2aa500df45b1f846f0d863a77749",
            "state": "OPEN_DRAFT_UNMERGED",
            "draft": True,
            "merged": False,
        }

    def build_receipt(self, **overrides):
        kwargs = {
            "control_center_ref": self.control_ref,
            "continuityos_ref": self.continuity_ref,
            "provider_capture_at": "2026-08-12T04:59:00+07:00",
            "lease_expires_at": "2026-08-12T10:59:00+07:00",
            "evaluated_at": "2026-08-19T23:50:00+07:00",
            "generated_at": "2026-08-19T23:50:00+07:00",
            "anti_amnesia_context_sha256": "c" * 64,
            "conflicts": (),
        }
        kwargs.update(overrides)
        return build_shadow_control_plane_receipt(self.case, self.packet, **kwargs)

    def test_stale_control_authority_evidence_forces_hold_and_wait(self):
        receipt = self.build_receipt()
        self.assertEqual(receipt["hanri"]["freshness"], "STALE")
        self.assertTrue(receipt["hanri"]["attention_required"])
        self.assertEqual(receipt["control_gate"], "HOLD")
        self.assertEqual(receipt["control_plane_action"], "WAIT")
        self.assertFalse(receipt["control_center_projection"]["apply"])
        self.assertFalse(receipt["continuity_and_return"]["checkpoint_write"])
        self.assertFalse(receipt["executor_boundary"]["enabled"])
        self.assertEqual(receipt["safety"]["capital_permission"], "DENY")

    def test_fresh_conflict_free_evidence_can_pass_shadow_without_effect(self):
        receipt = self.build_receipt(
            provider_capture_at="2026-08-19T22:00:00+07:00",
            lease_expires_at="2026-08-20T04:00:00+07:00",
        )
        self.assertEqual(receipt["hanri"]["freshness"], "FRESH")
        self.assertFalse(receipt["hanri"]["attention_required"])
        self.assertEqual(receipt["control_gate"], "PASS_SHADOW")
        self.assertEqual(receipt["control_plane_action"], "LONG")
        self.assertFalse(receipt["control_center_projection"]["current_truth_mutation"])
        self.assertFalse(receipt["continuity_and_return"]["event_append"])
        self.assertFalse(receipt["executor_boundary"]["enabled"])

    def test_conflict_forces_hold_even_when_fresh(self):
        receipt = self.build_receipt(
            provider_capture_at="2026-08-19T22:00:00+07:00",
            lease_expires_at="2026-08-20T04:00:00+07:00",
            conflicts=("CONTROL_POINTER_DRIFT",),
        )
        self.assertEqual(receipt["hanri"]["freshness"], "FRESH")
        self.assertTrue(receipt["hanri"]["attention_required"])
        self.assertEqual(receipt["control_gate"], "HOLD")
        self.assertEqual(receipt["control_plane_action"], "WAIT")

    def test_nonzero_effect_counter_fails_closed(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "effect_ceiling_breached:effects_authorized"):
            self.build_receipt(
                effect_counters={
                    "human_now": 0,
                    "effect_candidates": 0,
                    "effects_authorized": 1,
                    "executions_authorized": 0,
                }
            )

    def test_tampered_packet_is_rejected(self):
        packet = dict(self.packet)
        packet["system_recommendation"] = "WAIT"
        with self.assertRaisesRegex(ShadowIntegrationError, "control_plane_packet_hash_mismatch"):
            build_shadow_control_plane_receipt(
                self.case,
                packet,
                control_center_ref=self.control_ref,
                continuityos_ref=self.continuity_ref,
                provider_capture_at="2026-08-12T04:59:00+07:00",
                lease_expires_at="2026-08-12T10:59:00+07:00",
                evaluated_at="2026-08-19T23:50:00+07:00",
                generated_at="2026-08-19T23:50:00+07:00",
                anti_amnesia_context_sha256="c" * 64,
            )

    def test_merged_source_is_forbidden_in_p0_fixture(self):
        ref = dict(self.control_ref)
        ref["merged"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "merged_source_not_allowed"):
            self.build_receipt(control_center_ref=ref)


if __name__ == "__main__":
    unittest.main()
