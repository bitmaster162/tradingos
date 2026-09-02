from __future__ import annotations
import json, pytest
from r93_external_assertion_replay_backend_authenticity_assertion_fixtures import ROOT,m,backend_authenticity_assertion_policy,r92_context,backend_authenticity_assertion,build,clone

def test_build_validate_and_determinism():
    x=build(); assert x["schema"]==m.BINDING_SCHEMA; assert x["backend_authenticity_assertion_bound"] is True
    assert x==build(); assert x["binding_id"]==build()["binding_id"]

def test_challenge_is_deterministic_and_exactly_binds_r92():
    *_,r92b=r92_context(); p=backend_authenticity_assertion_policy(); c1=m.build_backend_authenticity_challenge(r92b,p); c2=m.build_backend_authenticity_challenge(r92b,p)
    assert c1==c2; assert set(c1)==m.CHALLENGE_KEYS; assert len(m.CHALLENGE_KEYS)==19; assert c1["r92_binding_id"]==r92b["binding_id"]
    assert c1["purpose"]=="R93_BACKEND_ARTIFACT_AUTHENTICITY_ASSERTION_BINDING_ONLY"
    assert c1["r92_binding_sha256"]==m.stable_sha256(r92b); assert c1["backend_registry_sha256"]==r92b["backend_registry_sha256"]
    assert c1["selected_backend_entry_sha256"]==r92b["selected_backend_entry_sha256"]
    assert c1["external_commit_receipt_sha256"]==r92b["external_commit_receipt_sha256"]
    assert c1["readback_evidence_sha256"]==r92b["readback_evidence_sha256"]; assert c1["readback_state_sha256"]==r92b["readback_state_sha256"]
    assert "nonce" not in c1; assert "timestamp" not in c1

def test_assertion_digest_substitution_rejected():
    *_,r92b=r92_context(); a=backend_authenticity_assertion(r92b); retained=m.stable_sha256(a); changed=clone(a); changed["public_key_sha256"]="8"*64
    with pytest.raises(ValueError,match="assertion digest mismatch"):
        _build_with(r92b,changed,expected_assertion_sha=retained)

def _build_with(r92b,auth,expected_assertion_sha=None,policy=None):
    ctx=r92_context(); items,manifest,aid,assertion,r84b,vreg,r85b,rreg,r86b,atomic,r87b,recovery,r88b,anchor,r89b,atomicity,r90b,cr,r91b,reg,prov,_=ctx
    from r83_attestation_set_fixtures import set_policy as r83_set_policy
    from r84_reviewer_key_possession_fixtures import m as r84m, policy as r84_policy
    from r85_external_verifier_provenance_fixtures import AUTHORITY_ROOT_SHA256, m as r85m, provenance_policy
    from r86_external_assertion_replay_guard_fixtures import m as r86m, replay_policy
    from r87_external_assertion_replay_atomic_cas_fixtures import m as r87m, atomic_cas_policy
    from r88_external_assertion_replay_writer_fencing_recovery_fixtures import m as r88m, writer_fencing_recovery_policy
    from r89_external_assertion_replay_writer_authority_anchor_fixtures import WRITER_AUTHORITY_ROOT_SHA256, m as r89m, writer_authority_anchor_policy
    from r90_external_assertion_replay_dual_state_atomicity_fixtures import m as r90m, dual_state_atomicity_policy
    from r91_external_assertion_replay_durable_commit_readback_evidence_fixtures import m as r91m, commit_readback_evidence_policy
    from r92_external_assertion_replay_backend_provenance_fixtures import m as r92m, BACKEND_AUTHORITY_ROOT_SHA256, backend_provenance_policy
    p=backend_authenticity_assertion_policy() if policy is None else policy
    return m.build_external_assertion_replay_backend_authenticity_assertion_binding(
        r92b,r91b,r90b,r89b,r88b,r87b,r86b,r85b,r84b,manifest,items,r83_set_policy(),aid,assertion,vreg,rreg,atomic,recovery,anchor,atomicity,cr,reg,prov,auth,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),key_possession_policy=r84_policy(),expected_verifier_registry_sha256=r85m.stable_sha256(vreg),
        expected_verifier_authority_root_sha256=AUTHORITY_ROOT_SHA256,expected_writer_authority_root_sha256=WRITER_AUTHORITY_ROOT_SHA256,provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(rreg),replay_guard_policy=replay_policy(),expected_atomic_verification_sha256=r87m.stable_sha256(atomic),
        atomic_cas_policy=atomic_cas_policy(),expected_recovery_verification_sha256=r88m.stable_sha256(recovery),writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=r89m.stable_sha256(anchor),writer_authority_anchor_policy=writer_authority_anchor_policy(),expected_atomicity_verification_sha256=r90m.stable_sha256(atomicity),
        dual_state_atomicity_policy=dual_state_atomicity_policy(),expected_commit_readback_evidence_sha256=r91m.stable_sha256(cr),commit_readback_evidence_policy=commit_readback_evidence_policy(),
        expected_backend_registry_sha256=r92m.stable_sha256(reg),expected_backend_authority_root_sha256=BACKEND_AUTHORITY_ROOT_SHA256,
        expected_backend_provenance_verification_sha256=r92m.stable_sha256(prov),backend_provenance_policy=backend_provenance_policy(),
        expected_backend_authenticity_assertion_sha256=m.stable_sha256(auth) if expected_assertion_sha is None else expected_assertion_sha,backend_authenticity_assertion_policy=p)

