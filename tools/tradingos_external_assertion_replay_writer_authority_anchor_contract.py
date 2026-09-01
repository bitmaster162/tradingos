"""TradingOS R89 R2 writer-authority-anchor binding with separated root domains."""
from __future__ import annotations
import hashlib, re
from typing import Any
from tools import tradingos_external_assertion_replay_writer_fencing_recovery_contract as r88
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes, stable_sha256

BINDING_SCHEMA="tradingos.external_assertion_replay_writer_authority_anchor_binding.v1"
AUTHORITY_ANCHOR_SCHEMA="control_center.external_assertion_replay_writer_authority_anchor.v1"
POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_WRITER_AUTHORITY_ANCHOR_POLICY_V1"
VERSION="1.0.0"
_ID=re.compile(r"^[0-9a-f]{24}$"); _SHA=re.compile(r"^[0-9a-f]{64}$")
OUTPUT_PERMISSIONS={"execution_authority":"NONE","signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","confers_authority":False}

POLICY_KEYS=set("""schema_version policy_id mode input_r88_binding_schema authority_anchor_schema
require_full_r88_validation require_expected_authority_anchor_digest require_expected_authority_root_digest require_exact_r88_anchor_binding require_retained_reference
network_access_in_core_allowed credential_access_in_core_allowed registry_write_allowed lease_registry_write_allowed receipt_index_write_allowed backend_write_allowed
authority_root_trust_inference_allowed authority_anchor_operator_identity_inference_allowed durable_commit_inference_allowed durable_single_use_inference_allowed
global_current_state_inference_allowed concurrent_writer_exclusion_inference_allowed freshness_inference_allowed liveness_inference_allowed verifier_trust_inference_allowed
reviewer_identity_inference_allowed physical_human_presence_inference_allowed distinct_reviewer_count_allowed consensus_inference_allowed approval_state_allowed recommendations_allowed
policy_update_allowed live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed persistence_in_core_allowed human_review_only shadow_only
attestation_set_consumption_authority memory_write_authority output_permissions""".split())
P_TRUE=set("require_full_r88_validation require_expected_authority_anchor_digest require_expected_authority_root_digest require_exact_r88_anchor_binding require_retained_reference human_review_only shadow_only".split())
P_FALSE=POLICY_KEYS-P_TRUE-{"schema_version","policy_id","mode","input_r88_binding_schema","authority_anchor_schema","attestation_set_consumption_authority","memory_write_authority","output_permissions"}

ANCHOR_KEYS=set("""schema anchor_scope r88_binding_id r88_binding_sha256 recovery_verification_sha256 writer_lease_sha256 current_receipt_index_sha256 receipt_candidate_sha256
current_fencing_token authority_root_sha256 retained_reference_required root_trust_verified anchor_operator_identity_verified live_writer_backend_proven durable_commit_proven
global_current_state_verified concurrent_writer_exclusion_proven registry_write_performed lease_registry_write_performed receipt_index_write_performed backend_write_performed
execution_authority can_execute apply_allowed confers_authority""".split())
A_FALSE=set("root_trust_verified anchor_operator_identity_verified live_writer_backend_proven durable_commit_proven global_current_state_verified concurrent_writer_exclusion_proven registry_write_performed lease_registry_write_performed receipt_index_write_performed backend_write_performed can_execute apply_allowed confers_authority".split())

BINDING_KEYS=set("""schema binding_id r88_binding_id r88_binding_sha256 r87_binding_id r86_binding_id r85_binding_id r84_binding_id authority_anchor_policy_sha256 authority_anchor_sha256
authority_anchor_digest_consumed authority_root_sha256 authority_root_digest_consumed anchor_scope recovery_verification_sha256 writer_lease_sha256 current_receipt_index_sha256
receipt_candidate_sha256 current_fencing_token retained_reference_required writer_authority_anchor_bound writer_authority_root_verified authority_anchor_operator_identity_verified
live_writer_backend_proven durable_commit_proven durable_single_use_enforced global_current_state_verified concurrent_writer_exclusion_proven registry_write_performed
lease_registry_write_performed receipt_index_write_performed backend_write_performed assertion_freshness_verified liveness_verified verifier_trust_root_verified
review_identity_verified physical_human_presence_proven distinct_reviewer_count_allowed consensus_inference_allowed approval_state_allowed shadow_only human_review_only
attestation_set_consumption_authority memory_write_authority policy_update_allowed live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed
execution_authority can_trade capital_permission confers_authority""".split())

KW=set("""expected_external_assertion_sha256 key_possession_policy expected_verifier_registry_sha256 expected_verifier_authority_root_sha256 expected_writer_authority_root_sha256
provenance_policy expected_replay_registry_sha256 replay_guard_policy expected_atomic_verification_sha256 atomic_cas_policy expected_recovery_verification_sha256
writer_fencing_recovery_policy expected_authority_anchor_sha256 writer_authority_anchor_policy""".split())

