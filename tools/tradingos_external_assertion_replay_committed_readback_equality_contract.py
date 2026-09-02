from __future__ import annotations
import hashlib,json,re
from tools import tradingos_external_assertion_replay_cryptographic_artifact_identity_contract as r98

BINDING_SCHEMA="tradingos.external_assertion_replay_committed_readback_equality_binding.v1"
COMMITTED_DERIVATION_SCHEMA="control_center.committed_state_derivation_record.v1"
READBACK_DERIVATION_SCHEMA="control_center.readback_state_derivation_record.v1"
EQUALITY_SCHEMA="control_center.committed_readback_equality_record.v1"
POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_COMMITTED_READBACK_EQUALITY_POLICY_V1"
VERSION="1.0.0"
POLICY_SHA256="0911fe214c44014716a5be5f2af71784856f23c3fe919e82dc32e4a5946ef8fd"
S=re.compile(r"^[0-9a-f]{64}$")
I=re.compile(r"^[0-9a-f]{24}$")
KNOWN_FIXTURE_DIGESTS={c*64 for c in "123456789abcdef"}
KNOWN_FIXTURE_COMMIT_IDS={"commit-r90-0001"}

BINDING_KEYS=set("""schema binding_id r98_binding_id r98_binding_sha256 equality_policy_sha256 committed_derivation_record_id committed_derivation_record_sha256 readback_derivation_record_id readback_derivation_record_sha256 equality_record_id equality_record_sha256 projection_schema_id projection_schema_version canonicalization_id canonicalization_version derivation_tool_sha256 committed_source_artifact_sha256 readback_source_artifact_sha256 committed_projected_state_sha256 readback_projected_state_sha256 committed_readback_projected_state_equality_bound independently_supplied_record_digests_consumed known_fixture_material_rejected commit_receipt_substitution_rejected full_r98_validation_consumed backend_commit_authenticity_verified readback_authenticity_verified durable_commit_proven durable_dual_state_atomicity_proven durable_single_use_enforced global_current_state_verified concurrent_writer_exclusion_proven execution_authority can_trade capital_permission confers_authority""".split())
EXTRA_KW={"expected_committed_derivation_record_sha256","expected_readback_derivation_record_sha256","expected_equality_record_sha256","committed_readback_equality_policy"}
KW=set(r98.KW)|EXTRA_KW

def stable_json_bytes(v): return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def stable_sha256(v): return hashlib.sha256(stable_json_bytes(v)).hexdigest()
def _s(v,n):
    if type(v) is not str or S.fullmatch(v) is None: raise ValueError(n)
    return v
def _i(v,n):
    if type(v) is not str or I.fullmatch(v) is None: raise ValueError(n)
    return v
def _t(v,n):
    if type(v) is not str or v!=v.strip() or not 1<=len(v)<=128 or any(not 33<=ord(c)<=126 for c in v): raise ValueError(n)
    return v
def _int(v,n):
    if type(v) is not int or v<1 or v>2147483647: raise ValueError(n)
    return v

def validate_committed_readback_equality_policy(p):
    if type(p) is not dict or type(p.get("schema_version")) is not int or p.get("schema_version")!=1 or p.get("policy_id")!=POLICY_ID: raise ValueError("unsupported equality policy")
    if stable_sha256(p)!=POLICY_SHA256: raise ValueError("unsafe equality policy")

def _reject_fixture_digest(v,n):
    v=_s(v,n)
    if v in KNOWN_FIXTURE_DIGESTS: raise ValueError("known fixture material")
    return v

