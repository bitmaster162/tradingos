from __future__ import annotations
import json, pytest
from r83_attestation_set_fixtures import set_policy as r83_set_policy
from r84_reviewer_key_possession_fixtures import m as r84m, policy as r84_policy
from r85_external_verifier_provenance_fixtures import AUTHORITY_ROOT_SHA256, m as r85m, provenance_policy
from r86_external_assertion_replay_guard_fixtures import m as r86m, replay_policy
from r87_external_assertion_replay_atomic_cas_fixtures import m as r87m, atomic_cas_policy
from r88_external_assertion_replay_writer_fencing_recovery_fixtures import m as r88m, writer_fencing_recovery_policy
from r89_external_assertion_replay_writer_authority_anchor_fixtures import WRITER_AUTHORITY_ROOT_SHA256, m as r89m, writer_authority_anchor_policy
from r90_external_assertion_replay_dual_state_atomicity_fixtures import m as r90m, dual_state_atomicity_policy
from r91_external_assertion_replay_durable_commit_readback_evidence_fixtures import m as r91m, commit_readback_evidence_policy
from r92_external_assertion_replay_backend_provenance_fixtures import (
    ROOT,m,BACKEND_AUTHORITY_ROOT_SHA256,backend_provenance_policy,r91_context,backend_registry,provenance_verification,build,clone)

def build_with(*,registry_override=None,provenance_override=None,expected_registry_sha=None,expected_backend_root=BACKEND_AUTHORITY_ROOT_SHA256,
               expected_provenance_sha=None,expected_verifier_root=AUTHORITY_ROOT_SHA256,expected_writer_root=WRITER_AUTHORITY_ROOT_SHA256,policy_override=None):
    (
        items,manifest,aid,assertion,r84_binding,verifier_reg,r85_binding,replay_reg,r86_binding,
        atomic_receipt,r87_binding,recovery_receipt,r88_binding,anchor,r89_binding,atomicity_receipt,r90_binding,cr,r91_binding
    )=r91_context()
    registry=backend_registry() if registry_override is None else registry_override
    provenance=provenance_verification(r91_binding,registry) if provenance_override is None else provenance_override
    return m.build_external_assertion_replay_backend_provenance_binding(
        r91_binding,r90_binding,r89_binding,r88_binding,r87_binding,r86_binding,r85_binding,r84_binding,
        manifest,items,r83_set_policy(),aid,assertion,verifier_reg,replay_reg,atomic_receipt,recovery_receipt,anchor,atomicity_receipt,cr,
        registry,provenance,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),expected_verifier_authority_root_sha256=expected_verifier_root,
        expected_writer_authority_root_sha256=expected_writer_root,provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=r89m.stable_sha256(anchor),writer_authority_anchor_policy=writer_authority_anchor_policy(),
        expected_atomicity_verification_sha256=r90m.stable_sha256(atomicity_receipt),dual_state_atomicity_policy=dual_state_atomicity_policy(),
        expected_commit_readback_evidence_sha256=r91m.stable_sha256(cr),commit_readback_evidence_policy=commit_readback_evidence_policy(),
        expected_backend_registry_sha256=m.stable_sha256(registry) if expected_registry_sha is None else expected_registry_sha,
        expected_backend_authority_root_sha256=expected_backend_root,
        expected_backend_provenance_verification_sha256=m.stable_sha256(provenance) if expected_provenance_sha is None else expected_provenance_sha,
        backend_provenance_policy=backend_provenance_policy() if policy_override is None else policy_override)

def test_build_validate_and_determinism():
    x=build(); assert x["schema"]==m.BINDING_SCHEMA; assert x["backend_provenance_bound"] is True
    assert x==build(); assert x["binding_id"]==build()["binding_id"]

def test_three_root_domains_are_distinct_and_bound():
    assert len({AUTHORITY_ROOT_SHA256,WRITER_AUTHORITY_ROOT_SHA256,BACKEND_AUTHORITY_ROOT_SHA256})==3
    x=build_with()
    assert x["verifier_authority_root_sha256"]==AUTHORITY_ROOT_SHA256
    assert x["writer_authority_root_sha256"]==WRITER_AUTHORITY_ROOT_SHA256
    assert x["backend_authority_root_sha256"]==BACKEND_AUTHORITY_ROOT_SHA256

@pytest.mark.parametrize("root", [AUTHORITY_ROOT_SHA256,WRITER_AUTHORITY_ROOT_SHA256])
def test_other_root_domains_cannot_substitute_for_backend_root(root):
    with pytest.raises(ValueError,match="backend authority root digest mismatch"): build_with(expected_backend_root=root)

def test_registry_digest_substitution_rejected():
    reg=backend_registry(); retained=m.stable_sha256(reg); changed=clone(reg); changed["entries"][0]["backend_metadata_sha256"]="4"*64
    with pytest.raises(ValueError,match="backend registry digest mismatch"):
        build_with(registry_override=changed,expected_registry_sha=retained)

def test_provenance_digest_substitution_rejected():
    *_,r91b=r91_context(); reg=backend_registry(); p=provenance_verification(r91b,reg); retained=m.stable_sha256(p)
    changed=clone(p); changed["readback_state_sha256"]="4"*64
    with pytest.raises(ValueError,match="provenance verification digest mismatch"):
        build_with(registry_override=reg,provenance_override=changed,expected_provenance_sha=retained)