def _sha(v,n):
    if not isinstance(v,str) or _SHA.fullmatch(v) is None: raise ValueError(f"{n} must be lowercase sha256")
    return v
def _id(v,n):
    if not isinstance(v,str) or _ID.fullmatch(v) is None: raise ValueError(f"{n} invalid")
    return v
def _counter(v,n):
    if type(v) is not int or not 0<=v<=2147483647: raise ValueError(f"{n} invalid")
    return v
def _exact(v,keys,msg):
    if not isinstance(v,dict) or set(v)!=keys: raise ValueError(msg)
    return v

def validate_writer_authority_anchor_policy(policy):
    p=_exact(policy,POLICY_KEYS,"writer-authority-anchor policy key set mismatch")
    if type(p.get("schema_version")) is not int or p["schema_version"]!=1 or p.get("policy_id")!=POLICY_ID: raise ValueError("unsupported writer-authority-anchor policy")
    if p.get("mode")!="OFFLINE_EXTERNAL_ASSERTION_REPLAY_WRITER_AUTHORITY_ANCHOR_BINDING_ONLY": raise ValueError("writer-authority-anchor policy mode drift")
    if p.get("input_r88_binding_schema")!=r88.BINDING_SCHEMA: raise ValueError("input R88 binding schema drift")
    if p.get("authority_anchor_schema")!=AUTHORITY_ANCHOR_SCHEMA: raise ValueError("authority anchor schema drift")
    for f in P_TRUE:
        if p.get(f) is not True: raise ValueError(f"required writer-authority-anchor guard disabled: {f}")
    for f in P_FALSE:
        if p.get(f) is not False: raise ValueError(f"unsafe writer-authority-anchor policy: {f}")
    if p.get("attestation_set_consumption_authority")!="NONE": raise ValueError("attestation-set consumption authority must remain NONE")
    if p.get("memory_write_authority")!="NONE": raise ValueError("memory write authority must remain NONE")
    o=_exact(p.get("output_permissions"),set(OUTPUT_PERMISSIONS),"unsafe writer-authority-anchor output permissions")
    for k,e in OUTPUT_PERMISSIONS.items():
        if o.get(k)!=e or type(o.get(k)) is not type(e): raise ValueError("unsafe writer-authority-anchor output permissions")

def _anchor(a,r88b,kw):
    a=_exact(a,ANCHOR_KEYS,"writer-authority anchor key set mismatch")
    if a.get("schema")!=AUTHORITY_ANCHOR_SCHEMA: raise ValueError("unsupported writer-authority anchor schema")
    d=stable_sha256(a)
    if d!=_sha(kw["expected_authority_anchor_sha256"],"expected_authority_anchor_sha256"): raise ValueError("writer-authority anchor digest mismatch")
    if a.get("anchor_scope")!="WRITER_LEASE_AND_RECEIPT_INDEX_ONLY": raise ValueError("writer-authority anchor scope invalid")
    e={"r88_binding_id":r88b["binding_id"],"r88_binding_sha256":stable_sha256(r88b),"recovery_verification_sha256":r88b["recovery_verification_sha256"],
       "writer_lease_sha256":r88b["writer_lease_sha256"],"current_receipt_index_sha256":r88b["current_receipt_index_sha256"],
       "receipt_candidate_sha256":r88b["receipt_candidate_sha256"],"current_fencing_token":r88b["current_fencing_token"]}
    for k,v in e.items():
        if a.get(k)!=v or type(a.get(k)) is not type(v): raise ValueError(f"writer-authority anchor R88 mismatch: {k}")
    if _sha(a.get("authority_root_sha256"),"authority_root_sha256")!=_sha(kw["expected_writer_authority_root_sha256"],"expected_writer_authority_root_sha256"):
        raise ValueError("writer-authority root digest mismatch")
    _counter(a.get("current_fencing_token"),"current_fencing_token")
    if a.get("retained_reference_required") is not True: raise ValueError("writer-authority retained-reference guard missing")
    for f in A_FALSE:
        if a.get(f) is not False: raise ValueError(f"writer-authority anchor overclaim: {f}")
    if a.get("execution_authority")!="NONE": raise ValueError("writer-authority anchor execution authority overclaim")
    return d

