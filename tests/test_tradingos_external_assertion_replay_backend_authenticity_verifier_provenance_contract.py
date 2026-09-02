from __future__ import annotations
import json, pytest
from r94_external_assertion_replay_backend_authenticity_verifier_provenance_fixtures import ROOT,f,m,AUTHENTICITY_VERIFIER_AUTHORITY_ROOT_SHA256,provenance_policy,r93_context,verifier_registry,_args,_kw,build,clone

def build_with(*,r93_override=None,registry=None,expected_registry_sha=None,expected_root=None,policy=None):
    ctx=list(r93_context()); r93b=ctx[-1] if r93_override is None else r93_override; ctx[-1]=r93b
    registry=verifier_registry(r93b) if registry is None else registry
    return m.build_external_assertion_replay_backend_authenticity_verifier_provenance_binding(
        *_args(tuple(ctx),registry),**_kw(tuple(ctx),registry,policy=policy,expected_registry_sha=expected_registry_sha,expected_root=expected_root))

def test_build_validate_and_determinism():
    x=build(); assert x["schema"]==m.BINDING_SCHEMA; assert x==build(); assert x["binding_id"]==build()["binding_id"]
    ctx=r93_context(); reg=verifier_registry(ctx[-1])
    m.validate_external_assertion_replay_backend_authenticity_verifier_provenance_binding(x,*_args(ctx,reg),**_kw(ctx,reg))

def test_exact_r93_and_policy_are_digest_bound():
    *_,r93b=r93_context(); x=build()
    assert x["r93_binding_id"]==r93b["binding_id"]; assert x["r93_binding_sha256"]==m.stable_sha256(r93b)
    assert x["backend_authenticity_verifier_provenance_policy_sha256"]==m.stable_sha256(provenance_policy())

def test_exact_registry_entry_is_bound():
    *_,r93b=r93_context(); reg=verifier_registry(r93b); x=build_with(r93_override=r93b,registry=reg); e=reg["entries"][0]
    assert x["backend_authenticity_verifier_registry_sha256"]==m.stable_sha256(reg)
    assert x["backend_authenticity_verifier_registry_entry_sha256"]==m.stable_sha256(e)
    assert x["backend_authenticity_verifier_registry_entry_exact_match"] is True
    assert x["backend_authenticity_verifier_provenance_bound"] is True
    assert e["verifier_id"]==r93b["backend_authenticity_verifier_id"]
    assert e["verifier_key_id"]==r93b["backend_authenticity_verifier_key_id"]
    assert e["verified_public_key_sha256"]==r93b["public_key_sha256"]
    assert e["algorithm"]==r93b["algorithm"]=="ED25519"

def test_registry_substitution_rejected_against_retained_digest():
    *_,r93b=r93_context(); original=verifier_registry(r93b); retained=m.stable_sha256(original); changed=clone(original); changed["registry_id"]+="x"
    with pytest.raises(ValueError,match="registry digest mismatch"): build_with(r93_override=r93b,registry=changed,expected_registry_sha=retained)

@pytest.mark.parametrize("field,value",[
    ("verifier_id","other-backend-auth-verifier"),("verifier_key_id","other-backend-auth-verifier-key"),
    ("verified_public_key_sha256","9"*64),("algorithm","ES256")])
def test_registry_entry_transplant_or_algorithm_drift_rejected(field,value):
    *_,r93b=r93_context(); reg=verifier_registry(r93b); reg["entries"][0][field]=value
    with pytest.raises(ValueError): build_with(r93_override=r93b,registry=reg)

def test_duplicate_or_ambiguous_exact_entry_rejected():
    *_,r93b=r93_context(); reg=verifier_registry(r93b); reg["entries"].append(clone(reg["entries"][0]))
    with pytest.raises(ValueError,match="duplicate backend authenticity verifier registry entry"): build_with(r93_override=r93b,registry=reg)

