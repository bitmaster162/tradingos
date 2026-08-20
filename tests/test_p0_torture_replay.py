import copy
import unittest

from tools.tradingos_shadow_integration import (
    SHADOW_SAFETY,
    ShadowIntegrationError,
    build_trade_case,
    build_trade_decision_packet,
    build_trade_thesis,
    normalize_triaxis_adjudication,
    sha256_obj,
)
from tools.unified_shadow_trading_advisory import (
    ADVISORY_SPECS,
    build_default_trading_advisory_evidence,
    build_shadow_trading_advisory_ledger,
)
from tools.unified_shadow_cognition_plane import (
    build_default_cognition_evidence,
    build_shadow_cognition_proposal_ledger,
)
from tools.unified_shadow_human_interface import (
    build_default_interface_evidence,
    build_shadow_human_interface_ledger,
)
from tools.unified_shadow_product_service import (
    build_default_product_service_evidence,
    build_shadow_product_service_ledger,
    build_unified_shadow_product_service_closure,
)
from tools.unified_shadow_parked_nontrading import (
    build_default_parked_nontrading_evidence,
    build_shadow_parked_nontrading_ledger,
)
from tools.unified_shadow_executor_boundary import (
    build_default_executor_evidence,
    build_shadow_executor_boundary_receipt,
    build_unified_shadow_executor_closure,
)

CASE_FREEZE_EPOCH = 1_787_151_600.0


