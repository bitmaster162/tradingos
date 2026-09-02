from __future__ import annotations
import ast,json,types,sys,importlib.util
from pathlib import Path
import pytest
from r99_external_assertion_replay_committed_readback_equality_fixtures import GOOD_R98,SAFETY,derivation,equality,stable,h,clone
ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/"tools"/"tradingos_external_assertion_replay_committed_readback_equality_contract.py"
POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_COMMITTED_READBACK_EQUALITY_POLICY_V1.json"
SCHEMA=ROOT/"schemas"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_COMMITTED_READBACK_EQUALITY_BINDING_V1.schema.json"

def load_with_stub():
    missing=object()
    old_tools=sys.modules.get("tools",missing)
    stub_name="tools.tradingos_external_assertion_replay_cryptographic_artifact_identity_contract"
    old_stub=sys.modules.get(stub_name,missing)
    try:
        pkg=types.ModuleType("tools"); pkg.__path__=[]; sys.modules["tools"]=pkg
        stub=types.ModuleType(stub_name)
        stub.KW=set(); stub.validate_external_assertion_replay_cryptographic_artifact_identity_binding=lambda *a,**kw: None
        sys.modules[stub_name]=stub
        spec=importlib.util.spec_from_file_location("r99r2c",CONTRACT); m=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(m)
        return m
    finally:
        if old_stub is missing: sys.modules.pop(stub_name,None)
        else: sys.modules[stub_name]=old_stub
        if old_tools is missing: sys.modules.pop("tools",None)
        else: sys.modules["tools"]=old_tools

def policy(): return json.loads(POLICY.read_text())

def isolated_vector(m):
    c=derivation("COMMITTED_STATE",h("provider committed state"))
    r=derivation("READBACK_STATE",GOOD_R98["readback_state_sha256"])
    e=equality(GOOD_R98,c,r)
    a=(GOOD_R98,*([None]*34),c,r,e)
    kw={"expected_committed_derivation_record_sha256":stable(c),"expected_readback_derivation_record_sha256":stable(r),"expected_equality_record_sha256":stable(e),"committed_readback_equality_policy":policy()}
    return a,kw,c,r,e

def test_stub_loader_restores_tools_import_state():
    before_tools=sys.modules.get("tools")
    before_stub=sys.modules.get("tools.tradingos_external_assertion_replay_cryptographic_artifact_identity_contract")
    m=load_with_stub()
    assert m.r98.KW==set()
    assert sys.modules.get("tools") is before_tools
    assert sys.modules.get("tools.tradingos_external_assertion_replay_cryptographic_artifact_identity_contract") is before_stub

def test_ast_json_schema_and_policy_hash():
    ast.parse(CONTRACT.read_text()); p=policy(); s=json.loads(SCHEMA.read_text()); m=load_with_stub()
    assert m.stable_sha256(p)==m.POLICY_SHA256
    assert set(s["required"])==m.BINDING_KEYS==set(s["properties"])

def test_isolated_build_binds_expected_digests_without_independence_claim():
    m=load_with_stub(); a,kw,*_=isolated_vector(m)
    x=m.build_external_assertion_replay_committed_readback_equality_binding(*a,**kw)
    assert x["expected_record_digests_bound"] is True
    assert x["expected_digest_independence_verified"] is False
    assert "independently_supplied_record_digests_consumed" not in x

def test_self_computed_expected_digest_is_not_promoted_to_independence():
    m=load_with_stub(); a,kw,*_=isolated_vector(m)
    x=m.build_external_assertion_replay_committed_readback_equality_binding(*a,**kw)
    assert x["expected_digest_independence_verified"] is False

def test_expected_digest_substitution_rejected():
    m=load_with_stub(); a,kw,*_=isolated_vector(m); kw=dict(kw)
    kw["expected_committed_derivation_record_sha256"]="a"*64
    with pytest.raises(ValueError,match="derivation record digest mismatch"):
        m.build_external_assertion_replay_committed_readback_equality_binding(*a,**kw)

