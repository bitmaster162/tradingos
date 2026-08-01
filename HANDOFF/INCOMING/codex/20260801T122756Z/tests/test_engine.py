from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
FIXTURES = ROOT / "fixtures"
REGISTRY = ROOT / "registry" / "HYPOTHESIS_FAMILY_REGISTRY.json"
sys.path.insert(0, str(ENGINE))

from edge_research.catalog import (  # noqa: E402
    authorize_outcome_command,
    derive_readiness,
    validate_catalog,
    verify_preregistration_receipt,
)
from edge_research.common import ContractError, canonical_bytes, ensure_full_sha256  # noqa: E402
from edge_research.core import (  # noqa: E402
    CostModel,
    apply_costs,
    assign_time_splits,
    cluster_events,
    delayed_entry_sensitivity,
    environment_capture,
    extract_events,
    grouped_ablation,
    holm_adjust,
    moving_block_bootstrap_means,
    percentile,
    purge_training_overlap,
    sign_permutation_pvalue,
    stationary_bootstrap_means,
    tail_sensitivity,
    walk_forward_windows,
)
from edge_research.decision import decide, validate_terminal  # noqa: E402
from edge_research.duplicate import compare_registry  # noqa: E402
from edge_research.preregistration import (  # noqa: E402
    compile_preregistration,
    validate_preregistration,
)
from edge_research_cli import main as cli_main  # noqa: E402


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ContractTestCase(unittest.TestCase):
    def assert_contract_error(self, code: str, function, *args, **kwargs) -> ContractError:
        with self.assertRaises(ContractError) as raised:
            function(*args, **kwargs)
        self.assertEqual(code, raised.exception.code)
        return raised.exception


class DuplicateRegistryTests(ContractTestCase):
    def setUp(self) -> None:
        self.registry = load(REGISTRY)

    def test_renamed_m1_h2_is_blocked(self) -> None:
        candidate = load(FIXTURES / "DUPLICATE_CANDIDATE_RENAMED_M1_H2.json")
        result = compare_registry(candidate, self.registry)
        self.assertEqual("RENAMED_KILLED_FAMILY", result["classification"])
        self.assertEqual("M1_H02_BTC_SFP_ETH_SMT_TRIGGER", result["matched_existing_id"])

    def test_renamed_m1_h3_is_blocked(self) -> None:
        candidate = {
            "id": "NEW_RSI_TREND_RESTART",
            "family": "regime_hidden_rsi_continuation",
            "claim": "Hidden RSI divergence in an EMA regime predicts continuation after a confirmed pivot.",
            "causal_signature": ["hidden_rsi_divergence", "ema_regime", "confirmed_pivot", "trend_continuation"],
            "required_channels": ["btc_price"],
        }
        result = compare_registry(candidate, self.registry)
        self.assertEqual("RENAMED_KILLED_FAMILY", result["classification"])

    def test_exact_identifier_is_material_duplicate(self) -> None:
        candidate = copy.deepcopy(self.registry["hypotheses"][0])
        result = compare_registry(candidate, self.registry)
        self.assertEqual("MATERIAL_DUPLICATE", result["classification"])

    def test_partial_overlap_is_reported(self) -> None:
        candidate = {
            "id": "PARTIAL_COMPRESSION",
            "family": "volatility_compression_breakout",
            "claim": "Compression with a distinct order-flow observation is recorded without an outcome.",
            "causal_signature": ["compression", "new_flow_observation"],
            "required_channels": ["new_flow_channel"],
        }
        self.assertEqual("PARTIAL_OVERLAP", compare_registry(candidate, self.registry)["classification"])

    def test_materially_distinct_candidate(self) -> None:
        candidate = {
            "id": "SYNTH_UNRELATED",
            "family": "synthetic_unrelated_contract",
            "claim": "A deterministic synthetic checksum fixture validates a parser.",
            "causal_signature": ["checksum_fixture", "parser_validation"],
            "required_channels": ["synthetic_checksum"],
        }
        self.assertEqual("MATERIALLY_DISTINCT", compare_registry(candidate, self.registry)["classification"])

    def test_incomplete_candidate_fails_closed(self) -> None:
        result = compare_registry({"id": "EMPTY"}, self.registry)
        self.assertEqual("INSUFFICIENT_EVIDENCE", result["classification"])


