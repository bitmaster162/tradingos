from __future__ import annotations
import copy,importlib.util,json
import r95_external_assertion_replay_backend_key_provenance_fixtures as f

ROOT=f.ROOT
POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_REPLAY_GUARD_POLICY_V1.json"
CONTRACT=ROOT/"tools"/"tradingos_external_assertion_replay_backend_authenticity_replay_guard_contract.py"
s=importlib.util.spec_from_file_location("r96c",CONTRACT); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def replay_policy(): return json.loads(POLICY.read_text(encoding="utf-8"))

def r95_context():
    ctx=f.r94_context(); *_,r94b=ctx
    keyreg=f.backend_key_registry(r94b)
    r95b=f.m.build_external_assertion_replay_backend_key_provenance_binding(*f._args(ctx,keyreg),**f._kw(ctx,keyreg))
    return (*ctx,keyreg,r95b)

def replay_registry(r95b=None):
    if r95b is None: *_,r95b=r95_context()
    return {
        "schema":m.REPLAY_REGISTRY_SCHEMA,"registry_id":"offline-backend-authenticity-replay-r96-01","generation":11,
        "previous_registry_sha256":"5"*64,
        "used_backend_authenticity_assertion_sha256s":sorted(["1"*64,"3"*64]),
        "used_backend_authenticity_challenge_sha256s":sorted(["2"*64,"4"*64]),
        "registry_scope":"BACKEND_AUTHENTICITY_ASSERTION_AND_CHALLENGE_REPLAY_GUARD_ONLY",
        "durable_commit_proven":False,"write_allowed":False,"apply_allowed":False,"confers_authority":False,
    }

def _args(ctx,registry):
    r94ctx=ctx[:26]; keyreg=ctx[26]; r95b=ctx[27]
    return (r95b,*f._args(r94ctx,keyreg),registry)

def _kw(ctx,registry,policy=None,expected_registry_sha=None):
    r94ctx=ctx[:26]; keyreg=ctx[26]
    out=f._kw(r94ctx,keyreg); out.update(
        expected_backend_authenticity_replay_registry_sha256=m.stable_sha256(registry) if expected_registry_sha is None else expected_registry_sha,
        backend_authenticity_replay_guard_policy=replay_policy() if policy is None else policy)
    return out

def build(registry=None):
    ctx=r95_context(); *_,r95b=ctx
    registry=replay_registry(r95b) if registry is None else registry
    return m.build_external_assertion_replay_backend_authenticity_replay_guard_binding(*_args(ctx,registry),**_kw(ctx,registry))

def clone(v): return copy.deepcopy(v)
