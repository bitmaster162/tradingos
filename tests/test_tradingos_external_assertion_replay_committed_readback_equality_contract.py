from __future__ import annotations
import ast,json,hashlib,types,sys,importlib.util
from pathlib import Path
import pytest
from r99_external_assertion_replay_committed_readback_equality_fixtures import GOOD_R98,derivation,equality,stable,h,clone
ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/"tools"/"tradingos_external_assertion_replay_committed_readback_equality_contract.py"
POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_COMMITTED_READBACK_EQUALITY_POLICY_V1.json"
SCHEMA=ROOT/"schemas"/"TRADINGOS_EXTERNAL_ASSERTION_REPLAY_COMMITTED_READBACK_EQUALITY_BINDING_V1.schema.json"

def load_with_stub():
    pkg=types.ModuleType("tools"); pkg.__path__=[]; sys.modules.setdefault("tools",pkg)
    stub=types.ModuleType("tools.tradingos_external_assertion_replay_cryptographic_artifact_identity_contract"); stub.KW=set(); stub.validate_external_assertion_replay_cryptographic_artifact_identity_binding=lambda *a,**kw: None
    sys.modules[stub.__name__]=stub
    spec=importlib.util.spec_from_file_location("r99c",CONTRACT); m=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(m); return m

def test_ast_json_and_schema_load():
    ast.parse(CONTRACT.read_text()); json.loads(POLICY.read_text()); json.loads(SCHEMA.read_text())

def test_record_validation_and_equality_positive_with_nonfixture_material():
    m=load_with_stub(); c=derivation("COMMITTED_STATE",h("provider committed state")); r=derivation("READBACK_STATE",GOOD_R98["readback_state_sha256"]); e=equality(GOOD_R98,c,r)
    cc=m.validate_derivation_record(c,"COMMITTED_STATE",stable(c)); rr=m.validate_derivation_record(r,"READBACK_STATE",stable(r),GOOD_R98["readback_state_sha256"]); rid,d=m.validate_equality_record(e,GOOD_R98,cc,rr,stable(e)); assert rid==e["record_id"] and d==stable(e)

def test_known_fixture_digest_rejected():
    m=load_with_stub(); c=derivation("COMMITTED_STATE","e"*64)
    with pytest.raises(ValueError,match="known fixture material"): m.validate_derivation_record(c,"COMMITTED_STATE",stable(c))

def test_commit_receipt_substitution_logic_is_present():
    text=CONTRACT.read_text(); assert 'commit receipt substituted for committed state' in text

def test_projected_state_inequality_rejected():
    m=load_with_stub(); c=derivation("COMMITTED_STATE",h("provider committed state")); r=derivation("READBACK_STATE",GOOD_R98["readback_state_sha256"],projected=h("different projected state")); cc=m.validate_derivation_record(c,"COMMITTED_STATE",stable(c)); rr=m.validate_derivation_record(r,"READBACK_STATE",stable(r),GOOD_R98["readback_state_sha256"]); e=equality(GOOD_R98,c,r)
    with pytest.raises(ValueError,match="projected state inequality"): m.validate_equality_record(e,GOOD_R98,cc,rr,stable(e))

def test_schema_matches_contract_narrow_key_set():
    m=load_with_stub(); s=json.loads(SCHEMA.read_text()); assert set(s["required"])==m.BINDING_KEYS==set(s["properties"])

def test_safety_ceiling_is_explicit():
    s=json.loads(SCHEMA.read_text()); p=s["properties"]; assert p["execution_authority"]["const"]=="NONE"; assert p["can_trade"]["const"] is False; assert p["capital_permission"]["const"]=="DENY"; assert p["confers_authority"]["const"] is False; assert p["durable_commit_proven"]["const"] is False; assert p["global_current_state_verified"]["const"] is False
