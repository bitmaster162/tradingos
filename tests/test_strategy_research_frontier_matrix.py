import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.strategy_research_frontier_matrix import build_report


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class StrategyResearchFrontierMatrixTests(unittest.TestCase):
    def test_rejected_families_do_not_promote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "BASIS_SHOCK_REVERSION_NESTED_HOLDOUT_2026-06-29.json",
                {"decision": "reject_no_train_qualified_basis_shock_candidate", "tested": 10, "train_qualified": 0, "can_trade": False},
            )

            report = build_report(docs)

        shock = next(item for item in report["families"] if item["family"] == "basis_shock_reversion")
        self.assertEqual(shock["status"], "rejected_research_only")
        self.assertEqual(report["decision"], "no_promotable_strategy_family")
        self.assertFalse(report["can_trade"])

    def test_observer_only_family_routes_to_forward_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19.json",
                {
                    "generated_at": "2026-07-12T00:00:00Z",
                    "decision": "blocked_waiting_crowd_fade_forward_outcomes",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc))

        self.assertEqual(report["decision"], "observer_families_waiting_forward_outcomes")
        self.assertEqual(report["summary"]["observer_only"], 1)

    def test_stale_observer_is_not_counted_as_collecting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "ALT_BREADTH_DISLOCATION_FORWARD_OBSERVER_2026-07-03.json",
                {
                    "generated_at": "2026-07-03T00:00:00Z",
                    "decision": "alt_breadth_forward_observer_collecting_sample",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "alt_breadth_dislocation")
        self.assertEqual(item["status"], "observer_only_stale_not_running")
        self.assertFalse(item["report_fresh"])
        self.assertEqual(report["summary"]["observer_only"], 0)
        self.assertEqual(report["summary"]["stale_observers"], 1)
        self.assertEqual(report["decision"], "observer_runtime_truth_gap_detected")

    def test_guard_matrix_is_not_mislabeled_as_an_independent_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "TRADE_LEDGER_GUARD_MATRIX_2026-06-30.json",
                {
                    "generated_at": "2026-07-12T00:00:00Z",
                    "decision": "guard_candidates_need_forward_observer",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "guard_matrix_overlay")
        self.assertEqual(item["status"], "research_tool_not_independent_strategy")
        self.assertEqual(item["runtime_role"], "offline_candidate_discovery_only")
        self.assertEqual(report["summary"]["candidate_needs_observer_runtime"], 0)
        self.assertEqual(report["summary"]["research_tools_not_runtime"], 1)
        self.assertEqual(report["decision"], "no_promotable_strategy_family")

    def test_real_crowd_research_failure_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19.json",
                {
                    "generated_at": "2026-07-12T00:00:00Z",
                    "decision": "blocked_crowd_fade_research_gate_failed",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "crowd_fade")
        self.assertEqual(item["status"], "rejected_research_only")

    def test_forward_collecting_decision_is_active_when_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "LIQUIDATION_TIMING_VOL_FORWARD_OBSERVER_RUNNER_2026-07-03.json",
                {
                    "generated_at": "2026-07-12T00:00:00Z",
                    "decision": "liquidation_timing_vol_continuation_forward_collecting_sample",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "liquidation_timing_vol_continuation")
        self.assertEqual(item["status"], "observer_only_waiting_forward")
        self.assertTrue(item["report_fresh"])

    def test_force_order_runtime_truth_prefers_data_quality_over_newer_child_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30.json",
                {
                    "generated_at": "2026-07-12T00:00:00Z",
                    "decision": "liquidation_force_order_collecting_insufficient_sample",
                    "can_trade": False,
                },
            )
            write_json(
                docs / "FORCE_ORDER_LIQUIDATION_RESEARCH_PIPELINE_FIRST_EVENT_AUTO_2026-07-01.json",
                {
                    "generated_at": "2026-07-12T00:30:00Z",
                    "decision": "force_order_pipeline_collecting_sample",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "force_order_liquidation_context")
        self.assertEqual(item["status"], "observer_only_waiting_forward")
        self.assertIn("LIQUIDATION_FORCE_ORDER_DATA_QUALITY", item["path"])
        self.assertEqual(item["decision"], "liquidation_force_order_collecting_insufficient_sample")

    def test_force_order_runtime_truth_prefers_preregistered_progress_over_data_quality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS_2026-07-12.json",
                {
                    "generated_at": "2026-07-12T00:30:00Z",
                    "decision": "force_order_preregistered_progress_collecting",
                    "ready_for_pipeline": False,
                    "can_trade": False,
                },
            )
            write_json(
                docs / "LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-07-12.json",
                {
                    "generated_at": "2026-07-12T00:45:00Z",
                    "decision": "liquidation_force_order_data_ready_for_preregistered_research",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "force_order_liquidation_context")
        self.assertEqual(item["status"], "observer_only_waiting_forward")
        self.assertIn("LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS", item["path"])
        self.assertEqual(item["decision"], "force_order_preregistered_progress_collecting")
        self.assertFalse(item["can_trade"])

    def test_liquidation_book_replenishment_forward_lock_is_observer_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "LIQUIDATION_BOOK_REPLENISHMENT_FORWARD_OBSERVER_2026-07-12.json",
                {
                    "generated_at": "2026-07-12T10:05:00Z",
                    "decision": "liquidation_book_replenishment_waiting_first_post_lock_event",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 11, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "liquidation_book_replenishment")
        self.assertEqual(item["status"], "observer_only_waiting_forward")
        self.assertEqual(report["summary"]["promotable"], 0)
        self.assertFalse(item["can_trade"])

    def test_liquidation_book_independence_gate_is_preferred_runtime_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "LIQUIDATION_BOOK_REPLENISHMENT_FORWARD_OBSERVER_2026-07-12.json",
                {
                    "generated_at": "2026-07-12T10:05:00Z",
                    "decision": "liquidation_book_replenishment_passed_for_manual_review_only",
                    "can_trade": False,
                },
            )
            write_json(
                docs / "LIQUIDATION_BOOK_REPLENISHMENT_INDEPENDENCE_GATE_2026-07-12.json",
                {
                    "generated_at": "2026-07-12T10:06:00Z",
                    "decision": "liquidation_book_replenishment_independence_gate_collecting_base_sample",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 11, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "liquidation_book_replenishment")
        self.assertIn("INDEPENDENCE_GATE", item["path"])
        self.assertEqual(item["status"], "observer_only_waiting_forward")
        self.assertEqual(report["summary"]["promotable"], 0)

    def test_unsafe_boundary_is_prioritized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "TREND_MIX_NESTED_HOLDOUT_2026-06-29.json",
                {"decision": "oos_pass_candidate", "can_trade": True},
            )

            report = build_report(docs)

        self.assertEqual(report["decision"], "unsafe_boundary_detected")
        self.assertEqual(report["summary"]["unsafe"], 1)

    def test_preregistered_microstructure_queue_is_not_promotable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE_AUDIT_2026-06-29.json",
                {"decision": "microstructure_prereg_queue_valid", "summary": {"registered": 4}, "can_trade": False},
            )

            report = build_report(docs)

        item = next(row for row in report["families"] if row["family"] == "microstructure_prereg_queue")
        self.assertEqual(item["status"], "preregistered_waiting_snapshot")
        self.assertEqual(report["summary"]["preregistered"], 1)
        self.assertEqual(report["summary"]["promotable"], 0)

    def test_cross_asset_residual_train_failure_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "CROSS_ASSET_COINTEGRATION_RESIDUAL_NESTED_HOLDOUT_2026-07-12.json",
                {
                    "decision": "reject_train_gate_failed_validation_and_oos_unopened",
                    "can_trade": False,
                },
            )

            report = build_report(docs)

        item = next(row for row in report["families"] if row["family"] == "cross_asset_residual_reversion")
        self.assertEqual(item["status"], "rejected_research_only")
        self.assertEqual(report["decision"], "no_promotable_strategy_family")

    def test_large_trade_tail_terminal_tombstone_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "LARGE_TRADE_TAIL_TERMINAL_REVIEW_2026-07-13.json",
                {
                    "generated_at": "2026-07-13T04:00:00Z",
                    "decision": "reject_large_trade_tail_nonpositive_forward_economics_tombstone",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 13, 5, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "cross_venue_large_trade_tail")
        self.assertEqual(item["status"], "rejected_research_only")
        self.assertEqual(report["summary"]["promotable"], 0)

    def test_cross_venue_receipt_leadership_collecting_is_observer_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER_V3_2026-07-13.json",
                {
                    "generated_at": "2026-07-13T05:05:00Z",
                    "decision": "liquidation_cross_venue_paired_leadership_collecting_forward_sample",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "cross_venue_liquidation_receipt_leadership")
        self.assertEqual(item["status"], "observer_only_waiting_forward")
        self.assertEqual(report["summary"]["promotable"], 0)
        self.assertFalse(item["can_trade"])

    def test_cross_venue_receipt_leadership_candidate_still_needs_new_forward_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER_V3_2026-07-13.json",
                {
                    "generated_at": "2026-07-13T05:05:00Z",
                    "decision": "liquidation_cross_venue_paired_leadership_candidate_for_manual_price_impact_preregistration",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "cross_venue_liquidation_receipt_leadership")
        self.assertEqual(item["status"], "candidate_needs_forward_proof")
        self.assertEqual(report["summary"]["promotable"], 1)
        self.assertFalse(item["can_trade"])

    def test_deribit_options_runtime_collecting_is_observer_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "DERIBIT_OPTIONS_RESEARCH_RUNTIME_AUDIT_2026-07-12.json",
                {
                    "generated_at": "2026-07-12T00:00:00Z",
                    "decision": "deribit_options_stack_forward_collecting_readiness",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "deribit_options_skew_forward")
        self.assertEqual(item["status"], "observer_only_waiting_forward")
        self.assertEqual(report["summary"]["promotable"], 0)

    def test_bybit_v5r2_outcome_blind_collection_is_observer_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5R2_2026-07-18.json",
                {
                    "generated_at": "2026-07-15T12:00:00Z",
                    "decision": "bybit_liquidation_canonical_v5_collecting_outcome_blind_sample",
                    "outcome_review": {
                        "interim_outcomes_hidden": True,
                        "outcome_fields_computed": False,
                    },
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "bybit_liquidation_canonical_reversal_v5r2")
        self.assertEqual(item["status"], "observer_only_waiting_forward")
        self.assertTrue(item["report_fresh"])
        self.assertEqual(report["summary"]["promotable"], 0)
        self.assertFalse(item["can_trade"])

    def test_exogenous_liquidity_waiting_new_macro_date_is_observer_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_json(
                docs / "EXOGENOUS_LIQUIDITY_REGIME_FORWARD_OBSERVER_2026-07-12.json",
                {
                    "generated_at": "2026-07-12T10:35:00Z",
                    "decision": "exogenous_liquidity_regime_waiting_first_new_macro_date",
                    "can_trade": False,
                },
            )

            report = build_report(docs, as_of=datetime(2026, 7, 12, 11, 0, tzinfo=timezone.utc))

        item = next(row for row in report["families"] if row["family"] == "exogenous_liquidity_regime")
        self.assertEqual(item["status"], "observer_only_waiting_forward")
        self.assertEqual(report["summary"]["promotable"], 0)
        self.assertFalse(item["can_trade"])


if __name__ == "__main__":
    unittest.main()
