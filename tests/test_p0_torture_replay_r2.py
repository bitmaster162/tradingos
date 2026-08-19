import copy
import unittest

from tools.tradingos_shadow_integration import (
    SHADOW_SAFETY,
    ShadowIntegrationError,
    build_trade_case,
    sha256_obj,
)
from tools.unified_shadow_temporal_anchor import (
    build_replay_anchor_candidate,
    build_temporal_evidence_bundle,
    build_temporal_replay_qualification,
    build_trusted_replay_input,
    derive_case_binding_sha256,
    verify_temporal_replay_qualification,
)

AUTHORITY_ID = "control-center:test-root"
AUTHORITY_ROOT = "c" * 64
ROOT_EFFECTIVE_AT = "2026-08-19T14:00:00Z"


def _case(case_id="trade-r2-001", scenario="R2 trusted replay fixture."):
    return build_trade_case(
        case_id=case_id,
        frozen_at="2026-08-19T15:00:00Z",
        symbol="BTCUSDT",
        venue="Binance",
        timeframe="1h",
        scenario=scenario,
        snapshot_ref={
            "source_id": "snapshot:r2",
            "sha256": "a" * 64,
            "schema": "market.snapshot/v1",
        },
        vision_ref={
            "source_id": "vision:r2",
            "sha256": "b" * 64,
            "schema": "vision.market/v1",
        },
    )


def _timing():
    return {
        "snapshot": {
            "source_id": "snapshot:r2",
            "sha256": "a" * 64,
            "schema": "market.snapshot/v1",
            "observed_at": "2026-08-19T14:58:00Z",
            "ingested_at": "2026-08-19T14:58:20Z",
            "fresh_until": "2026-08-19T15:02:00Z",
            "clock_verified": True,
            "provenance_verified": True,
            "custody_ref": "fixture:custody:snapshot:r2",
        },
        "vision": {
            "source_id": "vision:r2",
            "sha256": "b" * 64,
            "schema": "vision.market/v1",
            "observed_at": "2026-08-19T14:59:00Z",
            "ingested_at": "2026-08-19T14:59:10Z",
            "fresh_until": "2026-08-19T15:01:00Z",
            "clock_verified": True,
            "provenance_verified": True,
            "custody_ref": "fixture:custody:vision:r2",
        },
    }


def _qualified(case=None, timing=None):
    case = _case() if case is None else case
    timing = _timing() if timing is None else timing
    bundle = build_temporal_evidence_bundle(case, timing)
    binding = derive_case_binding_sha256(
        authority_id=AUTHORITY_ID,
        authority_root_sha256=AUTHORITY_ROOT,
        case_id=case["case_id"],
        case_sha256=case["case_sha256"],
        evidence_bundle_sha256=bundle["evidence_bundle_sha256"],
    )
    anchor = build_replay_anchor_candidate(
        case,
        bundle,
        authority_id=AUTHORITY_ID,
        authority_generation="TEST_ROOT_R1",
        authority_root_sha256=AUTHORITY_ROOT,
        root_effective_at=ROOT_EFFECTIVE_AT,
    )
    qualification = build_temporal_replay_qualification(
        case,
        timing,
        anchor,
        expected_authority_id=AUTHORITY_ID,
        expected_root_sha256=AUTHORITY_ROOT,
        expected_case_binding_sha256=binding,
        generated_at="2026-08-20T02:10:00+07:00",
    )
    return case, timing, bundle, anchor, binding, qualification