def validate_derivation_record(record,role,expected_sha256,expected_source_sha256=None):
    if type(record) is not dict: raise ValueError("derivation record")
    required={"schema","record_id","source_role","source_artifact_sha256","source_provenance_sha256","projection_schema_id","projection_schema_version","canonicalization_id","canonicalization_version","derivation_tool_sha256","canonical_projected_state_sha256"}
    expected_schema=COMMITTED_DERIVATION_SCHEMA if role=="COMMITTED_STATE" else READBACK_DERIVATION_SCHEMA
    if set(record)!=required or record.get("schema")!=expected_schema: raise ValueError("derivation record key set")
    rid=_i(record.get("record_id"),"record_id")
    if record.get("source_role")!=role: raise ValueError("source role mismatch")
    source=_reject_fixture_digest(record.get("source_artifact_sha256"),"source_artifact_sha256")
    if expected_source_sha256 is not None and source!=_s(expected_source_sha256,"expected_source_sha256"): raise ValueError("source artifact lineage mismatch")
    prov=_reject_fixture_digest(record.get("source_provenance_sha256"),"source_provenance_sha256")
    psid=_t(record.get("projection_schema_id"),"projection_schema_id"); psv=_int(record.get("projection_schema_version"),"projection_schema_version")
    cid=_t(record.get("canonicalization_id"),"canonicalization_id"); cv=_int(record.get("canonicalization_version"),"canonicalization_version")
    tool=_reject_fixture_digest(record.get("derivation_tool_sha256"),"derivation_tool_sha256")
    projected=_reject_fixture_digest(record.get("canonical_projected_state_sha256"),"canonical_projected_state_sha256")
    digest=stable_sha256(record)
    if digest!=_s(expected_sha256,"expected_derivation_record_sha256"): raise ValueError("derivation record digest mismatch")
    return {"id":rid,"sha":digest,"source":source,"provenance":prov,"projection_schema_id":psid,"projection_schema_version":psv,"canonicalization_id":cid,"canonicalization_version":cv,"tool":tool,"projected":projected}

def validate_equality_record(record,r98b,c,r,expected_sha256):
    if type(record) is not dict: raise ValueError("equality record")
    required={"schema","record_id","r98_binding_id","r98_binding_sha256","external_commit_receipt_sha256","readback_state_sha256","committed_derivation_record_sha256","readback_derivation_record_sha256","projection_schema_id","projection_schema_version","canonicalization_id","canonicalization_version","derivation_tool_sha256","committed_projected_state_sha256","readback_projected_state_sha256"}
    if set(record)!=required or record.get("schema")!=EQUALITY_SCHEMA: raise ValueError("equality record key set")
    rid=_i(record.get("record_id"),"record_id")
    if record.get("r98_binding_id")!=r98b.get("binding_id") or record.get("r98_binding_sha256")!=stable_sha256(r98b): raise ValueError("r98 lineage mismatch")
    if record.get("external_commit_receipt_sha256")!=r98b.get("external_commit_receipt_sha256") or record.get("readback_state_sha256")!=r98b.get("readback_state_sha256"): raise ValueError("r98 artifact lineage mismatch")
    if record.get("committed_derivation_record_sha256")!=c["sha"] or record.get("readback_derivation_record_sha256")!=r["sha"]: raise ValueError("derivation lineage mismatch")
    for k in ("projection_schema_id","projection_schema_version","canonicalization_id","canonicalization_version"):
        if record.get(k)!=c[k] or record.get(k)!=r[k]: raise ValueError("projection or canonicalization mismatch")
    if record.get("derivation_tool_sha256")!=c["tool"] or record.get("derivation_tool_sha256")!=r["tool"]: raise ValueError("derivation tool mismatch")
    if record.get("committed_projected_state_sha256")!=c["projected"] or record.get("readback_projected_state_sha256")!=r["projected"]: raise ValueError("projected state lineage mismatch")
    if c["projected"]!=r["projected"]: raise ValueError("projected state inequality")
    digest=stable_sha256(record)
    if digest!=_s(expected_sha256,"expected_equality_record_sha256"): raise ValueError("equality record digest mismatch")
    return rid,digest

