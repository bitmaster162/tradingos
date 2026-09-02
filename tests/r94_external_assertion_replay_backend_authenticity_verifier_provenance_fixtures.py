from __future__ import annotations
import copy, importlib.util, json
import r93_external_assertion_replay_backend_authenticity_assertion_fixtures as f

ROOT=f.ROOT
POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_VERIFIER_PROVENANCE_POLICY_V1.json"
CONTRACT=ROOT/"tools"/"tradingos_external_assertion_replay_backend_authenticity_verifier_provenance_contract.py"
AUTHENTICITY_VERIFIER_AUTHORITY_ROOT_SHA256="4"*64
s=importlib.util.spec_from_file_location("r94c",CONTRACT); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def provenance_policy(): return json.loads(POLICY.read_text(encoding="utf-8"))

def r93_context():
    ctx=f.r92_context()
    items,manifest,aid,assertion,r84b,verifier_reg,r85b,replay_reg,r86b,atomic,r87b,recovery,r88b,anchor,r89b,atomicity,r90b,cr,r91b,reg,prov,r92b=ctx
    auth=f.backend_authenticity_assertion(r92b)
    r93b=f.m.build_external_assertion_replay_backend_authenticity_assertion_binding(
        r92b,r91b,r90b,r89b,r88b,r87b,r86b,r85b,r84b,manifest,items,f.r83_set_policy(),aid,assertion,
        verifier_reg,replay_reg,atomic,recovery,anchor,atomicity,cr,reg,prov,auth,
        expected_external_assertion_sha256=f.r84m.stable_sha256(assertion),key_possession_policy=f.r84_policy(),
        expected_verifier_registry_sha256=f.r85m.stable_sha256(verifier_reg),expected_verifier_authority_root_sha256=f.AUTHORITY_ROOT_SHA256,
        expected_writer_authority_root_sha256=f.WRITER_AUTHORITY_ROOT_SHA256,provenance_policy=f.provenance_policy(),
        expected_replay_registry_sha256=f.r86m.stable_sha256(replay_reg),replay_guard_policy=f.replay_policy(),
        expected_atomic_verification_sha256=f.r87m.stable_sha256(atomic),atomic_cas_policy=f.atomic_cas_policy(),
        expected_recovery_verification_sha256=f.r88m.stable_sha256(recovery),writer_fencing_recovery_policy=f.writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=f.r89m.stable_sha256(anchor),writer_authority_anchor_policy=f.writer_authority_anchor_policy(),
        expected_atomicity_verification_sha256=f.r90m.stable_sha256(atomicity),dual_state_atomicity_policy=f.dual_state_atomicity_policy(),
        expected_commit_readback_evidence_sha256=f.r91m.stable_sha256(cr),commit_readback_evidence_policy=f.commit_readback_evidence_policy(),
        expected_backend_registry_sha256=f.r92m.stable_sha256(reg),expected_backend_authority_root_sha256=f.BACKEND_AUTHORITY_ROOT_SHA256,
        expected_backend_provenance_verification_sha256=f.r92m.stable_sha256(prov),backend_provenance_policy=f.backend_provenance_policy(),
        expected_backend_authenticity_assertion_sha256=f.m.stable_sha256(auth),backend_authenticity_assertion_policy=f.backend_authenticity_assertion_policy())
    return (*ctx,auth,r93b)

def verifier_registry(r93b=None):
    if r93b is None: *_,r93b=r93_context()
    return {"schema":m.VERIFIER_REGISTRY_SCHEMA,"registry_id":"offline-backend-authenticity-verifier-registry-r94-01",
        "authority_root_sha256":AUTHENTICITY_VERIFIER_AUTHORITY_ROOT_SHA256,
        "entries":[
            {"verifier_id":r93b["backend_authenticity_verifier_id"],"verifier_key_id":r93b["backend_authenticity_verifier_key_id"],
             "verified_public_key_sha256":r93b["public_key_sha256"],"algorithm":r93b["algorithm"]},
            {"verifier_id":"backend-auth-verifier-02","verifier_key_id":"backend-auth-verifier-key-2",
             "verified_public_key_sha256":"8"*64,"algorithm":"ED25519"}],
        "registry_scope":"BACKEND_AUTHENTICITY_VERIFIER_PROVENANCE_ONLY","trust_root_verified":False,"confers_authority":False}

