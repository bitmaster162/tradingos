from __future__ import annotations
import json,pytest
from r98_external_assertion_replay_cryptographic_artifact_identity_fixtures import ROOT,f,m,identity_policy,r97_context,identity_record,_args,_kw,build,clone
def build_with(*,r97_override=None,record=None,expected_record_sha=None,policy=None):
    ctx=list(r97_context()); r97b=ctx[-1] if r97_override is None else r97_override; ctx[-1]=r97b; record=identity_record(r97b,policy=policy) if record is None else record
    return m.build_external_assertion_replay_cryptographic_artifact_identity_binding(*_args(tuple(ctx),record),**_kw(tuple(ctx),record,policy=policy,expected_record_sha=expected_record_sha))
def test_build_validate_and_determinism():
    x=build(); assert x["schema"]==m.BINDING_SCHEMA; assert x==build(); assert x["binding_id"]==build()["binding_id"]
def test_exact_r97_policy_challenge_and_record_are_bound():
    *_,r97b=r97_context(); p=identity_policy(); r=identity_record(r97b,p); x=build_with(r97_override=r97b,record=r,policy=p); ch=m.build_cryptographic_artifact_identity_challenge(r97b,p)
    assert x["r97_binding_id"]==r97b["binding_id"]; assert x["r97_binding_sha256"]==m.stable_sha256(r97b); assert x["cryptographic_artifact_identity_policy_sha256"]==m.stable_sha256(p); assert x["cryptographic_artifact_identity_challenge_sha256"]==m.stable_sha256(ch); assert x["cryptographic_artifact_identity_record_sha256"]==m.stable_sha256(r)
def test_record_substitution_rejected_against_retained_digest():
    *_,r97b=r97_context(); original=identity_record(r97b); retained=m.stable_sha256(original); changed=clone(original); changed["commit_signature_sha256"]="e"*64
    with pytest.raises(ValueError,match="record digest mismatch"): build_with(r97_override=r97b,record=changed,expected_record_sha=retained)
@pytest.mark.parametrize("field,value",[("backend_id","other-backend"),("backend_key_id","other-key"),("public_key_sha256","6"*64),("commit_signature_target_sha256","7"*64),("readback_signature_target_sha256","8"*64),("readback_state_sha256","9"*64)])
def test_backend_key_public_key_or_target_transplant_rejected(field,value):
    *_,r97b=r97_context(); r=identity_record(r97b); r[field]=value
    with pytest.raises(ValueError): build_with(r97_override=r97b,record=r)
def test_challenge_algorithm_hidden_and_overclaim_rejected():
    *_,r97b=r97_context(); r=identity_record(r97b); r["challenge_sha256"]="a"*64
    with pytest.raises(ValueError,match="challenge digest mismatch"): build_with(r97_override=r97b,record=r)
    r=identity_record(r97b); r["algorithm"]="ES256"
    with pytest.raises(ValueError,match="algorithm mismatch"): build_with(r97_override=r97b,record=r)
    r=identity_record(r97b); r["raw_signature_bytes"]="forbidden"
    with pytest.raises(ValueError,match="record key set mismatch"): build_with(r97_override=r97b,record=r)
def test_raw_artifact_local_math_and_authority_overclaim_rejected():
    *_,r97b=r97_context()
    for field in ("local_signature_math_verified","cryptographic_artifact_bytes_retrieved","backend_commit_authenticity_verified","readback_authenticity_verified","backend_key_possession_proven","backend_identity_verified","confers_authority"):
        r=identity_record(r97b); r[field]=True
        with pytest.raises(ValueError,match="overclaim"): build_with(r97_override=r97b,record=r)
def test_policy_bool_schema_and_inference_widening_rejected():
    p=identity_policy(); p["schema_version"]=True
    with pytest.raises(ValueError,match="unsupported cryptographic artifact identity policy"): build_with(policy=p)
    for field in ("backend_authenticity_inference_allowed","cryptographic_artifact_identity_truth_inference_allowed","network_access_in_core_allowed","local_cryptographic_artifact_verification_allowed","signature_validity_inference_allowed"):
        p=identity_policy(); p[field]=True
        with pytest.raises(ValueError,match="unsafe cryptographic artifact identity policy"): build_with(policy=p)
def test_tampered_r97_binding_rejected_by_full_r97_validation():
    *_,r97b=r97_context(); bad=clone(r97b); bad["public_key_sha256"]="b"*64; r=identity_record(bad)
    with pytest.raises(ValueError): build_with(r97_override=bad,record=r)
def test_all_inherited_r97_fields_are_preserved_exactly():
    *_,r97b=r97_context(); x=build()
    for k in f.m.BINDING_KEYS-{"schema","binding_id"}: assert x[k]==r97b[k] and type(x[k]) is type(r97b[k])
def test_schema_required_and_properties_match_contract():
    schema=json.loads((ROOT/"schemas"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_CRYPTOGRAPHIC_ARTIFACT_IDENTITY_BINDING_V1.schema.json").read_text()); assert set(schema["required"])==m.BINDING_KEYS; assert set(schema["properties"])==m.BINDING_KEYS
def test_no_authenticity_identity_durability_current_state_or_authority_upgrade():
    x=build(); assert x["cryptographic_artifact_identity_record_bound"] is True; assert x["commit_signature_artifact_identity_bound"] is True; assert x["readback_signature_artifact_identity_bound"] is True; assert x["public_key_artifact_identity_bound"] is True; assert x["local_cryptographic_artifact_verification_performed"] is False; assert x["cryptographic_artifact_bytes_retrieved"] is False
    for field in ["backend_commit_authenticity_verified","readback_authenticity_verified","backend_key_possession_proven","backend_identity_verified","assertion_freshness_verified","liveness_verified","durable_single_use_enforced","live_backend_observed","durable_commit_proven","durable_dual_state_atomicity_proven","global_current_state_verified","concurrent_writer_exclusion_proven","write_performed","registry_write_performed","lease_registry_write_performed","receipt_index_write_performed","backend_write_performed","confers_authority"]: assert x[field] is False
    assert x["execution_authority"]=="NONE"; assert x["can_trade"] is False; assert x["capital_permission"]=="DENY"
