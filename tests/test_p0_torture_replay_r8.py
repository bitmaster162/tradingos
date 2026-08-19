from __future__ import annotations

import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_writer_fencing_recovery import (
    _CONTROL_R8_EFFECTS,
    _R7_EFFECTS,
    build_writer_fencing_recovery_closure,
)

CASE_SHA = "1" * 64
CHALLENGE = "2" * 64
APPROVAL = "3" * 64
ATOMIC = "4" * 64
R6_CLOSURE = "5" * 64
LEASE = "6" * 64
RECEIPT = "7" * 64
INDEX = "8" * 64
ATTEMPT = "9" * 64


def r7_fixture():
    body = {
        "schema": "bitevo.shadow_human_gate_consume_closure.v1",
        "case_id": "case-r8",
        "case_sha256": CASE_SHA,
        "challenge_id": CHALLENGE,
        "asymmetric_reveal_closure_sha256": R6_CLOSURE,
        "asymmetric_approval_verification_sha256": APPROVAL,
        "atomic_consume_verification_sha256": ATOMIC,
        "prior_human_gate_state_sha256": "a" * 64,
        "next_human_gate_state_candidate_sha256": "b" * 64,
        "cas_generation_from": 10,
        "cas_generation_to": 11,
        "toctou_guard_model": "COMPARE_AND_SWAP_PRECONDITION",
        "single_use_protocol": "BOUND_BUT_NOT_DURABLY_COMMITTED",
        "status": "HUMAN_GATE_CONSUME_BOUND_SHADOW_ONLY",
        "durable_commit_proven": False,
        "human_gate_write_performed": False,
        "current_truth_promotion_allowed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "decision": "HOLD",
        "action": "WAIT",
        "effects": dict(_R7_EFFECTS),
        "safety": dict(SHADOW_SAFETY),
        "generated_at": "2026-08-20T04:15:00+07:00",
    }
    body["human_gate_consume_closure_sha256"] = sha256_obj(body)
    return body


def recovery_fixture():
    body = {
        "schema": "control_center.shadow_human_gate_crash_recovery_verification.v1",
        "attempt_sha256": ATTEMPT,
        "receipt_candidate_sha256": RECEIPT,
        "current_writer_lease_sha256": LEASE,
        "current_receipt_index_sha256": INDEX,
        "case_id": "case-r8",
        "case_sha256": CASE_SHA,
        "challenge_id": CHALLENGE,
        "approval_verification_sha256": APPROVAL,
        "atomic_consume_verification_sha256": ATOMIC,
        "commit_id": "c" * 64,
        "idempotency_key_sha256": "d" * 64,
        "attempt_writer_lease_sha256": LEASE,
        "attempt_fencing_token": 7,
        "current_fencing_token": 7,
        "stale_writer_fenced": False,
        "split_brain_same_token_rejected": True,
        "crash_point": "AFTER_WRITE_BEFORE_RECEIPT",
        "readback_state_sha256": "b" * 64,
        "readback_generation": 11,
        "receipt_indexed": False,
        "current_lease_live": True,
        "recovery_status": "WRITE_OBSERVED_RECEIPT_ABSENT_HOLD",
        "recovery_action": "HOLD_AND_RECONCILE_EXTERNAL_BACKEND",
        "retry_allowed": False,
        "blind_retry_allowed": False,
        "fencing_model": "MONOTONIC_FENCING_TOKEN_PLUS_LEASE_DIGEST",
        "crash_recovery_protocol": "READBACK_PLUS_RECEIPT_INDEX_DEDUP",
        "protocol_status": "FENCING_AND_CRASH_RECOVERY_VERIFIED_SHADOW_ONLY",
        "live_writer_backend_proven": False,
        "durable_commit_proven": False,
        "human_gate_write_performed": False,
        "current_truth_promotion_allowed": False,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "observed_at": "2026-08-20T04:20:00+07:00",
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(_CONTROL_R8_EFFECTS),
    }
    body["recovery_verification_sha256"] = sha256_obj(body)
    return body


class P0TortureReplayR8Tests(unittest.TestCase):
    def build(self, r7=None, recovery=None, expected_r7=None, expected_recovery=None):
        r7 = r7_fixture() if r7 is None else r7
        recovery = recovery_fixture() if recovery is None else recovery
        return build_writer_fencing_recovery_closure(
            r7,
            recovery,
            expected_human_gate_consume_closure_sha256=r7["human_gate_consume_closure_sha256"] if expected_r7 is None else expected_r7,
            expected_crash_recovery_verification_sha256=recovery["recovery_verification_sha256"] if expected_recovery is None else expected_recovery,
            generated_at="2026-08-20T04:21:00+07:00",
        )

    def test_valid_r8_closure_remains_hold_wait_and_no_effect(self):
        closure = self.build()
        self.assertEqual(closure["schema"], "bitevo.shadow_writer_fencing_recovery_closure.v1")
        self.assertEqual(closure["status"], "WRITER_FENCING_RECOVERY_BOUND_SHADOW_ONLY")
        self.assertEqual(closure["decision"], "HOLD")
        self.assertEqual(closure["action"], "WAIT")
        self.assertFalse(closure["durable_commit_proven"])
        self.assertFalse(closure["human_gate_write_performed"])
        self.assertEqual(closure["execution_authority"], "NONE")

    def test_wrong_retained_recovery_digest_is_rejected(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "writer_recovery_recovery_external_digest_mismatch"):
            self.build(expected_recovery="0" * 64)

    def test_cross_case_recovery_rehash_is_rejected(self):
        forged = copy.deepcopy(recovery_fixture())
        forged["case_id"] = "other-case"
        forged["recovery_verification_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "recovery_verification_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "writer_recovery_case_mismatch"):
            self.build(recovery=forged)

    def test_durable_commit_overclaim_is_rejected_after_rehash(self):
        forged = copy.deepcopy(recovery_fixture())
        forged["durable_commit_proven"] = True
        forged["recovery_verification_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "recovery_verification_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "writer_recovery_live_or_durable_overclaim"):
            self.build(recovery=forged)

    def test_missing_split_brain_guard_is_rejected_after_rehash(self):
        forged = copy.deepcopy(recovery_fixture())
        forged["split_brain_same_token_rejected"] = False
        forged["recovery_verification_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "recovery_verification_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "writer_recovery_split_brain_guard_missing"):
            self.build(recovery=forged)

    def test_r7_hold_to_pass_widening_is_rejected_after_rehash(self):
        forged = copy.deepcopy(r7_fixture())
        forged["decision"] = "PASS_SHADOW"
        forged["action"] = "LONG"
        forged["human_gate_consume_closure_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "human_gate_consume_closure_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "writer_recovery_r7_gate_widening_forbidden"):
            self.build(r7=forged)


if __name__ == "__main__":
    unittest.main()