def _args(ctx,registry):
    items,manifest,aid,assertion,r84b,verifier_reg,r85b,replay_reg,r86b,atomic,r87b,recovery,r88b,anchor,r89b,atomicity,r90b,cr,r91b,reg,prov,r92b,auth,r93b=ctx
    return (r93b,r92b,r91b,r90b,r89b,r88b,r87b,r86b,r85b,r84b,manifest,items,f.r83_set_policy(),aid,assertion,
            verifier_reg,replay_reg,atomic,recovery,anchor,atomicity,cr,reg,prov,auth,registry)

def _kw(ctx,registry,policy=None,expected_registry_sha=None,expected_root=None):
    items,manifest,aid,assertion,r84b,verifier_reg,r85b,replay_reg,r86b,atomic,r87b,recovery,r88b,anchor,r89b,atomicity,r90b,cr,r91b,reg,prov,r92b,auth,r93b=ctx
    p=provenance_policy() if policy is None else policy
    return dict(
        expected_external_assertion_sha256=f.r84m.stable_sha256(assertion),key_possession_policy=f.r84_policy(),
        expected_verifier_registry_sha256=f.r85m.stable_sha256(verifier_reg),expected_verifier_authority_root_sha256=f.AUTHORITY_ROOT_SHA256,
        expected_writer_authority_root_sha256=f.WRITER_AUTHORITY_ROOT_SHA256,provenance_policy=f.provenance_policy(),
        expected_replay_registry_sha256=f.r86m.stable_sha256(replay_reg),replay_guard_policy=f.replay_policy(),
        expected_atomic_verification_sha256=f.r87m.stable_sha256(atomic),atomic_cas_policy=f.atomic_cas_policy(),
        expected_recovery_verification_sha256=f.r88m.stable_sha256(recovery),writer_fencing_recovery_policy=f.writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=f.r89m.stable_sha256(anchor),writer_authority_anchor_policy=f.writer_authority_anchor_policy(),
        expected_atomicity_verification_sha256=f.r90m.stable_sha256(atomicity),dual_state_atomicity_policy=f.dual_state_atomicity_policy(),
        expected_commit_readback_evidence_sha256=f.r91m.stable_sha256(cr),commit_readback_evidence_policy=f.commit_readback_evidence_policy(),
        expected_backend_registry_sha256=f.r92m.stable_sha256(reg),expected_backend_authority_root_sha256=f.BACKEND_AUTHORITY_ROOT_SHA256,
        expected_backend_provenance_verification_sha256=f.r92m.stable_sha256(prov),backend_provenance_policy=f.backend_provenance_policy(),
        expected_backend_authenticity_assertion_sha256=f.m.stable_sha256(auth),backend_authenticity_assertion_policy=f.backend_authenticity_assertion_policy(),
        expected_backend_authenticity_verifier_registry_sha256=m.stable_sha256(registry) if expected_registry_sha is None else expected_registry_sha,
        expected_backend_authenticity_verifier_authority_root_sha256=AUTHENTICITY_VERIFIER_AUTHORITY_ROOT_SHA256 if expected_root is None else expected_root,
        backend_authenticity_verifier_provenance_policy=p)

def build(registry=None):
    ctx=r93_context(); *_,r93b=ctx
    registry=verifier_registry(r93b) if registry is None else registry
    return m.build_external_assertion_replay_backend_authenticity_verifier_provenance_binding(*_args(ctx,registry),**_kw(ctx,registry))

def clone(v): return copy.deepcopy(v)
