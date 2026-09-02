from __future__ import annotations
import copy,importlib.util,json
import r96_external_assertion_replay_backend_authenticity_replay_guard_fixtures as f
ROOT=f.ROOT
POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHORITY_ROOT_TRUST_ASSERTION_POLICY_V1.json"
CONTRACT=ROOT/"tools"/"tradingos_external_assertion_replay_backend_authority_root_trust_assertion_contract.py"
s=importlib.util.spec_from_file_location("r97c",CONTRACT); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
TRUST_EVALUATOR_ID="backend-root-trust-evaluator-r97-01"; TRUST_EVALUATOR_KEY_ID="backend-root-trust-evaluator-key-r97-01"; ASSERTION_ID="97"*12
def trust_policy(): return json.loads(POLICY.read_text(encoding="utf-8"))
def r96_context():
    ctx=f.r95_context(); *_,r95b=ctx; registry=f.replay_registry(r95b)
    r96b=f.m.build_external_assertion_replay_backend_authenticity_replay_guard_binding(*f._args(ctx,registry),**f._kw(ctx,registry)); return (*ctx,registry,r96b)
def trust_assertion(r96b=None,policy=None):
    if r96b is None: *_,r96b=r96_context()
    p=trust_policy() if policy is None else policy; challenge=m.build_backend_authority_root_trust_challenge(r96b,p)
    return {"schema":m.TRUST_ASSERTION_SCHEMA,"assertion_id":ASSERTION_ID,"challenge_sha256":m.stable_sha256(challenge),"backend_authority_root_sha256":r96b["backend_authority_root_sha256"],"backend_registry_sha256":r96b["backend_registry_sha256"],"backend_key_registry_sha256":r96b["backend_key_registry_sha256"],"backend_id":r96b["backend_id"],"backend_key_id":r96b["backend_key_id"],"trust_evaluator_id":TRUST_EVALUATOR_ID,"trust_evaluator_key_id":TRUST_EVALUATOR_KEY_ID,"algorithm":r96b["algorithm"],"backend_authority_root_trust_asserted":True,"local_trust_evaluation_performed":False,"assertion_scope":"BACKEND_AUTHORITY_ROOT_TRUST_ASSERTION_ONLY","confers_authority":False}
def _args(ctx,assertion):
    r95ctx=ctx[:28]; replayreg=ctx[28]; r96b=ctx[29]; return (r96b,*f._args(r95ctx,replayreg),assertion)
def _kw(ctx,assertion,policy=None,expected_assertion_sha=None):
    r95ctx=ctx[:28]; replayreg=ctx[28]; out=f._kw(r95ctx,replayreg); p=trust_policy() if policy is None else policy
    out.update(expected_backend_authority_root_trust_assertion_sha256=m.stable_sha256(assertion) if expected_assertion_sha is None else expected_assertion_sha,backend_authority_root_trust_assertion_policy=p); return out
def build(assertion=None):
    ctx=r96_context(); *_,r96b=ctx; assertion=trust_assertion(r96b) if assertion is None else assertion
    return m.build_external_assertion_replay_backend_authority_root_trust_assertion_binding(*_args(ctx,assertion),**_kw(ctx,assertion))
def clone(v): return copy.deepcopy(v)
