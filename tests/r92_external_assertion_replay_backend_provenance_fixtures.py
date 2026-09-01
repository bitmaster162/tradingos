from __future__ import annotations
import copy, importlib.util, json
from r83_attestation_set_fixtures import set_policy as r83_set_policy
from r84_reviewer_key_possession_fixtures import m as r84m, policy as r84_policy
from r85_external_verifier_provenance_fixtures import AUTHORITY_ROOT_SHA256, m as r85m, provenance_policy
from r86_external_assertion_replay_guard_fixtures import m as r86m, replay_policy
from r87_external_assertion_replay_atomic_cas_fixtures import m as r87m, atomic_cas_policy
from r88_external_assertion_replay_writer_fencing_recovery_fixtures import m as r88m, writer_fencing_recovery_policy
from r89_external_assertion_replay_writer_authority_anchor_fixtures import WRITER_AUTHORITY_ROOT_SHA256, m as r89m, writer_authority_anchor_policy
from r90_external_assertion_replay_dual_state_atomicity_fixtures import m as r90m, dual_state_atomicity_policy
from r91_external_assertion_replay_durable_commit_readback_evidence_fixtures import ROOT, m as r91m, commit_readback_evidence_policy, r90_context, commit_readback_evidence

POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_PROVENANCE_POLICY_V1.json"
CONTRACT=ROOT/"tools"/"tradingos_external_assertion_replay_backend_provenance_contract.py"
s=importlib.util.spec_from_file_location("r92c",CONTRACT); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

BACKEND_AUTHORITY_ROOT_SHA256="2"*64
BACKEND_METADATA_SHA256="3"*64

def backend_provenance_policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))

def r91_context():
    (
        items,manifest,aid,assertion,r84_binding,verifier_reg,r85_binding,replay_reg,r86_binding,
        atomic_receipt,r87_binding,recovery_receipt,r88_binding,anchor,r89_binding,atomicity_receipt,r90_binding
    )=r90_context()
    cr=commit_readback_evidence(r90_binding)
    r91_binding=r91m.build_external_assertion_replay_durable_commit_readback_evidence_binding(
        r90_binding,r89_binding,r88_binding,r87_binding,r86_binding,r85_binding,r84_binding,
        manifest,items,r83_set_policy(),aid,assertion,verifier_reg,replay_reg,atomic_receipt,recovery_receipt,anchor,atomicity_receipt,cr,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),expected_verifier_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        expected_writer_authority_root_sha256=WRITER_AUTHORITY_ROOT_SHA256,provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=r89m.stable_sha256(anchor),writer_authority_anchor_policy=writer_authority_anchor_policy(),
        expected_atomicity_verification_sha256=r90m.stable_sha256(atomicity_receipt),dual_state_atomicity_policy=dual_state_atomicity_policy(),
        expected_commit_readback_evidence_sha256=r91m.stable_sha256(cr),commit_readback_evidence_policy=commit_readback_evidence_policy())
    return (
        items,manifest,aid,assertion,r84_binding,verifier_reg,r85_binding,replay_reg,r86_binding,
        atomic_receipt,r87_binding,recovery_receipt,r88_binding,anchor,r89_binding,atomicity_receipt,r90_binding,cr,r91_binding)

def backend_registry():
    entries=[
        {"backend_id":"backend-decoy","backend_key_id":"key-decoy","backend_metadata_sha256":"1"*64,
         "backend_kind":"TRANSACTIONAL_STORE","receipt_format":"COMMIT_RECEIPT_V1","readback_format":"STATE_READBACK_V1"},
        {"backend_id":"backend-main","backend_key_id":"key-main","backend_metadata_sha256":BACKEND_METADATA_SHA256,
         "backend_kind":"TRANSACTIONAL_STORE","receipt_format":"COMMIT_RECEIPT_V1","readback_format":"STATE_READBACK_V1"},
    ]
    entries=sorted(entries,key=lambda x:(x["backend_id"],x["backend_key_id"],x["backend_metadata_sha256"],x["backend_kind"],x["receipt_format"],x["readback_format"]))
    return {"schema":m.BACKEND_REGISTRY_SCHEMA,"registry_scope":"COMMIT_READBACK_BACKEND_METADATA_PROVENANCE_ONLY",
            "backend_authority_root_sha256":BACKEND_AUTHORITY_ROOT_SHA256,"entries":entries,
            "backend_trust_root_verified":False,"backend_registry_operator_identity_verified":False,
            "backend_registry_write_performed":False,"confers_authority":False}

