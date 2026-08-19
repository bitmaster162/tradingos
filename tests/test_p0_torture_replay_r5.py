from __future__ import annotations

import copy
import unittest

from tests.test_p0_torture_replay_r4 import build_fixture
from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_domain_history_closure import build_domain_history_closure
from tools.unified_shadow_domain_subjects import build_domain_history_verification
from tools.unified_shadow_human_custody import (
    APPROVAL_REGISTRY_SCHEMA,
    APPROVAL_VERIFICATION_SCHEMA,
    build_authenticated_reveal_closure,
    derive_reveal_intent_sha256,
)

CONTROL_EFFECTS = {
    "human_gate_write": False,
    "current_truth_apply": False,
    "decision_ledger_write": False,
    "command_queue_write": False,
    "runtime_activation": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}

HUMAN = "operator:owner"
PROVIDER = "custody:test"
VERIFIER = "verifier:test"
KEY_ID = "key:test:v1"


def closed_r4_fixture():
    case, replay, twin, packet, reveal, outcome, intake, manifest, adm, candidates, history = build_fixture()
    domain = build_domain_history_verification(
        case,
        history,
        candidates,
        manifest,
        expected_history_verification_sha256=history["history_verification_sha256"],
        generated_at="2026-08-20T02:17:00+07:00",
    )
    closure = build_domain_history_closure(
        case,
        adm,
        history,
        manifest,
        domain,
        generated_at="2026-08-20T02:18:00+07:00",
    )
    return case, reveal, manifest, closure


def approval(case, reveal, **overrides):
    challenge_id = "1" * 64
    challenge_sha = "2" * 64
    attestation_sha = "3" * 64
    registry = {
        "schema": APPROVAL_REGISTRY_SCHEMA,
        "registry_id": "human-approval-registry:r5",
        "authority_root_sha256": "4" * 64,
        "entries": (
            {
                "challenge_id": challenge_id,
                "challenge_sha256": challenge_sha,
                "attestation_sha256": attestation_sha,
            },
        ),
        "entry_count": 1,
        "write_allowed": False,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(CONTROL_EFFECTS),
    }
    registry["registry_sha256"] = sha256_obj(registry)
    body = {
        "schema": APPROVAL_VERIFICATION_SCHEMA,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "packet_sha256": reveal["packet_sha256"],
        "twin_prediction_id": reveal["twin_prediction_id"],
        "challenge_id": challenge_id,
        "challenge_sha256": challenge_sha,
        "attestation_sha256": attestation_sha,
        "prior_registry_sha256": "5" * 64,
        "next_registry_candidate": registry,
        "next_registry_candidate_sha256": registry["registry_sha256"],
        "human_subject_id": HUMAN,
        "session_id": "session:r5:001",
        "device_id": "device:r5:trusted",
        "custody_provider_id": PROVIDER,
        "verifier_id": VERIFIER,
        "verifier_key_id": KEY_ID,
        "actual_choice": reveal["actual_choice"],
        "responded_at": reveal["decided_at"],
        "verified_at": "2026-08-20T02:11:00+07:00",
        "approved_reveal_intent_sha256": derive_reveal_intent_sha256(
            case_id=case["case_id"],
            case_sha256=case["case_sha256"],
            packet_sha256=reveal["packet_sha256"],
            twin_prediction_id=reveal["twin_prediction_id"],
            actual_choice=reveal["actual_choice"],
            responded_at=reveal["decided_at"],
        ),
        "custody_mac_verified": True,
        "challenge_window_verified": True,
        "challenge_unused_in_expected_registry": True,
        "single_use_status": "ADMITTABLE_UNUSED_CHALLENGE_SHADOW_ONLY",
        "human_identity_scope": "CUSTODY_PROVIDER_SUBJECT_ASSERTION_ONLY",
        "cryptographic_property": "HMAC_SHA256_VERIFIER_KEY_POSSESSION",
        "physical_human_presence_proven": False,
        "approval_scope": "HUMAN_REVEAL_ONLY",
        "status": "HUMAN_CUSTODY_APPROVAL_VERIFIED_SHADOW_ONLY",
        "registry_write_performed": False,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(CONTROL_EFFECTS),
    }
    body.update(overrides)
    body["approval_verification_sha256"] = sha256_obj(body)
    return body


def close(case, reveal, manifest, domain_closure, app, **kwargs):
    params = {
        "expected_approval_verification_sha256": app["approval_verification_sha256"],
        "expected_human_subject_id": HUMAN,
        "expected_custody_provider_id": PROVIDER,
        "expected_verifier_id": VERIFIER,
        "expected_verifier_key_id": KEY_ID,
        "generated_at": "2026-08-20T02:19:00+07:00",
    }
    params.update(kwargs)
    return build_authenticated_reveal_closure(case, reveal, manifest, domain_closure, app, **params)


