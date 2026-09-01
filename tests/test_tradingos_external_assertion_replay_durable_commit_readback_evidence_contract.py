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
from r91_external_assertion_replay_durable_commit_readback_evidence_fixtures import ROOT,m,commit_readback_evidence_policy,r90_context,commit_readback_evidence,build,clone

def build_with(*,r90_binding_override=None,evidence_override=None,expected_evidence_sha=None,
               expected_verifier_root=AUTHORITY_ROOT_SHA256,expected_writer_root=WRITER_AUTHORITY_ROOT_SHA256,
               policy_override=None):
    (
        items,manifest,aid,assertion,r84_binding,verifier_reg,r85_binding,replay_reg,r86_binding,
        atomic_receipt,r87_binding,recovery_receipt,r88_binding,anchor,r89_binding,atomicity_receipt,r90_binding
    )=r90_context()
    r90_binding=r90_binding if r90_binding_override is None else r90_binding_override
    evidence=commit_readback_evidence(r90_binding) if evidence_override is None else evidence_override
    expected_evidence_sha=m.stable_sha256(evidence) if expected_evidence_sha is None else expected_evidence_sha
    return m.build_external_assertion_replay_durable_commit_readback_evidence_binding(
        r90_binding,r89_binding,r88_binding,r87_binding,r86_binding,r85_binding,r84_binding,
        manifest,items,r83_set_policy(),aid,assertion,verifier_reg,replay_reg,atomic_receipt,
        recovery_receipt,anchor,atomicity_receipt,evidence,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_verifier_authority_root_sha256=expected_verifier_root,
        expected_writer_authority_root_sha256=expected_writer_root,provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),
        writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=r89m.stable_sha256(anchor),writer_authority_anchor_policy=writer_authority_anchor_policy(),
        expected_atomicity_verification_sha256=r90m.stable_sha256(atomicity_receipt),dual_state_atomicity_policy=dual_state_atomicity_policy(),
        expected_commit_readback_evidence_sha256=expected_evidence_sha,
        commit_readback_evidence_policy=commit_readback_evidence_policy() if policy_override is None else policy_override)

def test_build_validate_and_determinism():
    x=build(); assert x["schema"]==m.BINDING_SCHEMA; assert x["external_commit_receipt_evidence_bound"] is True
    assert x==build(); assert x["binding_id"]==build()["binding_id"]

def test_root_domains_remain_distinct():
    assert AUTHORITY_ROOT_SHA256!=WRITER_AUTHORITY_ROOT_SHA256
    x=build_with(); assert x["verifier_authority_root_sha256"]==AUTHORITY_ROOT_SHA256
    assert x["writer_authority_root_sha256"]==WRITER_AUTHORITY_ROOT_SHA256

def test_cross_domain_root_substitutions_rejected():
    with pytest.raises(ValueError): build_with(expected_verifier_root=WRITER_AUTHORITY_ROOT_SHA256)
    with pytest.raises(ValueError): build_with(expected_writer_root=AUTHORITY_ROOT_SHA256)

def test_evidence_digest_substitution_rejected():
    *_,r90b=r90_context(); original=commit_readback_evidence(r90b); retained=m.stable_sha256(original)
    changed=clone(original); changed["readback_state_sha256"]="2"*64
    with pytest.raises(ValueError,match="evidence digest mismatch"):
        build_with(r90_binding_override=r90b,evidence_override=changed,expected_evidence_sha=retained)

@pytest.mark.parametrize("field",["authority_anchor_sha256","writer_lease_sha256","prior_receipt_index_sha256","lease_lineage_sha256","idempotency_key_sha256"])
def test_r90_transition_transplant_rejected(field):
    *_,r90b=r90_context(); e=commit_readback_evidence(r90b); e[field]="3"*64
    with pytest.raises(ValueError,match="R90 mismatch"): build_with(r90_binding_override=r90b,evidence_override=e)

