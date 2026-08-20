from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from tools.p0_release_qualification import (
    EXPECTED_SCHEMA_MATRIX,
    P0ReleaseQualificationError,
    qualify_p0_release_candidate,
    sha256_obj,
)

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "evidence" / "p0_release_candidate_manifest_r1.json"
GENERATED_AT = "2026-08-20T05:59:30+07:00"


def load_manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def rehash(manifest):
    manifest["manifest_sha256"] = sha256_obj({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    return manifest


class P0ReleaseQualificationTests(unittest.TestCase):
    def test_manifest_qualifies_only_with_conditions_and_no_release_authority(self):
        manifest = load_manifest()
        receipt = qualify_p0_release_candidate(
            manifest,
            expected_manifest_sha256=manifest["manifest_sha256"],
            generated_at=GENERATED_AT,
        )
        self.assertEqual(receipt["status"], "P0_RELEASE_CANDIDATE_QUALIFIED_WITH_CONDITIONS")
        self.assertEqual(receipt["decision"], "HOLD")
        self.assertEqual(receipt["action"], "WAIT")
        self.assertTrue(receipt["global_invariants_verified"])
        self.assertTrue(receipt["schema_compatibility_verified"])
        self.assertTrue(receipt["p0_architecture_closed_for_candidate_review"])
        self.assertFalse(receipt["production_qualified"])
        self.assertFalse(receipt["release_ready"])
        self.assertFalse(receipt["merge_ready"])
        self.assertFalse(receipt["deploy_ready"])
        self.assertFalse(receipt["runtime_ready"])
        self.assertEqual(receipt["execution_authority"], "NONE")
        self.assertFalse(receipt["can_trade"])
        self.assertEqual(receipt["capital_permission"], "DENY")
        self.assertGreater(receipt["ci_blocked_surface_count"], 0)

    def test_rehashed_head_substitution_cannot_replace_retained_manifest(self):
        manifest = load_manifest()
        expected = manifest["manifest_sha256"]
        forged = copy.deepcopy(manifest)
        forged["surfaces"][1]["head_sha"] = "a" * 40
        rehash(forged)
        with self.assertRaisesRegex(P0ReleaseQualificationError, "release_manifest_external_digest_mismatch"):
            qualify_p0_release_candidate(
                forged,
                expected_manifest_sha256=expected,
                generated_at=GENERATED_AT,
            )

    def test_schema_drift_fails_even_with_new_expected_digest(self):
        manifest = load_manifest()
        forged = copy.deepcopy(manifest)
        forged["schema_matrix"]["R9"][-1] = "bitevo.shadow_dual_state_atomicity_closure.v999"
        rehash(forged)
        with self.assertRaisesRegex(P0ReleaseQualificationError, "release_schema_matrix_mismatch:R9"):
            qualify_p0_release_candidate(
                forged,
                expected_manifest_sha256=forged["manifest_sha256"],
                generated_at=GENERATED_AT,
            )

    def test_effect_or_permission_laundering_fails_closed(self):
        manifest = load_manifest()
        forged = copy.deepcopy(manifest)
        forged["global_invariants"]["merge_allowed"] = True
        rehash(forged)
        with self.assertRaisesRegex(P0ReleaseQualificationError, "release_global_invariant_breached:merge_allowed"):
            qualify_p0_release_candidate(
                forged,
                expected_manifest_sha256=forged["manifest_sha256"],
                generated_at=GENERATED_AT,
            )

    def test_surface_cannot_be_merged_or_ready(self):
        manifest = load_manifest()
        forged = copy.deepcopy(manifest)
        forged["surfaces"][0]["merged"] = True
        forged["surfaces"][0]["draft"] = False
        rehash(forged)
        with self.assertRaisesRegex(P0ReleaseQualificationError, "must_remain_open_draft_unmerged"):
            qualify_p0_release_candidate(
                forged,
                expected_manifest_sha256=forged["manifest_sha256"],
                generated_at=GENERATED_AT,
            )

    def test_green_continuity_evidence_cannot_be_downgraded(self):
        manifest = load_manifest()
        forged = copy.deepcopy(manifest)
        row = next(x for x in forged["surfaces"] if x["id"] == "continuityos_history_p0")
        row["ci_classification"] = "CI_BLOCKED_PRE_JOB"
        rehash(forged)
        with self.assertRaisesRegex(P0ReleaseQualificationError, "release_green_continuity_surface_regressed"):
            qualify_p0_release_candidate(
                forged,
                expected_manifest_sha256=forged["manifest_sha256"],
                generated_at=GENERATED_AT,
            )

    def test_known_conditions_cannot_be_silently_dropped(self):
        manifest = load_manifest()
        forged = copy.deepcopy(manifest)
        forged["known_conditions"].remove("NO_MERGE_AUTHORIZATION")
        rehash(forged)
        with self.assertRaisesRegex(P0ReleaseQualificationError, "release_known_conditions_mismatch"):
            qualify_p0_release_candidate(
                forged,
                expected_manifest_sha256=forged["manifest_sha256"],
                generated_at=GENERATED_AT,
            )

    def test_local_schema_constants_match_release_matrix(self):
        from tools.tradingos_shadow_integration import (
            TRADE_CASE_SCHEMA,
            TRADE_THESIS_SCHEMA,
            DECISION_PACKET_SCHEMA,
            OUTCOME_RECEIPT_SCHEMA,
            TRIAXIS_REQUEST_SCHEMA,
            TRIAXIS_SCHEMA,
            SCT_PREDICTION_SCHEMA,
        )
        from tools.unified_shadow_temporal_anchor import (
            TEMPORAL_EVIDENCE_BUNDLE_SCHEMA,
            REPLAY_ANCHOR_SCHEMA,
            REPLAY_QUALIFICATION_SCHEMA,
            TRUSTED_REPLAY_INPUT_SCHEMA,
        )
        from tools.unified_shadow_history_replay import HISTORY_VERIFICATION_SCHEMA
        from tools.unified_shadow_domain_subjects import (
            HUMAN_REVEAL_SCHEMA,
            SUBJECT_MANIFEST_SCHEMA,
            DOMAIN_HISTORY_VERIFICATION_SCHEMA,
        )
        from tools.unified_shadow_domain_history_closure import DOMAIN_HISTORY_CLOSURE_SCHEMA
        from tools.unified_shadow_human_custody import AUTHENTICATED_REVEAL_CLOSURE_SCHEMA
        from tools.unified_shadow_human_asymmetric_custody_v2 import ASYMMETRIC_REVEAL_CLOSURE_SCHEMA_V2
        from tools.unified_shadow_human_gate_consume import HUMAN_GATE_CONSUME_CLOSURE_SCHEMA
        from tools.unified_shadow_writer_fencing_recovery import R8_CLOSURE_SCHEMA
        from tools.unified_shadow_writer_fencing_recovery_v2 import R8_1_CLOSURE_SCHEMA
        from tools.unified_shadow_dual_state_atomicity import R9_CLOSURE_SCHEMA

        expected_local = {
            "R1": {
                TRADE_CASE_SCHEMA, TRADE_THESIS_SCHEMA, DECISION_PACKET_SCHEMA, OUTCOME_RECEIPT_SCHEMA,
                TRIAXIS_REQUEST_SCHEMA, TRIAXIS_SCHEMA, SCT_PREDICTION_SCHEMA,
            },
            "R2": {
                TEMPORAL_EVIDENCE_BUNDLE_SCHEMA, REPLAY_ANCHOR_SCHEMA,
                REPLAY_QUALIFICATION_SCHEMA, TRUSTED_REPLAY_INPUT_SCHEMA,
            },
            "R3": {HISTORY_VERIFICATION_SCHEMA},
            "R4": {HUMAN_REVEAL_SCHEMA, SUBJECT_MANIFEST_SCHEMA, DOMAIN_HISTORY_VERIFICATION_SCHEMA, DOMAIN_HISTORY_CLOSURE_SCHEMA},
            "R5": {AUTHENTICATED_REVEAL_CLOSURE_SCHEMA},
            "R6.1": {ASYMMETRIC_REVEAL_CLOSURE_SCHEMA_V2},
            "R7": {HUMAN_GATE_CONSUME_CLOSURE_SCHEMA},
            "R8": {R8_CLOSURE_SCHEMA},
            "R8.1": {R8_1_CLOSURE_SCHEMA},
            "R9": {R9_CLOSURE_SCHEMA},
        }
        for generation, values in expected_local.items():
            self.assertTrue(values.issubset(set(EXPECTED_SCHEMA_MATRIX[generation])), generation)


if __name__ == "__main__":
    unittest.main()