def test_challenge_substitution_rejected():
    *_,r92b=r92_context(); a=backend_authenticity_assertion(r92b); a["challenge_sha256"]="8"*64
    with pytest.raises(ValueError,match="challenge mismatch"): _build_with(r92b,a)

@pytest.mark.parametrize("field",["backend_id","backend_key_id","backend_metadata_sha256"])
def test_r92_backend_metadata_transplant_rejected(field):
    *_,r92b=r92_context(); a=backend_authenticity_assertion(r92b); a[field]="9"*64 if field.endswith("sha256") else "other-backend"
    with pytest.raises(ValueError,match="R92 mismatch"): _build_with(r92b,a)

@pytest.mark.parametrize("field",["commit_signature_verified_by_external_asymmetric_verifier","readback_signature_verified_by_external_asymmetric_verifier","same_backend_key_claim_bound"])
def test_required_external_assertion_guards(field):
    *_,r92b=r92_context(); a=backend_authenticity_assertion(r92b); a[field]=False
    with pytest.raises(ValueError): _build_with(r92b,a)

def test_local_signature_math_overclaim_rejected():
    *_,r92b=r92_context(); a=backend_authenticity_assertion(r92b); a["local_signature_math_verified"]=True
    with pytest.raises(ValueError,match="local signature math overclaim"): _build_with(r92b,a)

def test_unsupported_algorithm_rejected():
    *_,r92b=r92_context(); a=backend_authenticity_assertion(r92b); a["algorithm"]="RSA"
    with pytest.raises(ValueError,match="unsupported backend authenticity algorithm"): _build_with(r92b,a)

@pytest.mark.parametrize("field",["backend_commit_authenticity_verified","readback_authenticity_verified","backend_key_possession_proven","backend_identity_verified","backend_trust_root_verified","backend_authenticity_verifier_trust_root_verified","assertion_freshness_verified","confers_authority"])
def test_assertion_overclaims_rejected(field):
    *_,r92b=r92_context(); a=backend_authenticity_assertion(r92b); a[field]=True
    with pytest.raises(ValueError,match="assertion overclaim"): _build_with(r92b,a)

def test_hidden_assertion_key_rejected():
    *_,r92b=r92_context(); a=backend_authenticity_assertion(r92b); a["raw_signature_bytes"]="forbidden"
    with pytest.raises(ValueError,match="assertion key set mismatch"): _build_with(r92b,a)

def test_policy_drift_and_bool_schema_version_rejected():
    *_,r92b=r92_context(); a=backend_authenticity_assertion(r92b)
    p=backend_authenticity_assertion_policy(); p["backend_authenticity_inference_allowed"]=True
    with pytest.raises(ValueError,match="unsafe backend authenticity assertion policy"): _build_with(r92b,a,policy=p)
    p=backend_authenticity_assertion_policy(); p["schema_version"]=True
    with pytest.raises(ValueError,match="unsupported backend authenticity assertion policy"): _build_with(r92b,a,policy=p)

def test_tampered_r92_binding_rejected_by_full_r92_validation():
    *_,r92b=r92_context(); bad=clone(r92b); bad["backend_metadata_sha256"]="a"*64; a=backend_authenticity_assertion(bad)
    with pytest.raises(ValueError): _build_with(bad,a)

def test_schema_required_keys_match_contract():
    schema=json.loads((ROOT/"schemas"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_ASSERTION_BINDING_V1.schema.json").read_text())
    assert set(schema["required"])==m.BINDING_KEYS; assert set(schema["properties"])==m.BINDING_KEYS

def test_no_authenticity_durability_or_authority_upgrade():
    x=build()
    for f in ["backend_commit_authenticity_verified","readback_authenticity_verified","backend_key_possession_proven","backend_identity_verified","backend_trust_root_verified","backend_authenticity_verifier_trust_root_verified","live_backend_observed","durable_commit_proven","durable_dual_state_atomicity_proven","durable_single_use_enforced","write_performed","global_current_state_verified","concurrent_writer_exclusion_proven"]:
        assert x[f] is False
    assert x["execution_authority"]=="NONE"; assert x["can_trade"] is False; assert x["capital_permission"]=="DENY"
