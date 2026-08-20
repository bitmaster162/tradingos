import unittest

from tools.unified_shadow_federation import SYSTEM_IDS
from tools.unified_shadow_router import PLANE_MEMBERS, PLANE_ORDER, build_trade_case_route


class UnifiedShadowRouterTests(unittest.TestCase):
    def test_every_registered_node_is_assigned_exactly_once(self):
        assigned = [node for plane in PLANE_ORDER for node in PLANE_MEMBERS[plane]]
        self.assertEqual(len(assigned), len(SYSTEM_IDS))
        self.assertEqual(len(set(assigned)), len(SYSTEM_IDS))
        self.assertEqual(set(assigned), set(SYSTEM_IDS))

    def test_route_accounts_for_all_63_without_external_runtime_calls(self):
        route = build_trade_case_route(case_id="trade-route-001", case_sha256="a" * 64)
        self.assertEqual(route["registered_node_count"], 63)
        self.assertTrue(route["all_nodes_assigned_exactly_once"])
        all_nodes = route["active_nodes"] + route["accounted_noninvoked_nodes"]
        self.assertEqual(len(all_nodes), 63)
        self.assertEqual(set(all_nodes), set(SYSTEM_IDS))
        for plane in route["planes"]:
            for node in plane["nodes"]:
                self.assertFalse(node["external_runtime_called"])
                self.assertEqual(node["execution_authority"], "NONE")
        self.assertFalse(route["executor_boundary"]["enabled"])
        self.assertEqual(route["executor_boundary"]["reason"], "P0_SHADOW_NO_EFFECT")
        self.assertEqual(route["safety"]["capital_permission"], "DENY")
        self.assertFalse(route["safety"]["can_trade"])

    def test_trading_route_contains_governance_memory_cognition_audit_and_domain_planes(self):
        route = build_trade_case_route(case_id="trade-route-002", case_sha256="b" * 64)
        planes = {row["plane"]: {node["node_id"] for node in row["nodes"]} for row in route["planes"]}
        self.assertIn("portfolio:control-canter", planes["AUTHORITY_AND_INTERFACE"])
        self.assertIn("portfolio:continuityos", planes["EVIDENCE_AND_CONTINUITY"])
        self.assertIn("entity:sct", planes["COGNITION_AND_PERSONALIZATION"])
        self.assertIn("entity:triaxis", planes["PERCEPTION_SIMULATION_AND_AUDIT"])
        self.assertIn("portfolio:tradingos", planes["TRADING_INTELLIGENCE"])
        self.assertIn("entity:executor_network", planes["EXECUTOR_BOUNDARY"])

    def test_nontrading_projects_remain_accounted_but_not_falsely_invoked(self):
        route = build_trade_case_route(case_id="trade-route-003", case_sha256="c" * 64)
        noninvoked = set(route["accounted_noninvoked_nodes"])
        for expected in (
            "portfolio:parasite-killer",
            "portfolio:crypto-guides",
            "portfolio:openclaw",
            "portfolio:amora",
            "portfolio:rtf-starcoin",
        ):
            self.assertIn(expected, noninvoked)


if __name__ == "__main__":
    unittest.main()