@pytest.mark.parametrize("field",["external_commit_receipt_sha256","readback_evidence_sha256","readback_state_sha256"])
def test_r91_artifact_transplant_rejected(field):
    *_,r91b=r91_context(); reg=backend_registry(); p=provenance_verification(r91b,reg); p[field]="5"*64
    with pytest.raises(ValueError,match="R91 mismatch"): build_with(registry_override=reg,provenance_override=p)

def test_duplicate_registry_entry_rejected():
    reg=backend_registry(); reg["entries"].append(clone(reg["entries"][0])); reg["entries"]=sorted(reg["entries"],key=lambda x:(x["backend_id"],x["backend_key_id"],x["backend_metadata_sha256"],x["backend_kind"],x["receipt_format"],x["readback_format"]))
    *_,r91b=r91_context(); p=provenance_verification(r91b,backend_registry())
    with pytest.raises(ValueError,match="duplicate backend registry entry"): build_with(registry_override=reg,provenance_override=p)

def test_missing_unique_metadata_match_rejected():
    *_,r91b=r91_context(); reg=backend_registry(); p=provenance_verification(r91b,reg); p["backend_key_id"]="missing-key"
    with pytest.raises(ValueError,match="unique metadata match required"): build_with(registry_override=reg,provenance_override=p)

def test_selected_entry_digest_mismatch_rejected():
    *_,r91b=r91_context(); reg=backend_registry(); p=provenance_verification(r91b,reg); p["selected_backend_entry_sha256"]="6"*64
    with pytest.raises(ValueError,match="selected backend entry digest mismatch"): build_with(registry_override=reg,provenance_override=p)

@pytest.mark.parametrize("field",["same_backend_metadata_claim_bound","commit_receipt_backend_metadata_bound","readback_backend_metadata_bound","backend_provenance_match"])
def test_required_provenance_guards(field):
    *_,r91b=r91_context(); reg=backend_registry(); p=provenance_verification(r91b,reg); p[field]=False
    with pytest.raises(ValueError,match="required backend provenance guard missing"): build_with(registry_override=reg,provenance_override=p)

@pytest.mark.parametrize("field",[
"backend_commit_authenticity_verified","readback_authenticity_verified","backend_identity_verified","backend_trust_root_verified",
"backend_registry_operator_identity_verified","live_backend_observed","durable_commit_proven","durable_dual_state_atomicity_proven",
"write_performed","global_current_state_verified","concurrent_writer_exclusion_proven","can_execute","apply_allowed","confers_authority"])
def test_provenance_overclaims_rejected(field):
    *_,r91b=r91_context(); reg=backend_registry(); p=provenance_verification(r91b,reg); p[field]=True
    with pytest.raises(ValueError,match="backend provenance overclaim"): build_with(registry_override=reg,provenance_override=p)

def test_execution_authority_overclaim_rejected():
    *_,r91b=r91_context(); reg=backend_registry(); p=provenance_verification(r91b,reg); p["execution_authority"]="LIVE"
    with pytest.raises(ValueError,match="execution authority overclaim"): build_with(registry_override=reg,provenance_override=p)

def test_hidden_registry_key_rejected():
    reg=backend_registry(); reg["hidden_trust"]=True
    *_,r91b=r91_context(); p=provenance_verification(r91b,backend_registry())
    with pytest.raises(ValueError,match="backend registry key set mismatch"): build_with(registry_override=reg,provenance_override=p)

def test_hidden_provenance_key_rejected():
    *_,r91b=r91_context(); reg=backend_registry(); p=provenance_verification(r91b,reg); p["hidden_authenticity"]=True
    with pytest.raises(ValueError,match="verification key set mismatch"): build_with(registry_override=reg,provenance_override=p)

def test_unsafe_policy_drift_rejected():
    p=backend_provenance_policy(); p["backend_authenticity_inference_allowed"]=True
    with pytest.raises(ValueError,match="unsafe backend provenance policy"): build_with(policy_override=p)

def test_bool_schema_version_rejected():
    p=backend_provenance_policy(); p["schema_version"]=True
    with pytest.raises(ValueError,match="unsupported backend provenance policy"): build_with(policy_override=p)

def test_schema_required_keys_match_contract():
    schema=json.loads((ROOT/"schemas"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_PROVENANCE_BINDING_V1.schema.json").read_text())
    assert set(schema["required"])==m.BINDING_KEYS
    assert set(schema["properties"])==m.BINDING_KEYS

def test_no_backend_authenticity_durability_or_authority_upgrade():
    x=build()
    for f in ["backend_commit_authenticity_verified","readback_authenticity_verified","backend_identity_verified","backend_trust_root_verified",
              "backend_registry_operator_identity_verified","live_backend_observed","durable_commit_proven","durable_dual_state_atomicity_proven",
              "durable_single_use_enforced","write_performed","global_current_state_verified","concurrent_writer_exclusion_proven"]:
        assert x[f] is False
    assert x["execution_authority"]=="NONE"; assert x["can_trade"] is False; assert x["capital_permission"]=="DENY"
