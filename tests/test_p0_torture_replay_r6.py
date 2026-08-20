from __future__ import annotations

import copy
import unittest
from datetime import datetime

from tools.tradingos_shadow_integration import (
    SHADOW_SAFETY,
    ShadowIntegrationError,
    build_trade_case,
    build_trade_decision_packet,
    build_trade_thesis,
    normalize_triaxis_adjudication,
    sha256_obj,
)
from tools.unified_shadow_domain_subjects import build_human_reveal_receipt
from tools.unified_shadow_human_asymmetric_custody import (
    build_asymmetric_reveal_closure,
    derive_reveal_intent_sha256,
)

CASE_SHA_FILL = "a"
CREDENTIAL_ID = "5" * 64
PUBLIC_KEY_SHA = "6" * 64
ROOT = "1" * 64
PRIOR_CREDENTIAL = "2" * 64
PRIOR_NONCE = "3" * 64

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

DOMAIN_EFFECTS = {
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


def make_case():
    return build_trade_case(
        case_id="case-r6-001",
        frozen_at="2026-08-20T03:00:00+07:00",
        symbol="BTCUSDT",
        venue="Binance",
        timeframe="1h",
        scenario="R6 asymmetric custody fixture.",
        snapshot_ref={"source_id": "snapshot:r6", "sha256": "a" * 64, "schema": "market.snapshot/v1"},
        vision_ref={"source_id": "vision:r6", "sha256": "b" * 64, "schema": "vision.market/v1"},
    )


def make_twin(case):
    body = {
        "schema": "sct.prediction/v3",
        "case_id": case["case_id"],
        "arm": "sct",
        "options": tuple(case["options"]),
        "option_probabilities": {"LONG": 0.7, "SHORT": 0.1, "WAIT": 0.2},
        "predicted_choice": "LONG",
        "confidence": 0.7,
        "reasons": ("fixture",),
        "change_conditions": ("invalidate",),
        "would_escalate": False,
        "committed_at": datetime.fromisoformat("2026-08-20T03:01:00+07:00").timestamp(),
        "execution_authority": "NONE",
        "can_execute": False,
    }
    body["prediction_id"] = sha256_obj(body)
    return body


def make_packet(case, twin):
    thesis = build_trade_thesis(case, {"schema": "tradingos.decision_brief.v2", "status": "READY", "stance": "WATCH_LONG"})
    triaxis = normalize_triaxis_adjudication(
        case_id=case["case_id"],
        verdict="PASS",
        strongest_case=("support",),
        falsifiers=(),
        surviving_claims=("support",),
        evidence_refs=("snapshot:r6", "vision:r6"),
    )
    return build_trade_decision_packet(
        case,
        thesis,
        twin,
        triaxis,
        {"veto": False, "reasons": (), "can_trade": False, "capital_permission": "DENY"},
    )


def make_manifest(case, reveal):
    body = {
        "schema": "tradingos.shadow_domain_subject_manifest.v1",
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "subjects": (
            {"event_type": "CASE_QUALIFIED", "subject_sha256": "1" * 64, "subject_schema": "tradingos.trusted_replay_input.v1"},
            {"event_type": "TWIN_COMMITTED", "subject_sha256": reveal["twin_prediction_id"], "subject_schema": "sct.prediction/v3"},
            {"event_type": "DECISION_PACKET", "subject_sha256": reveal["packet_sha256"], "subject_schema": "tradingos.trade_decision_packet.v1"},
            {"event_type": "HUMAN_REVEAL", "subject_sha256": reveal["reveal_sha256"], "subject_schema": reveal["schema"]},
            {"event_type": "OUTCOME_RECEIPT", "subject_sha256": "4" * 64, "subject_schema": "tradingos.trade_outcome_receipt.v1"},
            {"event_type": "RETURN_INTAKE", "subject_sha256": "5" * 64, "subject_schema": "control_return_broker.shadow_intake_receipt.v1"},
        ),
        "subject_binding_complete": True,
        "semantic_acceptance": "NOT_PERFORMED",
        "effects": dict(DOMAIN_EFFECTS),
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
    }
    body["subject_manifest_sha256"] = sha256_obj(body)
    return body


def make_domain_closure(case, manifest):
    body = {
        "schema": "bitevo.shadow_domain_history_closure.v1",
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "case_binding_sha256": "6" * 64,
        "admission_candidate_sha256": "7" * 64,
        "history_verification_sha256": "8" * 64,
        "subject_manifest_sha256": manifest["subject_manifest_sha256"],
        "domain_history_verification_sha256": "9" * 64,
        "case_qualified_replay_input_sha256": "1" * 64,
        "subject_binding_complete": True,
        "admission_binding_complete": True,
        "status": "DOMAIN_HISTORY_CLOSED_SHADOW_ONLY",
        "history_write_performed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "effects": dict(DOMAIN_EFFECTS),
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
        "generated_at": "2026-08-20T03:12:00+07:00",
    }
    body["domain_history_closure_sha256"] = sha256_obj(body)
    return body


def make_registry_candidate(schema, *, sign_count=None):
    if schema.endswith("credential_registry_snapshot.v1"):
        body = {
            "schema": schema,
            "registry_id": "credentials-r6",
            "authority_root_sha256": ROOT,
            "entries": ({
                "human_subject_id": "robert",
                "device_id": "device-r6",
                "custody_provider_id": "custody-r6",
                "credential_id_sha256": CREDENTIAL_ID,
                "public_key_sha256": PUBLIC_KEY_SHA,
                "algorithm": "ED25519",
                "key_epoch": 3,
                "status": "ACTIVE",
                "not_before": "2026-08-20T00:00:00+07:00",
                "not_after": "2026-08-21T00:00:00+07:00",
                "revoked_at": None,
                "counter_supported": True,
                "sign_count": 42 if sign_count is None else sign_count,
            },),
            "entry_count": 1,
            "write_allowed": False,
            "apply_allowed": False,
            "execution_authority": "NONE",
            "safety": dict(SHADOW_SAFETY),
            "effects": dict(CONTROL_EFFECTS),
        }
    else:
        body = {
            "schema": schema,
            "registry_id": "nonces-r6",
            "authority_root_sha256": ROOT,
            "epoch_number": 12,
            "epoch_started_at": "2026-08-20T00:00:00+07:00",
            "epoch_expires_at": "2026-08-21T00:00:00+07:00",
            "previous_epoch_sha256": "f" * 64,
            "used_nonce_sha256s": ("a" * 64,),
            "used_challenge_ids": ("b" * 64,),
            "cumulative_history": True,
            "write_allowed": False,
            "apply_allowed": False,
            "execution_authority": "NONE",
            "safety": dict(SHADOW_SAFETY),
            "effects": dict(CONTROL_EFFECTS),
        }
    body["registry_sha256"] = sha256_obj(body)
    return body


def make_approval(case, reveal):
    next_credential = make_registry_candidate("control_center.shadow_human_credential_registry_snapshot.v1")
    next_nonce = make_registry_candidate("control_center.shadow_human_nonce_epoch_registry_snapshot.v1")
    body = {
        "schema": "control_center.shadow_asymmetric_human_approval_verification.v1",
        "challenge_id": "b" * 64,
        "challenge_sha256": "c" * 64,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "packet_sha256": reveal["packet_sha256"],
        "twin_prediction_id": reveal["twin_prediction_id"],
        "human_subject_id": "robert",
        "session_id": "session-r6",
        "device_id": "device-r6",
        "custody_provider_id": "custody-r6",
        "credential_id_sha256": CREDENTIAL_ID,
        "public_key_sha256": PUBLIC_KEY_SHA,
        "algorithm": "ED25519",
        "key_epoch": 3,
        "signature_sha256": "d" * 64,
        "verifier_id": "webauthn-verifier-r6",
        "verifier_key_id": "verifier-key-r6-01",
        "origin": "https://control.example.invalid",
        "rp_id": "control.example.invalid",
        "actual_choice": reveal["actual_choice"],
        "responded_at": reveal["decided_at"],
        "verified_at": "2026-08-20T03:06:00+07:00",
        "approved_reveal_intent_sha256": derive_reveal_intent_sha256(
            case_id=case["case_id"],
            case_sha256=case["case_sha256"],
            packet_sha256=reveal["packet_sha256"],
            twin_prediction_id=reveal["twin_prediction_id"],
            actual_choice=reveal["actual_choice"],
            responded_at=reveal["decided_at"],
        ),
        "signature_verified_by_external_asymmetric_verifier": True,
        "local_signature_math_verified": False,
        "user_present": True,
        "user_verified": True,
        "physical_human_presence_proven": False,
        "credential_status_verified": True,
        "credential_epoch_verified": True,
        "credential_counter_verified": True,
        "nonce_unused_in_expected_cumulative_registry": True,
        "challenge_unused_in_expected_cumulative_registry": True,
        "nonce_epoch_verified": True,
        "prior_credential_registry_sha256": PRIOR_CREDENTIAL,
        "next_credential_registry_candidate": next_credential,
        "next_credential_registry_candidate_sha256": next_credential["registry_sha256"],
        "prior_nonce_registry_sha256": PRIOR_NONCE,
        "next_nonce_registry_candidate": next_nonce,
        "next_nonce_registry_candidate_sha256": next_nonce["registry_sha256"],
        "registry_write_performed": False,
        "approval_scope": "HUMAN_REVEAL_ONLY",
        "status": "ASYMMETRIC_HUMAN_APPROVAL_VERIFIED_SHADOW_ONLY",
        "human_identity_scope": "CREDENTIAL_SUBJECT_ASSERTION_ONLY",
        "cryptographic_property": "EXTERNAL_ASYMMETRIC_SIGNATURE_VERIFIER_ASSERTION",
        "execution_authority": "NONE",
        "can_execute": False,
        "apply_allowed": False,
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(CONTROL_EFFECTS),
    }
    body["asymmetric_approval_verification_sha256"] = sha256_obj(body)
    return body


class P0TortureReplayR6Tests(unittest.TestCase):
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
        self.approval = make_approval(self.case, self.reveal)

    def build(self, approval=None, **overrides):
        approval = self.approval if approval is None else approval
        return build_asymmetric_reveal_closure(
            self.case,
            self.reveal,
            self.manifest,
            self.domain,
            approval,
            expected_asymmetric_approval_verification_sha256=overrides.pop("expected_approval", approval["asymmetric_approval_verification_sha256"]),
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
            generated_at=overrides.pop("generated_at", "2026-08-20T03:15:00+07:00"),
            **overrides,
        )

    @staticmethod
    def rehash(approval):
        approval["asymmetric_approval_verification_sha256"] = sha256_obj(
            {k: v for k, v in approval.items() if k != "asymmetric_approval_verification_sha256"}
        )
        return approval

    def test_valid_r6_closure_preserves_no_effect_boundary(self):
        closure = self.build()
        self.assertEqual(closure["authentication_status"], "ASYMMETRIC_CUSTODY_VERIFIED_SHADOW_ONLY")
        self.assertEqual(closure["human_identity_scope"], "CREDENTIAL_SUBJECT_ASSERTION_ONLY")
        self.assertFalse(closure["local_signature_math_verified"])
        self.assertFalse(closure["physical_human_presence_proven"])
        self.assertTrue(closure["single_use_nonce_candidate_verified"])
        self.assertTrue(closure["credential_epoch_verified"])
        self.assertTrue(all(value is False for value in closure["effects"].values()))
        self.assertEqual(closure["execution_authority"], "NONE")
        self.assertFalse(closure["can_execute"])

    def test_reveal_choice_transplant_is_rejected(self):
        approval = self.rehash({**self.approval, "actual_choice": "SHORT"})
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_approval_reveal_mismatch"):
            self.build(approval)

    def test_old_key_epoch_policy_is_rejected(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_key_epoch_mismatch"):
            self.build(expected_key_epoch=4)

    def test_public_key_policy_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_public_key_mismatch"):
            self.build(expected_public_key="9" * 64)

    def test_origin_policy_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_origin_or_rp_mismatch"):
            self.build(expected_origin="https://evil.example.invalid")

    def test_nonce_registry_external_digest_is_required(self):
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_prior_nonce_registry_sha256_external_digest_mismatch"):
            self.build(expected_nonce_registry="0" * 64)

    def test_locally_rehashed_approval_cannot_replace_retained_digest(self):
        approval = self.rehash({**self.approval, "verifier_key_id": "different-key"})
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_approval_external_digest_mismatch"):
            self.build(approval, expected_approval=self.approval["asymmetric_approval_verification_sha256"])

    def test_local_signature_verification_overclaim_is_rejected(self):
        approval = self.rehash({**self.approval, "local_signature_math_verified": True})
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_local_signature_math_overclaim"):
            self.build(approval)

    def test_physical_presence_overclaim_is_rejected(self):
        approval = self.rehash({**self.approval, "physical_human_presence_proven": True})
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_physical_presence_overclaim"):
            self.build(approval)

    def test_nonce_guard_cannot_be_dropped(self):
        approval = self.rehash({**self.approval, "nonce_unused_in_expected_cumulative_registry": False})
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_nonce_or_challenge_guard_missing"):
            self.build(approval)

    def test_registry_candidate_effect_smuggling_is_rejected(self):
        approval = copy.deepcopy(self.approval)
        candidate = dict(approval["next_nonce_registry_candidate"])
        candidate["effects"] = dict(candidate["effects"])
        candidate["effects"]["nonce_registry_write"] = True
        candidate["registry_sha256"] = sha256_obj({k: v for k, v in candidate.items() if k != "registry_sha256"})
        approval["next_nonce_registry_candidate"] = candidate
        approval["next_nonce_registry_candidate_sha256"] = candidate["registry_sha256"]
        approval = self.rehash(approval)
        with self.assertRaisesRegex(ShadowIntegrationError, "human_asym_next_nonce_registry_candidate_effect_boundary_breached"):
            self.build(approval)


if __name__ == "__main__":
    unittest.main()