def provenance_verification(r91_binding=None,registry=None):
    if r91_binding is None: *_,r91_binding=r91_context()
    registry=backend_registry() if registry is None else registry
    selected=next(x for x in registry["entries"] if x["backend_id"]=="backend-main")
    return {"schema":m.PROVENANCE_SCHEMA,"provenance_scope":"R91_COMMIT_AND_READBACK_BACKEND_METADATA_ONLY",
        "r91_binding_id":r91_binding["binding_id"],"r91_binding_sha256":m.stable_sha256(r91_binding),
        "backend_registry_sha256":m.stable_sha256(registry),"backend_authority_root_sha256":BACKEND_AUTHORITY_ROOT_SHA256,
        "selected_backend_entry_sha256":m.stable_sha256(selected),
        "external_commit_receipt_sha256":r91_binding["external_commit_receipt_sha256"],
        "readback_evidence_sha256":r91_binding["readback_evidence_sha256"],"readback_state_sha256":r91_binding["readback_state_sha256"],
        **selected,
        "same_backend_metadata_claim_bound":True,"commit_receipt_backend_metadata_bound":True,"readback_backend_metadata_bound":True,
        "backend_provenance_match":True,"backend_commit_authenticity_verified":False,"readback_authenticity_verified":False,
        "backend_identity_verified":False,"backend_trust_root_verified":False,"backend_registry_operator_identity_verified":False,
        "live_backend_observed":False,"durable_commit_proven":False,"durable_dual_state_atomicity_proven":False,"write_performed":False,
        "global_current_state_verified":False,"concurrent_writer_exclusion_proven":False,"execution_authority":"NONE",
        "can_execute":False,"apply_allowed":False,"confers_authority":False}

def build(registry=None,provenance=None):
    (
        items,manifest,aid,assertion,r84_binding,verifier_reg,r85_binding,replay_reg,r86_binding,
        atomic_receipt,r87_binding,recovery_receipt,r88_binding,anchor,r89_binding,atomicity_receipt,r90_binding,cr,r91_binding
    )=r91_context()
    registry=backend_registry() if registry is None else registry
    provenance=provenance_verification(r91_binding,registry) if provenance is None else provenance
    return m.build_external_assertion_replay_backend_provenance_binding(
        r91_binding,r90_binding,r89_binding,r88_binding,r87_binding,r86_binding,r85_binding,r84_binding,
        manifest,items,r83_set_policy(),aid,assertion,verifier_reg,replay_reg,atomic_receipt,recovery_receipt,anchor,atomicity_receipt,cr,
        registry,provenance,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),expected_verifier_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        expected_writer_authority_root_sha256=WRITER_AUTHORITY_ROOT_SHA256,provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=r89m.stable_sha256(anchor),writer_authority_anchor_policy=writer_authority_anchor_policy(),
        expected_atomicity_verification_sha256=r90m.stable_sha256(atomicity_receipt),dual_state_atomicity_policy=dual_state_atomicity_policy(),
        expected_commit_readback_evidence_sha256=r91m.stable_sha256(cr),commit_readback_evidence_policy=commit_readback_evidence_policy(),
        expected_backend_registry_sha256=m.stable_sha256(registry),expected_backend_authority_root_sha256=BACKEND_AUTHORITY_ROOT_SHA256,
        expected_backend_provenance_verification_sha256=m.stable_sha256(provenance),backend_provenance_policy=backend_provenance_policy())

def clone(v): return copy.deepcopy(v)
