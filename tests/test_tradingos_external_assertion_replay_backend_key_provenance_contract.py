from __future__ import annotations
import json, pytest
from r95_external_assertion_replay_backend_key_provenance_fixtures import ROOT,f,m,provenance_policy,r94_context,backend_key_registry,_args,_kw,build,clone

def build_with(*,r94_override=None,registry=None,expected_registry_sha=None,policy=None):
    ctx=list(r94_context()); r94b=ctx[-1] if r94_override is None else r94_override; ctx[-1]=r94b
    registry=backend_key_registry(r94b) if registry is None else registry
    return m.build_external_assertion_replay_backend_key_provenance_binding(*_args(tuple(ctx),registry),**_kw(tuple(ctx),registry,policy=policy,expected_registry_sha=expected_registry_sha))

def test_build_validate_and_determinism():
    x=build(); assert x["schema"]==m.BINDING_SCHEMA; assert x==build(); assert x["binding_id"]==build()["binding_id"]
    ctx=r94_context(); reg=backend_key_registry(ctx[-1]); m.validate_external_assertion_replay_backend_key_provenance_binding(x,*_args(ctx,reg),**_kw(ctx,reg))

def test_exact_r94_and_policy_are_digest_bound():
    *_,r94b=r94_context(); x=build(); assert x["r94_binding_id"]==r94b["binding_id"]; assert x["r94_binding_sha256"]==m.stable_sha256(r94b)
    assert x["backend_key_provenance_policy_sha256"]==m.stable_sha256(provenance_policy())

def test_exact_backend_key_registry_entry_is_bound():
    *_,r94b=r94_context(); reg=backend_key_registry(r94b); x=build_with(r94_override=r94b,registry=reg)
    e=next(row for row in reg["entries"] if row["backend_id"]==r94b["backend_id"] and row["backend_key_id"]==r94b["backend_key_id"])
    assert x["backend_key_registry_sha256"]==m.stable_sha256(reg); assert x["backend_key_registry_entry_sha256"]==m.stable_sha256(e)
    assert x["backend_key_registry_authority_root_sha256"]==r94b["backend_authority_root_sha256"]
    assert x["backend_key_registry_authority_root_matches_backend_authority_root"] is True
    assert x["backend_key_registry_entry_exact_match"] is True; assert x["backend_key_provenance_bound"] is True
    assert x["backend_key_to_backend_metadata_bound"] is True; assert x["backend_public_key_digest_bound"] is True
    for k in ("backend_id","backend_key_id","public_key_sha256","algorithm","backend_metadata_sha256"): assert e[k]==r94b[k]

def test_registry_substitution_rejected_against_retained_digest():
    *_,r94b=r94_context(); original=backend_key_registry(r94b); retained=m.stable_sha256(original); changed=clone(original); changed["registry_id"]+="x"
    with pytest.raises(ValueError,match="registry digest mismatch"): build_with(r94_override=r94b,registry=changed,expected_registry_sha=retained)

@pytest.mark.parametrize("field,value",[("backend_id","other-backend"),("backend_key_id","other-key"),("public_key_sha256","5"*64),("backend_metadata_sha256","6"*64),("algorithm","ES256")])
def test_key_or_backend_metadata_transplant_rejected(field,value):
    *_,r94b=r94_context(); reg=backend_key_registry(r94b); target=next(x for x in reg["entries"] if x["backend_id"]==r94b["backend_id"]); target[field]=value
    with pytest.raises(ValueError): build_with(r94_override=r94b,registry=reg)

def test_duplicate_or_ambiguous_exact_entry_rejected():
    *_,r94b=r94_context(); reg=backend_key_registry(r94b); target=next(x for x in reg["entries"] if x["backend_id"]==r94b["backend_id"]); reg["entries"].append(clone(target))
    with pytest.raises(ValueError,match="duplicate backend key registry entry"): build_with(r94_override=r94b,registry=reg)

