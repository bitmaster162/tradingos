from __future__ import annotations
import copy,importlib.util,json
import r97_external_assertion_replay_backend_authority_root_trust_assertion_fixtures as f
ROOT=f.ROOT
POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_CRYPTOGRAPHIC_ARTIFACT_IDENTITY_POLICY_V1.json"
CONTRACT=ROOT/"tools"/"tradingos_external_assertion_replay_cryptographic_artifact_identity_contract.py"
s=importlib.util.spec_from_file_location("r98c",CONTRACT); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
RECORD_ID="98"*12
COMMIT_SIGNATURE_SHA256="c"*64
READBACK_SIGNATURE_SHA256="d"*64
def identity_policy(): return json.loads(POLICY.read_text(encoding="utf-8"))
def r97_context():
    ctx=f.r96_context(); *_,r96b=ctx; assertion=f.trust_assertion(r96b)
    r97b=f.m.build_external_assertion_replay_backend_authority_root_trust_assertion_binding(*f._args(ctx,assertion),**f._kw(ctx,assertion)); return (*ctx,assertion,r97b)
def identity_record(r97b=None,policy=None):
    if r97b is None: *_,r97b=r97_context()
    p=identity_policy() if policy is None else policy; challenge=m.build_cryptographic_artifact_identity_challenge(r97b,p)
    return {"schema":m.RECORD_SCHEMA,"record_id":RECORD_ID,"challenge_sha256":m.stable_sha256(challenge),"backend_id":r97b["backend_id"],"backend_key_id":r97b["backend_key_id"],"public_key_sha256":r97b["public_key_sha256"],"algorithm":r97b["algorithm"],"commit_signature_sha256":COMMIT_SIGNATURE_SHA256,"readback_signature_sha256":READBACK_SIGNATURE_SHA256,"commit_signature_target_sha256":r97b["external_commit_receipt_sha256"],"readback_signature_target_sha256":r97b["readback_evidence_sha256"],"readback_state_sha256":r97b["readback_state_sha256"],"artifact_scope":"BACKEND_COMMIT_READBACK_CRYPTOGRAPHIC_ARTIFACT_IDENTITY_ONLY","local_signature_math_verified":False,"cryptographic_artifact_bytes_retrieved":False,"backend_commit_authenticity_verified":False,"readback_authenticity_verified":False,"backend_key_possession_proven":False,"backend_identity_verified":False,"confers_authority":False}
def _args(ctx,record):
    r96ctx=ctx[:30]; assertion=ctx[30]; r97b=ctx[31]; return (r97b,*f._args(r96ctx,assertion),record)
def _kw(ctx,record,policy=None,expected_record_sha=None):
    r96ctx=ctx[:30]; assertion=ctx[30]; out=f._kw(r96ctx,assertion); p=identity_policy() if policy is None else policy
    out.update(expected_cryptographic_artifact_identity_record_sha256=m.stable_sha256(record) if expected_record_sha is None else expected_record_sha,cryptographic_artifact_identity_policy=p); return out
def build(record=None):
    ctx=r97_context(); *_,r97b=ctx; record=identity_record(r97b) if record is None else record
    return m.build_external_assertion_replay_cryptographic_artifact_identity_binding(*_args(ctx,record),**_kw(ctx,record))
def clone(v): return copy.deepcopy(v)