class PreregistrationTests(ContractTestCase):
    def setUp(self) -> None:
        self.path = FIXTURES / "PREREGISTRATION_SYNTHETIC_VALID.json"
        self.document = load(self.path)

    def test_valid_fixture(self) -> None:
        self.assertEqual("VALID", validate_preregistration(self.document)["status"])

    def test_compilation_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            first = compile_preregistration(self.path, Path(left))
            second = compile_preregistration(self.path, Path(right))
            self.assertEqual(first["canonical_sha256"], second["canonical_sha256"])
            self.assertEqual(
                (Path(left) / "PREREGISTRATION_RECEIPT.json").read_bytes(),
                (Path(right) / "PREREGISTRATION_RECEIPT.json").read_bytes(),
            )

    def test_threshold_selected_after_final_read_is_rejected(self) -> None:
        self.document["event_contract"]["event_definition"] = "Threshold selected after final test read."
        self.assert_contract_error("MUTABLE_OR_PLACEHOLDER_RULE", validate_preregistration, self.document)

    def test_contaminated_oos_is_rejected(self) -> None:
        self.document["data_split"]["train"]["end"] = "2025-02-01T00:00:00Z"
        self.assert_contract_error("CONTAMINATED_OOS_INTERVAL", validate_preregistration, self.document)

    def test_short_purge_is_rejected(self) -> None:
        self.document["data_split"]["purge_embargo"]["purge_seconds"] = 1
        self.assert_contract_error("INSUFFICIENT_PURGE_EMBARGO", validate_preregistration, self.document)

    def test_missing_cost_field_is_rejected(self) -> None:
        del self.document["cost_model"]["slippage_bps"]
        self.assert_contract_error("MISSING_REQUIRED_FIELDS", validate_preregistration, self.document)

    def test_zero_economic_costs_are_rejected(self) -> None:
        for name in ("fees_bps", "spread_bps", "slippage_bps"):
            self.document["cost_model"][name] = 0
        self.assert_contract_error("MISSING_ECONOMIC_COSTS", validate_preregistration, self.document)

    def test_bad_multiple_testing_correction_is_rejected(self) -> None:
        self.document["statistical_plan"]["correction"] = "NONE"
        self.assert_contract_error("INVALID_MULTIPLE_TESTING_CORRECTION", validate_preregistration, self.document)

    def test_incomplete_terminal_rule_is_rejected(self) -> None:
        del self.document["decision_rule"]["INVALID_RESEARCH_RETURN"]
        self.assert_contract_error("INCOMPLETE_DECISION_RULE", validate_preregistration, self.document)

    def test_unsafe_effect_ceiling_is_rejected(self) -> None:
        self.document["effect_ceiling"]["can_trade"] = True
        self.assert_contract_error("UNSAFE_EFFECT_CEILING", validate_preregistration, self.document)

    def test_receipt_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as target:
            compile_preregistration(self.path, Path(target))
            canonical = load(Path(target) / "PREREGISTRATION_CANONICAL.json")
            receipt = load(Path(target) / "PREREGISTRATION_RECEIPT.json")
            receipt["canonical_sha256"] = "0" * 64
            self.assert_contract_error(
                "PREREGISTRATION_SHA_MISMATCH",
                verify_preregistration_receipt,
                canonical,
                receipt,
            )

    def test_receipt_compiler_identity_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as target:
            compile_preregistration(self.path, Path(target))
            canonical = load(Path(target) / "PREREGISTRATION_CANONICAL.json")
            receipt = load(Path(target) / "PREREGISTRATION_RECEIPT.json")
            receipt["compiler_version"] = "UNAPPROVED_COMPILER"
            self.assert_contract_error(
                "INVALID_PREREGISTRATION_RECEIPT_FORMAT",
                verify_preregistration_receipt,
                canonical,
                receipt,
            )


