from __future__ import annotations

import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_dual_state_atomicity import (
    _CONTROL_EFFECTS,
    _TRADING_EFFECTS,
    build_dual_state_atomicity_closure,
)

ROOT = "1" * 64
CASE_SHA = "2" * 64
CHALLENGE = "3" * 64
LEASE = "4" * 64
PAIRED = "5" * 64
NEXT_PAIRED = "6" * 64
LEASE_LINEAGE = "7" * 64
COMMIT = "8" * 64
IDEM = "9" * 64

def r8_1_fixture():
    body = {
        "schema": "bitevo.shadow_writer_fencing_recovery_closure.v2",
        "prior_writer_fencing_recovery_closure_sha256": "a" * 64,
        "recovery_verification_v2_sha256": "b" * 64,
        "authority_anchor_sha256": "c" * 64,
        "authority_root_sha256": ROOT,
        "case_id": "case-r9",
        "case_sha256": CASE_SHA,
        "challenge_id": CHALLENGE,
        "writer_lease_sha256": LEASE,
        "legacy_receipt_index_sha256": "d" * 64,
        "paired_receipt_index_sha256": PAIRED,
        "receipt_candidate_sha256": "e" * 64,
        "recovery_status": "RECEIPT_INDEXED_DEDUP_NO_RETRY",
        "recovery_action": "DEDUP_AND_ACK_ONLY",
        "paired_receipt_identity_verified": True,
        "authority_root_anchor_consumed": True,
        "cross_plane_anchor_verified": True,
        "status": "WRITER_FENCING_RECOVERY_HARDENED_SHADOW_ONLY",
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
        "effects": dict(_TRADING_EFFECTS),
        "safety": dict(SHADOW_SAFETY),
        "generated_at": "2026-08-20T05:20:00+07:00",
    }
    body["writer_fencing_recovery_closure_sha256"] = sha256_obj(body)
    return body

def atomicity_fixture():
    body = {
        "schema": "control_center.shadow_human_gate_dual_state_atomicity_verification.v1",
        "dual_commit_candidate_sha256": "f" * 64,
        "readback_sha256": "0" * 64,
        "authority_root_sha256": ROOT,
        "recovery_verification_v2_sha256": "ab" * 32,
        "lease_lineage_sha256": LEASE_LINEAGE,
        "case_id": "case-r9",
        "case_sha256": CASE_SHA,
        "challenge_id": CHALLENGE,
        "current_writer_lease_sha256": LEASE,
        "commit_id": COMMIT,
        "idempotency_key_sha256": IDEM,
        "prior_human_gate_state_sha256": "11" * 32,
        "next_human_gate_state_sha256": "12" * 32,
        "prior_paired_receipt_index_sha256": PAIRED,
        "next_paired_receipt_index_sha256": NEXT_PAIRED,
        "crash_point": "AFTER_ATOMIC_DUAL_WRITE_BEFORE_ACK",
        "observed_pair_state": "POST_COMMIT_PAIR_OBSERVED_SHADOW_ONLY",
        "recovery_action": "DEDUP_RECONCILE_NO_SECOND_WRITE",
        "dual_state_atomicity_model": "ONE_TRANSACTION_TWO_LOGICAL_RECORDS",
        "split_state_rejected": True,
        "lease_epoch_lineage_verified": True,
        "aba_guard_verified": True,
        "durability_status": "PROTOCOL_VERIFIED_NO_DURABLE_BACKEND",
        "protocol_status": "DUAL_STATE_ATOMICITY_VERIFIED_SHADOW_ONLY",
        "write_performed": False,
        "durable_commit_proven": False,
        "live_backend_observed": False,
        "current_truth_promotion_allowed": False,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(_CONTROL_EFFECTS),
    }
    body["atomicity_verification_sha256"] = sha256_obj(body)
    return body