def _inputs(a,kw):
    if len(a)!=38 or set(kw)!=KW: raise ValueError("inputs")
    r98b=a[0]; upstream_args=a[1:35]; committed=a[35]; readback=a[36]; equality=a[37]
    p=kw["committed_readback_equality_policy"]; validate_committed_readback_equality_policy(p)
    r98.validate_external_assertion_replay_cryptographic_artifact_identity_binding(r98b,*upstream_args,**{k:kw[k] for k in r98.KW})
    if r98b.get("commit_id") in KNOWN_FIXTURE_COMMIT_IDS: raise ValueError("known fixture material")
    receipt=_s(r98b.get("external_commit_receipt_sha256"),"external_commit_receipt_sha256")
    readback_source=_s(r98b.get("readback_state_sha256"),"readback_state_sha256")
    if receipt in KNOWN_FIXTURE_DIGESTS or readback_source in KNOWN_FIXTURE_DIGESTS: raise ValueError("known fixture material")
    c=validate_derivation_record(committed,"COMMITTED_STATE",kw["expected_committed_derivation_record_sha256"])
    r=validate_derivation_record(readback,"READBACK_STATE",kw["expected_readback_derivation_record_sha256"],readback_source)
    if c["source"]==receipt: raise ValueError("commit receipt substituted for committed state")
    if (c["projection_schema_id"],c["projection_schema_version"])!=(r["projection_schema_id"],r["projection_schema_version"]): raise ValueError("projection mismatch")
    if (c["canonicalization_id"],c["canonicalization_version"])!=(r["canonicalization_id"],r["canonicalization_version"]): raise ValueError("canonicalization mismatch")
    if c["tool"]!=r["tool"]: raise ValueError("derivation tool mismatch")
    eid,esha=validate_equality_record(equality,r98b,c,r,kw["expected_equality_record_sha256"])
    return r98b,c,r,eid,esha,p

def _binding(a,kw):
    r98b,c,r,eid,esha,p=_inputs(a,kw)
    x={
      "schema":BINDING_SCHEMA,"r98_binding_id":r98b["binding_id"],"r98_binding_sha256":stable_sha256(r98b),"equality_policy_sha256":stable_sha256(p),
      "committed_derivation_record_id":c["id"],"committed_derivation_record_sha256":c["sha"],"readback_derivation_record_id":r["id"],"readback_derivation_record_sha256":r["sha"],"equality_record_id":eid,"equality_record_sha256":esha,
      "projection_schema_id":c["projection_schema_id"],"projection_schema_version":c["projection_schema_version"],"canonicalization_id":c["canonicalization_id"],"canonicalization_version":c["canonicalization_version"],"derivation_tool_sha256":c["tool"],
      "committed_source_artifact_sha256":c["source"],"readback_source_artifact_sha256":r["source"],"committed_projected_state_sha256":c["projected"],"readback_projected_state_sha256":r["projected"],
      "committed_readback_projected_state_equality_bound":True,"independently_supplied_record_digests_consumed":True,"known_fixture_material_rejected":True,"commit_receipt_substitution_rejected":True,"full_r98_validation_consumed":True,
      "backend_commit_authenticity_verified":False,"readback_authenticity_verified":False,"durable_commit_proven":False,"durable_dual_state_atomicity_proven":False,"durable_single_use_enforced":False,"global_current_state_verified":False,"concurrent_writer_exclusion_proven":False,
      "execution_authority":"NONE","can_trade":False,"capital_permission":"DENY","confers_authority":False
    }
    return x

def _bid(x): return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]

def build_external_assertion_replay_committed_readback_equality_binding(*a,**kw):
    x=_binding(a,kw); x["binding_id"]=_bid(x); validate_external_assertion_replay_committed_readback_equality_binding(x,*a,**kw); return x

def validate_external_assertion_replay_committed_readback_equality_binding(b,*a,**kw):
    if type(b) is not dict or set(b)!=BINDING_KEYS or b.get("schema")!=BINDING_SCHEMA: raise ValueError("binding")
    _i(b.get("binding_id"),"binding_id"); e=_binding(a,kw)
    if any(b.get(k)!=v or type(b.get(k)) is not type(v) for k,v in e.items()) or b["binding_id"]!=_bid(b): raise ValueError("binding mismatch")