class CatalogGateTests(ContractTestCase):
    def setUp(self) -> None:
        self.catalog = load(FIXTURES / "EDGE_DATA_CATALOG_SYNTHETIC.json")
        self.hypothesis = load(FIXTURES / "PREREGISTRATION_SYNTHETIC_VALID.json")

    def compiled(self) -> tuple[dict, dict]:
        with tempfile.TemporaryDirectory() as target:
            compile_preregistration(FIXTURES / "PREREGISTRATION_SYNTHETIC_VALID.json", Path(target))
            return (
                load(Path(target) / "PREREGISTRATION_CANONICAL.json"),
                load(Path(target) / "PREREGISTRATION_RECEIPT.json"),
            )

    @staticmethod
    def approve(readiness: dict, task_id: str = "TRADING_EDGE_RESEARCH_ENGINE_M2B") -> None:
        readiness["controller_adjudication"] = {
            "status": "APPROVED",
            "controller_id": "GPT_CONTROLLER",
            "generation": "R64",
            "authorized_task_id": task_id,
            "task_sha256": "a" * 64,
            "adjudicated_at_utc": "2026-08-02T00:00:00Z",
            "outcome_budget": "ALLOW",
        }

    def test_catalog_validates(self) -> None:
        self.assertEqual("CATALOG_VALID", validate_catalog(self.catalog)["status"])

    def test_complete_catalog_derives_data_ready_but_pending(self) -> None:
        result = derive_readiness(self.hypothesis, self.catalog)
        self.assertEqual("DATA_READY", result["status"])
        self.assertEqual("PENDING", result["controller_adjudication"]["status"])
        self.assertEqual("DENY", result["controller_adjudication"]["outcome_budget"])

    def test_missing_one_channel_is_partial(self) -> None:
        self.catalog["raw_packets"].pop()
        self.assertEqual("PARTIAL_DATA", derive_readiness(self.hypothesis, self.catalog)["status"])

    def test_wrong_source_identity_cannot_satisfy_channel(self) -> None:
        self.catalog["raw_packets"][0]["source_id"] = "unapproved_spot_source"
        result = derive_readiness(self.hypothesis, self.catalog)
        self.assertEqual("PARTIAL_DATA", result["status"])
        self.assertEqual(["fixture_spot"], result["missing_source_ids"])

    def test_missing_all_channels_is_no_data(self) -> None:
        for packet in self.catalog["raw_packets"]:
            packet["channel"] = f"other_{packet['source_id']}"
        self.assertEqual("NO_DATA", derive_readiness(self.hypothesis, self.catalog)["status"])

    def test_bad_provenance_is_blocked(self) -> None:
        self.catalog["raw_packets"][0]["provenance_status"] = "UNKNOWN"
        self.assertEqual("PROVENANCE_BLOCKED", derive_readiness(self.hypothesis, self.catalog)["status"])

    def test_clock_skew_artifact_is_blocked(self) -> None:
        self.catalog["raw_packets"][0]["clock_skew_ms"] = 101
        self.assertEqual("PROVENANCE_BLOCKED", derive_readiness(self.hypothesis, self.catalog)["status"])

    def test_missingness_is_blocked_by_default(self) -> None:
        self.catalog["raw_packets"][0]["missingness_rate"] = 0.001
        self.assertEqual("PROVENANCE_BLOCKED", derive_readiness(self.hypothesis, self.catalog)["status"])

    def test_duplicate_rows_are_blocked_by_default(self) -> None:
        self.catalog["raw_packets"][0]["duplicate_rows"] = 1
        self.assertEqual("PROVENANCE_BLOCKED", derive_readiness(self.hypothesis, self.catalog)["status"])

    def test_stale_source_is_blocked(self) -> None:
        self.catalog["raw_packets"][0]["freshness_status"] = "STALE"
        self.assertEqual("PROVENANCE_BLOCKED", derive_readiness(self.hypothesis, self.catalog)["status"])

    def test_incomplete_join_coverage_is_partial(self) -> None:
        self.catalog["raw_packets"][0]["join_coverage"] = 0.98
        self.assertEqual("PARTIAL_DATA", derive_readiness(self.hypothesis, self.catalog)["status"])

    def test_duplicate_source_id_is_rejected(self) -> None:
        self.catalog["raw_packets"][1]["source_id"] = self.catalog["raw_packets"][0]["source_id"]
        self.assert_contract_error("DUPLICATE_SOURCE_ID", validate_catalog, self.catalog)

    def test_partial_sha_is_rejected(self) -> None:
        self.catalog["raw_packets"][0]["sha256"] = "abc"
        self.assert_contract_error("INVALID_SHA256", validate_catalog, self.catalog)

    def test_outcome_before_census_authorization_is_rejected(self) -> None:
        prereg, receipt = self.compiled()
        readiness = derive_readiness(prereg, self.catalog)
        self.assert_contract_error(
            "CONTROLLER_ADJUDICATION_MISSING",
            authorize_outcome_command,
            self.catalog,
            readiness,
            prereg,
            receipt,
        )

    def test_source_catalog_mutation_is_rejected(self) -> None:
        prereg, receipt = self.compiled()
        readiness = derive_readiness(prereg, self.catalog)
        self.approve(readiness)
        self.catalog["raw_packets"][0]["bytes"] += 1
        self.assert_contract_error(
            "SOURCE_CATALOG_MUTATION",
            authorize_outcome_command,
            self.catalog,
            readiness,
            prereg,
            receipt,
        )

    def test_forged_data_ready_is_rejected(self) -> None:
        prereg, receipt = self.compiled()
        self.catalog["raw_packets"].pop()
        readiness = derive_readiness(prereg, self.catalog)
        readiness["status"] = "DATA_READY"
        self.approve(readiness)
        self.assert_contract_error(
            "READINESS_DERIVATION_MISMATCH",
            authorize_outcome_command,
            self.catalog,
            readiness,
            prereg,
            receipt,
        )

    def test_m2a_cannot_authorize_itself(self) -> None:
        prereg, receipt = self.compiled()
        readiness = derive_readiness(prereg, self.catalog)
        self.approve(readiness, "TRADING_EDGE_RESEARCH_ENGINE_M2A")
        self.assert_contract_error(
            "M2A_OUTCOME_FORBIDDEN",
            authorize_outcome_command,
            self.catalog,
            readiness,
            prereg,
            receipt,
        )

    def test_future_controller_task_can_pass_authorization_gate_only(self) -> None:
        prereg, receipt = self.compiled()
        readiness = derive_readiness(prereg, self.catalog)
        self.approve(readiness)
        result = authorize_outcome_command(self.catalog, readiness, prereg, receipt)
        self.assertEqual("OUTCOME_COMMAND_AUTHORIZED", result["status"])
        self.assertFalse(result["can_trade"])