def test_hidden_registry_claim_rejected():
    *_,r93b=r93_context(); reg=verifier_registry(r93b); reg["registry_operator"]="forbidden"
    with pytest.raises(ValueError,match="registry key set mismatch"): build_with(r93_override=r93b,registry=reg)

def test_registry_trust_and_authority_overclaims_rejected():
    *_,r93b=r93_context(); reg=verifier_registry(r93b); reg["trust_root_verified"]=True
    with pytest.raises(ValueError,match="trust-root overclaim"): build_with(r93_override=r93b,registry=reg)
    reg=verifier_registry(r93b); reg["confers_authority"]=True
    with pytest.raises(ValueError,match="registry authority overclaim"): build_with(r93_override=r93b,registry=reg)

def test_r94_root_substitution_rejected():
    *_,r93b=r93_context()
    assert AUTHENTICITY_VERIFIER_AUTHORITY_ROOT_SHA256 not in {
        r93b["verifier_authority_root_sha256"],r93b["writer_authority_root_sha256"],r93b["backend_authority_root_sha256"]}
    for old_root in (r93b["verifier_authority_root_sha256"],r93b["writer_authority_root_sha256"],r93b["backend_authority_root_sha256"]):
        with pytest.raises(ValueError,match="authority root digest mismatch"): build_with(r93_override=r93b,expected_root=old_root)

def test_policy_drift_bool_schema_and_algorithm_widening_rejected():
    p=provenance_policy(); p["network_access_in_core_allowed"]=True
    with pytest.raises(ValueError,match="unsafe backend authenticity verifier provenance policy"): build_with(policy=p)
    p=provenance_policy(); p["schema_version"]=True
    with pytest.raises(ValueError,match="unsupported backend authenticity verifier provenance policy"): build_with(policy=p)
    p=provenance_policy(); p["allowed_algorithms"]=["ED25519","ES256"]
    with pytest.raises(ValueError,match="algorithm allowlist drift"): build_with(policy=p)

def test_tampered_r93_binding_rejected_by_full_r93_validation():
    *_,r93b=r93_context(); bad=clone(r93b); bad["backend_authenticity_verifier_id"]="substituted-verifier"; reg=verifier_registry(bad)
    with pytest.raises(ValueError): build_with(r93_override=bad,registry=reg)

def test_all_inherited_r93_fields_are_preserved_exactly():
    *_,r93b=r93_context(); x=build()
    for k in f.m.BINDING_KEYS-{"schema","binding_id"}:
        assert x[k]==r93b[k] and type(x[k]) is type(r93b[k])

def test_schema_required_and_properties_match_contract():
    schema=json.loads((ROOT/"schemas"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_VERIFIER_PROVENANCE_BINDING_V1.schema.json").read_text())
    assert set(schema["required"])==m.BINDING_KEYS; assert set(schema["properties"])==m.BINDING_KEYS
    assert len(m.BINDING_KEYS)==95

def test_no_authenticity_trust_durability_or_authority_upgrade():
    x=build()
    assert x["backend_authenticity_verifier_provenance_bound"] is True
    assert x["backend_authenticity_verifier_identity_verified"] is False
    assert x["backend_authenticity_verifier_registry_operator_identity_verified"] is False
    for field in ["backend_authenticity_verifier_trust_root_verified","backend_commit_authenticity_verified","readback_authenticity_verified",
                  "backend_key_possession_proven","backend_identity_verified","backend_trust_root_verified","assertion_freshness_verified","liveness_verified",
                  "live_backend_observed","durable_commit_proven","durable_dual_state_atomicity_proven","durable_single_use_enforced",
                  "global_current_state_verified","concurrent_writer_exclusion_proven","write_performed","registry_write_performed",
                  "lease_registry_write_performed","receipt_index_write_performed","backend_write_performed","confers_authority"]:
        assert x[field] is False
    assert x["execution_authority"]=="NONE"; assert x["can_trade"] is False; assert x["capital_permission"]=="DENY"