def test_full_safety_ceiling_materialized_and_drift_rejected():
    m=load_with_stub(); a,kw,*_=isolated_vector(m)
    x=m.build_external_assertion_replay_committed_readback_equality_binding(*a,**kw)
    for k,v in SAFETY.items(): assert x[k]==v and type(x[k]) is type(v)
    bad=list(a); bad[0]=clone(GOOD_R98); bad[0]["backend_identity_verified"]=True
    with pytest.raises(ValueError,match="r98 safety ceiling drift"):
        m.build_external_assertion_replay_committed_readback_equality_binding(*tuple(bad),**kw)

def test_fixture_commit_and_digest_rejected():
    m=load_with_stub(); a,kw,*_=isolated_vector(m)
    bad=list(a); bad[0]=clone(GOOD_R98); bad[0]["commit_id"]="commit-r90-0001"
    with pytest.raises(ValueError,match="known fixture material"):
        m.build_external_assertion_replay_committed_readback_equality_binding(*tuple(bad),**kw)
    c=derivation("COMMITTED_STATE","e"*64)
    with pytest.raises(ValueError,match="known fixture material"):
        m.validate_derivation_record(c,"COMMITTED_STATE",stable(c))

def test_projected_state_inequality_rejected():
    m=load_with_stub()
    c=derivation("COMMITTED_STATE",h("provider committed state"))
    r=derivation("READBACK_STATE",GOOD_R98["readback_state_sha256"],projected=h("different projected state"))
    cc=m.validate_derivation_record(c,"COMMITTED_STATE",stable(c))
    rr=m.validate_derivation_record(r,"READBACK_STATE",stable(r),GOOD_R98["readback_state_sha256"])
    e=equality(GOOD_R98,c,r)
    with pytest.raises(ValueError,match="projected state inequality"):
        m.validate_equality_record(e,GOOD_R98,cc,rr,stable(e))

def test_commit_receipt_substitution_rejected():
    m=load_with_stub(); a,kw,*_=isolated_vector(m)
    bad=list(a); c=clone(bad[35]); c["source_artifact_sha256"]=GOOD_R98["external_commit_receipt_sha256"]; bad[35]=c
    e=equality(GOOD_R98,c,bad[36]); bad[37]=e; kw=dict(kw)
    kw["expected_committed_derivation_record_sha256"]=stable(c); kw["expected_equality_record_sha256"]=stable(e)
    with pytest.raises(ValueError,match="commit receipt substituted for committed state"):
        m.build_external_assertion_replay_committed_readback_equality_binding(*tuple(bad),**kw)

def test_real_r98_validator_regression_when_repo_chain_available():
    try:
        import r98_external_assertion_replay_cryptographic_artifact_identity_fixtures as rf
    except ImportError:
        pytest.skip("exact upstream R84-R98 chain not materialized in isolated packet")
    ctx=rf.r97_context(); *_,r97b=ctx; rec=rf.identity_record(r97b)
    r98b=rf.m.build_external_assertion_replay_cryptographic_artifact_identity_binding(*rf._args(ctx,rec),**rf._kw(ctx,rec))
    spec=importlib.util.spec_from_file_location("r99r2real",CONTRACT); m=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(m)
    original=m.r98.validate_external_assertion_replay_cryptographic_artifact_identity_binding; called={"v":False}
    def wrapped(*a,**kw): called["v"]=True; return original(*a,**kw)
    m.r98.validate_external_assertion_replay_cryptographic_artifact_identity_binding=wrapped
    c=derivation("COMMITTED_STATE",h("provider committed state"))
    r=derivation("READBACK_STATE",r98b["readback_state_sha256"]); e=equality(r98b,c,r)
    args=(r98b,*rf._args(ctx,rec),c,r,e); kw=rf._kw(ctx,rec)
    kw.update(expected_committed_derivation_record_sha256=stable(c),expected_readback_derivation_record_sha256=stable(r),expected_equality_record_sha256=stable(e),committed_readback_equality_policy=policy())
    with pytest.raises(ValueError,match="known fixture material"):
        m.build_external_assertion_replay_committed_readback_equality_binding(*args,**kw)
    assert called["v"] is True