class P0TortureReplayR2Tests(unittest.TestCase):
    def test_r2_valid_temporal_and_external_anchor_qualification_is_no_effect(self):
        case, _, _, _, binding, qualification = _qualified()
        verified = verify_temporal_replay_qualification(
            case,
            qualification,
            expected_authority_id=AUTHORITY_ID,
            expected_root_sha256=AUTHORITY_ROOT,
            expected_case_binding_sha256=binding,
        )
        replay = build_trusted_replay_input(
            case,
            qualification,
            expected_authority_id=AUTHORITY_ID,
            expected_root_sha256=AUTHORITY_ROOT,
            expected_case_binding_sha256=binding,
        )
        self.assertEqual(verified, qualification["qualification_sha256"])
        self.assertEqual(replay["schema"], "tradingos.trusted_replay_input.v1")
        self.assertEqual(replay["qualification_sha256"], verified)
        self.assertEqual(replay["replay_mode"], "OFFLINE_TRUSTED_REPLAY_ONLY")
        self.assertFalse(replay["source_authenticity_created_here"])
        self.assertEqual(replay["execution_authority"], "NONE")
        self.assertFalse(replay["can_execute"])
        self.assertTrue(all(value is False for value in replay["effects"].values()))
        self.assertEqual(replay["safety"], SHADOW_SAFETY)

    def test_r2_post_freeze_snapshot_observation_is_rejected(self):
        timing = _timing()
        timing["snapshot"]["observed_at"] = "2026-08-19T15:00:01Z"
        timing["snapshot"]["ingested_at"] = "2026-08-19T15:00:02Z"
        with self.assertRaisesRegex(ShadowIntegrationError, "snapshot_ingested_after_case_freeze"):
            build_temporal_evidence_bundle(_case(), timing)

    def test_r2_stale_snapshot_at_freeze_is_rejected(self):
        timing = _timing()
        timing["snapshot"]["fresh_until"] = "2026-08-19T14:59:59Z"
        with self.assertRaisesRegex(ShadowIntegrationError, "snapshot_stale_at_case_freeze"):
            build_temporal_evidence_bundle(_case(), timing)

    def test_r2_post_freeze_vision_is_rejected(self):
        timing = _timing()
        timing["vision"]["observed_at"] = "2026-08-19T15:00:01Z"
        timing["vision"]["ingested_at"] = "2026-08-19T15:00:02Z"
        with self.assertRaisesRegex(ShadowIntegrationError, "vision_ingested_after_case_freeze"):
            build_temporal_evidence_bundle(_case(), timing)

    def test_r2_missing_vision_temporal_binding_is_rejected(self):
        timing = _timing()
        del timing["vision"]
        with self.assertRaisesRegex(ShadowIntegrationError, "evidence_coverage_mismatch"):
            build_temporal_evidence_bundle(_case(), timing)

    def test_r2_anchor_that_became_effective_after_freeze_is_rejected(self):
        case = _case()
        bundle = build_temporal_evidence_bundle(case, _timing())
        with self.assertRaisesRegex(ShadowIntegrationError, "root_effective_after_case_freeze"):
            build_replay_anchor_candidate(
                case,
                bundle,
                authority_id=AUTHORITY_ID,
                authority_generation="TEST_ROOT_R1",
                authority_root_sha256=AUTHORITY_ROOT,
                root_effective_at="2026-08-19T15:00:01Z",
            )

    def test_r2_rehashed_anchor_with_wrong_external_root_is_rejected(self):
        case, timing, _, anchor, binding, _ = _qualified()
        forged = copy.deepcopy(anchor)
        forged["authority_root_sha256"] = "d" * 64
        forged["case_binding_sha256"] = derive_case_binding_sha256(
            authority_id=AUTHORITY_ID,
            authority_root_sha256="d" * 64,
            case_id=case["case_id"],
            case_sha256=case["case_sha256"],
            evidence_bundle_sha256=forged["evidence_bundle_sha256"],
        )
        forged["anchor_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "anchor_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "external_root_mismatch"):
            build_temporal_replay_qualification(
                case,
                timing,
                forged,
                expected_authority_id=AUTHORITY_ID,
                expected_root_sha256=AUTHORITY_ROOT,
                expected_case_binding_sha256=binding,
                generated_at="2026-08-20T02:10:00+07:00",
            )

    def test_r2_whole_case_rewrite_and_rehash_cannot_reuse_external_case_binding(self):
        original_case, _, _, _, original_binding, _ = _qualified()
        forged_case = _case(case_id="trade-r2-forged", scenario="Whole tree rewritten and rehashed.")
        timing = _timing()
        forged_bundle = build_temporal_evidence_bundle(forged_case, timing)
        forged_anchor = build_replay_anchor_candidate(
            forged_case,
            forged_bundle,
            authority_id=AUTHORITY_ID,
            authority_generation="TEST_ROOT_R1",
            authority_root_sha256=AUTHORITY_ROOT,
            root_effective_at=ROOT_EFFECTIVE_AT,
        )
        self.assertNotEqual(forged_case["case_sha256"], original_case["case_sha256"])
        with self.assertRaisesRegex(ShadowIntegrationError, "external_case_binding_mismatch"):
            build_temporal_replay_qualification(
                forged_case,
                timing,
                forged_anchor,
                expected_authority_id=AUTHORITY_ID,
                expected_root_sha256=AUTHORITY_ROOT,
                expected_case_binding_sha256=original_binding,
                generated_at="2026-08-20T02:10:00+07:00",
            )

    def test_r2_reordered_or_modified_evidence_cannot_reuse_old_external_binding(self):
        case, _, _, _, old_binding, _ = _qualified()
        timing = _timing()
        timing["vision"]["observed_at"] = "2026-08-19T14:58:59Z"
        bundle = build_temporal_evidence_bundle(case, timing)
        anchor = build_replay_anchor_candidate(
            case,
            bundle,
            authority_id=AUTHORITY_ID,
            authority_generation="TEST_ROOT_R1",
            authority_root_sha256=AUTHORITY_ROOT,
            root_effective_at=ROOT_EFFECTIVE_AT,
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "external_case_binding_mismatch"):
            build_temporal_replay_qualification(
                case,
                timing,
                anchor,
                expected_authority_id=AUTHORITY_ID,
                expected_root_sha256=AUTHORITY_ROOT,
                expected_case_binding_sha256=old_binding,
                generated_at="2026-08-20T02:10:00+07:00",
            )

    def test_r2_wrong_external_authority_identity_is_rejected(self):
        case, timing, _, anchor, binding, _ = _qualified()
        with self.assertRaisesRegex(ShadowIntegrationError, "authority_id_mismatch"):
            build_temporal_replay_qualification(
                case,
                timing,
                anchor,
                expected_authority_id="control-center:other-root",
                expected_root_sha256=AUTHORITY_ROOT,
                expected_case_binding_sha256=binding,
                generated_at="2026-08-20T02:10:00+07:00",
            )


if __name__ == "__main__":
    unittest.main()