class TradingR9Tests(unittest.TestCase):
    def test_happy_path_is_hold_wait_and_no_effect(self):
        r8 = r8_1_fixture(); atomic = atomicity_fixture()
        closure = build_dual_state_atomicity_closure(r8, atomic, expected_r8_1_closure_sha256=r8["writer_fencing_recovery_closure_sha256"], expected_atomicity_verification_sha256=atomic["atomicity_verification_sha256"], expected_authority_root_sha256=ROOT, generated_at="2026-08-20T05:35:00+07:00")
        self.assertEqual(closure["status"], "DUAL_STATE_ATOMICITY_BOUND_SHADOW_ONLY")
        self.assertEqual(closure["decision"], "HOLD")
        self.assertEqual(closure["action"], "WAIT")
        self.assertTrue(closure["dual_state_atomicity_verified"])
        self.assertFalse(closure["durable_commit_proven"])
        self.assertEqual(closure["execution_authority"], "NONE")

    def test_wrong_retained_atomicity_digest_rejected(self):
        r8 = r8_1_fixture(); atomic = atomicity_fixture()
        with self.assertRaisesRegex(ShadowIntegrationError, "dual_state_atomicity_external_digest_mismatch"):
            build_dual_state_atomicity_closure(r8, atomic, expected_r8_1_closure_sha256=r8["writer_fencing_recovery_closure_sha256"], expected_atomicity_verification_sha256="cd" * 32, expected_authority_root_sha256=ROOT, generated_at="2026-08-20T05:35:00+07:00")

    def test_cross_case_atomicity_rejected_even_after_rehash(self):
        r8 = r8_1_fixture(); atomic = atomicity_fixture(); atomic["case_id"] = "case-other"
        atomic["atomicity_verification_sha256"] = sha256_obj({k: v for k, v in atomic.items() if k != "atomicity_verification_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "dual_state_cross_plane_case_id_mismatch"):
            build_dual_state_atomicity_closure(r8, atomic, expected_r8_1_closure_sha256=r8["writer_fencing_recovery_closure_sha256"], expected_atomicity_verification_sha256=atomic["atomicity_verification_sha256"], expected_authority_root_sha256=ROOT, generated_at="2026-08-20T05:35:00+07:00")

    def test_rehashed_authority_root_substitution_rejected(self):
        r8 = r8_1_fixture(); atomic = atomicity_fixture(); atomic["authority_root_sha256"] = "fe" * 32
        atomic["atomicity_verification_sha256"] = sha256_obj({k: v for k, v in atomic.items() if k != "atomicity_verification_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "dual_state_atomicity_authority_root_mismatch"):
            build_dual_state_atomicity_closure(r8, atomic, expected_r8_1_closure_sha256=r8["writer_fencing_recovery_closure_sha256"], expected_atomicity_verification_sha256=atomic["atomicity_verification_sha256"], expected_authority_root_sha256=ROOT, generated_at="2026-08-20T05:35:00+07:00")

    def test_missing_lineage_guard_rejected_after_rehash(self):
        r8 = r8_1_fixture(); atomic = atomicity_fixture(); atomic["lease_epoch_lineage_verified"] = False
        atomic["atomicity_verification_sha256"] = sha256_obj({k: v for k, v in atomic.items() if k != "atomicity_verification_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "dual_state_lease_lineage_guard_missing"):
            build_dual_state_atomicity_closure(r8, atomic, expected_r8_1_closure_sha256=r8["writer_fencing_recovery_closure_sha256"], expected_atomicity_verification_sha256=atomic["atomicity_verification_sha256"], expected_authority_root_sha256=ROOT, generated_at="2026-08-20T05:35:00+07:00")

    def test_durable_commit_overclaim_rejected_after_rehash(self):
        r8 = r8_1_fixture(); atomic = atomicity_fixture(); atomic["durable_commit_proven"] = True
        atomic["atomicity_verification_sha256"] = sha256_obj({k: v for k, v in atomic.items() if k != "atomicity_verification_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "dual_state_durable_write_overclaim"):
            build_dual_state_atomicity_closure(r8, atomic, expected_r8_1_closure_sha256=r8["writer_fencing_recovery_closure_sha256"], expected_atomicity_verification_sha256=atomic["atomicity_verification_sha256"], expected_authority_root_sha256=ROOT, generated_at="2026-08-20T05:35:00+07:00")

    def test_r8_hold_to_pass_widening_rejected(self):
        r8 = r8_1_fixture(); atomic = atomicity_fixture(); r8["decision"] = "PASS"
        r8["writer_fencing_recovery_closure_sha256"] = sha256_obj({k: v for k, v in r8.items() if k != "writer_fencing_recovery_closure_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "dual_state_r8_1_gate_widening_forbidden"):
            build_dual_state_atomicity_closure(r8, atomic, expected_r8_1_closure_sha256=r8["writer_fencing_recovery_closure_sha256"], expected_atomicity_verification_sha256=atomic["atomicity_verification_sha256"], expected_authority_root_sha256=ROOT, generated_at="2026-08-20T05:35:00+07:00")

if __name__ == "__main__":
    unittest.main()
