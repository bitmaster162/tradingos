from __future__ import annotations
import ast,json,pytest
from r100_external_derivation_provenance_verification_fixtures import ROOT,m,policy,r99_binding,provenance_record,args,kw,stable,clone

CONTRACT=ROOT/"tools"/"tradingos_external_derivation_provenance_verification_contract.py"
SCHEMA=ROOT/"schemas"/"TRADINGOS_EXTERNAL_DERIVATION_PROVENANCE_VERIFICATION_BINDING_V1.schema.json"

def isolated_build(monkeypatch,*,r99b=None,record=None,policy_override=None,expected_record_sha=None):
    b=r99_binding() if r99b is None else r99b
    r=provenance_record(b) if record is None else record
    monkeypatch.setattr(m.r99,"validate_external_assertion_replay_committed_readback_equality_binding",lambda *a,**k: None)
    return m.build_external_derivation_provenance_verification_binding(
        *args(b,r),**kw(r,policy_override=policy_override,expected_record_sha=expected_record_sha))

def test_ast_json_schema_and_policy(monkeypatch):
    ast.parse(CONTRACT.read_text(encoding="utf-8"))
    p=policy(); m.validate_external_derivation_provenance_verification_policy(p)
    s=json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert set(s["required"])==m.BINDING_KEYS==set(s["properties"])
    assert len(m.BINDING_KEYS)==95
    x=isolated_build(monkeypatch)
    assert x["schema"]==m.BINDING_SCHEMA

def test_build_validate_and_determinism(monkeypatch):
    x=isolated_build(monkeypatch)
    y=isolated_build(monkeypatch)
    assert x==y
    assert x["binding_id"]==y["binding_id"]

def test_expected_provenance_record_digest_substitution_rejected(monkeypatch):
    b=r99_binding(); r=provenance_record(b)
    with pytest.raises(ValueError,match="record digest mismatch"):
        isolated_build(monkeypatch,r99b=b,record=r,expected_record_sha="f"*64)

@pytest.mark.parametrize("field",[
    "committed_derivation_record_sha256","readback_derivation_record_sha256","equality_record_sha256",
    "committed_source_artifact_sha256","readback_source_artifact_sha256","derivation_tool_sha256"])
def test_digest_lineage_transplant_rejected(monkeypatch,field):
    b=r99_binding(); r=provenance_record(b); r[field]="e"*64
    with pytest.raises(ValueError,match="lineage mismatch"):
        isolated_build(monkeypatch,r99b=b,record=r)

@pytest.mark.parametrize("field,value",[
    ("projection_schema_id","OTHER_PROJECTION"),
    ("projection_schema_version",2),
    ("canonicalization_id","OTHER_CANONICALIZATION"),
    ("canonicalization_version",2)])
def test_projection_or_canonicalization_transplant_rejected(monkeypatch,field,value):
    b=r99_binding(); r=provenance_record(b); r[field]=value
    with pytest.raises(ValueError,match="lineage mismatch"):
        isolated_build(monkeypatch,r99b=b,record=r)

@pytest.mark.parametrize("field",[
    "committed_derivation_digest_recomputed","readback_derivation_digest_recomputed",
    "equality_record_digest_recomputed","projected_state_equality_recomputed",
    "out_of_process_recomputation_claimed"])
def test_required_recomputation_claims(monkeypatch,field):
    b=r99_binding(); r=provenance_record(b); r[field]=False
    with pytest.raises(ValueError,match="recomputation claim"):
        isolated_build(monkeypatch,r99b=b,record=r)

def test_hidden_record_key_and_authority_overclaim_rejected(monkeypatch):
    b=r99_binding(); r=provenance_record(b); r["raw_provider_credential"]="forbidden"
    with pytest.raises(ValueError,match="record key set mismatch"):
        isolated_build(monkeypatch,r99b=b,record=r)
    r=provenance_record(b); r["confers_authority"]=True
    with pytest.raises(ValueError,match="authority overclaim"):
        isolated_build(monkeypatch,r99b=b,record=r)

def test_policy_bool_schema_and_inference_widening_rejected(monkeypatch):
    p=policy(); p["schema_version"]=True
    with pytest.raises(ValueError,match="unsupported external derivation provenance policy"):
        isolated_build(monkeypatch,policy_override=p)
    for field in ("expected_digest_independence_inference_allowed","verifier_trust_inference_allowed","provider_honesty_inference_allowed","backend_write_allowed"):
        p=policy(); p[field]=True
        with pytest.raises(ValueError,match="unsafe external derivation provenance policy"):
            isolated_build(monkeypatch,policy_override=p)

def test_r99_safety_and_independence_drift_rejected(monkeypatch):
    b=r99_binding(); b["backend_identity_verified"]=True; r=provenance_record(b)
    with pytest.raises(ValueError,match="r99 safety ceiling drift"):
        isolated_build(monkeypatch,r99b=b,record=r)
    b=r99_binding(); b["expected_digest_independence_verified"]=True; r=provenance_record(b)
    with pytest.raises(ValueError,match="independence must remain unverified"):
        isolated_build(monkeypatch,r99b=b,record=r)

def test_real_r99_validator_is_consumed_before_r100_record(monkeypatch):
    b=r99_binding(); r=provenance_record(b); called={"v":False}
    original=m.r99.validate_external_assertion_replay_committed_readback_equality_binding
    def wrapped(*a,**k):
        called["v"]=True
        return original(*a,**k)
    monkeypatch.setattr(m.r99,"validate_external_assertion_replay_committed_readback_equality_binding",wrapped)
    with pytest.raises(ValueError):
        m.build_external_derivation_provenance_verification_binding(*args(b,r),**kw(r))
    assert called["v"] is True

def test_no_independence_trust_provider_durability_or_authority_upgrade(monkeypatch):
    x=isolated_build(monkeypatch)
    assert x["external_derivation_provenance_record_bound"] is True
    assert x["out_of_process_recomputation_claim_bound"] is True
    assert x["expected_provenance_digest_bound"] is True
    assert x["full_r99_validation_consumed"] is True
    assert x["full_r99_safety_ceiling_preserved"] is True
    for field in (
        "expected_digest_independence_verified","external_provenance_digest_independence_verified",
        "external_provenance_record_retention_verified","external_derivation_verifier_identity_verified",
        "external_derivation_verifier_trust_root_verified","provider_honesty_verified",
        "durable_commit_proven","global_current_state_verified","concurrent_writer_exclusion_proven"):
        assert x[field] is False
    assert x["execution_authority"]=="NONE"
    assert x["can_trade"] is False
    assert x["capital_permission"]=="DENY"
    assert x["confers_authority"] is False
