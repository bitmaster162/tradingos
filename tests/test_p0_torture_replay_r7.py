from __future__ import annotations

import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_human_gate_consume import build_human_gate_consume_closure

R6_EFFECTS = {
    "human_gate_write": False,
    "credential_registry_write": False,
    "nonce_registry_write": False,
    "registry_write": False,
    "ledger_write": False,
    "return_index_write": False,
    "current_truth_apply": False,
    "runtime_activation": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}
CONTROL_EFFECTS = {
    "human_gate_write": False,
    "credential_registry_write": False,
    "nonce_registry_write": False,
    "current_truth_apply": False,
    "decision_ledger_write": False,
    "command_queue_write": False,
    "runtime_activation": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}


def make_r6():
    body = {
        "schema": "bitevo.shadow_asymmetric_reveal_closure.v2",
        "case_id": "case-r7",
        "case_sha256": "1" * 64,
        "challenge_id": "2" * 64,
        "asymmetric_approval_verification_sha256": "3" * 64,
        "authentication_status": "ASYMMETRIC_CUSTODY_VERIFIED_SHADOW_ONLY",
        "trust_upgrade": "INDEPENDENT_ASSERTION_AND_APPROVAL_DIGESTS_BOUND",
        "external_assertion_digest_consumed": True,
        "local_signature_math_verified": False,
        "physical_human_presence_proven": False,
        "human_gate_write_performed": False,
        "current_truth_promotion_allowed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "effects": dict(R6_EFFECTS),
        "safety": dict(SHADOW_SAFETY),
    }
    body["asymmetric_reveal_closure_sha256"] = sha256_obj(body)
    return body


def make_atomic():
    body = {
        "schema": "control_center.shadow_human_gate_atomic_consume_verification.v1",
        "approval_verification_sha256": "3" * 64,
        "case_id": "case-r7",
        "case_sha256": "1" * 64,
        "challenge_id": "2" * 64,
        "nonce_sha256": "4" * 64,
        "prior_state_sha256": "5" * 64,
        "next_state_candidate_sha256": "6" * 64,
        "cas_generation_from": 10,
        "cas_generation_to": 11,
        "toctou_guard_model": "COMPARE_AND_SWAP_PRECONDITION",
        "atomicity_status": "PROTOCOL_VERIFIED_NO_DURABLE_COMMIT",
        "single_use_status": "CANDIDATE_ONLY_NOT_DURABLY_ENFORCED",
        "commit_performed": False,
        "human_gate_write_performed": False,
        "current_truth_promotion_allowed": False,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "effects": dict(CONTROL_EFFECTS),
        "safety": dict(SHADOW_SAFETY),
    }
    body["atomic_consume_verification_sha256"] = sha256_obj(body)
    return body


class P0TortureReplayR7Tests(unittest.TestCase):
    def test_r7_binds_atomic_protocol_without_claiming_commit(self):
        r6 = make_r6()
        atomic = make_atomic()
        closure = build_human_gate_consume_closure(
            r6,
            atomic,
            expected_asymmetric_reveal_closure_sha256=r6["asymmetric_reveal_closure_sha256"],
            expected_atomic_consume_verification_sha256=atomic["atomic_consume_verification_sha256"],
            generated_at="2026-08-20T04:10:00+07:00",
        )
        self.assertEqual(closure["status"], "HUMAN_GATE_CONSUME_BOUND_SHADOW_ONLY")
        self.assertFalse(closure["durable_commit_proven"])
        self.assertFalse(closure["human_gate_write_performed"])
        self.assertEqual(closure["decision"], "HOLD")
        self.assertEqual(closure["action"], "WAIT")
        self.assertEqual(closure["execution_authority"], "NONE")

    def test_wrong_atomic_external_digest_is_rejected(self):
        r6 = make_r6()
        atomic = make_atomic()
        with self.assertRaisesRegex(ShadowIntegrationError, "human_gate_consume_atomic_external_digest_mismatch"):
            build_human_gate_consume_closure(
                r6,
                atomic,
                expected_asymmetric_reveal_closure_sha256=r6["asymmetric_reveal_closure_sha256"],
                expected_atomic_consume_verification_sha256="0" * 64,
                generated_at="2026-08-20T04:10:00+07:00",
            )

    def test_cross_case_atomic_receipt_is_rejected_even_if_rehashed(self):
        r6 = make_r6()
        atomic = make_atomic()
        atomic["case_id"] = "other-case"
        atomic["atomic_consume_verification_sha256"] = sha256_obj(
            {k: v for k, v in atomic.items() if k != "atomic_consume_verification_sha256"}
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "human_gate_consume_case_mismatch"):
            build_human_gate_consume_closure(
                r6,
                atomic,
                expected_asymmetric_reveal_closure_sha256=r6["asymmetric_reveal_closure_sha256"],
                expected_atomic_consume_verification_sha256=atomic["atomic_consume_verification_sha256"],
                generated_at="2026-08-20T04:10:00+07:00",
            )

    def test_durable_commit_overclaim_is_rejected_even_if_rehashed(self):
        r6 = make_r6()
        atomic = make_atomic()
        atomic["commit_performed"] = True
        atomic["atomic_consume_verification_sha256"] = sha256_obj(
            {k: v for k, v in atomic.items() if k != "atomic_consume_verification_sha256"}
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "human_gate_consume_atomic_commit_overclaim"):
            build_human_gate_consume_closure(
                r6,
                atomic,
                expected_asymmetric_reveal_closure_sha256=r6["asymmetric_reveal_closure_sha256"],
                expected_atomic_consume_verification_sha256=atomic["atomic_consume_verification_sha256"],
                generated_at="2026-08-20T04:10:00+07:00",
            )

    def test_generation_skip_is_rejected(self):
        r6 = make_r6()
        atomic = make_atomic()
        atomic["cas_generation_to"] = 12
        atomic["atomic_consume_verification_sha256"] = sha256_obj(
            {k: v for k, v in atomic.items() if k != "atomic_consume_verification_sha256"}
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "human_gate_consume_atomic_generation_transition_invalid"):
            build_human_gate_consume_closure(
                r6,
                atomic,
                expected_asymmetric_reveal_closure_sha256=r6["asymmetric_reveal_closure_sha256"],
                expected_atomic_consume_verification_sha256=atomic["atomic_consume_verification_sha256"],
                generated_at="2026-08-20T04:10:00+07:00",
            )


if __name__ == "__main__":
    unittest.main()