class P0TortureReplayR5Tests(unittest.TestCase):
    def test_authenticated_reveal_closes_without_effect_or_execution_authority(self):
        case, reveal, manifest, domain_closure = closed_r4_fixture()
        app = approval(case, reveal)
        result = close(case, reveal, manifest, domain_closure, app)
        self.assertEqual(result["status"] if "status" in result else result["authentication_status"], "TRUSTED_CUSTODY_ATTESTED_SHADOW_ONLY")
        self.assertEqual(result["authentication_status"], "TRUSTED_CUSTODY_ATTESTED_SHADOW_ONLY")
        self.assertFalse(result["physical_human_presence_proven"])
        self.assertTrue(result["single_use_registry_candidate_verified"])
        self.assertFalse(result["current_truth_promotion_allowed"])
        self.assertFalse(result["human_gate_write_performed"])
        self.assertFalse(result["apply_allowed"])
        self.assertEqual(result["execution_authority"], "NONE")
        self.assertTrue(all(value is False for value in result["effects"].values()))

    def test_wrong_externally_retained_approval_digest_is_rejected(self):
        case, reveal, manifest, domain_closure = closed_r4_fixture()
        app = approval(case, reveal)
        with self.assertRaisesRegex(ShadowIntegrationError, "human_custody_approval_external_digest_mismatch"):
            close(
                case,
                reveal,
                manifest,
                domain_closure,
                app,
                expected_approval_verification_sha256="0" * 64,
            )

    def test_packet_transplant_is_rejected_even_after_rehash(self):
        case, reveal, manifest, domain_closure = closed_r4_fixture()
        app = approval(case, reveal, packet_sha256="9" * 64)
        with self.assertRaisesRegex(ShadowIntegrationError, "human_custody_approval_upstream_binding_mismatch"):
            close(case, reveal, manifest, domain_closure, app)

    def test_choice_transplant_is_rejected(self):
        case, reveal, manifest, domain_closure = closed_r4_fixture()
        app = approval(case, reveal, actual_choice="WAIT")
        with self.assertRaisesRegex(ShadowIntegrationError, "human_custody_approval_reveal_mismatch"):
            close(case, reveal, manifest, domain_closure, app)

    def test_replayed_challenge_flag_is_rejected(self):
        case, reveal, manifest, domain_closure = closed_r4_fixture()
        app = approval(case, reveal, challenge_unused_in_expected_registry=False)
        with self.assertRaisesRegex(ShadowIntegrationError, "human_custody_challenge_replay_guard_missing"):
            close(case, reveal, manifest, domain_closure, app)

    def test_physical_presence_overclaim_is_rejected(self):
        case, reveal, manifest, domain_closure = closed_r4_fixture()
        app = approval(case, reveal, physical_human_presence_proven=True)
        with self.assertRaisesRegex(ShadowIntegrationError, "human_custody_physical_presence_overclaim"):
            close(case, reveal, manifest, domain_closure, app)

    def test_effect_smuggling_through_human_gate_is_rejected(self):
        case, reveal, manifest, domain_closure = closed_r4_fixture()
        app = approval(case, reveal)
        forged = copy.deepcopy(app)
        forged["effects"]["human_gate_write"] = True
        forged["approval_verification_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "approval_verification_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "human_custody_approval_effect_boundary_breached"):
            close(case, reveal, manifest, domain_closure, forged)

    def test_registry_candidate_must_bind_same_challenge_and_attestation(self):
        case, reveal, manifest, domain_closure = closed_r4_fixture()
        app = approval(case, reveal)
        forged = copy.deepcopy(app)
        registry = forged["next_registry_candidate"]
        rows = list(registry["entries"])
        rows[0] = {**rows[0], "attestation_sha256": "0" * 64}
        registry["entries"] = tuple(rows)
        registry["registry_sha256"] = sha256_obj({k: v for k, v in registry.items() if k != "registry_sha256"})
        forged["next_registry_candidate_sha256"] = registry["registry_sha256"]
        forged["approval_verification_sha256"] = sha256_obj({k: v for k, v in forged.items() if k != "approval_verification_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "human_custody_registry_candidate_subject_mismatch"):
            close(case, reveal, manifest, domain_closure, forged)

    def test_verifier_key_policy_mismatch_is_rejected(self):
        case, reveal, manifest, domain_closure = closed_r4_fixture()
        app = approval(case, reveal)
        with self.assertRaisesRegex(ShadowIntegrationError, "human_custody_verifier_key_mismatch"):
            close(case, reveal, manifest, domain_closure, app, expected_verifier_key_id="key:other")


if __name__ == "__main__":
    unittest.main()
