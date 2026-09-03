from __future__ import annotations
import json,pytest
from r96_external_assertion_replay_backend_authenticity_replay_guard_fixtures import ROOT,f,m,replay_policy,r95_context,replay_registry,_args,_kw,build,clone

def build_with(*,r95_override=None,registry=None,expected_registry_sha=None,policy=None):
    ctx=list(r95_context()); r95b=ctx[-1] if r95_override is None else r95_override; ctx[-1]=r95b
    registry=replay_registry(r95b) if registry is None else registry
    return m.build_external_assertion_replay_backend_authenticity_replay_guard_binding(
        *_args(tuple(ctx),registry),**_kw(tuple(ctx),registry,policy=policy,expected_registry_sha=expected_registry_sha))

def test_build_validate_and_determinism():
    x=build(); assert x["schema"]==m.BINDING_SCHEMA; assert x==build(); assert x["binding_id"]==build()["binding_id"]
    ctx=r95_context(); reg=replay_registry(ctx[-1]); m.validate_external_assertion_replay_backend_authenticity_replay_guard_binding(x,*_args(ctx,reg),**_kw(ctx,reg))

def test_exact_r95_policy_assertion_and_challenge_are_bound():
    *_,r95b=r95_context(); x=build()
    assert x["r95_binding_id"]==r95b["binding_id"]; assert x["r95_binding_sha256"]==m.stable_sha256(r95b)
    assert x["backend_authenticity_replay_guard_policy_sha256"]==m.stable_sha256(replay_policy())
    assert x["backend_authenticity_assertion_sha256"]==r95b["backend_authenticity_assertion_sha256"]
    assert x["challenge_sha256"]==r95b["challenge_sha256"]

def test_replay_snapshot_and_candidate_are_bound():
    ctx=r95_context(); *_,r95b=ctx; reg=replay_registry(r95b); x=build_with(r95_override=r95b,registry=reg)
    assert x["backend_registry_sha256"]==_kw(ctx,reg)["expected_backend_registry_sha256"]
    assert x["backend_authenticity_replay_registry_sha256"]==m.stable_sha256(reg)
    assert x["backend_authenticity_replay_prior_generation"]==11
    assert x["backend_authenticity_replay_next_generation"]==12
    assert x["backend_authenticity_assertion_absent_in_expected_replay_registry"] is True
    assert x["backend_authenticity_challenge_absent_in_expected_replay_registry"] is True
    assert x["backend_authenticity_replay_guard_candidate_bound"] is True
    assert x["backend_authenticity_replay_absence_bound"] is True

def test_registry_substitution_rejected_against_retained_digest():
    *_,r95b=r95_context(); original=replay_registry(r95b); retained=m.stable_sha256(original); changed=clone(original); changed["registry_id"]+="x"
    with pytest.raises(ValueError,match="registry digest mismatch"): build_with(r95_override=r95b,registry=changed,expected_registry_sha=retained)

def test_assertion_replay_rejected():
    *_,r95b=r95_context(); reg=replay_registry(r95b); reg["used_backend_authenticity_assertion_sha256s"]=sorted([*reg["used_backend_authenticity_assertion_sha256s"],r95b["backend_authenticity_assertion_sha256"]])
    with pytest.raises(ValueError,match="assertion replay detected"): build_with(r95_override=r95b,registry=reg)

def test_challenge_replay_rejected():
    *_,r95b=r95_context(); reg=replay_registry(r95b); reg["used_backend_authenticity_challenge_sha256s"]=sorted([*reg["used_backend_authenticity_challenge_sha256s"],r95b["challenge_sha256"]])
    with pytest.raises(ValueError,match="challenge replay detected"): build_with(r95_override=r95b,registry=reg)

