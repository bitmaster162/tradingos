from __future__ import annotations
import hashlib,json,re
from tools import tradingos_external_assertion_replay_cryptographic_artifact_identity_contract as r98

BINDING_SCHEMA="tradingos.external_assertion_replay_committed_readback_equality_binding.v1"
COMMITTED_DERIVATION_SCHEMA="control_center.committed_state_derivation_record.v1"
READBACK_DERIVATION_SCHEMA="control_center.readback_state_derivation_record.v1"
EQUALITY_SCHEMA="control_center.committed_readback_equality_record.v1"
POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_COMMITTED_READBACK_EQUALITY_POLICY_V1"
VERSION="1.1.0"
POLICY_SHA256="7b0220203814bd8ca91e4bc81479ce2a196d2e2b5d5eaabab1231741ade62b95"
S=re.compile(r"^[0-9a-f]{64}$")
I=re.compile(r"^[0-9a-f]{24}$")
KNOWN_FIXTURE_DIGESTS={c*64 for c in "0123456789abcdef"}
KNOWN_FIXTURE_COMMIT_IDS={"commit-r90-0001"}
SAFETY_EXPECTED={'approval_state_allowed': False, 'assertion_freshness_verified': False, 'attestation_set_consumption_authority': 'NONE', 'backend_authenticity_replay_candidate_apply_allowed': False, 'backend_authenticity_replay_candidate_write_performed': False, 'backend_authenticity_replay_registry_write_performed': False, 'backend_authenticity_verifier_identity_verified': False, 'backend_authenticity_verifier_registry_operator_identity_verified': False, 'backend_authenticity_verifier_trust_root_verified': False, 'backend_authority_root_trust_assertion_freshness_verified': False, 'backend_authority_root_trust_evaluator_identity_verified': False, 'backend_authority_root_trust_evaluator_trust_root_verified': False, 'backend_commit_authenticity_verified': False, 'backend_identity_verified': False, 'backend_key_possession_proven': False, 'backend_key_registry_operator_identity_verified': False, 'backend_key_registry_write_performed': False, 'backend_registry_operator_identity_verified': False, 'backend_trust_root_verified': False, 'backend_write_performed': False, 'can_trade': False, 'capital_permission': 'DENY', 'concurrent_writer_exclusion_proven': False, 'confers_authority': False, 'consensus_inference_allowed': False, 'cryptographic_artifact_bytes_retrieved': False, 'distinct_reviewer_count_allowed': False, 'durable_commit_proven': False, 'durable_dual_state_atomicity_proven': False, 'durable_single_use_enforced': False, 'execution_authority': 'NONE', 'global_current_state_verified': False, 'human_review_only': True, 'lease_registry_write_performed': False, 'live_backend_observed': False, 'live_decision_feedback_allowed': False, 'live_decision_use_allowed': False, 'liveness_verified': False, 'local_backend_authority_root_trust_evaluation_performed': False, 'local_cryptographic_artifact_verification_performed': False, 'local_signature_math_verified': False, 'memory_write_authority': 'NONE', 'model_selection_use_allowed': False, 'policy_update_allowed': False, 'readback_authenticity_verified': False, 'receipt_index_write_performed': False, 'registry_write_performed': False, 'review_identity_verified': False, 'shadow_only': True, 'verifier_trust_root_verified': False, 'write_performed': False, 'writer_authority_root_verified': False}
BINDING_KEYS={'expected_digest_independence_verified', 'backend_key_registry_write_performed', 'lease_registry_write_performed', 'consensus_inference_allowed', 'memory_write_authority', 'projection_schema_id', 'can_trade', 'policy_update_allowed', 'readback_source_artifact_sha256', 'backend_authenticity_replay_candidate_apply_allowed', 'attestation_set_consumption_authority', 'liveness_verified', 'capital_permission', 'backend_commit_authenticity_verified', 'durable_commit_proven', 'binding_id', 'backend_authenticity_verifier_trust_root_verified', 'live_decision_use_allowed', 'committed_derivation_record_sha256', 'backend_trust_root_verified', 'writer_authority_root_verified', 'schema', 'receipt_index_write_performed', 'r98_binding_sha256', 'write_performed', 'expected_record_digests_bound', 'execution_authority', 'backend_authenticity_verifier_identity_verified', 'backend_authenticity_verifier_registry_operator_identity_verified', 'committed_readback_projected_state_equality_bound', 'registry_write_performed', 'canonicalization_id', 'confers_authority', 'global_current_state_verified', 'model_selection_use_allowed', 'cryptographic_artifact_bytes_retrieved', 'equality_record_id', 'durable_single_use_enforced', 'review_identity_verified', 'backend_registry_operator_identity_verified', 'backend_identity_verified', 'assertion_freshness_verified', 'backend_authority_root_trust_assertion_freshness_verified', 'backend_authenticity_replay_candidate_write_performed', 'concurrent_writer_exclusion_proven', 'backend_write_performed', 'backend_authenticity_replay_registry_write_performed', 'readback_derivation_record_sha256', 'known_fixture_material_rejected', 'full_r98_validation_consumed', 'live_backend_observed', 'equality_record_sha256', 'backend_key_possession_proven', 'full_r98_safety_ceiling_preserved', 'readback_derivation_record_id', 'r98_binding_id', 'local_backend_authority_root_trust_evaluation_performed', 'committed_derivation_record_id', 'committed_projected_state_sha256', 'readback_projected_state_sha256', 'equality_policy_sha256', 'projection_schema_version', 'local_signature_math_verified', 'distinct_reviewer_count_allowed', 'backend_authority_root_trust_evaluator_identity_verified', 'derivation_tool_sha256', 'human_review_only', 'canonicalization_version', 'backend_authority_root_trust_evaluator_trust_root_verified', 'verifier_trust_root_verified', 'shadow_only', 'committed_source_artifact_sha256', 'readback_authenticity_verified', 'approval_state_allowed', 'commit_receipt_substitution_rejected', 'backend_key_registry_operator_identity_verified', 'durable_dual_state_atomicity_proven', 'live_decision_feedback_allowed', 'local_cryptographic_artifact_verification_performed'}
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

