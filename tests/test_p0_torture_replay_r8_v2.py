from __future__ import annotations

import copy
import unittest

from tools.unified_shadow_writer_fencing_recovery_v2 import (
    CONTROL_R8_EFFECTS,
    R8_EFFECTS,
    SHADOW_SAFETY,
    WriterRecoveryV2Error,
    build_writer_fencing_recovery_closure_v2,
    sha256_obj,
)

ROOT = "1" * 64
LEASE = "2" * 64
LEGACY_INDEX = "3" * 64
PAIRED_INDEX = "4" * 64
RECEIPT = "5" * 64
CASE_SHA = "7" * 64
CHALLENGE = "8" * 64


def r8_closure_fixture():
    body = {
        "schema": "bitevo.shadow_writer_fencing_recovery_closure.v1",
        "case_id": "case-r8-1",
        "case_sha256": CASE_SHA,
        "challenge_id": CHALLENGE,
        "human_gate_consume_closure_sha256": "9" * 64,
        "recovery_verification_sha256": "a" * 64,
        "writer_lease_sha256": LEASE,
        "receipt_candidate_sha256": RECEIPT,
        "current_receipt_index_sha256": LEGACY_INDEX,
        "attempt_fencing_token": 8,
        "current_fencing_token": 8,
        "stale_writer_fenced": False,
        "crash_point": "AFTER_RECEIPT_BEFORE_ACK",
        "recovery_status": "RECEIPT_INDEXED_DEDUP_NO_RETRY",
        "recovery_action": "DEDUP_AND_ACK_ONLY",
        "fencing_model": "MONOTONIC_FENCING_TOKEN_PLUS_LEASE_DIGEST",
        "crash_recovery_protocol": "READBACK_PLUS_RECEIPT_INDEX_DEDUP",
        "status": "WRITER_FENCING_RECOVERY_BOUND_SHADOW_ONLY",
        "live_writer_backend_proven": False,
        "durable_commit_proven": False,
        "human_gate_write_performed": False,
        "current_truth_promotion_allowed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "decision": "HOLD",
        "action": "WAIT",
        "effects": dict(R8_EFFECTS),
        "safety": dict(SHADOW_SAFETY),
        "generated_at": "2026-08-20T04:50:00+07:00",
    }
    body["writer_fencing_recovery_closure_sha256"] = sha256_obj(body)
    return body


def anchor_fixture():
    body = {
        "schema": "control_center.shadow_human_gate_writer_authority_anchor.v1",
        "authority_root_sha256": ROOT,
        "writer_lease_sha256": LEASE,
        "legacy_receipt_index_sha256": LEGACY_INDEX,
        "paired_receipt_index_sha256": PAIRED_INDEX,
        "anchor_scope": "WRITER_LEASE_AND_RECEIPT_INDEX_ONLY",
        "retained_reference_required": True,
        "current_truth_promotion_allowed": False,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "retained_at": "2026-08-20T04:49:00+07:00",
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(CONTROL_R8_EFFECTS),
    }
    body["authority_anchor_sha256"] = sha256_obj(body)
    return body


def recovery_v2_fixture(anchor):
    body = {
        "schema": "control_center.shadow_human_gate_crash_recovery_verification.v2",
        "legacy_recovery_verification_sha256": "b" * 64,
        "paired_receipt_index_sha256": PAIRED_INDEX,
        "authority_anchor_sha256": anchor["authority_anchor_sha256"],
        "authority_root_sha256": ROOT,
        "current_writer_lease_sha256": LEASE,
        "legacy_current_receipt_index_sha256": LEGACY_INDEX,
        "case_id": "case-r8-1",
        "case_sha256": CASE_SHA,
        "challenge_id": CHALLENGE,
        "approval_verification_sha256": "c" * 64,
        "atomic_consume_verification_sha256": "d" * 64,
        "receipt_candidate_sha256": RECEIPT,
        "commit_id": "e" * 64,
        "idempotency_key_sha256": "f" * 64,
        "receipt_indexed": True,
        "recovery_status": "RECEIPT_INDEXED_DEDUP_NO_RETRY",
        "recovery_action": "DEDUP_AND_ACK_ONLY",
        "paired_receipt_identity_verified": True,
        "authority_root_anchor_consumed": True,
        "cross_plane_anchor_scope": "CONTROL_CENTER_WRITER_LEASE_RECEIPT_INDEX",
        "protocol_status": "FENCING_AND_CRASH_RECOVERY_HARDENED_SHADOW_ONLY",
        "live_writer_backend_proven": False,
        "durable_commit_proven": False,
        "human_gate_write_performed": False,
        "current_truth_promotion_allowed": False,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(CONTROL_R8_EFFECTS),
    }
    body["recovery_verification_sha256"] = sha256_obj(body)
    return body


