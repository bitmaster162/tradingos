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
from tools.unified_shadow_closure import build_unified_shadow_closure
from tools.unified_shadow_control_plane import build_shadow_control_plane_receipt
from tools.unified_shadow_federation import build_default_shadow_contributions, build_unified_shadow_receipt
from tools.unified_shadow_router import build_trade_case_route
from tools.unified_shadow_transaction import build_unified_shadow_transaction


class UnifiedShadowClosureTests(unittest.TestCase):
    def setUp(self):
        self.case = build_trade_case(
            case_id="trade-closure-001",
            frozen_at="2026-08-19T17:00:00Z",
            symbol="BTCUSDT",
            venue="Binance",
            timeframe="1h",
            scenario="Offline full-system closure fixture.",
            snapshot_ref={"source_id": "snapshot:closure", "sha256": "a" * 64, "schema": "tradingos.market_snapshot.v1"},
            vision_ref={"source_id": "vision:closure", "sha256": "b" * 64, "schema": "tradingos.visual_market_evidence.v1"},
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
            evidence_refs=["snapshot:closure", "vision:closure"],
        )
        twin = {
            "schema": "sct.prediction/v3",
            "case_id": self.case["case_id"],
            "arm": "sct",
            "options": tuple(self.case["options"]),
            "option_probabilities": {"LONG": 0.7, "SHORT": 0.1, "WAIT": 0.2},
            "predicted_choice": "LONG",
            "confidence": 0.7,
            "reasons": (),
            "change_conditions": (),
            "would_escalate": False,
            "committed_at": 1_787_158_900.0,
            "execution_authority": "NONE",
            "can_execute": False,
        }
        twin["prediction_id"] = sha256_obj(twin)
        self.packet = build_trade_decision_packet(
            self.case,
            thesis,
            twin,
            adjudication,
            {"veto": False, "reasons": [], "can_trade": False, "capital_permission": "DENY"},
        )
        contributions = build_default_shadow_contributions(
            case_id=self.case["case_id"],
            case_sha256=self.case["case_sha256"],
            packet_sha256=self.packet["packet_sha256"],
        )
        federation = build_unified_shadow_receipt(
            self.case,
            self.packet,
            contributions,
            generated_at="2026-08-19T17:01:00Z",
        )
        route = build_trade_case_route(case_id=self.case["case_id"], case_sha256=self.case["case_sha256"])
        control = build_shadow_control_plane_receipt(
            self.case,
            self.packet,
            control_center_ref={
                "repo": "bitmaster162/control-center",
                "pr_number": 30,
                "head_sha": "9c3f3642211501867b8f089decb3b9b6166de350",
                "state": "OPEN_DRAFT_UNMERGED",
                "draft": True,
                "merged": False,
            },
            continuityos_source_ref={
                "repo": "bitmaster162/continuityos",
                "branch": "master",
                "head_sha": "9dfb9e5b847a27113ca7c709a0adee900e3ff63f",
            },
            sct_adapter_ref={
                "repo": "bitmaster162/continuityos",
                "pr_number": 91,
                "head_sha": "a0a244d40f0a2aa500df45b1f846f0d863a77749",
                "state": "OPEN_DRAFT_UNMERGED",
                "draft": True,
                "merged": False,
            },
            provider_capture_at="2026-08-12T04:59:00+07:00",
            lease_expires_at="2026-08-12T10:59:00+07:00",
            evaluated_at="2026-08-19T23:50:00+07:00",
            generated_at="2026-08-19T23:50:00+07:00",
            anti_amnesia_context_sha256="c" * 64,
        )
        self.transaction = build_unified_shadow_transaction(
            self.case,
            self.packet,
            federation,
            route,
            control,
            frozen_at="2026-08-19T17:02:00Z",
        )
        self.continuity = self._continuity_fixture()
        self.return_intake = self._return_intake_fixture()
        self.projection = self._projection_fixture()
        self.hanri = self._hanri_fixture()

    def _continuity_fixture(self):
        body = {
            "schema": "continuityos.shadow_continuity_receipt.v1",
            "source_transaction_sha256": self.transaction["transaction_sha256"],
            "case_id": self.transaction["case_id"],
            "disposition": "HOLD_SHADOW_NO_WRITE",
            "modern_source": {
                "repo": "bitmaster162/continuityos",
                "branch": "master",
                "head_sha": "9dfb9e5b847a27113ca7c709a0adee900e3ff63f",
                "claim_dimension": "SOURCE_IDENTITY",
                "claim_ceiling": "MODERN_GITHUB_SOURCE_ONLY",
                "proves_live_runtime": False,
                "proves_current_host_state": False,
            },
            "historical_lineage": {
                "r52_local_adoption": {"claim_ceiling": "LOCAL_CONTROL_LIBRARY_ADOPTION_ONLY", "proves_live_runtime": False},
                "r57_runtime_preflight": {"terminal": "REVISE", "claim_ceiling": "PREFLIGHT_ONLY", "proves_live_runtime": False},
                "live_host_state": "UNVERIFIED",
            },
            "checkpoint_candidate": {"write_allowed": False},
            "replay_candidate": {"apply_allowed": False},
            "return_candidate": {"semantic_acceptance": "NOT_PERFORMED", "write_allowed": False},
            "writes": {
                "event_append": False,
                "memory_write": False,
                "checkpoint_write": False,
                "replay_write": False,
                "return_broker_write": False,
                "archive_write": False,
                "runtime_activation": False,
                "pointer_update": False,
            },
            "authority": {
                "execution_authority": "NONE",
                "apply_authorized": False,
                "human_authority_required_for_effects": True,
            },
            "semantics": {"checkpoint_candidate_is_not_canonical_checkpoint": True},
            "safety": dict(SHADOW_SAFETY),
        }
        body["continuity_receipt_sha256"] = sha256_obj(body)
        return body

    def _return_intake_fixture(self):
        body = {
            "schema": "control_return_broker.shadow_intake_receipt.v1",
            "source_transaction_sha256": self.transaction["transaction_sha256"],
            "continuity_receipt_sha256": self.continuity["continuity_receipt_sha256"],
            "slot": "WORK",
            "work_order_id": "P0-SHADOW-CLOSURE-001",
            "zip_sha256": "f" * 64,
            "zip_bytes": 128,
            "physical_verification": {
                "passed": True,
                "sidecar_match": True,
                "crc_status": "PASS",
                "duplicate_members": [],
                "unsafe_members": [],
                "ready_json_valid": True,
                "ready_last_mtime": True,
            },
            "physical_status": "VERIFIED_READ_ONLY",
            "transport": {
                "publish_performed": False,
                "collect_performed": False,
                "incoming_write": False,
                "slot_pointer_write": False,
                "registry_write": False,
                "generation_promotion": False,
                "controller_bundle_sealed": False,
                "drive_write": False,
            },
            "semantic_acceptance": "NOT_PERFORMED",
            "content_acceptance_claimed": False,
            "source_bytes_unchanged": True,
            "authority": {"execution_authority": "NONE", "apply_authorized": False},
            "safety": dict(SHADOW_SAFETY),
        }
        body["shadow_intake_sha256"] = sha256_obj(body)
        return body

    def _projection_fixture(self):
        body = {
            "schema": "control_center.unified_shadow_projection.v1",
            "projection_kind": "NON_AUTHORITY_SHADOW_PROJECTION",
            "source_transaction_sha256": self.transaction["transaction_sha256"],
            "case_id": self.transaction["case_id"],
            "registered_node_count": 63,
            "authority_reference": {"generation": "R64"},
            "authority_freshness": {
                "provider_backed_status": "STALE",
                "continuous_freshness_claimed": False,
                "attention_required": True,
            },
            "decision_view": {
                "system_recommendation": self.transaction["system_recommendation"],
                "control_gate": "HOLD",
                "control_plane_action": "WAIT",
                "disposition": "HOLD_NO_APPLY",
            },
            "mutations": {
                "current_truth": False,
                "command_queue": False,
                "decision_ledger": False,
                "return_registry": False,
                "human_gate": False,
                "runtime": False,
                "trading": False,
                "capital": False,
            },
            "human_sovereign": True,
            "apply": False,
            "effect_candidates_created": 0,
            "executions_authorized": 0,
            "semantics": {"projection_is_not_current_truth": True},
            "safety": dict(SHADOW_SAFETY),
        }
        body["projection_sha256"] = sha256_obj(body)
        return body

    def _hanri_fixture(self):
        body = {
            "schema": "hanri.shadow-evidence-governor.receipt/v1",
            "generated_at": "2026-08-20T00:26:00+07:00",
            "source_transaction_sha256": self.transaction["transaction_sha256"],
            "case_id": self.transaction["case_id"],
            "human_sovereign": True,
            "authority_reference": {"generation": "R64"},
            "hanri_source": {
                "repo": "bitmaster162/control-center",
                "branch": "hanri/r37-product-pilot-accepted",
                "head_sha": "ef5c504179de8ae8c16bd70c168b14b79bd2f466",
                "role": "BOUNDED_RUNTIME_ATTENTION_GOVERNOR_PROJECTION",
                "authority_root": False,
                "can_promote_self": False,
            },
            "archiveos": {
                "status": "BLOCKED_REVERIFY",
                "freshness": "STALE",
                "current_claim_allowed": False,
                "promotion_eligible": False,
                "proof_gap": ["fresh full archive-integrity receipt is missing"],
                "role": "NON_AUTHORITATIVE_EVIDENCE_VAULT",
                "canonical_root": "C:\\PROJECTS\\archiveos_api",
                "drive_role": "MIRROR_EVIDENCE_ONLY",
            },
            "archive_tooling": {
                "role": "ARTIFACT_COMPILER_NOT_ARCHIVE_ENGINE",
                "historical_handoff_sha256": "af9f06b74fa380a1b3e9c6bf69b871d17228abd70ae6c13f77ca8984836e0856",
                "authoritative_archive_engine": False,
                "semantic_acceptance_authority": False,
            },
            "knowledge_memory": {
                "claim_admission": "NOT_PERFORMED",
                "durable_memory_write": False,
                "project_canon_write": False,
                "current_truth_write": False,
                "reasoning_derivative_is_evidence": False,
                "memory_is_permission": False,
                "archive_bytes_are_current_truth": False,
            },
            "governor": {
                "gate": "HOLD",
                "action": "WAIT",
                "hold_reasons": ["UPSTREAM_CONTROL_GATE_HOLD", "ARCHIVEOS_BLOCKED_REVERIFY"],
                "attention_required": True,
                "promotion_eligible": False,
                "auto_promotion": False,
            },
            "source_precedence": [
                "HUMAN_SOVEREIGN",
                "R64_CONTROL_CENTER_AUTHORITY",
                "ACCEPTED_SEMANTIC_ADJUDICATIONS",
                "HANRI_BOUNDED_REPOSITORY_EVIDENCE",
                "LIVE_RUNTIME_RECEIPTS_FOR_RUNTIME_CLAIMS",
                "DASHBOARD_PROJECTION_ONLY",
            ],
            "effects": {
                "github_write": False,
                "drive_write": False,
                "archiveos_write": False,
                "knowledge_write": False,
                "memory_write": False,
                "current_truth_apply": False,
                "runtime_write": False,
                "scheduler_write": False,
                "external_message": False,
                "signal": False,
                "order": False,
                "capital_effect": False,
            },
            "semantics": {
                "hanri_is_not_second_authority_root": True,
                "archive_tooling_is_not_archiveos_core": True,
                "durable_memory_is_separate_from_current_truth": True,
            },
            "safety": dict(SHADOW_SAFETY),
        }
        body["hanri_receipt_sha256"] = sha256_obj(body)
        return body

    def build_closure(self, continuity=None, return_intake=None, projection=None, hanri=None):
        return build_unified_shadow_closure(
            self.transaction,
            self.continuity if continuity is None else continuity,
            self.return_intake if return_intake is None else return_intake,
            self.projection if projection is None else projection,
            self.hanri if hanri is None else hanri,
            closed_at="2026-08-20T00:30:00+07:00",
        )

    def test_closure_binds_five_planes_without_effect(self):
        closure = self.build_closure()
        self.assertEqual(closure["schema"], "bitevo.unified_shadow_closure.v3")
        self.assertEqual(closure["status"], "P0_SHADOW_CLOSED_NO_EFFECT")
        self.assertEqual(closure["registered_node_count"], 63)
        self.assertEqual(closure["upstream_control_gate"], "HOLD")
        self.assertEqual(closure["effective_gate"], "HOLD")
        self.assertEqual(closure["effective_action"], "WAIT")
        self.assertEqual(closure["planes"]["hanri_evidence_governor"], "BOUND_NON_AUTHORITY_FAIL_CLOSED")
        self.assertEqual(closure["planes"]["archiveos"], "BOUND_EVIDENCE_STATUS_ONLY")
        self.assertEqual(closure["planes"]["knowledge_memory"], "BOUND_NO_ADMISSION_NO_WRITE")
        self.assertEqual(closure["planes"]["executor"], "DISABLED")
        self.assertEqual(closure["hanri_receipt_sha256"], self.hanri["hanri_receipt_sha256"])
        self.assertTrue(all(value is False for value in closure["effect_summary"].values()))
        self.assertEqual(closure["safety"]["execution_authority"], "NONE")
        self.assertEqual(closure["safety"]["capital_permission"], "DENY")

    def test_hanri_cannot_widen_upstream_hold(self):
        hanri = copy.deepcopy(self.hanri)
        hanri["governor"]["gate"] = "PASS_SHADOW"
        hanri["governor"]["action"] = "LONG"
        hanri["hanri_receipt_sha256"] = sha256_obj({k: v for k, v in hanri.items() if k != "hanri_receipt_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "closure_hanri_cannot_widen_upstream_hold"):
            self.build_closure(hanri=hanri)

    def test_hanri_can_narrow_but_never_promote(self):
        hanri = copy.deepcopy(self.hanri)
        self.assertEqual(hanri["governor"]["gate"], "HOLD")
        self.assertFalse(hanri["governor"]["promotion_eligible"])
        self.assertFalse(hanri["hanri_source"]["authority_root"])
        self.assertFalse(hanri["hanri_source"]["can_promote_self"])

    def test_archive_tooling_cannot_become_archive_engine(self):
        hanri = copy.deepcopy(self.hanri)
        hanri["archive_tooling"]["authoritative_archive_engine"] = True
        hanri["hanri_receipt_sha256"] = sha256_obj({k: v for k, v in hanri.items() if k != "hanri_receipt_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "closure_archive_tooling_engine_overclaim"):
            self.build_closure(hanri=hanri)

    def test_memory_cannot_become_permission_or_current_truth(self):
        hanri = copy.deepcopy(self.hanri)
        hanri["knowledge_memory"]["current_truth_write"] = True
        hanri["hanri_receipt_sha256"] = sha256_obj({k: v for k, v in hanri.items() if k != "hanri_receipt_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "closure_knowledge_memory_write_breached:current_truth_write"):
            self.build_closure(hanri=hanri)

    def test_continuity_cannot_claim_live_host(self):
        receipt = copy.deepcopy(self.continuity)
        receipt["historical_lineage"]["live_host_state"] = "VERIFIED"
        receipt["continuity_receipt_sha256"] = sha256_obj({k: v for k, v in receipt.items() if k != "continuity_receipt_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "closure_live_host_state_overclaim"):
            self.build_closure(continuity=receipt)

    def test_return_transport_cannot_mutate(self):
        intake = copy.deepcopy(self.return_intake)
        intake["transport"]["registry_write"] = True
        intake["shadow_intake_sha256"] = sha256_obj({k: v for k, v in intake.items() if k != "shadow_intake_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "closure_return_transport_mutation_breached"):
            self.build_closure(return_intake=intake)

    def test_control_projection_cannot_apply_current_truth(self):
        projection = copy.deepcopy(self.projection)
        projection["mutations"]["current_truth"] = True
        projection["projection_sha256"] = sha256_obj({k: v for k, v in projection.items() if k != "projection_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "closure_control_projection_mutation_breached"):
            self.build_closure(projection=projection)

    def test_closure_is_deterministic(self):
        first = self.build_closure()
        second = self.build_closure()
        self.assertEqual(first, second)
        self.assertEqual(first["closure_sha256"], second["closure_sha256"])


if __name__ == "__main__":
    unittest.main()
