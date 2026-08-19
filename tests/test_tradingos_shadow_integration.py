import unittest

from tools.tradingos_shadow_integration import (
    SHADOW_SAFETY,
    ShadowIntegrationError,
    build_trade_case,
    build_trade_decision_packet,
    build_trade_outcome_receipt,
    build_trade_thesis,
    build_triaxis_trade_audit_request,
    normalize_triaxis_adjudication,
)


class TradingOSShadowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.case = build_trade_case(
            case_id="trade-001",
            frozen_at="2026-08-19T15:00:00Z",
            symbol="BTCUSDT",
            venue="Binance",
            timeframe="1h",
            scenario="BTC tests resistance after an upside sweep.",
            snapshot_ref={
                "source_id": "snapshot:001",
                "sha256": "a" * 64,
                "schema": "tradingos.market_snapshot.v1",
            },
            vision_ref={
                "source_id": "vision:001",
                "sha256": "b" * 64,
                "schema": "tradingos.visual_market_evidence.v1",
            },
        )

    @staticmethod
    def twin(probs, *, prediction_id="pred", authority="NONE", can_execute=False):
        max_p = max(probs.values())
        leaders = [option for option, p in probs.items() if abs(p - max_p) <= 1e-15]
        predicted = leaders[0] if len(leaders) == 1 else None
        return {
            "schema": "sct.prediction/v3",
            "prediction_id": prediction_id,
            "predicted_choice": predicted,
            "confidence": max_p,
            "option_probabilities": probs,
            "execution_authority": authority,
            "can_execute": can_execute,
        }

    def adjudication(self, verdict="PASS"):
        return normalize_triaxis_adjudication(
            case_id="trade-001",
            verdict=verdict,
            strongest_case=[],
            falsifiers=[],
            surviving_claims=[],
            evidence_refs=[],
        )

    def test_builds_shadow_only_case(self):
        self.assertEqual(self.case["safety"], SHADOW_SAFETY)
        self.assertEqual(self.case["human_decision_status"], "UNREVEALED")
        self.assertEqual(tuple(self.case["options"]), ("LONG", "SHORT", "WAIT"))

    def test_wait_option_is_mandatory(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "wait_option_required"):
            build_trade_case(
                case_id="trade-no-wait",
                frozen_at="2026-08-19T15:00:00Z",
                symbol="BTCUSDT",
                venue="Binance",
                timeframe="1h",
                scenario="Unsafe option set.",
                snapshot_ref={
                    "source_id": "snapshot:no-wait",
                    "sha256": "c" * 64,
                    "schema": "tradingos.market_snapshot.v1",
                },
                options=("LONG", "SHORT"),
            )

    def test_triaxis_request_is_evidence_first_and_non_executing(self):
        thesis = build_trade_thesis(
            self.case,
            {"schema": "tradingos.decision_brief.v2", "status": "READY", "stance": "WATCH_LONG"},
        )
        request = build_triaxis_trade_audit_request(self.case, thesis)
        self.assertEqual(request["schema"], "triaxis.trade_audit_request.v1")
        self.assertEqual(request["candidate_action"], "LONG")
        self.assertEqual(len(request["evidence_refs"]), 2)
        self.assertIn("direct_falsification", request["protocol"])
        self.assertEqual(request["constraints"]["countermodel_default"], False)
        self.assertEqual(request["constraints"]["triaxis_is_oracle"], False)
        self.assertEqual(request["execution_authority"], "NONE")
        self.assertFalse(request["can_execute"])

    def test_packet_forces_wait_when_triaxis_rejects(self):
        thesis = build_trade_thesis(
            self.case,
            {"schema": "tradingos.decision_brief.v2", "status": "READY", "stance": "WATCH_LONG"},
        )
        packet = build_trade_decision_packet(
            self.case,
            thesis,
            self.twin({"LONG": 0.7, "SHORT": 0.1, "WAIT": 0.2}, prediction_id="pred-001"),
            self.adjudication("REJECT"),
            {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
        )
        self.assertEqual(packet["system_recommendation"], "WAIT")
        self.assertEqual(packet["recommendation_reason"], "TRIAXIS_REJECT")
        self.assertTrue(packet["divergence"])
        self.assertEqual(packet["divergence_status"], "DEFINED")
        self.assertEqual(tuple(packet["options"]), ("LONG", "SHORT", "WAIT"))
        self.assertFalse(packet["safety"]["can_trade"])

    def test_risk_veto_beats_pass_and_twin_tie_is_not_lexicographically_resolved(self):
        thesis = build_trade_thesis(self.case, {"stance": "WATCH_LONG"})
        packet = build_trade_decision_packet(
            self.case,
            thesis,
            self.twin({"LONG": 0.4, "SHORT": 0.2, "WAIT": 0.4}, prediction_id="pred-002"),
            self.adjudication("PASS"),
            {"veto": True, "reasons": ["daily loss limit"], "can_trade": False, "capital_permission": "DENY"},
        )
        self.assertEqual(packet["system_recommendation"], "WAIT")
        self.assertEqual(packet["recommendation_reason"], "RISK_VETO")
        self.assertIsNone(packet["twin"]["predicted_choice"])
        self.assertEqual(packet["twin"]["prediction_status"], "TIE")
        self.assertIsNone(packet["divergence"])
        self.assertEqual(packet["divergence_status"], "UNDEFINED_TWIN_TIE")

    def test_unsafe_twin_authority_is_rejected(self):
        thesis = build_trade_thesis(self.case, {"stance": "WAIT"})
        twin = self.twin(
            {"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6},
            prediction_id="pred-003",
            authority="TRADE",
            can_execute=True,
        )
        with self.assertRaises(ShadowIntegrationError):
            build_trade_decision_packet(
                self.case,
                thesis,
                twin,
                self.adjudication("HOLD"),
                {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
            )

    def test_stale_sct_v2_schema_is_rejected(self):
        thesis = build_trade_thesis(self.case, {"stance": "WAIT"})
        twin = self.twin({"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6}, prediction_id="pred-v2")
        twin["schema"] = "sct.prediction/v2"
        with self.assertRaisesRegex(ShadowIntegrationError, "unsupported_twin_prediction_schema"):
            build_trade_decision_packet(
                self.case,
                thesis,
                twin,
                self.adjudication("PASS"),
                {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
            )

    def test_tampered_triaxis_adjudication_hash_is_rejected(self):
        thesis = build_trade_thesis(self.case, {"stance": "WAIT"})
        adjudication = self.adjudication("PASS")
        adjudication["verdict"] = "REJECT"
        with self.assertRaisesRegex(ShadowIntegrationError, "triaxis_adjudication_hash_mismatch"):
            build_trade_decision_packet(
                self.case,
                thesis,
                self.twin({"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6}),
                adjudication,
                {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
            )

    def test_outcome_rejects_choice_outside_case_options(self):
        thesis = build_trade_thesis(self.case, {"stance": "WAIT"})
        packet = build_trade_decision_packet(
            self.case,
            thesis,
            self.twin({"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6}, prediction_id="pred-outside"),
            self.adjudication("PASS"),
            {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "actual_choice_outside_case_options"):
            build_trade_outcome_receipt(
                packet,
                actual_choice="HEDGE",
                decided_at="2026-08-19T15:05:00Z",
                market_outcome={"quality_label": "N/A"},
            )

    def test_outcome_keeps_prediction_and_quality_separate(self):
        thesis = build_trade_thesis(self.case, {"stance": "WATCH_LONG"})
        packet = build_trade_decision_packet(
            self.case,
            thesis,
            self.twin({"LONG": 0.8, "SHORT": 0.1, "WAIT": 0.1}, prediction_id="pred-004"),
            self.adjudication("PASS"),
            {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
        )
        receipt = build_trade_outcome_receipt(
            packet,
            actual_choice="LONG",
            decided_at="2026-08-19T15:05:00Z",
            market_outcome={"pnl_r": -1.0, "quality_label": "LOSS"},
        )
        self.assertTrue(receipt["twin_fidelity_match"])
        self.assertEqual(receipt["twin_fidelity_status"], "SCORABLE")
        self.assertEqual(receipt["market_outcome"]["pnl_r"], -1.0)
        self.assertFalse(receipt["safety"]["can_trade"])

    def test_tie_prediction_is_unscorable_not_wrong(self):
        thesis = build_trade_thesis(self.case, {"stance": "WATCH_LONG"})
        packet = build_trade_decision_packet(
            self.case,
            thesis,
            self.twin({"LONG": 0.4, "SHORT": 0.2, "WAIT": 0.4}, prediction_id="pred-tie"),
            self.adjudication("HOLD"),
            {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
        )
        receipt = build_trade_outcome_receipt(
            packet,
            actual_choice="LONG",
            decided_at="2026-08-19T15:05:00Z",
            market_outcome={"quality_label": "N/A"},
        )
        self.assertIsNone(receipt["twin_fidelity_match"])
        self.assertEqual(receipt["twin_fidelity_status"], "UNSCORABLE_TIE")

    def test_tampered_decision_packet_fails_before_outcome(self):
        thesis = build_trade_thesis(self.case, {"stance": "WAIT"})
        packet = build_trade_decision_packet(
            self.case,
            thesis,
            self.twin({"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6}, prediction_id="pred-tamper"),
            self.adjudication("PASS"),
            {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
        )
        packet["system_recommendation"] = "LONG"
        with self.assertRaisesRegex(ShadowIntegrationError, "decision_packet_hash_mismatch"):
            build_trade_outcome_receipt(
                packet,
                actual_choice="WAIT",
                decided_at="2026-08-19T15:05:00Z",
                market_outcome={"quality_label": "N/A"},
            )


if __name__ == "__main__":
    unittest.main()