class WriterFencingRecoveryV2Tests(unittest.TestCase):
    def build_chain(self):
        r8 = r8_closure_fixture()
        anchor = anchor_fixture()
        recovery = recovery_v2_fixture(anchor)
        closure = build_writer_fencing_recovery_closure_v2(
            r8,
            recovery,
            anchor,
            expected_r8_closure_sha256=r8["writer_fencing_recovery_closure_sha256"],
            expected_recovery_verification_v2_sha256=recovery["recovery_verification_sha256"],
            expected_authority_anchor_sha256=anchor["authority_anchor_sha256"],
            expected_authority_root_sha256=ROOT,
            generated_at="2026-08-20T04:51:00+07:00",
        )
        return r8, recovery, anchor, closure

    def test_happy_path_is_hardened_but_hold_wait(self):
        *_, closure = self.build_chain()
        self.assertTrue(closure["paired_receipt_identity_verified"])
        self.assertTrue(closure["cross_plane_anchor_verified"])
        self.assertEqual(closure["decision"], "HOLD")
        self.assertEqual(closure["action"], "WAIT")
        self.assertFalse(closure["durable_commit_proven"])
        self.assertEqual(closure["execution_authority"], "NONE")

    def test_wrong_retained_anchor_digest_is_rejected(self):
        r8, recovery, anchor, _ = self.build_chain()
        with self.assertRaisesRegex(WriterRecoveryV2Error, "authority_anchor_external_digest_mismatch"):
            build_writer_fencing_recovery_closure_v2(
                r8, recovery, anchor,
                expected_r8_closure_sha256=r8["writer_fencing_recovery_closure_sha256"],
                expected_recovery_verification_v2_sha256=recovery["recovery_verification_sha256"],
                expected_authority_anchor_sha256="0" * 64,
                expected_authority_root_sha256=ROOT,
                generated_at="2026-08-20T04:51:00+07:00",
            )

    def test_wrong_authority_root_is_rejected(self):
        r8, recovery, anchor, _ = self.build_chain()
        with self.assertRaisesRegex(WriterRecoveryV2Error, "authority_anchor_root_mismatch"):
            build_writer_fencing_recovery_closure_v2(
                r8, recovery, anchor,
                expected_r8_closure_sha256=r8["writer_fencing_recovery_closure_sha256"],
                expected_recovery_verification_v2_sha256=recovery["recovery_verification_sha256"],
                expected_authority_anchor_sha256=anchor["authority_anchor_sha256"],
                expected_authority_root_sha256="0" * 64,
                generated_at="2026-08-20T04:51:00+07:00",
            )

    def test_cross_case_recovery_is_rejected_even_if_rehashed(self):
        r8, recovery, anchor, _ = self.build_chain()
        forged = copy.deepcopy(recovery)
        forged["case_id"] = "other-case"
        forged["recovery_verification_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "recovery_verification_sha256"})
        with self.assertRaisesRegex(WriterRecoveryV2Error, "r8_1_cross_plane_case_id_mismatch"):
            build_writer_fencing_recovery_closure_v2(
                r8, forged, anchor,
                expected_r8_closure_sha256=r8["writer_fencing_recovery_closure_sha256"],
                expected_recovery_verification_v2_sha256=forged["recovery_verification_sha256"],
                expected_authority_anchor_sha256=anchor["authority_anchor_sha256"],
                expected_authority_root_sha256=ROOT,
                generated_at="2026-08-20T04:51:00+07:00",
            )

    def test_missing_paired_identity_guard_is_rejected(self):
        r8, recovery, anchor, _ = self.build_chain()
        forged = copy.deepcopy(recovery)
        forged["paired_receipt_identity_verified"] = False
        forged["recovery_verification_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "recovery_verification_sha256"})
        with self.assertRaisesRegex(WriterRecoveryV2Error, "recovery_v2_paired_identity_missing"):
            build_writer_fencing_recovery_closure_v2(
                r8, forged, anchor,
                expected_r8_closure_sha256=r8["writer_fencing_recovery_closure_sha256"],
                expected_recovery_verification_v2_sha256=forged["recovery_verification_sha256"],
                expected_authority_anchor_sha256=anchor["authority_anchor_sha256"],
                expected_authority_root_sha256=ROOT,
                generated_at="2026-08-20T04:51:00+07:00",
            )

    def test_r8_hold_to_pass_widening_is_rejected(self):
        r8, recovery, anchor, _ = self.build_chain()
        forged = copy.deepcopy(r8)
        forged["decision"] = "PASS"
        forged["action"] = "LONG"
        forged["writer_fencing_recovery_closure_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "writer_fencing_recovery_closure_sha256"})
        with self.assertRaisesRegex(WriterRecoveryV2Error, "r8_closure_gate_widening_forbidden"):
            build_writer_fencing_recovery_closure_v2(
                forged, recovery, anchor,
                expected_r8_closure_sha256=forged["writer_fencing_recovery_closure_sha256"],
                expected_recovery_verification_v2_sha256=recovery["recovery_verification_sha256"],
                expected_authority_anchor_sha256=anchor["authority_anchor_sha256"],
                expected_authority_root_sha256=ROOT,
                generated_at="2026-08-20T04:51:00+07:00",
            )

    def test_durable_commit_overclaim_is_rejected(self):
        r8, recovery, anchor, _ = self.build_chain()
        forged = copy.deepcopy(recovery)
        forged["durable_commit_proven"] = True
        forged["recovery_verification_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "recovery_verification_sha256"})
        with self.assertRaisesRegex(WriterRecoveryV2Error, "recovery_v2_durability_overclaim"):
            build_writer_fencing_recovery_closure_v2(
                r8, forged, anchor,
                expected_r8_closure_sha256=r8["writer_fencing_recovery_closure_sha256"],
                expected_recovery_verification_v2_sha256=forged["recovery_verification_sha256"],
                expected_authority_anchor_sha256=anchor["authority_anchor_sha256"],
                expected_authority_root_sha256=ROOT,
                generated_at="2026-08-20T04:51:00+07:00",
            )


if __name__ == "__main__":
    unittest.main()
