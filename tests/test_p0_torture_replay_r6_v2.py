from __future__ import annotations

import copy
import unittest

from tools.tradingos_shadow_integration import ShadowIntegrationError, sha256_obj
from tools.unified_shadow_domain_subjects import build_human_reveal_receipt
from tools.unified_shadow_human_asymmetric_custody_v2 import build_asymmetric_reveal_closure_v2
from tests.test_p0_torture_replay_r6 import (
    CREDENTIAL_ID,
    PRIOR_CREDENTIAL,
    PRIOR_NONCE,
    PUBLIC_KEY_SHA,
    make_approval,
    make_case,
    make_domain_closure,
    make_manifest,
    make_packet,
    make_twin,
)

EXTERNAL_ASSERTION_SHA = "e" * 64


def upgrade_approval_v2(v1: dict, *, external_assertion_sha: str = EXTERNAL_ASSERTION_SHA) -> dict:
    prior_sha = v1["asymmetric_approval_verification_sha256"]
    body = {k: v for k, v in v1.items() if k not in {"schema", "asymmetric_approval_verification_sha256"}}
    body.update(
        {
            "schema": "control_center.shadow_asymmetric_human_approval_verification.v2",
            "prior_asymmetric_approval_verification_sha256": prior_sha,
            "external_assertion_sha256": external_assertion_sha,
            "external_assertion_digest_consumed": True,
            "external_asymmetric_verifier_evidence": "EXPECTED_DIGEST_BOUND",
            "trust_upgrade": "SELF_HASH_TO_INDEPENDENT_ASSERTION_DIGEST",
        }
    )
    body["asymmetric_approval_verification_sha256"] = sha256_obj(body)
    return body


class P0TortureReplayR6V2Tests(unittest.TestCase):
    def setUp(self):
        self.case = make_case()
        twin = make_twin(self.case)
        packet = make_packet(self.case, twin)
        self.reveal = build_human_reveal_receipt(
            self.case,
            packet,
            actual_choice="LONG",
            decided_at="2026-08-20T03:05:00+07:00",
        )
        self.manifest = make_manifest(self.case, self.reveal)
        self.domain = make_domain_closure(self.case, self.manifest)
        self.approval_v1 = make_approval(self.case, self.reveal)
        self.approval_v2 = upgrade_approval_v2(self.approval_v1)

    def build(self, approval=None, **overrides):
        approval = self.approval_v2 if approval is None else approval
        return build_asymmetric_reveal_closure_v2(
            self.case,
            self.reveal,
            self.manifest,
            self.domain,
            approval,
            expected_asymmetric_approval_verification_sha256=overrides.pop(
                "expected_approval",
                approval["asymmetric_approval_verification_sha256"],
            ),
            expected_external_assertion_sha256=overrides.pop("expected_assertion", EXTERNAL_ASSERTION_SHA),
            expected_credential_registry_sha256=overrides.pop("expected_credential_registry", PRIOR_CREDENTIAL),
            expected_nonce_registry_sha256=overrides.pop("expected_nonce_registry", PRIOR_NONCE),
            expected_human_subject_id=overrides.pop("expected_human_subject_id", "robert"),
            expected_custody_provider_id=overrides.pop("expected_custody_provider_id", "custody-r6"),
            expected_verifier_id=overrides.pop("expected_verifier_id", "webauthn-verifier-r6"),
            expected_verifier_key_id=overrides.pop("expected_verifier_key_id", "verifier-key-r6-01"),
            expected_credential_id_sha256=overrides.pop("expected_credential_id", CREDENTIAL_ID),
            expected_public_key_sha256=overrides.pop("expected_public_key", PUBLIC_KEY_SHA),
            expected_algorithm=overrides.pop("expected_algorithm", "ED25519"),
            expected_key_epoch=overrides.pop("expected_key_epoch", 3),
            expected_origin=overrides.pop("expected_origin", "https://control.example.invalid"),
            expected_rp_id=overrides.pop("expected_rp_id", "control.example.invalid"),
            generated_at=overrides.pop("generated_at", "2026-08-20T03:18:00+07:00"),
            **overrides,
        )

    @staticmethod
    def rehash(approval):
        approval["asymmetric_approval_verification_sha256"] = sha256_obj(
            {k: v for k, v in approval.items() if k != "asymmetric_approval_verification_sha256"}
        )
        return approval

    def test_valid_v2_closure_binds_independent_assertion_and_approval_digests(self):
        closure = self.build()
        self.assertEqual(closure["schema"], "bitevo.shadow_asymmetric_reveal_closure.v2")
        self.assertEqual(closure["external_assertion_sha256"], EXTERNAL_ASSERTION_SHA)
        self.assertTrue(closure["external_assertion_digest_consumed"])
        self.assertEqual(closure["asymmetric_approval_verification_sha256"], self.approval_v2["asymmetric_approval_verification_sha256"])
        self.assertEqual(closure["trust_upgrade"], "INDEPENDENT_ASSERTION_AND_APPROVAL_DIGESTS_BOUND")
        self.assertFalse(closure["local_signature_math_verified"])
        self.assertFalse(closure["physical_human_presence_proven"])
        self.assertTrue(all(value is False for value in closure["effects"].values()))
        self.assertEqual(closure["execution_authority"], "NONE")
        self.assertFalse(closure["can_execute"])

    def test_locally_rehashed_v2_approval_cannot_replace_retained_approval_digest(self):
        forged = copy.deepcopy(self.approval_v2)
        forged["verifier_key_id"] = "forged-key"
        forged = self.rehash(forged)
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_v2_approval_external_digest_mismatch"):
            self.build(
                forged,
                expected_approval=self.approval_v2["asymmetric_approval_verification_sha256"],
            )

    def test_wrong_external_assertion_digest_is_rejected(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_v2_assertion_external_digest_mismatch"):
            self.build(expected_assertion="0" * 64)

    def test_assertion_digest_guard_cannot_be_dropped_even_if_rehashed(self):
        weakened = copy.deepcopy(self.approval_v2)
        weakened["external_assertion_digest_consumed"] = False
        weakened = self.rehash(weakened)
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_v2_assertion_digest_guard_missing"):
            self.build(weakened, expected_approval=weakened["asymmetric_approval_verification_sha256"])

    def test_prior_v1_approval_lineage_cannot_be_rewritten(self):
        forged = copy.deepcopy(self.approval_v2)
        forged["prior_asymmetric_approval_verification_sha256"] = "0" * 64
        forged = self.rehash(forged)
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_v2_prior_approval_reconstruction_mismatch"):
            self.build(forged, expected_approval=forged["asymmetric_approval_verification_sha256"])


if __name__ == "__main__":
    unittest.main()