def test_commit_id_transplant_rejected():
    *_,r90b=r90_context(); e=commit_readback_evidence(r90b); e["commit_id"]="commit-other"
    with pytest.raises(ValueError,match="R90 mismatch"): build_with(r90_binding_override=r90b,evidence_override=e)

@pytest.mark.parametrize("field",["commit_receipt_index_sha256","readback_receipt_index_sha256"])
def test_receipt_index_must_match_r90_next_candidate(field):
    *_,r90b=r90_context(); e=commit_readback_evidence(r90b); e[field]="4"*64
    with pytest.raises(ValueError,match="R90 mismatch"): build_with(r90_binding_override=r90b,evidence_override=e)

@pytest.mark.parametrize("field",["receipt_identity_bound","read_after_write_match","commit_receipt_retained","readback_retained"])
def test_required_evidence_guards(field):
    *_,r90b=r90_context(); e=commit_readback_evidence(r90b); e[field]=False
    with pytest.raises(ValueError,match="guard missing"): build_with(r90_binding_override=r90b,evidence_override=e)

@pytest.mark.parametrize("field",[
"backend_commit_authenticity_verified","backend_identity_verified","live_backend_observed","durable_commit_proven",
"durable_dual_state_atomicity_proven","write_performed","global_current_state_verified","concurrent_writer_exclusion_proven",
"registry_write_performed","lease_registry_write_performed","receipt_index_write_performed","backend_write_performed",
"can_execute","apply_allowed","confers_authority"])
def test_evidence_overclaims_rejected(field):
    *_,r90b=r90_context(); e=commit_readback_evidence(r90b); e[field]=True
    with pytest.raises(ValueError,match="evidence overclaim"): build_with(r90_binding_override=r90b,evidence_override=e)

def test_execution_authority_overclaim_rejected():
    *_,r90b=r90_context(); e=commit_readback_evidence(r90b); e["execution_authority"]="LIVE"
    with pytest.raises(ValueError,match="execution authority overclaim"): build_with(r90_binding_override=r90b,evidence_override=e)

def test_hidden_key_rejected():
    *_,r90b=r90_context(); e=commit_readback_evidence(r90b); e["hidden_durable_claim"]=True
    with pytest.raises(ValueError,match="key set mismatch"): build_with(r90_binding_override=r90b,evidence_override=e)

def test_unsafe_policy_drift_rejected():
    p=commit_readback_evidence_policy(); p["durable_commit_inference_allowed"]=True
    with pytest.raises(ValueError,match="unsafe commit/readback evidence policy"): build_with(policy_override=p)

def test_bool_schema_version_rejected():
    p=commit_readback_evidence_policy(); p["schema_version"]=True
    with pytest.raises(ValueError,match="unsupported commit/readback evidence policy"): build_with(policy_override=p)

def test_tampered_r90_binding_rejected_by_full_r90_validation():
    *_,r90b=r90_context(); bad=clone(r90b); bad["next_receipt_index_candidate_sha256"]="5"*64
    e=commit_readback_evidence(bad)
    with pytest.raises(ValueError): build_with(r90_binding_override=bad,evidence_override=e)

def test_schema_required_keys_match_contract():
    schema=json.loads((ROOT/"schemas"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_DURABLE_COMMIT_READBACK_EVIDENCE_BINDING_V1.schema.json").read_text())
    assert set(schema["required"])==m.BINDING_KEYS
    assert set(schema["properties"])==m.BINDING_KEYS

def test_no_durable_backend_or_authority_upgrade():
    x=build()
    for f in ["backend_commit_authenticity_verified","backend_identity_verified","live_backend_observed",
              "durable_commit_proven","durable_dual_state_atomicity_proven","write_performed",
              "global_current_state_verified","concurrent_writer_exclusion_proven"]:
        assert x[f] is False
    assert x["execution_authority"]=="NONE"; assert x["can_trade"] is False; assert x["capital_permission"]=="DENY"