class ResearchCoreTests(ContractTestCase):
    def test_event_extraction_is_sorted(self) -> None:
        rows = [
            {"event_id": "b", "timestamp": 2, "active": True},
            {"event_id": "a", "timestamp": 1, "active": True},
            {"event_id": "c", "timestamp": 3, "active": False},
        ]
        self.assertEqual(["a", "b"], [row["event_id"] for row in extract_events(rows, lambda row: row["active"])])

    def test_duplicate_events_cannot_inflate_sample(self) -> None:
        rows = [
            {"event_id": "same", "timestamp": 1, "active": True},
            {"event_id": "same", "timestamp": 2, "active": True},
        ]
        self.assert_contract_error("DUPLICATE_EVENT_ID", extract_events, rows, lambda row: row["active"])

    def test_event_clustering_is_deterministic(self) -> None:
        events = load(FIXTURES / "SYNTHETIC_EVENTS.json")["events"]
        first = cluster_events(events, 20)
        second = cluster_events(list(reversed(events)), 20)
        self.assertEqual(first, second)
        self.assertEqual([2, 1, 1], [item["event_count"] for item in first])

    def test_time_splits_are_ordered(self) -> None:
        records = [{"timestamp": value} for value in (1, 12, 22, 99)]
        result = assign_time_splits(records, {"train": (0, 10), "validation": (11, 20), "final_test": (21, 30)})
        self.assertEqual([1], [item["timestamp"] for item in result["train"]])
        self.assertEqual([12], [item["timestamp"] for item in result["validation"]])
        self.assertEqual([22], [item["timestamp"] for item in result["final_test"]])

    def test_contaminated_numeric_splits_are_rejected(self) -> None:
        self.assert_contract_error(
            "CONTAMINATED_OOS_INTERVAL",
            assign_time_splits,
            [],
            {"train": (0, 10), "validation": (9, 20), "final_test": (21, 30)},
        )

    def test_purge_and_embargo_remove_overlap(self) -> None:
        samples = [{"outcome_end_timestamp": 80}, {"outcome_end_timestamp": 90}]
        self.assertEqual([80], [row["outcome_end_timestamp"] for row in purge_training_overlap(samples, 100, 5, 5)])

    def test_walk_forward_windows_are_frozen(self) -> None:
        result = walk_forward_windows(100, 40, 10, 10, 10)
        self.assertEqual((0, 40), result[0]["train"])
        self.assertEqual((50, 60), result[0]["final_test"])
        self.assertEqual(5, len(result))

    def test_costs_are_deducted_once(self) -> None:
        model = CostModel(10, 2, 3, 1)
        self.assertAlmostEqual(0.0084, apply_costs([0.01], model)[0])

    def test_delayed_entry_uses_later_snapshot(self) -> None:
        model = CostModel(0, 1, 1)
        result = delayed_entry_sensitivity([100, 101, 102, 103], [0], [1], [1], 1, model)
        self.assertAlmostEqual(102 / 101 - 1 - 0.0002, result["1"][0])

    def test_moving_block_bootstrap_replay_is_equal(self) -> None:
        first = moving_block_bootstrap_means([1, 2, 3, 4], 2, 20, 2202)
        second = moving_block_bootstrap_means([1, 2, 3, 4], 2, 20, 2202)
        self.assertEqual(first, second)

    def test_stationary_bootstrap_replay_is_equal(self) -> None:
        first = stationary_bootstrap_means([1, 2, 3, 4], 0.25, 20, 2202)
        second = stationary_bootstrap_means([1, 2, 3, 4], 0.25, 20, 2202)
        self.assertEqual(first, second)

    def test_permutation_replay_is_equal(self) -> None:
        first = sign_permutation_pvalue([0.1, -0.02, 0.04], 100, 2202)
        second = sign_permutation_pvalue([0.1, -0.02, 0.04], 100, 2202)
        self.assertEqual(first, second)

    def test_holm_correction_is_monotonic(self) -> None:
        adjusted = holm_adjust({"a": 0.01, "b": 0.04, "c": 0.03})
        self.assertEqual(0.03, adjusted["a"])
        self.assertGreaterEqual(adjusted["b"], adjusted["c"])

    def test_source_ablation_exposes_group_dependence(self) -> None:
        result = grouped_ablation([1.0, 1.0, -0.1], ["dominant", "dominant", "other"])
        self.assertEqual(-0.1, result["one_group_removed"]["dominant"]["mean"])

    def test_tail_sensitivity_exposes_one_event_domination(self) -> None:
        result = tail_sensitivity([0.01, 0.01, 0.01, 1.0])
        self.assertGreater(result["max_absolute_event_share"], 0.9)

    def test_percentile_interpolates(self) -> None:
        self.assertEqual(2.5, percentile([1, 2, 3, 4], 0.5))

    def test_environment_capture_has_no_trade_permission(self) -> None:
        capture = environment_capture(2202)
        self.assertEqual(2202, capture["seed"])
        self.assertFalse(capture["can_trade"])


