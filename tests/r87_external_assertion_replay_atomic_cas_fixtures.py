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
from r86_external_assertion_replay_guard_fixtures import (
    ROOT,
    m as r86m,
    replay_policy,
    r85_context,
    replay_registry,
)

HERE = Path(__file__).resolve().parent
POLICY = ROOT / "configs" / "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_ATOMIC_CAS_POLICY_V1.json"
CONTRACT = ROOT / "tools" / "tradingos_external_assertion_replay_atomic_cas_contract.py"

s = importlib.util.spec_from_file_location("r87c", CONTRACT)
assert s and s.loader
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


def atomic_cas_policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def r86_context():
    items, manifest, aid, assertion, r84_binding, verifier_reg, r85_binding = r85_context()
    replay_reg = replay_registry(r84_binding)
    r86_binding = r86m.build_external_assertion_replay_guard_binding(
        r85_binding,
        r84_binding,
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        verifier_reg,
        replay_reg,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),
        replay_guard_policy=replay_policy(),
    )
    return (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding,
    )


def atomic_verification(r86_binding=None):
    if r86_binding is None:
        *_, r86_binding = r86_context()
    return {
        "schema": m.ATOMIC_VERIFICATION_SCHEMA,
        "atomic_scope": "EXTERNAL_ASSERTION_REPLAY_REGISTRY_ONLY",
        "r86_binding_id": r86_binding["binding_id"],
        "r86_binding_sha256": m.stable_sha256(r86_binding),
        "replay_registry_sha256": r86_binding["replay_registry_sha256"],
        "next_registry_candidate_sha256": r86_binding["next_registry_candidate_sha256"],
        "external_assertion_sha256": r86_binding["external_assertion_sha256"],
        "challenge_sha256": r86_binding["challenge_sha256"],
        "cas_generation_from": r86_binding["prior_generation"],
        "cas_generation_to": r86_binding["next_generation"],
        "toctou_guard_model": "COMPARE_AND_SWAP_PRECONDITION",
        "atomicity_status": "PROTOCOL_VERIFIED_NO_DURABLE_COMMIT",
        "single_use_status": "CANDIDATE_ONLY_NOT_DURABLY_ENFORCED",
        "commit_performed": False,
        "registry_write_performed": False,
        "durable_commit_proven": False,
        "global_current_state_verified": False,
        "concurrent_writer_exclusion_proven": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "apply_allowed": False,
        "confers_authority": False,
    }


def build(atomic_receipt=None):
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding,
    ) = r86_context()
    atomic_receipt = atomic_verification(r86_binding) if atomic_receipt is None else atomic_receipt
    return m.build_external_assertion_replay_atomic_cas_binding(
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
        expected_atomic_verification_sha256=m.stable_sha256(atomic_receipt),
        atomic_cas_policy=atomic_cas_policy(),
    )


def clone(value):
    return copy.deepcopy(value)
