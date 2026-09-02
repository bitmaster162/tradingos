from __future__ import annotations
import copy, importlib.util, json
import r94_external_assertion_replay_backend_authenticity_verifier_provenance_fixtures as f

ROOT=f.ROOT
POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_KEY_PROVENANCE_POLICY_V1.json"
CONTRACT=ROOT/"tools"/"tradingos_external_assertion_replay_backend_key_provenance_contract.py"
s=importlib.util.spec_from_file_location("r95c",CONTRACT); assert s and s.loader
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)

def provenance_policy(): return json.loads(POLICY.read_text(encoding="utf-8"))

def r94_context():
    r93ctx=f.r93_context(); *_,r93b=r93ctx
    vreg=f.verifier_registry(r93b)
    r94b=f.m.build_external_assertion_replay_backend_authenticity_verifier_provenance_binding(*f._args(r93ctx,vreg),**f._kw(r93ctx,vreg))
    return (*r93ctx,vreg,r94b)

def backend_key_registry(r94b=None):
    if r94b is None: *_,r94b=r94_context()
    entries=[
        {"backend_id":"backend-decoy","backend_key_id":"key-decoy","public_key_sha256":"9"*64,"algorithm":"ED25519","backend_metadata_sha256":"8"*64},
        {"backend_id":r94b["backend_id"],"backend_key_id":r94b["backend_key_id"],"public_key_sha256":r94b["public_key_sha256"],
         "algorithm":r94b["algorithm"],"backend_metadata_sha256":r94b["backend_metadata_sha256"]},
    ]
    entries=sorted(entries,key=lambda x:(x["backend_id"],x["backend_key_id"],x["public_key_sha256"],x["algorithm"],x["backend_metadata_sha256"]))
    return {"schema":m.BACKEND_KEY_REGISTRY_SCHEMA,"registry_id":"offline-backend-key-registry-r95-01",
        "backend_authority_root_sha256":r94b["backend_authority_root_sha256"],"entries":entries,
        "registry_scope":"BACKEND_KEY_TO_BACKEND_METADATA_PROVENANCE_ONLY","backend_trust_root_verified":False,
        "backend_registry_operator_identity_verified":False,"backend_key_registry_write_performed":False,"confers_authority":False}

def _args(ctx,registry):
    r93ctx=ctx[:24]; vreg=ctx[24]; r94b=ctx[25]
    return (r94b,*f._args(r93ctx,vreg),registry)

def _kw(ctx,registry,policy=None,expected_registry_sha=None):
    r93ctx=ctx[:24]; vreg=ctx[24]
    out=f._kw(r93ctx,vreg); out.update(
        expected_backend_key_registry_sha256=m.stable_sha256(registry) if expected_registry_sha is None else expected_registry_sha,
        backend_key_provenance_policy=provenance_policy() if policy is None else policy)
    return out

def build(registry=None):
    ctx=r94_context(); *_,r94b=ctx
    registry=backend_key_registry(r94b) if registry is None else registry
    return m.build_external_assertion_replay_backend_key_provenance_binding(*_args(ctx,registry),**_kw(ctx,registry))

def clone(v): return copy.deepcopy(v)
