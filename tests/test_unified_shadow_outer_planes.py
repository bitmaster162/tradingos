import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, sha256_obj
from tools.unified_shadow_executor_boundary import (
    build_default_executor_evidence,
    build_shadow_executor_boundary_receipt,
    build_unified_shadow_executor_closure,
)
from tools.unified_shadow_parked_nontrading import (
    build_default_parked_nontrading_evidence,
    build_shadow_parked_nontrading_ledger,
    build_unified_shadow_parked_nontrading_closure,
)
from tools.unified_shadow_product_service import (
    build_default_product_service_evidence,
    build_shadow_product_service_ledger,
    build_unified_shadow_product_service_closure,
)


class UnifiedShadowOuterPlanesTests(unittest.TestCase):
    def test_v10_to_v13_preserves_hold_wait_and_zero_effects(self):
        base = {
            "schema": "bitevo.unified_shadow_closure.v10",
            "closed_at": "2026-08-20T01:40:00+07:00",
            "case_id": "outer-planes-e2e-001",
            "transaction_sha256": "d" * 64,
            "registered_node_count": 63,
            "effective_gate": "HOLD",
            "effective_action": "WAIT",
            "status": "P0_SHADOW_CLOSED_NO_EFFECT",
            "planes": {"human_interface_plane": "BOUND_3_OF_3_PRESENTATION_ONLY_NO_EFFECT"},
            "effect_summary": {"merge": False, "deploy": False, "signal": False, "order": False, "capital_effect": False},
            "safety": dict(SHADOW_SAFETY),
        }
        base["closure_sha256"] = sha256_obj(base)

        products = build_shadow_product_service_ledger(
            base,
            build_default_product_service_evidence(),
            generated_at="2026-08-20T01:41:00+07:00",
        )
        v11 = build_unified_shadow_product_service_closure(
            base, products, closed_at="2026-08-20T01:42:00+07:00"
        )

        parked = build_shadow_parked_nontrading_ledger(
            v11,
            build_default_parked_nontrading_evidence(),
            generated_at="2026-08-20T01:43:00+07:00",
        )
        v12 = build_unified_shadow_parked_nontrading_closure(
            v11, parked, closed_at="2026-08-20T01:44:00+07:00"
        )

        executor = build_shadow_executor_boundary_receipt(
            v12,
            build_default_executor_evidence(),
            generated_at="2026-08-20T01:45:00+07:00",
        )
        v13 = build_unified_shadow_executor_closure(
            v12, executor, closed_at="2026-08-20T01:46:00+07:00"
        )

        self.assertEqual(v11["schema"], "bitevo.unified_shadow_closure.v11")
        self.assertEqual(v12["schema"], "bitevo.unified_shadow_closure.v12")
        self.assertEqual(v13["schema"], "bitevo.unified_shadow_closure.v13")
        self.assertEqual((v13["effective_gate"], v13["effective_action"]), ("HOLD", "WAIT"))
        self.assertTrue(all(value is False for value in v13["effect_summary"].values()))
        self.assertFalse(v13["executor_status"]["executor_enabled"])
        self.assertEqual(v13["safety"]["capital_permission"], "DENY")
        self.assertFalse(v13["safety"]["can_trade"])


if __name__ == "__main__":
    unittest.main()
