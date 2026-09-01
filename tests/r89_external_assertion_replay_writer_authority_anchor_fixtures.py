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
from r87_external_assertion_replay_atomic_cas_fixtures import m as r87m, atomic_cas_policy
from r88_external_assertion_replay_writer_fencing_recovery_fixtures import (
    ROOT,
    m as r88m,
    writer_fencing_recovery_policy,
    r87_context,
    recovery_verification,
)

HERE = Path(__file__).resolve().parent
POLICY = ROOT / "configs" / "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_WRITER_AUTHORITY_ANCHOR_POLICY_V1.json"
CONTRACT = ROOT / "tools" / "tradingos_external_assertion_replay_writer_authority_anchor_contract.py"

s = importlib.util.spec_from_file_location("r89c", CONTRACT)
assert s and s.loader
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

WRITER_AUTHORITY_ROOT_SHA256 = "a" * 64


def writer_authority_anchor_policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def r88_context():
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
    ) = r87_context()
    recovery_receipt = recovery_verification(r87_binding)
    r88_binding = r88m.build_external_assertion_replay_writer_fencing_recovery_binding(
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
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),
        writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
    )
    return (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
        recovery_receipt, r88_binding,
    )


def authority_anchor(r88_binding=None):
    if r88_binding is None:
        *_, r88_binding = r88_context()
    return {
        "schema": m.AUTHORITY_ANCHOR_SCHEMA,
        "anchor_scope": "WRITER_LEASE_AND_RECEIPT_INDEX_ONLY",
        "r88_binding_id": r88_binding["binding_id"],
        "r88_binding_sha256": m.stable_sha256(r88_binding),
        "recovery_verification_sha256": r88_binding["recovery_verification_sha256"],
        "writer_lease_sha256": r88_binding["writer_lease_sha256"],
        "current_receipt_index_sha256": r88_binding["current_receipt_index_sha256"],
        "receipt_candidate_sha256": r88_binding["receipt_candidate_sha256"],
        "current_fencing_token": r88_binding["current_fencing_token"],
        "authority_root_sha256": WRITER_AUTHORITY_ROOT_SHA256,
        "retained_reference_required": True,
        "root_trust_verified": False,
        "anchor_operator_identity_verified": False,
        "live_writer_backend_proven": False,
        "durable_commit_proven": False,
        "global_current_state_verified": False,
        "concurrent_writer_exclusion_proven": False,
        "registry_write_performed": False,
        "lease_registry_write_performed": False,
        "receipt_index_write_performed": False,
        "backend_write_performed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "apply_allowed": False,
        "confers_authority": False,
    }


def build(anchor=None):
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
        recovery_receipt, r88_binding,
    ) = r88_context()
    anchor = authority_anchor(r88_binding) if anchor is None else anchor
    return m.build_external_assertion_replay_writer_authority_anchor_binding(
        r88_binding,
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
        anchor,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_authority_root_sha256=WRITER_AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),
        replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),
        atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),
        writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=m.stable_sha256(anchor),
        writer_authority_anchor_policy=writer_authority_anchor_policy(),
    )


def clone(value):
    return copy.deepcopy(value)