def test_hidden_registry_claim_rejected():
    *_,r94b=r94_context(); reg=backend_key_registry(r94b); reg["key_owner_identity"]="forbidden"
    with pytest.raises(ValueError,match="registry key set mismatch"): build_with(r94_override=r94b,registry=reg)

def test_registry_root_domain_transplants_rejected():
    *_,r94b=r94_context(); wrong_roots={r94b["verifier_authority_root_sha256"],r94b["writer_authority_root_sha256"],r94b["backend_authenticity_verifier_authority_root_sha256"]}
    assert r94b["backend_authority_root_sha256"] not in wrong_roots
    for root in wrong_roots:
        reg=backend_key_registry(r94b); reg["backend_authority_root_sha256"]=root
        with pytest.raises(ValueError,match="authority root mismatch"): build_with(r94_override=r94b,registry=reg)

def test_registry_trust_operator_write_and_authority_overclaims_rejected():
    *_,r94b=r94_context()
    for field in ("backend_trust_root_verified","backend_registry_operator_identity_verified","backend_key_registry_write_performed","confers_authority"):
        reg=backend_key_registry(r94b); reg[field]=True
        with pytest.raises(ValueError): build_with(r94_override=r94b,registry=reg)

def test_policy_drift_bool_schema_and_algorithm_widening_rejected():
    p=provenance_policy(); p["network_access_in_core_allowed"]=True
    with pytest.raises(ValueError,match="unsafe backend key provenance policy"): build_with(policy=p)
    p=provenance_policy(); p["schema_version"]=True
    with pytest.raises(ValueError,match="unsupported backend key provenance policy"): build_with(policy=p)
    p=provenance_policy(); p["allowed_algorithms"]=["ED25519","ES256"]
    with pytest.raises(ValueError,match="algorithm allowlist drift"): build_with(policy=p)

def test_tampered_r94_binding_rejected_by_full_r94_validation():
    *_,r94b=r94_context(); bad=clone(r94b); bad["backend_key_id"]="substituted-key"; reg=backend_key_registry(bad)
    with pytest.raises(ValueError): build_with(r94_override=bad,registry=reg)

def test_all_inherited_r94_fields_are_preserved_exactly():
    *_,r94b=r94_context(); x=build()
    for k in f.m.BINDING_KEYS-{"schema","binding_id"}: assert x[k]==r94b[k] and type(x[k]) is type(r94b[k])

def test_schema_required_and_properties_match_contract():
    schema=json.loads((ROOT/"schemas"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_KEY_PROVENANCE_BINDING_V1.schema.json").read_text())
    assert set(schema["required"])==m.BINDING_KEYS; assert set(schema["properties"])==m.BINDING_KEYS; assert len(m.BINDING_KEYS)==110

def test_no_key_possession_authenticity_trust_durability_or_authority_upgrade():
    x=build(); assert x["backend_key_provenance_bound"] is True; assert x["backend_key_to_backend_metadata_bound"] is True; assert x["backend_public_key_digest_bound"] is True
    assert x["backend_key_registry_operator_identity_verified"] is False; assert x["backend_key_registry_write_performed"] is False
    for field in ["backend_key_possession_proven","backend_identity_verified","backend_trust_root_verified","backend_commit_authenticity_verified","readback_authenticity_verified",
                  "backend_authenticity_verifier_trust_root_verified","assertion_freshness_verified","liveness_verified","live_backend_observed","durable_commit_proven",
                  "durable_dual_state_atomicity_proven","durable_single_use_enforced","global_current_state_verified","concurrent_writer_exclusion_proven","write_performed",
                  "registry_write_performed","lease_registry_write_performed","receipt_index_write_performed","backend_write_performed","confers_authority"]: assert x[field] is False
    assert x["execution_authority"]=="NONE"; assert x["can_trade"] is False; assert x["capital_permission"]=="DENY"
