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
from r91_external_assertion_replay_durable_commit_readback_evidence_fixtures import m as r91m, commit_readback_evidence_policy
from r92_external_assertion_replay_backend_provenance_fixtures import ROOT,m as r92m,BACKEND_AUTHORITY_ROOT_SHA256,backend_provenance_policy,r91_context,backend_registry,provenance_verification

POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_ASSERTION_POLICY_V1.json"
CONTRACT=ROOT/"tools"/"tradingos_external_assertion_replay_backend_authenticity_assertion_contract.py"
s=importlib.util.spec_from_file_location("r93c",CONTRACT); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
PUBLIC_KEY_SHA256="7"*64

def backend_authenticity_assertion_policy(): return json.loads(POLICY.read_text(encoding="utf-8"))

def r92_context():
    (
        items,manifest,aid,assertion,r84_binding,verifier_reg,r85_binding,replay_reg,r86_binding,
        atomic_receipt,r87_binding,recovery_receipt,r88_binding,anchor,r89_binding,atomicity_receipt,r90_binding,cr,r91_binding
    )=r91_context()
    reg=backend_registry(); prov=provenance_verification(r91_binding,reg)
    r92_binding=r92m.build_external_assertion_replay_backend_provenance_binding(
        r91_binding,r90_binding,r89_binding,r88_binding,r87_binding,r86_binding,r85_binding,r84_binding,
        manifest,items,r83_set_policy(),aid,assertion,verifier_reg,replay_reg,atomic_receipt,recovery_receipt,anchor,atomicity_receipt,cr,reg,prov,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),expected_verifier_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        expected_writer_authority_root_sha256=WRITER_AUTHORITY_ROOT_SHA256,provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=r89m.stable_sha256(anchor),writer_authority_anchor_policy=writer_authority_anchor_policy(),
        expected_atomicity_verification_sha256=r90m.stable_sha256(atomicity_receipt),dual_state_atomicity_policy=dual_state_atomicity_policy(),
        expected_commit_readback_evidence_sha256=r91m.stable_sha256(cr),commit_readback_evidence_policy=commit_readback_evidence_policy(),
        expected_backend_registry_sha256=r92m.stable_sha256(reg),expected_backend_authority_root_sha256=BACKEND_AUTHORITY_ROOT_SHA256,
        expected_backend_provenance_verification_sha256=r92m.stable_sha256(prov),backend_provenance_policy=backend_provenance_policy())
    return items,manifest,aid,assertion,r84_binding,verifier_reg,r85_binding,replay_reg,r86_binding,atomic_receipt,r87_binding,recovery_receipt,r88_binding,anchor,r89_binding,atomicity_receipt,r90_binding,cr,r91_binding,reg,prov,r92_binding

def backend_authenticity_assertion(r92_binding=None, policy=None):
    if r92_binding is None: *_,r92_binding=r92_context()
    policy=backend_authenticity_assertion_policy() if policy is None else policy
    ch=m.build_backend_authenticity_challenge(r92_binding,policy)
    return {"schema":m.ASSERTION_SCHEMA,"challenge_sha256":m.stable_sha256(ch),"backend_id":r92_binding["backend_id"],
        "backend_key_id":r92_binding["backend_key_id"],"backend_metadata_sha256":r92_binding["backend_metadata_sha256"],
        "public_key_sha256":PUBLIC_KEY_SHA256,"algorithm":"ED25519","verifier_id":"backend-auth-verifier",
        "verifier_key_id":"backend-auth-verifier-key-1","commit_signature_verified_by_external_asymmetric_verifier":True,
        "readback_signature_verified_by_external_asymmetric_verifier":True,"same_backend_key_claim_bound":True,
        "local_signature_math_verified":False,"assertion_scope":"BACKEND_COMMIT_AND_READBACK_SIGNATURE_ASSERTION_ONLY",
        "backend_commit_authenticity_verified":False,"readback_authenticity_verified":False,"backend_key_possession_proven":False,
        "backend_identity_verified":False,"backend_trust_root_verified":False,"backend_authenticity_verifier_trust_root_verified":False,
        "assertion_freshness_verified":False,"confers_authority":False}

def build(auth_assertion=None):
    ctx=r92_context(); *_,r92_binding=ctx
    auth_assertion=backend_authenticity_assertion(r92_binding) if auth_assertion is None else auth_assertion
    items,manifest,aid,assertion,r84_binding,verifier_reg,r85_binding,replay_reg,r86_binding,atomic_receipt,r87_binding,recovery_receipt,r88_binding,anchor,r89_binding,atomicity_receipt,r90_binding,cr,r91_binding,reg,prov,r92_binding=ctx
    return m.build_external_assertion_replay_backend_authenticity_assertion_binding(
        r92_binding,r91_binding,r90_binding,r89_binding,r88_binding,r87_binding,r86_binding,r85_binding,r84_binding,
        manifest,items,r83_set_policy(),aid,assertion,verifier_reg,replay_reg,atomic_receipt,recovery_receipt,anchor,atomicity_receipt,cr,reg,prov,auth_assertion,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),expected_verifier_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        expected_writer_authority_root_sha256=WRITER_AUTHORITY_ROOT_SHA256,provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=r89m.stable_sha256(anchor),writer_authority_anchor_policy=writer_authority_anchor_policy(),
        expected_atomicity_verification_sha256=r90m.stable_sha256(atomicity_receipt),dual_state_atomicity_policy=dual_state_atomicity_policy(),
        expected_commit_readback_evidence_sha256=r91m.stable_sha256(cr),commit_readback_evidence_policy=commit_readback_evidence_policy(),
        expected_backend_registry_sha256=r92m.stable_sha256(reg),expected_backend_authority_root_sha256=BACKEND_AUTHORITY_ROOT_SHA256,
        expected_backend_provenance_verification_sha256=r92m.stable_sha256(prov),backend_provenance_policy=backend_provenance_policy(),
        expected_backend_authenticity_assertion_sha256=m.stable_sha256(auth_assertion),backend_authenticity_assertion_policy=backend_authenticity_assertion_policy())

def clone(v): return copy.deepcopy(v)