class DecisionAndCliTests(ContractTestCase):
    def evidence(self) -> dict:
        return {
            "preregistration_valid": True,
            "source_provenance_valid": True,
            "source_hashes_match": True,
            "final_test_evaluated": True,
            "independent_sample_sufficient": True,
            "post_cost_expectancy": 0.01,
            "bootstrap_lower_bound": 0.001,
            "placebo_materially_weaker": True,
            "tail_risk_acceptable": True,
            "source_ablation_robust": True,
            "regime_ablation_robust": True,
            "leakage_detected": False,
        }

    def test_validation_only_cannot_keep(self) -> None:
        evidence = self.evidence()
        evidence["final_test_evaluated"] = False
        self.assertEqual("INSUFFICIENT_DATA", decide(evidence)["terminal"])

    def test_matching_placebo_kills_claim(self) -> None:
        evidence = self.evidence()
        evidence["placebo_materially_weaker"] = False
        self.assertEqual("KILL", decide(evidence)["terminal"])

    def test_leakage_invalidates_return(self) -> None:
        evidence = self.evidence()
        evidence["leakage_detected"] = True
        self.assertEqual("INVALID_RESEARCH_RETURN", decide(evidence)["terminal"])

    def test_synthetic_keep_is_measurement_only(self) -> None:
        result = decide(self.evidence())
        self.assertEqual("KEEP_FOR_FORWARD_PAPER", result["terminal"])
        self.assertFalse(result["strategy_accepted"])
        self.assertFalse(result["can_trade"])

    def test_unknown_terminal_is_rejected(self) -> None:
        self.assert_contract_error("INVALID_RESEARCH_TERMINAL", validate_terminal, "PROFITABLE")

    def test_cli_denies_outcome_before_controller_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as target:
            target_path = Path(target)
            compile_preregistration(FIXTURES / "PREREGISTRATION_SYNTHETIC_VALID.json", target_path)
            prereg_path = target_path / "PREREGISTRATION_CANONICAL.json"
            receipt_path = target_path / "PREREGISTRATION_RECEIPT.json"
            readiness = derive_readiness(load(prereg_path), load(FIXTURES / "EDGE_DATA_CATALOG_SYNTHETIC.json"))
            readiness_path = target_path / "READINESS.json"
            readiness_path.write_text(json.dumps(readiness), encoding="utf-8")
            stderr = StringIO()
            with redirect_stderr(stderr):
                exit_code = cli_main([
                    "outcome-run",
                    "--catalog", str(FIXTURES / "EDGE_DATA_CATALOG_SYNTHETIC.json"),
                    "--readiness", str(readiness_path),
                    "--prereg", str(prereg_path),
                    "--receipt", str(receipt_path),
                ])
            self.assertEqual(4, exit_code)
            self.assertIn("CONTROLLER_ADJUDICATION_MISSING", stderr.getvalue())

    def test_cli_never_computes_outcomes_even_after_future_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as target:
            target_path = Path(target)
            compile_preregistration(FIXTURES / "PREREGISTRATION_SYNTHETIC_VALID.json", target_path)
            prereg_path = target_path / "PREREGISTRATION_CANONICAL.json"
            receipt_path = target_path / "PREREGISTRATION_RECEIPT.json"
            catalog = load(FIXTURES / "EDGE_DATA_CATALOG_SYNTHETIC.json")
            readiness = derive_readiness(load(prereg_path), catalog)
            CatalogGateTests.approve(readiness)
            readiness_path = target_path / "READINESS.json"
            readiness_path.write_text(json.dumps(readiness), encoding="utf-8")
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = cli_main([
                    "outcome-run",
                    "--catalog", str(FIXTURES / "EDGE_DATA_CATALOG_SYNTHETIC.json"),
                    "--readiness", str(readiness_path),
                    "--prereg", str(prereg_path),
                    "--receipt", str(receipt_path),
                ])
            self.assertEqual(6, exit_code)
            payload = json.loads(stdout.getvalue())
            self.assertEqual("OUTCOME_EXECUTION_DEFERRED_TO_M2B", payload["status"])
            self.assertFalse(payload["outcomes_computed"])

    def test_full_synthetic_replay_is_byte_equal(self) -> None:
        events = load(FIXTURES / "SYNTHETIC_EVENTS.json")["events"]
        def replay() -> bytes:
            payload = {
                "clusters": cluster_events(events, 20),
                "bootstrap": moving_block_bootstrap_means([0.01, -0.02, 0.03], 2, 25, 2202),
                "adjusted": holm_adjust({"primary": 0.01, "placebo": 0.2}),
            }
            return canonical_bytes(payload)
        self.assertEqual(replay(), replay())


class CommonContractTests(ContractTestCase):
    def test_canonical_json_ignores_key_insertion_order(self) -> None:
        self.assertEqual(canonical_bytes({"b": 2, "a": 1}), canonical_bytes({"a": 1, "b": 2}))

    def test_full_sha_is_required(self) -> None:
        self.assert_contract_error("INVALID_SHA256", ensure_full_sha256, "abc", "test")

    def test_nan_is_not_canonical_json(self) -> None:
        self.assert_contract_error("NON_CANONICAL_JSON_VALUE", canonical_bytes, {"value": float("nan")})


if __name__ == "__main__":
    unittest.main(verbosity=2)