def _closure(schema, *, case_id="torture-001", tx="f" * 64):
    body = {
        "schema": schema,
        "closed_at": "2026-08-20T01:50:00+07:00",
        "case_id": case_id,
        "transaction_sha256": tx,
        "registered_node_count": 63,
        "effective_gate": "HOLD",
        "effective_action": "WAIT",
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {},
        "effect_summary": {
            "merge": False,
            "deploy": False,
            "runtime_activation": False,
            "current_truth_apply": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body


def _case():
    return build_trade_case(
        case_id="trade-torture-001",
        frozen_at="2026-08-19T15:00:00Z",
        symbol="BTCUSDT",
        venue="Binance",
        timeframe="1h",
        scenario="Frozen torture fixture.",
        snapshot_ref={"source_id": "snapshot:torture", "sha256": "a" * 64, "schema": "market.snapshot/v1"},
        vision_ref={"source_id": "vision:torture", "sha256": "b" * 64, "schema": "vision.market/v1"},
    )


def _twin(*, case_id="trade-torture-001", committed_at=CASE_FREEZE_EPOCH + 10.0):
    body = {
        "schema": "sct.prediction/v3",
        "case_id": case_id,
        "arm": "sct",
        "options": ("LONG", "SHORT", "WAIT"),
        "option_probabilities": {"LONG": 0.2, "SHORT": 0.1, "WAIT": 0.7},
        "predicted_choice": "WAIT",
        "confidence": 0.7,
        "reasons": (),
        "change_conditions": (),
        "would_escalate": False,
        "committed_at": committed_at,
        "execution_authority": "NONE",
        "can_execute": False,
    }
    body["prediction_id"] = sha256_obj(body)
    return body


def _triaxis(case_id="trade-torture-001", verdict="HOLD"):
    return normalize_triaxis_adjudication(
        case_id=case_id,
        verdict=verdict,
        strongest_case=(),
        falsifiers=(),
        surviving_claims=(),
        evidence_refs=(),
    )


class P0TortureReplayTests(unittest.TestCase):
    """Adversarial replay across the P0 shadow federation. No runtime/effect calls."""

    def test_t01_forged_trade_case_hash_is_rejected(self):
        case = _case()
        case["scenario"] = "tampered after freeze"
        with self.assertRaisesRegex(ShadowIntegrationError, "trade_case_hash_mismatch"):
            build_trade_thesis(case, {"stance": "WAIT"})

    def test_t02_cross_case_sct_prediction_is_rejected_even_if_self_hash_is_valid(self):
        case = _case()
        thesis = build_trade_thesis(case, {"stance": "WAIT"})
        with self.assertRaisesRegex(ShadowIntegrationError, "twin_case_mismatch"):
            build_trade_decision_packet(
                case,
                thesis,
                _twin(case_id="trade-other"),
                _triaxis(),
                {"veto": False, "can_trade": False, "capital_permission": "DENY"},
            )

    def test_t03_pre_freeze_sct_commit_is_rejected(self):
        case = _case()
        thesis = build_trade_thesis(case, {"stance": "WAIT"})
        with self.assertRaisesRegex(ShadowIntegrationError, "twin_prediction_precedes_case_freeze"):
            build_trade_decision_packet(
                case,
                thesis,
                _twin(committed_at=CASE_FREEZE_EPOCH - 1.0),
                _triaxis(),
                {"veto": False, "can_trade": False, "capital_permission": "DENY"},
            )

    def test_t04_risk_vector_cannot_smuggle_trade_authority(self):
        case = _case()
        thesis = build_trade_thesis(case, {"stance": "WAIT"})
        with self.assertRaisesRegex(ShadowIntegrationError, "unsafe_risk_vector"):
            build_trade_decision_packet(
                case,
                thesis,
                _twin(),
                _triaxis(),
                {"veto": False, "can_trade": True, "capital_permission": "ALLOW"},
            )

    def test_t05_post_freeze_advisory_proof_cannot_influence_frozen_case(self):
        base = _closure("bitevo.unified_shadow_closure.v6")
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:claude-bitunix"
        evidence[node]["case_relevance_verified"] = True
        for field in ADVISORY_SPECS[node]["required_bool_fields"]:
            evidence[node][field] = True
        evidence[node]["finding"] = "RISK_FLAG"
        evidence[node]["outcome"] = "FAIL"
        ledger = build_shadow_trading_advisory_ledger(base, evidence, generated_at="2026-08-20T01:51:00+07:00")
        receipt = next(row for row in ledger["receipts"] if row["node_id"] == node)
        self.assertFalse(receipt["pre_freeze_evidence_verified"])
        self.assertFalse(receipt["admitted_for_narrowing"])
        self.assertEqual(receipt["gate_effect"], "NONE")

    def test_t06_cognition_model_call_is_rejected(self):
        base = _closure("bitevo.unified_shadow_closure.v8")
        evidence = build_default_cognition_evidence()
        evidence["portfolio:arbiter-content-engine"]["model_call_performed"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "cognition_effect_boundary_breached"):
            build_shadow_cognition_proposal_ledger(base, evidence, generated_at="2026-08-20T01:52:00+07:00")

    def test_t07_interface_cannot_write_current_truth(self):
        base = _closure("bitevo.unified_shadow_closure.v9")
        evidence = build_default_interface_evidence()
        evidence["portfolio:unified-dashboard"]["current_truth_written"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "human_interface_effect_boundary_breached"):
            build_shadow_human_interface_ledger(base, evidence, generated_at="2026-08-20T01:53:00+07:00")

    def test_t08_product_plane_cannot_send_external_message(self):
        base = _closure("bitevo.unified_shadow_closure.v10")
        evidence = build_default_product_service_evidence()
        evidence["portfolio:ai-client-hunter"]["external_message_sent"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "product_service_effect_boundary_breached"):
            build_shadow_product_service_ledger(base, evidence, generated_at="2026-08-20T01:54:00+07:00")

    def test_t09_parked_plane_cannot_reactivate_wallet_effect(self):
        base = _closure("bitevo.unified_shadow_closure.v11")
        evidence = build_default_parked_nontrading_evidence()
        evidence["portfolio:parasite-killer"]["wallet_accessed"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "parked_effect_boundary_breached"):
            build_shadow_parked_nontrading_ledger(base, evidence, generated_at="2026-08-20T01:55:00+07:00")

    def test_t10_executor_caller_chosen_effect_class_bypass_is_rejected(self):
        base = _closure("bitevo.unified_shadow_closure.v12")
        evidence = build_default_executor_evidence()
        evidence["caller_chosen_effect_class_allowed"] = True
        with self.assertRaisesRegex(ShadowIntegrationError, "executor_effect_or_bypass_observed"):
            build_shadow_executor_boundary_receipt(base, evidence, generated_at="2026-08-20T01:56:00+07:00")

    def test_t11_rehashed_executor_self_merge_attempt_is_still_rejected(self):
        base = _closure("bitevo.unified_shadow_closure.v12")
        receipt = build_shadow_executor_boundary_receipt(
            base,
            build_default_executor_evidence(),
            generated_at="2026-08-20T01:57:00+07:00",
        )
        forged = copy.deepcopy(receipt)
        forged["may_self_merge"] = True
        forged["executor_receipt_sha256"] = sha256_obj(
            {k: v for k, v in forged.items() if k != "executor_receipt_sha256"}
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "executor_closure_disabled_invariant_breached"):
            build_unified_shadow_executor_closure(base, forged, closed_at="2026-08-20T01:58:00+07:00")

    def test_t12_rehashed_product_hold_to_pass_escalation_is_rejected_by_parent_binding(self):
        base = _closure("bitevo.unified_shadow_closure.v10")
        ledger = build_shadow_product_service_ledger(
            base,
            build_default_product_service_evidence(),
            generated_at="2026-08-20T01:59:00+07:00",
        )
        forged = copy.deepcopy(ledger)
        forged["base_gate"] = "PASS_SHADOW"
        forged["product_ledger_sha256"] = sha256_obj(
            {k: v for k, v in forged.items() if k != "product_ledger_sha256"}
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "product_service_closure_decision_mismatch"):
            build_unified_shadow_product_service_closure(base, forged, closed_at="2026-08-20T02:00:00+07:00")


if __name__ == "__main__":
    unittest.main()
