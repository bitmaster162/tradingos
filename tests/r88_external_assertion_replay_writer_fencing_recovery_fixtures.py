from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from r83_attestation_set_fixtures import set_policy as r83_set_policy
from r84_reviewer_key_possession_fixtures import m as r84m, policy as r84_policy
from r85_external_verifier_provenance_fixtures import (
    AUTHORITY_ROOT_SHA256,
    m as r85m,
    provenance_policy,
)
from r86_external_assertion_replay_guard_fixtures import m as r86m, replay_policy
from r87_external_assertion_replay_atomic_cas_fixtures import (
    ROOT,
    m as r87m,
    atomic_cas_policy,
    r86_context,
    atomic_verification,
)

HERE = Path(__file__).resolve().parent
POLICY = ROOT / "configs" / "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_WRITER_FENCING_RECOVERY_POLICY_V1.json"
CONTRACT = ROOT / "tools" / "tradingos_external_assertion_replay_writer_fencing_recovery_contract.py"

s = importlib.util.spec_from_file_location("r88c", CONTRACT)
assert s and s.loader
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

WRITER_LEASE_SHA256 = "7" * 64
RECEIPT_CANDIDATE_SHA256 = "8" * 64
CURRENT_RECEIPT_INDEX_SHA256 = "9" * 64


def writer_fencing_recovery_policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def r87_context():
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding,
    ) = r86_context()
    atomic_receipt = atomic_verification(r86_binding)
    r87_binding = r87m.build_external_assertion_replay_atomic_cas_binding(
        r86_binding,
        r85_binding,
        r84_binding,
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        verifier_reg,
        replay_reg,
        atomic_receipt,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),
        replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),
        atomic_cas_policy=atomic_cas_policy(),
    )
    return (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
    )


def recovery_verification(r87_binding=None):
    if r87_binding is None:
        *_, r87_binding = r87_context()
    return {
        "schema": m.RECOVERY_VERIFICATION_SCHEMA,
        "recovery_scope": "EXTERNAL_ASSERTION_REPLAY_REGISTRY_WRITER_ONLY",
        "r87_binding_id": r87_binding["binding_id"],
        "r87_binding_sha256": m.stable_sha256(r87_binding),
        "atomic_verification_sha256": r87_binding["atomic_verification_sha256"],
        "replay_registry_sha256": r87_binding["replay_registry_sha256"],
        "next_registry_candidate_sha256": r87_binding["next_registry_candidate_sha256"],
        "cas_generation_from": r87_binding["cas_generation_from"],
        "cas_generation_to": r87_binding["cas_generation_to"],
        "writer_lease_sha256": WRITER_LEASE_SHA256,
        "receipt_candidate_sha256": RECEIPT_CANDIDATE_SHA256,
        "current_receipt_index_sha256": CURRENT_RECEIPT_INDEX_SHA256,
        "attempt_fencing_token": 12,
        "current_fencing_token": 12,
        "fencing_model": "MONOTONIC_FENCING_TOKEN_PLUS_LEASE_DIGEST",
        "crash_recovery_protocol": "READBACK_PLUS_RECEIPT_INDEX_DEDUP",
        "blind_retry_allowed": False,
        "split_brain_same_token_rejected": True,
        "stale_writer_fenced": False,
        "crash_point": "BEFORE_CAS",
        "recovery_status": "NO_WRITE_OBSERVED_RETRY_REQUIRES_FRESH_CAS",
        "recovery_action": "RETRY_WITH_FRESH_CAS",
        "live_writer_backend_proven": False,
        "commit_performed": False,
        "registry_write_performed": False,
        "lease_registry_write_performed": False,
        "receipt_index_write_performed": False,
        "backend_write_performed": False,
        "durable_commit_proven": False,
        "global_current_state_verified": False,
        "concurrent_writer_exclusion_proven": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "apply_allowed": False,
        "confers_authority": False,
    }


def build(recovery_receipt=None):
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
    ) = r87_context()
    recovery_receipt = (
        recovery_verification(r87_binding) if recovery_receipt is None else recovery_receipt
    )
    return m.build_external_assertion_replay_writer_fencing_recovery_binding(
        r87_binding,
        r86_binding,
        r85_binding,
        r84_binding,
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        verifier_reg,
        replay_reg,
        atomic_receipt,
        recovery_receipt,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),
        replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),
        atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=m.stable_sha256(recovery_receipt),
        writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
    )


def clone(value):
    return copy.deepcopy(value)
