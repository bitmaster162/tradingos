from __future__ import annotations

import copy
import importlib.util
import json

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
    m as r88m,
    writer_fencing_recovery_policy,
)
from r89_external_assertion_replay_writer_authority_anchor_fixtures import (
    ROOT,
    WRITER_AUTHORITY_ROOT_SHA256,
    m as r89m,
    writer_authority_anchor_policy,
    r88_context,
    authority_anchor,
)

POLICY = ROOT / "configs" / "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_DUAL_STATE_ATOMICITY_POLICY_V1.json"
CONTRACT = ROOT / "tools" / "tradingos_external_assertion_replay_dual_state_atomicity_contract.py"

s = importlib.util.spec_from_file_location("r90c", CONTRACT)
assert s and s.loader
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

NEXT_RECEIPT_INDEX_CANDIDATE_SHA256 = "b" * 64
LEASE_LINEAGE_SHA256 = "c" * 64
IDEMPOTENCY_KEY_SHA256 = "d" * 64

def dual_state_atomicity_policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))

def r89_context():
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
        recovery_receipt, r88_binding,
    ) = r88_context()
    anchor = authority_anchor(r88_binding)
    r89_binding = r89m.build_external_assertion_replay_writer_authority_anchor_binding(
        r88_binding, r87_binding, r86_binding, r85_binding, r84_binding,
        manifest, items, r83_set_policy(), aid, assertion, verifier_reg,
        replay_reg, atomic_receipt, recovery_receipt, anchor,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_verifier_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        expected_writer_authority_root_sha256=WRITER_AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),
        replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),
        atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),
        writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=r89m.stable_sha256(anchor),
        writer_authority_anchor_policy=writer_authority_anchor_policy(),
    )
    return (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
        recovery_receipt, r88_binding, anchor, r89_binding,
    )

def atomicity_verification(r89_binding=None):
    if r89_binding is None:
        *_, r89_binding = r89_context()
    return {
        "schema": m.ATOMICITY_VERIFICATION_SCHEMA,
        "atomicity_scope": "WRITER_LEASE_AND_RECEIPT_INDEX_DUAL_STATE_ONLY",
        "r89_binding_id": r89_binding["binding_id"],
        "r89_binding_sha256": m.stable_sha256(r89_binding),
        "authority_anchor_sha256": r89_binding["authority_anchor_sha256"],
        "writer_authority_root_sha256": r89_binding["authority_root_sha256"],
        "writer_lease_sha256": r89_binding["writer_lease_sha256"],
        "prior_receipt_index_sha256": r89_binding["current_receipt_index_sha256"],
        "next_receipt_index_candidate_sha256": NEXT_RECEIPT_INDEX_CANDIDATE_SHA256,
        "lease_lineage_sha256": LEASE_LINEAGE_SHA256,
        "commit_id": "commit-r90-0001",
        "idempotency_key_sha256": IDEMPOTENCY_KEY_SHA256,
        "observed_pair_state": "PROTOCOL_CANDIDATE_ONLY_NO_DURABLE_BACKEND",
        "dual_state_atomicity_model": "ONE_TRANSACTION_TWO_LOGICAL_RECORDS",
        "split_state_rejected": True,
        "lease_epoch_lineage_verified": True,
        "aba_guard_verified": True,
        "durability_status": "PROTOCOL_VERIFIED_NO_DURABLE_BACKEND",
        "write_performed": False,
        "live_backend_observed": False,
        "durable_commit_proven": False,
        "durable_dual_state_atomicity_proven": False,
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

def build(atomicity_receipt=None):
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
        recovery_receipt, r88_binding, anchor, r89_binding,
    ) = r89_context()
    atomicity_receipt = (
        atomicity_verification(r89_binding)
        if atomicity_receipt is None
        else atomicity_receipt
    )
    return m.build_external_assertion_replay_dual_state_atomicity_binding(
        r89_binding, r88_binding, r87_binding, r86_binding, r85_binding, r84_binding,
        manifest, items, r83_set_policy(), aid, assertion, verifier_reg, replay_reg,
        atomic_receipt, recovery_receipt, anchor, atomicity_receipt,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_verifier_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        expected_writer_authority_root_sha256=WRITER_AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),
        replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),
        atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),
        writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=r89m.stable_sha256(anchor),
        writer_authority_anchor_policy=writer_authority_anchor_policy(),
        expected_atomicity_verification_sha256=m.stable_sha256(atomicity_receipt),
        dual_state_atomicity_policy=dual_state_atomicity_policy(),
    )

def clone(value):
    return copy.deepcopy(value)