def _inputs(args,kw):
    if set(kw)!=KW: raise ValueError("writer-authority-anchor keyword set mismatch")
    validate_writer_authority_anchor_policy(kw["writer_authority_anchor_policy"])
    r88b,r87b,r86b,r85b,r84b,es,ei,sp,aid,ea,vrs,rrs,av,rv,a=args
    r88.validate_external_assertion_replay_writer_fencing_recovery_binding(
        r88b,r87b,r86b,r85b,r84b,es,ei,sp,aid,ea,vrs,rrs,av,rv,
        expected_external_assertion_sha256=kw["expected_external_assertion_sha256"],
        key_possession_policy=kw["key_possession_policy"],
        expected_verifier_registry_sha256=kw["expected_verifier_registry_sha256"],
        expected_authority_root_sha256=kw["expected_verifier_authority_root_sha256"],
        provenance_policy=kw["provenance_policy"],
        expected_replay_registry_sha256=kw["expected_replay_registry_sha256"],
        replay_guard_policy=kw["replay_guard_policy"],
        expected_atomic_verification_sha256=kw["expected_atomic_verification_sha256"],
        atomic_cas_policy=kw["atomic_cas_policy"],
        expected_recovery_verification_sha256=kw["expected_recovery_verification_sha256"],
        writer_fencing_recovery_policy=kw["writer_fencing_recovery_policy"])
    return _anchor(a,r88b,kw)

def _payload(args,anchor_sha,policy):
    r88b,r87b,r86b,r85b,r84b,*_,a=args
    x={"schema":BINDING_SCHEMA,"r88_binding_id":r88b["binding_id"],"r88_binding_sha256":stable_sha256(r88b),"r87_binding_id":r87b["binding_id"],
       "r86_binding_id":r86b["binding_id"],"r85_binding_id":r85b["binding_id"],"r84_binding_id":r84b["binding_id"],
       "authority_anchor_policy_sha256":stable_sha256(policy),"authority_anchor_sha256":anchor_sha,"authority_anchor_digest_consumed":True,
       "authority_root_sha256":a["authority_root_sha256"],"authority_root_digest_consumed":True,"anchor_scope":"WRITER_LEASE_AND_RECEIPT_INDEX_ONLY",
       "recovery_verification_sha256":r88b["recovery_verification_sha256"],"writer_lease_sha256":r88b["writer_lease_sha256"],
       "current_receipt_index_sha256":r88b["current_receipt_index_sha256"],"receipt_candidate_sha256":r88b["receipt_candidate_sha256"],
       "current_fencing_token":r88b["current_fencing_token"],"retained_reference_required":True,"writer_authority_anchor_bound":True,
       "writer_authority_root_verified":False,"authority_anchor_operator_identity_verified":False,"live_writer_backend_proven":False,
       "durable_commit_proven":False,"durable_single_use_enforced":False,"global_current_state_verified":False,"concurrent_writer_exclusion_proven":False,
       "registry_write_performed":False,"lease_registry_write_performed":False,"receipt_index_write_performed":False,"backend_write_performed":False,
       "assertion_freshness_verified":False,"liveness_verified":False,"verifier_trust_root_verified":False,"review_identity_verified":False,
       "physical_human_presence_proven":False,"distinct_reviewer_count_allowed":False,"consensus_inference_allowed":False,"approval_state_allowed":False,
       "shadow_only":True,"human_review_only":True,"attestation_set_consumption_authority":"NONE","memory_write_authority":"NONE","policy_update_allowed":False,
       "live_decision_feedback_allowed":False,"live_decision_use_allowed":False,"model_selection_use_allowed":False,"execution_authority":"NONE",
       "can_trade":False,"capital_permission":"DENY","confers_authority":False}
    return x

def _bid(x):
    return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]

def build_external_assertion_replay_writer_authority_anchor_binding(*args,**kw):
    if len(args)!=15: raise ValueError("writer-authority-anchor positional input mismatch")
    ah=_inputs(args,kw); x=_payload(args,ah,kw["writer_authority_anchor_policy"]); x["binding_id"]=_bid(x)
    validate_external_assertion_replay_writer_authority_anchor_binding(x,*args,**kw); return x

def validate_external_assertion_replay_writer_authority_anchor_binding(binding,*args,**kw):
    if len(args)!=15: raise ValueError("writer-authority-anchor positional input mismatch")
    ah=_inputs(args,kw)
    if not isinstance(binding,dict) or set(binding)!=BINDING_KEYS: raise ValueError("writer-authority-anchor binding key set mismatch")
    if binding.get("schema")!=BINDING_SCHEMA: raise ValueError("unsupported writer-authority-anchor binding schema")
    _id(binding.get("binding_id"),"binding_id")
    e=_payload(args,ah,kw["writer_authority_anchor_policy"])
    for k,v in e.items():
        if binding.get(k)!=v or type(binding.get(k)) is not type(v): raise ValueError(f"writer-authority-anchor binding mismatch: {k}")
    if binding["binding_id"]!=_bid(binding): raise ValueError("binding_id binding mismatch")