def test_unsorted_or_duplicate_digest_history_rejected():
    *_,r95b=r95_context(); reg=replay_registry(r95b); reg["used_backend_authenticity_assertion_sha256s"]=["3"*64,"1"*64]
    with pytest.raises(ValueError,match="sorted and unique"): build_with(r95_override=r95b,registry=reg)
    reg=replay_registry(r95b); reg["used_backend_authenticity_challenge_sha256s"]=["2"*64,"2"*64]
    with pytest.raises(ValueError,match="sorted and unique"): build_with(r95_override=r95b,registry=reg)

def test_generation_bool_and_overflow_rejected():
    *_,r95b=r95_context(); reg=replay_registry(r95b); reg["generation"]=True
    with pytest.raises(ValueError,match="generation invalid"): build_with(r95_override=r95b,registry=reg)
    reg=replay_registry(r95b); reg["generation"]=2147483647
    with pytest.raises(ValueError,match="generation invalid"): build_with(r95_override=r95b,registry=reg)

def test_hidden_registry_claim_and_overclaims_rejected():
    *_,r95b=r95_context(); reg=replay_registry(r95b); reg["current_truth"]=True
    with pytest.raises(ValueError,match="registry key set mismatch"): build_with(r95_override=r95b,registry=reg)
    for field in ("durable_commit_proven","write_allowed","apply_allowed","confers_authority"):
        reg=replay_registry(r95b); reg[field]=True
        with pytest.raises(ValueError): build_with(r95_override=r95b,registry=reg)

def test_policy_bool_schema_and_inference_widening_rejected():
    p=replay_policy(); p["schema_version"]=True
    with pytest.raises(ValueError,match="unsupported backend authenticity replay policy"): build_with(policy=p)
    for field in ("freshness_inference_allowed","durable_single_use_inference_allowed","network_access_in_core_allowed"):
        p=replay_policy(); p[field]=True
        with pytest.raises(ValueError,match="unsafe backend authenticity replay policy"): build_with(policy=p)

def test_tampered_r95_binding_rejected_by_full_r95_validation():
    *_,r95b=r95_context(); bad=clone(r95b); bad["backend_authenticity_assertion_sha256"]="a"*64; reg=replay_registry(bad)
    with pytest.raises(ValueError): build_with(r95_override=bad,registry=reg)

def test_all_inherited_r95_fields_are_preserved_exactly():
    *_,r95b=r95_context(); x=build()
    for k in f.m.BINDING_KEYS-{"schema","binding_id"}: assert x[k]==r95b[k] and type(x[k]) is type(r95b[k])

def test_schema_required_and_properties_match_contract():
    schema=json.loads((ROOT/"schemas"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_REPLAY_GUARD_BINDING_V1.schema.json").read_text())
    assert set(schema["required"])==m.BINDING_KEYS; assert set(schema["properties"])==m.BINDING_KEYS; assert len(m.BINDING_KEYS)==128

def test_no_freshness_single_use_authenticity_trust_durability_or_authority_upgrade():
    x=build()
    assert x["backend_authenticity_replay_guard_candidate_bound"] is True
    assert x["backend_authenticity_replay_registry_write_performed"] is False
    assert x["backend_authenticity_replay_candidate_write_performed"] is False
    assert x["backend_authenticity_replay_candidate_apply_allowed"] is False
    for field in ["assertion_freshness_verified","liveness_verified","durable_single_use_enforced","backend_commit_authenticity_verified",
                  "readback_authenticity_verified","backend_key_possession_proven","backend_identity_verified","backend_trust_root_verified",
                  "backend_authenticity_verifier_trust_root_verified","live_backend_observed","durable_commit_proven","durable_dual_state_atomicity_proven",
                  "global_current_state_verified","concurrent_writer_exclusion_proven","write_performed","registry_write_performed",
                  "lease_registry_write_performed","receipt_index_write_performed","backend_write_performed","confers_authority"]:
        assert x[field] is False
    assert x["execution_authority"]=="NONE"; assert x["can_trade"] is False; assert x["capital_permission"]=="DENY"
