from __future__ import annotations
import copy, importlib.util, json
from r83_attestation_set_fixtures import set_policy as r83_set_policy
from r84_reviewer_key_possession_fixtures import m as r84m, policy as r84_policy
from r85_external_verifier_provenance_fixtures import AUTHORITY_ROOT_SHA256, m as r85m, provenance_policy
from r86_external_assertion_replay_guard_fixtures import m as r86m, replay_policy
from r87_external_assertion_replay_atomic_cas_fixtures import m as r87m, atomic_cas_policy
from r88_external_assertion_replay_writer_fencing_recovery_fixtures import m as r88m, writer_fencing_recovery_policy
from r89_external_assertion_replay_writer_authority_anchor_fixtures import WRITER_AUTHORITY_ROOT_SHA256, m as r89m, writer_authority_anchor_policy
from r90_external_assertion_replay_dual_state_atomicity_fixtures import ROOT, m as r90m, dual_state_atomicity_policy, r89_context, atomicity_verification

POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_DURABLE_COMMIT_READBACK_EVIDENCE_POLICY_V1.json"
CONTRACT=ROOT/"tools"/"tradingos_external_assertion_replay_durable_commit_readback_evidence_contract.py"
s=importlib.util.spec_from_file_location("r91c",CONTRACT); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

EXTERNAL_COMMIT_RECEIPT_SHA256="e"*64
READBACK_STATE_SHA256="f"*64
READBACK_EVIDENCE_SHA256="1"*64

def commit_readback_evidence_policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))

def r90_context():
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
        recovery_receipt, r88_binding, anchor, r89_binding,
    )=r89_context()
    atomicity_receipt=atomicity_verification(r89_binding)
    r90_binding=r90m.build_external_assertion_replay_dual_state_atomicity_binding(
        r89_binding,r88_binding,r87_binding,r86_binding,r85_binding,r84_binding,
        manifest,items,r83_set_policy(),aid,assertion,verifier_reg,replay_reg,
        atomic_receipt,recovery_receipt,anchor,atomicity_receipt,
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
        expected_atomicity_verification_sha256=r90m.stable_sha256(atomicity_receipt),
        dual_state_atomicity_policy=dual_state_atomicity_policy())
    return (
        items,manifest,aid,assertion,r84_binding,verifier_reg,r85_binding,replay_reg,r86_binding,
        atomic_receipt,r87_binding,recovery_receipt,r88_binding,anchor,r89_binding,atomicity_receipt,r90_binding)

def commit_readback_evidence(r90_binding=None):
    if r90_binding is None: *_,r90_binding=r90_context()
    return {
        "schema":m.EVIDENCE_SCHEMA,
        "evidence_scope":"R90_RECEIPT_INDEX_COMMIT_AND_READBACK_ONLY",
        "r90_binding_id":r90_binding["binding_id"],
        "r90_binding_sha256":m.stable_sha256(r90_binding),
        "authority_anchor_sha256":r90_binding["authority_anchor_sha256"],
        "writer_authority_root_sha256":r90_binding["writer_authority_root_sha256"],
        "writer_lease_sha256":r90_binding["writer_lease_sha256"],
        "prior_receipt_index_sha256":r90_binding["prior_receipt_index_sha256"],
        "commit_receipt_index_sha256":r90_binding["next_receipt_index_candidate_sha256"],
        "readback_receipt_index_sha256":r90_binding["next_receipt_index_candidate_sha256"],
        "lease_lineage_sha256":r90_binding["lease_lineage_sha256"],
        "commit_id":r90_binding["commit_id"],
        "idempotency_key_sha256":r90_binding["idempotency_key_sha256"],
        "external_commit_receipt_sha256":EXTERNAL_COMMIT_RECEIPT_SHA256,
        "readback_state_sha256":READBACK_STATE_SHA256,
        "readback_evidence_sha256":READBACK_EVIDENCE_SHA256,
        "receipt_identity_bound":True,
        "read_after_write_match":True,
        "commit_receipt_retained":True,
        "readback_retained":True,
        "backend_commit_authenticity_verified":False,
        "backend_identity_verified":False,
        "live_backend_observed":False,
        "durable_commit_proven":False,
        "durable_dual_state_atomicity_proven":False,
        "write_performed":False,
        "global_current_state_verified":False,
        "concurrent_writer_exclusion_proven":False,
        "registry_write_performed":False,
        "lease_registry_write_performed":False,
        "receipt_index_write_performed":False,
        "backend_write_performed":False,
        "execution_authority":"NONE",
        "can_execute":False,
        "apply_allowed":False,
        "confers_authority":False,
    }

def build(evidence=None):
    (
        items,manifest,aid,assertion,r84_binding,verifier_reg,r85_binding,replay_reg,r86_binding,
        atomic_receipt,r87_binding,recovery_receipt,r88_binding,anchor,r89_binding,atomicity_receipt,r90_binding
    )=r90_context()
    evidence=commit_readback_evidence(r90_binding) if evidence is None else evidence
    return m.build_external_assertion_replay_durable_commit_readback_evidence_binding(
        r90_binding,r89_binding,r88_binding,r87_binding,r86_binding,r85_binding,r84_binding,
        manifest,items,r83_set_policy(),aid,assertion,verifier_reg,replay_reg,atomic_receipt,
        recovery_receipt,anchor,atomicity_receipt,evidence,
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
        expected_atomicity_verification_sha256=r90m.stable_sha256(atomicity_receipt),
        dual_state_atomicity_policy=dual_state_atomicity_policy(),
        expected_commit_readback_evidence_sha256=m.stable_sha256(evidence),
        commit_readback_evidence_policy=commit_readback_evidence_policy())

def clone(v): return copy.deepcopy(v)