def _materialize_r98_safety_ceiling(r98b):
    if type(r98b) is not dict: raise ValueError("r98 binding")
    out={}
    for k,expected in SAFETY_EXPECTED.items():
        if k not in r98b or r98b[k]!=expected or type(r98b[k]) is not type(expected):
            raise ValueError("r98 safety ceiling drift")
        out[k]=r98b[k]
    return out

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
    safety=_materialize_r98_safety_ceiling(r98b)
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
    return r98b,c,r,eid,esha,p,safety

def _binding(a,kw):
    r98b,c,r,eid,esha,p,safety=_inputs(a,kw)
    x=dict(safety)
    x.update({
      "schema":BINDING_SCHEMA,"r98_binding_id":r98b["binding_id"],"r98_binding_sha256":stable_sha256(r98b),"equality_policy_sha256":stable_sha256(p),
      "committed_derivation_record_id":c["id"],"committed_derivation_record_sha256":c["sha"],
      "readback_derivation_record_id":r["id"],"readback_derivation_record_sha256":r["sha"],
      "equality_record_id":eid,"equality_record_sha256":esha,
      "projection_schema_id":c["projection_schema_id"],"projection_schema_version":c["projection_schema_version"],
      "canonicalization_id":c["canonicalization_id"],"canonicalization_version":c["canonicalization_version"],"derivation_tool_sha256":c["tool"],
      "committed_source_artifact_sha256":c["source"],"readback_source_artifact_sha256":r["source"],
      "committed_projected_state_sha256":c["projected"],"readback_projected_state_sha256":r["projected"],
      "committed_readback_projected_state_equality_bound":True,
      "expected_record_digests_bound":True,"expected_digest_independence_verified":False,
      "known_fixture_material_rejected":True,"commit_receipt_substitution_rejected":True,
      "full_r98_validation_consumed":True,"full_r98_safety_ceiling_preserved":True
    })
    return x

def _bid(x):
    return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]

def build_external_assertion_replay_committed_readback_equality_binding(*a,**kw):
    x=_binding(a,kw); x["binding_id"]=_bid(x)
    validate_external_assertion_replay_committed_readback_equality_binding(x,*a,**kw)
    return x

def validate_external_assertion_replay_committed_readback_equality_binding(b,*a,**kw):
    if type(b) is not dict or set(b)!=BINDING_KEYS or b.get("schema")!=BINDING_SCHEMA: raise ValueError("binding")
    _i(b.get("binding_id"),"binding_id"); e=_binding(a,kw)
    if any(b.get(k)!=v or type(b.get(k)) is not type(v) for k,v in e.items()) or b["binding_id"]!=_bid(b): raise ValueError("binding mismatch")
