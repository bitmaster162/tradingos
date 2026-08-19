import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_advisory_closure import build_unified_shadow_advisory_closure
from tools.unified_shadow_trading_advisory import (
    ADVISORY_SPECS,
    build_default_trading_advisory_evidence,
    build_shadow_trading_advisory_ledger,
)


class UnifiedShadowAdvisoryClosureTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema": "bitevo.unified_shadow_closure.v6",
            "closed_at": "2026-08-20T00:44:00+07:00",
            "case_id": "trade-advisory-close-001",
            "transaction_sha256": "b" * 64,
            "registered_node_count": 63,
            "effective_gate": "HOLD",
            "effective_action": "WAIT",
            "status": "P0_SHADOW_CLOSED_NO_EFFECT",
            "planes": {"capability_influence_bus": "BOUND_63_OF_63_NO_EFFECT_AUTHORITY"},
            "effect_summary": {
                "merge": False,
                "deploy": False,
                "runtime_activation": False,
                "signal": False,
                "order": False,
                "capital_effect": False,
            },
            "safety": dict(SHADOW_SAFETY),
        }
        self.base["closure_sha256"] = sha256_obj(self.base)
        self.ledger = build_shadow_trading_advisory_ledger(
            self.base,
            build_default_trading_advisory_evidence(),
            generated_at="2026-08-20T00:45:00+07:00",
        )

    def close(self, base=None, ledger=None):
        return build_unified_shadow_advisory_closure(
            self.base if base is None else base,
            self.ledger if ledger is None else ledger,
            closed_at="2026-08-20T00:46:00+07:00",
        )

    def test_current_hold_is_preserved_and_no_advisory_effect_exists(self):
        closure = self.close()
        self.assertEqual(closure["schema"], "bitevo.unified_shadow_closure.v7")
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertEqual(closure["typed_advisory_node_count"], 5)
        self.assertEqual(closure["advisory_status"]["admitted_nodes"], 0)
        self.assertEqual(closure["advisory_status"]["not_admitted_nodes"], 5)
        self.assertEqual(closure["advisory_status"]["narrowing_nodes"], ())
        self.assertFalse(closure["advisory_status"]["gate_widening_allowed"])
        self.assertFalse(closure["advisory_status"]["execution_authority_granted"])
        self.assertTrue(all(value is False for value in closure["effect_summary"].values()))
        self.assertFalse(closure["safety"]["can_trade"])
        self.assertEqual(closure["safety"]["capital_permission"], "DENY")

    def test_verified_risk_narrows_pass_shadow_to_hold(self):
        base = copy.deepcopy(self.base)
        base["effective_gate"] = "PASS_SHADOW"
        base["effective_action"] = "REVIEW"
        base["closure_sha256"] = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:delist-drs"
        for field in ADVISORY_SPECS[node]["required_bool_fields"]:
            evidence[node][field] = True
        evidence[node]["finding"] = "RISK_FLAG"
        evidence[node]["outcome"] = "REPAIR"
        ledger = build_shadow_trading_advisory_ledger(
            base,
            evidence,
            generated_at="2026-08-20T00:47:00+07:00",
        )
        closure = self.close(base=base, ledger=ledger)
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertEqual(closure["advisory_status"]["narrowing_nodes"], (node,))

    def test_verified_no_objection_cannot_upgrade_hold(self):
        evidence = build_default_trading_advisory_evidence()
        node = "portfolio:grid-os"
        for field in ADVISORY_SPECS[node]["required_bool_fields"]:
            evidence[node][field] = True
        evidence[node]["finding"] = "NO_OBJECTION"
        evidence[node]["outcome"] = "KEEP"
        ledger = build_shadow_trading_advisory_ledger(
            self.base,
            evidence,
            generated_at="2026-08-20T00:48:00+07:00",
        )
        closure = self.close(ledger=ledger)
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")

    def test_tampered_advisory_receipt_is_rejected(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["receipts"][0]["execution_authority"] = "TRADE"
        ledger["receipts"][0]["advisory_receipt_sha256"] = sha256_obj(
            {k: v for k, v in ledger["receipts"][0].items() if k != "advisory_receipt_sha256"}
        )
        ledger["advisory_ledger_sha256"] = sha256_obj(
            {k: v for k, v in ledger.items() if k != "advisory_ledger_sha256"}
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "advisory_closure_execution_authority_detected"):
            self.close(ledger=ledger)

    def test_unadmitted_narrowing_is_rejected(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["receipts"][0]["gate_effect"] = "NARROW_TO_HOLD"
        ledger["receipts"][0]["advisory_receipt_sha256"] = sha256_obj(
            {k: v for k, v in ledger["receipts"][0].items() if k != "advisory_receipt_sha256"}
        )
        ledger["narrowing_nodes"] = (ledger["receipts"][0]["node_id"],)
        ledger["advisory_ledger_sha256"] = sha256_obj(
            {k: v for k, v in ledger.items() if k != "advisory_ledger_sha256"}
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "advisory_closure_unadmitted_narrowing"):
            self.close(ledger=ledger)

    def test_closure_is_deterministic(self):
        self.assertEqual(self.close(), self.close())


if __name__ == "__main__":
    unittest.main()
