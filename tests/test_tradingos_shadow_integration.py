import unittest

from tools.tradingos_shadow_integration import (
    SHADOW_SAFETY,
    ShadowIntegrationError,
    build_trade_case,
    build_trade_decision_packet,
    build_trade_outcome_receipt,
    build_trade_thesis,
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
                "schema": "visionassist.market_observation.v1",
            },
        )

    def test_builds_shadow_only_case(self):
        self.assertEqual(self.case["safety"], SHADOW_SAFETY)
        self.assertEqual(self.case["human_decision_status"], "UNREVEALED")
        self.assertEqual(tuple(self.case["options"]), ("LONG", "SHORT", "WAIT"))

    def test_packet_forces_wait_when_triaxis_rejects(self):
        thesis = build_trade_thesis(
            self.case,
            {"schema": "tradingos.decision_brief.v2", "status": "READY", "stance": "WATCH_LONG"},
        )
        adjudication = normalize_triaxis_adjudication(
            case_id="trade-001",
            verdict="REJECT",
            strongest_case=["trend and spot flow support long"],
            falsifiers=["resistance rejection invalidates entry"],
            surviving_claims=["higher timeframe trend remains bullish"],
            evidence_refs=["snapshot:001", "vision:001"],
        )
        twin = {
            "schema": "sct.prediction/v2",
            "prediction_id": "pred-001",
            "option_probabilities": {"LONG": 0.7, "SHORT": 0.1, "WAIT": 0.2},
            "execution_authority": "NONE",
            "can_execute": False,
        }
        packet = build_trade_decision_packet(
            self.case,
            thesis,
            twin,
            adjudication,
            {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
        )
        self.assertEqual(packet["system_recommendation"], "WAIT")
        self.assertEqual(packet["recommendation_reason"], "TRIAXIS_REJECT")
        self.assertTrue(packet["divergence"])
        self.assertFalse(packet["safety"]["can_trade"])

    def test_risk_veto_beats_pass(self):
        thesis = build_trade_thesis(self.case, {"stance": "WATCH_LONG"})
        adjudication = normalize_triaxis_adjudication(
            case_id="trade-001",
            verdict="PASS",
            strongest_case=[],
            falsifiers=[],
            surviving_claims=[],
            evidence_refs=[],
        )
        twin = {
            "prediction_id": "pred-002",
            "option_probabilities": {"LONG": 0.4, "SHORT": 0.2, "WAIT": 0.4},
            "execution_authority": "NONE",
            "can_execute": False,
        }
        packet = build_trade_decision_packet(
            self.case,
            thesis,
            twin,
            adjudication,
            {"veto": True, "reasons": ["daily loss limit"], "can_trade": False, "capital_permission": "DENY"},
        )
        self.assertEqual(packet["system_recommendation"], "WAIT")
        self.assertEqual(packet["recommendation_reason"], "RISK_VETO")

    def test_unsafe_twin_authority_is_rejected(self):
        thesis = build_trade_thesis(self.case, {"stance": "WAIT"})
        adjudication = normalize_triaxis_adjudication(
            case_id="trade-001",
            verdict="HOLD",
            strongest_case=[],
            falsifiers=[],
            surviving_claims=[],
            evidence_refs=[],
        )
        twin = {
            "prediction_id": "pred-003",
            "option_probabilities": {"LONG": 0.2, "SHORT": 0.2, "WAIT": 0.6},
            "execution_authority": "TRADE",
            "can_execute": True,
        }
        with self.assertRaises(ShadowIntegrationError):
            build_trade_decision_packet(
                self.case,
                thesis,
                twin,
                adjudication,
                {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
            )

    def test_outcome_keeps_prediction_and_quality_separate(self):
        thesis = build_trade_thesis(self.case, {"stance": "WATCH_LONG"})
        adjudication = normalize_triaxis_adjudication(
            case_id="trade-001",
            verdict="PASS",
            strongest_case=[],
            falsifiers=[],
            surviving_claims=[],
            evidence_refs=[],
        )
        twin = {
            "prediction_id": "pred-004",
            "option_probabilities": {"LONG": 0.8, "SHORT": 0.1, "WAIT": 0.1},
            "execution_authority": "NONE",
            "can_execute": False,
        }
        packet = build_trade_decision_packet(
            self.case,
            thesis,
            twin,
            adjudication,
            {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
        )
        receipt = build_trade_outcome_receipt(
            packet,
            actual_choice="LONG",
            decided_at="2026-08-19T15:05:00Z",
            market_outcome={"pnl_r": -1.0, "quality_label": "LOSS"},
        )
        self.assertTrue(receipt["twin_fidelity_match"])
        self.assertEqual(receipt["market_outcome"]["pnl_r"], -1.0)
        self.assertFalse(receipt["safety"]["can_trade"])


if __name__ == "__main__":
    unittest.main()
