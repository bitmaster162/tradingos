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
    sha256_obj,
)

CASE_FREEZE_EPOCH = 1_787_151_600.0
PREDICTION_COMMIT_EPOCH = CASE_FREEZE_EPOCH + 100.0


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
    def twin(
        probs,
        *,
        prediction_id=None,
        case_id="trade-001",
        authority="NONE",
        can_execute=False,
        committed_at=PREDICTION_COMMIT_EPOCH,
    ):
        max_p = max(probs.values())
        leaders = [option for option, p in probs.items() if abs(p - max_p) <= 1e-15]
        predicted = leaders[0] if len(leaders) == 1 else None
        body = {
            "schema": "sct.prediction/v3",
            "case_id": case_id,
            "arm": "sct",
            "options": ("LONG", "SHORT", "WAIT"),
            "option_probabilities": probs,
            "predicted_choice": predicted,
            "confidence": max_p,
            "reasons": (),
            "change_conditions": (),
            "would_escalate": False,
            "committed_at": committed_at,
            "execution_authority": authority,
            "can_execute": can_execute,
        }
        body["prediction_id"] = sha256_obj(body) if prediction_id is None else prediction_id
        return body

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

    def test_trade_case_requires_timezone_aware_freeze_timestamp(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "frozen_at_timezone_required"):
            build_trade_case(
                case_id="trade-naive-time",
                frozen_at="2026-08-19T15:00:00",
                symbol="BTCUSDT",
                venue="Binance",
                timeframe="1h",
                scenario="Naive timestamp is not a frozen evidence boundary.",
                snapshot_ref={
                    "source_id": "snapshot:naive",
                    "sha256": "c" * 64,
                    "schema": "tradingos.market_snapshot.v1",
                },
            )

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
            self.twin({"LONG": 0.7, "SHORT": 0.1, "WAIT": 0.2}),
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
            self.twin({"LONG": 0.4, "SHORT": 0.2, "WAIT": 0.4}),
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

    def test_cross_case_twin_is_rejected_even_with_valid_content_hash(self):
        thesis = build_trade_thesis(self.case, {"stance": "WAIT"})
        twin = self.twin({"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6}, case_id="trade-OTHER")
        with self.assertRaisesRegex(ShadowIntegrationError, "twin_case_mismatch"):
            build_trade_decision_packet(
                self.case,
                thesis,
                twin,
                self.adjudication("HOLD"),
                {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
            )

    def test_pre_freeze_twin_commit_is_rejected(self):
        thesis = build_trade_thesis(self.case, {"stance": "WAIT"})
        twin = self.twin(
            {"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6},
            committed_at=CASE_FREEZE_EPOCH - 1.0,
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "twin_prediction_precedes_case_freeze"):
            build_trade_decision_packet(
                self.case,
                thesis,
                twin,
                self.adjudication("HOLD"),
                {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
            )

    def test_tampered_twin_projection_with_stale_prediction_id_is_rejected(self):
        thesis = build_trade_thesis(self.case, {"stance": "WAIT"})
        twin = self.twin({"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6})
        twin["option_probabilities"] = {"LONG": 0.7, "SHORT": 0.1, "WAIT": 0.2}
        twin["predicted_choice"] = "LONG"
        twin["confidence"] = 0.7
        with self.assertRaisesRegex(ShadowIntegrationError, "twin_prediction_hash_mismatch"):
            build_trade_decision_packet(
                self.case,
                thesis,
                twin,
                self.adjudication("HOLD"),
                {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
            )

    def test_stale_sct_v2_schema_is_rejected(self):
        thesis = build_trade_thesis(self.case, {"stance": "WAIT"})
        twin = self.twin({"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6})
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
            self.twin({"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6}),
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
            self.twin({"LONG": 0.8, "SHORT": 0.1, "WAIT": 0.1}),
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
            self.twin({"LONG": 0.4, "SHORT": 0.2, "WAIT": 0.4}),
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
            self.twin({"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6}),
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
