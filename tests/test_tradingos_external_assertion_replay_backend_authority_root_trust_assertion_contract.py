from __future__ import annotations
import json,pytest
from r97_external_assertion_replay_backend_authority_root_trust_assertion_fixtures import ROOT,f,m,trust_policy,r96_context,trust_assertion,_args,_kw,build,clone
def build_with(*,r96_override=None,assertion=None,expected_assertion_sha=None,policy=None):
    ctx=list(r96_context()); r96b=ctx[-1] if r96_override is None else r96_override; ctx[-1]=r96b; assertion=trust_assertion(r96b,policy=policy) if assertion is None else assertion
    return m.build_external_assertion_replay_backend_authority_root_trust_assertion_binding(*_args(tuple(ctx),assertion),**_kw(tuple(ctx),assertion,policy=policy,expected_assertion_sha=expected_assertion_sha))
def test_build_validate_and_determinism():
    x=build(); assert x["schema"]==m.BINDING_SCHEMA; assert x==build(); assert x["binding_id"]==build()["binding_id"]
def test_exact_r96_policy_challenge_and_assertion_are_bound():
    *_,r96b=r96_context(); p=trust_policy(); a=trust_assertion(r96b,p); x=build_with(r96_override=r96b,assertion=a,policy=p); ch=m.build_backend_authority_root_trust_challenge(r96b,p)
    assert x["r96_binding_id"]==r96b["binding_id"]; assert x["r96_binding_sha256"]==m.stable_sha256(r96b); assert x["backend_authority_root_trust_assertion_policy_sha256"]==m.stable_sha256(p); assert x["backend_authority_root_trust_challenge_sha256"]==m.stable_sha256(ch); assert x["backend_authority_root_trust_assertion_sha256"]==m.stable_sha256(a); assert ch["backend_registry_sha256"]==r96b["backend_registry_sha256"]
def test_assertion_substitution_rejected_against_retained_digest():
    *_,r96b=r96_context(); original=trust_assertion(r96b); retained=m.stable_sha256(original); changed=clone(original); changed["trust_evaluator_id"]="other-evaluator"
    with pytest.raises(ValueError,match="assertion digest mismatch"): build_with(r96_override=r96b,assertion=changed,expected_assertion_sha=retained)
@pytest.mark.parametrize("field,value",[("backend_authority_root_sha256","6"*64),("backend_registry_sha256","7"*64),("backend_key_registry_sha256","8"*64),("backend_id","other-backend"),("backend_key_id","other-key")])
def test_root_registry_or_backend_transplant_rejected(field,value):
    *_,r96b=r96_context(); a=trust_assertion(r96b); a[field]=value
    with pytest.raises(ValueError,match="assertion mismatch"): build_with(r96_override=r96b,assertion=a)
def test_challenge_algorithm_hidden_and_overclaim_rejected():
    *_,r96b=r96_context(); a=trust_assertion(r96b); a["challenge_sha256"]="9"*64
    with pytest.raises(ValueError,match="challenge digest mismatch"): build_with(r96_override=r96b,assertion=a)
    a=trust_assertion(r96b); a["algorithm"]="ES256"
    with pytest.raises(ValueError,match="algorithm mismatch"): build_with(r96_override=r96b,assertion=a)
    a=trust_assertion(r96b); a["certificate_chain_verified"]="forbidden"
    with pytest.raises(ValueError,match="assertion key set mismatch"): build_with(r96_override=r96b,assertion=a)
def test_trust_false_local_evaluation_and_authority_overclaim_rejected():
    *_,r96b=r96_context(); a=trust_assertion(r96b); a["backend_authority_root_trust_asserted"]=False
    with pytest.raises(ValueError,match="must assert trust"): build_with(r96_override=r96b,assertion=a)
    a=trust_assertion(r96b); a["local_trust_evaluation_performed"]=True
    with pytest.raises(ValueError,match="local backend authority-root trust evaluation forbidden"): build_with(r96_override=r96b,assertion=a)
    a=trust_assertion(r96b); a["confers_authority"]=True
    with pytest.raises(ValueError,match="authority overclaim"): build_with(r96_override=r96b,assertion=a)
def test_policy_bool_schema_and_inference_widening_rejected():
    p=trust_policy(); p["schema_version"]=True
    with pytest.raises(ValueError,match="unsupported backend authority-root trust assertion policy"): build_with(policy=p)
    for field in ("backend_trust_root_inference_allowed","backend_authority_root_trust_evaluator_identity_inference_allowed","network_access_in_core_allowed","local_trust_evaluation_allowed"):
        p=trust_policy(); p[field]=True
        with pytest.raises(ValueError,match="unsafe backend authority-root trust assertion policy"): build_with(policy=p)
def test_tampered_r96_binding_rejected_by_full_r96_validation():
    *_,r96b=r96_context(); bad=clone(r96b); bad["backend_authority_root_sha256"]="b"*64; a=trust_assertion(bad)
    with pytest.raises(ValueError): build_with(r96_override=bad,assertion=a)
def test_all_inherited_r96_fields_are_preserved_exactly():
    *_,r96b=r96_context(); x=build()
    for k in f.m.BINDING_KEYS-{"schema","binding_id"}: assert x[k]==r96b[k] and type(x[k]) is type(r96b[k])
def test_schema_required_and_properties_match_contract():
    schema=json.loads((ROOT/"schemas"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHORITY_ROOT_TRUST_ASSERTION_BINDING_V1.schema.json").read_text()); assert set(schema["required"])==m.BINDING_KEYS; assert set(schema["properties"])==m.BINDING_KEYS; assert len(m.BINDING_KEYS)==146
def test_no_root_trust_authenticity_identity_durability_or_authority_upgrade():
    x=build(); assert x["backend_authority_root_trust_assertion_bound"] is True; assert x["backend_authority_root_trust_asserted_by_external_evaluator"] is True; assert x["backend_authority_root_trust_evaluator_identity_verified"] is False; assert x["backend_authority_root_trust_evaluator_trust_root_verified"] is False; assert x["backend_authority_root_trust_assertion_freshness_verified"] is False; assert x["local_backend_authority_root_trust_evaluation_performed"] is False
    for field in ["backend_trust_root_verified","backend_commit_authenticity_verified","readback_authenticity_verified","backend_key_possession_proven","backend_identity_verified","assertion_freshness_verified","liveness_verified","durable_single_use_enforced","live_backend_observed","durable_commit_proven","durable_dual_state_atomicity_proven","global_current_state_verified","concurrent_writer_exclusion_proven","write_performed","registry_write_performed","lease_registry_write_performed","receipt_index_write_performed","backend_write_performed","confers_authority"]: assert x[field] is False
    assert x["execution_authority"]=="NONE"; assert x["can_trade"] is False; assert x["capital_permission"]=="DENY"
