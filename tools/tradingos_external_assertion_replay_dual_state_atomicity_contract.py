"""TradingOS R90 dual-state atomicity evidence binding."""
from __future__ import annotations
import hashlib,re
from tools import tradingos_external_assertion_replay_writer_authority_anchor_contract as r89
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes,stable_sha256

BINDING_SCHEMA="tradingos.external_assertion_replay_dual_state_atomicity_binding.v1"
ATOMICITY_VERIFICATION_SCHEMA="control_center.external_assertion_replay_dual_state_atomicity_verification.v1"
POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_DUAL_STATE_ATOMICITY_POLICY_V1"; VERSION="1.0.0"
_ID=re.compile(r"^[0-9a-f]{24}$"); _SHA=re.compile(r"^[0-9a-f]{64}$")
OUTPUT_PERMISSIONS={"execution_authority":"NONE","signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","confers_authority":False}

POLICY_KEYS=set("""schema_version policy_id mode input_r89_binding_schema atomicity_verification_schema require_full_r89_validation
require_expected_atomicity_verification_digest require_exact_r89_atomicity_binding require_dual_state_atomicity_model require_split_state_rejected
require_lease_epoch_lineage_verified require_aba_guard_verified network_access_in_core_allowed credential_access_in_core_allowed registry_write_allowed
lease_registry_write_allowed receipt_index_write_allowed backend_write_allowed durable_dual_state_atomicity_inference_allowed durable_commit_inference_allowed
durable_single_use_inference_allowed global_current_state_inference_allowed concurrent_writer_exclusion_inference_allowed freshness_inference_allowed
liveness_inference_allowed writer_authority_root_trust_inference_allowed verifier_trust_inference_allowed reviewer_identity_inference_allowed
physical_human_presence_inference_allowed distinct_reviewer_count_allowed consensus_inference_allowed approval_state_allowed recommendations_allowed
policy_update_allowed live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed persistence_in_core_allowed human_review_only shadow_only
attestation_set_consumption_authority memory_write_authority output_permissions""".split())
P_TRUE=set("require_full_r89_validation require_expected_atomicity_verification_digest require_exact_r89_atomicity_binding require_dual_state_atomicity_model require_split_state_rejected require_lease_epoch_lineage_verified require_aba_guard_verified human_review_only shadow_only".split())
P_FALSE=POLICY_KEYS-P_TRUE-{"schema_version","policy_id","mode","input_r89_binding_schema","atomicity_verification_schema","attestation_set_consumption_authority","memory_write_authority","output_permissions"}

ATOMICITY_KEYS=set("""schema atomicity_scope r89_binding_id r89_binding_sha256 authority_anchor_sha256 writer_authority_root_sha256 writer_lease_sha256
prior_receipt_index_sha256 next_receipt_index_candidate_sha256 lease_lineage_sha256 commit_id idempotency_key_sha256 observed_pair_state
dual_state_atomicity_model split_state_rejected lease_epoch_lineage_verified aba_guard_verified durability_status write_performed live_backend_observed
durable_commit_proven durable_dual_state_atomicity_proven global_current_state_verified concurrent_writer_exclusion_proven registry_write_performed
lease_registry_write_performed receipt_index_write_performed backend_write_performed execution_authority can_execute apply_allowed confers_authority""".split())
A_FALSE=set("write_performed live_backend_observed durable_commit_proven durable_dual_state_atomicity_proven global_current_state_verified concurrent_writer_exclusion_proven registry_write_performed lease_registry_write_performed receipt_index_write_performed backend_write_performed can_execute apply_allowed confers_authority".split())

BINDING_KEYS=set("""schema binding_id r89_binding_id r89_binding_sha256 r88_binding_id r87_binding_id r86_binding_id r85_binding_id r84_binding_id
atomicity_policy_sha256 atomicity_verification_sha256 atomicity_verification_digest_consumed verifier_authority_root_sha256 verifier_authority_root_digest_consumed
writer_authority_root_sha256 writer_authority_root_digest_consumed authority_anchor_sha256 writer_lease_sha256 prior_receipt_index_sha256 next_receipt_index_candidate_sha256
lease_lineage_sha256 commit_id idempotency_key_sha256 observed_pair_state dual_state_atomicity_model split_state_rejected lease_epoch_lineage_verified aba_guard_verified
dual_state_atomicity_evidence_bound write_performed live_backend_observed durable_dual_state_atomicity_proven durable_commit_proven durable_single_use_enforced
global_current_state_verified concurrent_writer_exclusion_proven registry_write_performed lease_registry_write_performed receipt_index_write_performed backend_write_performed
assertion_freshness_verified liveness_verified writer_authority_root_verified verifier_trust_root_verified review_identity_verified physical_human_presence_proven
distinct_reviewer_count_allowed consensus_inference_allowed approval_state_allowed shadow_only human_review_only attestation_set_consumption_authority memory_write_authority
policy_update_allowed live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed execution_authority can_trade capital_permission confers_authority""".split())
KW=set("""expected_external_assertion_sha256 key_possession_policy expected_verifier_registry_sha256 expected_verifier_authority_root_sha256
expected_writer_authority_root_sha256 provenance_policy expected_replay_registry_sha256 replay_guard_policy expected_atomic_verification_sha256 atomic_cas_policy
expected_recovery_verification_sha256 writer_fencing_recovery_policy expected_authority_anchor_sha256 writer_authority_anchor_policy
expected_atomicity_verification_sha256 dual_state_atomicity_policy""".split())

def _sha(v,n):
    if not isinstance(v,str) or _SHA.fullmatch(v) is None: raise ValueError(f"{n} must be lowercase sha256")
    return v
def _id(v,n):
    if not isinstance(v,str) or _ID.fullmatch(v) is None: raise ValueError(f"{n} invalid")
    return v
def _token(v,n):
    if not isinstance(v,str) or v!=v.strip() or not 1<=len(v)<=128 or any(ord(c)<33 or ord(c)>126 for c in v): raise ValueError(f"{n} invalid")
    return v
def _exact(v,keys,msg):
    if not isinstance(v,dict) or set(v)!=keys: raise ValueError(msg)
    return v

def validate_dual_state_atomicity_policy(policy):
    p=_exact(policy,POLICY_KEYS,"dual-state atomicity policy key set mismatch")
    if type(p.get("schema_version")) is not int or p["schema_version"]!=1 or p.get("policy_id")!=POLICY_ID: raise ValueError("unsupported dual-state atomicity policy")
    if p.get("mode")!="OFFLINE_EXTERNAL_ASSERTION_REPLAY_DUAL_STATE_ATOMICITY_BINDING_ONLY": raise ValueError("dual-state atomicity policy mode drift")
    if p.get("input_r89_binding_schema")!=r89.BINDING_SCHEMA: raise ValueError("input R89 binding schema drift")
    if p.get("atomicity_verification_schema")!=ATOMICITY_VERIFICATION_SCHEMA: raise ValueError("atomicity verification schema drift")
    for f in P_TRUE:
        if p.get(f) is not True: raise ValueError(f"required dual-state atomicity guard disabled: {f}")
    for f in P_FALSE:
        if p.get(f) is not False: raise ValueError(f"unsafe dual-state atomicity policy: {f}")
    if p.get("attestation_set_consumption_authority")!="NONE": raise ValueError("attestation-set consumption authority must remain NONE")
    if p.get("memory_write_authority")!="NONE": raise ValueError("memory write authority must remain NONE")
    o=_exact(p.get("output_permissions"),set(OUTPUT_PERMISSIONS),"unsafe dual-state atomicity output permissions")
    for k,e in OUTPUT_PERMISSIONS.items():
        if o.get(k)!=e or type(o.get(k)) is not type(e): raise ValueError("unsafe dual-state atomicity output permissions")

def _atomicity(a,r89b,kw):
    a=_exact(a,ATOMICITY_KEYS,"dual-state atomicity verification key set mismatch")
    if a.get("schema")!=ATOMICITY_VERIFICATION_SCHEMA: raise ValueError("unsupported dual-state atomicity verification schema")
    d=stable_sha256(a)
    if d!=_sha(kw["expected_atomicity_verification_sha256"],"expected_atomicity_verification_sha256"): raise ValueError("dual-state atomicity verification digest mismatch")
    if a.get("atomicity_scope")!="WRITER_LEASE_AND_RECEIPT_INDEX_DUAL_STATE_ONLY": raise ValueError("dual-state atomicity scope invalid")
    e={"r89_binding_id":r89b["binding_id"],"r89_binding_sha256":stable_sha256(r89b),"authority_anchor_sha256":r89b["authority_anchor_sha256"],
       "writer_authority_root_sha256":r89b["authority_root_sha256"],"writer_lease_sha256":r89b["writer_lease_sha256"],"prior_receipt_index_sha256":r89b["current_receipt_index_sha256"]}
    for k,v in e.items():
        if a.get(k)!=v or type(a.get(k)) is not type(v): raise ValueError(f"dual-state atomicity R89 mismatch: {k}")
    _sha(a.get("next_receipt_index_candidate_sha256"),"next_receipt_index_candidate_sha256"); _sha(a.get("lease_lineage_sha256"),"lease_lineage_sha256")
    _token(a.get("commit_id"),"commit_id"); _sha(a.get("idempotency_key_sha256"),"idempotency_key_sha256")
    if a.get("observed_pair_state")!="PROTOCOL_CANDIDATE_ONLY_NO_DURABLE_BACKEND": raise ValueError("dual-state atomicity observed pair state invalid")
    if a.get("dual_state_atomicity_model")!="ONE_TRANSACTION_TWO_LOGICAL_RECORDS": raise ValueError("dual-state atomicity model invalid")
    for f,msg in (("split_state_rejected","dual-state split-state guard missing"),("lease_epoch_lineage_verified","dual-state lease lineage guard missing"),("aba_guard_verified","dual-state ABA guard missing")):
        if a.get(f) is not True: raise ValueError(msg)
    if a.get("durability_status")!="PROTOCOL_VERIFIED_NO_DURABLE_BACKEND": raise ValueError("dual-state durability status invalid")
    for f in A_FALSE:
        if a.get(f) is not False: raise ValueError(f"dual-state atomicity overclaim: {f}")
    if a.get("execution_authority")!="NONE": raise ValueError("dual-state atomicity execution authority overclaim")
    return d

def _inputs(args,kw):
    if len(args)!=17: raise ValueError("dual-state atomicity positional input mismatch")
    if set(kw)!=KW: raise ValueError("dual-state atomicity keyword set mismatch")
    validate_dual_state_atomicity_policy(kw["dual_state_atomicity_policy"])
    r89b,r88b,r87b,r86b,r85b,r84b,es,ei,sp,aid,ea,vrs,rrs,av,rv,aa,da=args
    r89.validate_external_assertion_replay_writer_authority_anchor_binding(
        r89b,r88b,r87b,r86b,r85b,r84b,es,ei,sp,aid,ea,vrs,rrs,av,rv,aa,
        expected_external_assertion_sha256=kw["expected_external_assertion_sha256"],key_possession_policy=kw["key_possession_policy"],
        expected_verifier_registry_sha256=kw["expected_verifier_registry_sha256"],expected_verifier_authority_root_sha256=kw["expected_verifier_authority_root_sha256"],
        expected_writer_authority_root_sha256=kw["expected_writer_authority_root_sha256"],provenance_policy=kw["provenance_policy"],
        expected_replay_registry_sha256=kw["expected_replay_registry_sha256"],replay_guard_policy=kw["replay_guard_policy"],
        expected_atomic_verification_sha256=kw["expected_atomic_verification_sha256"],atomic_cas_policy=kw["atomic_cas_policy"],
        expected_recovery_verification_sha256=kw["expected_recovery_verification_sha256"],writer_fencing_recovery_policy=kw["writer_fencing_recovery_policy"],
        expected_authority_anchor_sha256=kw["expected_authority_anchor_sha256"],writer_authority_anchor_policy=kw["writer_authority_anchor_policy"])
    return _atomicity(da,r89b,kw)

def _payload(args,atomicity_sha,kw):
    r89b,r88b,r87b,r86b,r85b,r84b,*_,da=args
    return {"schema":BINDING_SCHEMA,"r89_binding_id":r89b["binding_id"],"r89_binding_sha256":stable_sha256(r89b),"r88_binding_id":r88b["binding_id"],
    "r87_binding_id":r87b["binding_id"],"r86_binding_id":r86b["binding_id"],"r85_binding_id":r85b["binding_id"],"r84_binding_id":r84b["binding_id"],
    "atomicity_policy_sha256":stable_sha256(kw["dual_state_atomicity_policy"]),"atomicity_verification_sha256":atomicity_sha,"atomicity_verification_digest_consumed":True,
    "verifier_authority_root_sha256":_sha(kw["expected_verifier_authority_root_sha256"],"verifier_authority_root_sha256"),"verifier_authority_root_digest_consumed":True,
    "writer_authority_root_sha256":_sha(kw["expected_writer_authority_root_sha256"],"writer_authority_root_sha256"),"writer_authority_root_digest_consumed":True,
    "authority_anchor_sha256":r89b["authority_anchor_sha256"],"writer_lease_sha256":r89b["writer_lease_sha256"],"prior_receipt_index_sha256":r89b["current_receipt_index_sha256"],
    "next_receipt_index_candidate_sha256":da["next_receipt_index_candidate_sha256"],"lease_lineage_sha256":da["lease_lineage_sha256"],"commit_id":da["commit_id"],
    "idempotency_key_sha256":da["idempotency_key_sha256"],"observed_pair_state":"PROTOCOL_CANDIDATE_ONLY_NO_DURABLE_BACKEND","dual_state_atomicity_model":"ONE_TRANSACTION_TWO_LOGICAL_RECORDS",
    "split_state_rejected":True,"lease_epoch_lineage_verified":True,"aba_guard_verified":True,"dual_state_atomicity_evidence_bound":True,
    "write_performed":False,"live_backend_observed":False,"durable_dual_state_atomicity_proven":False,"durable_commit_proven":False,"durable_single_use_enforced":False,
    "global_current_state_verified":False,"concurrent_writer_exclusion_proven":False,"registry_write_performed":False,"lease_registry_write_performed":False,
    "receipt_index_write_performed":False,"backend_write_performed":False,"assertion_freshness_verified":False,"liveness_verified":False,
    "writer_authority_root_verified":False,"verifier_trust_root_verified":False,"review_identity_verified":False,"physical_human_presence_proven":False,
    "distinct_reviewer_count_allowed":False,"consensus_inference_allowed":False,"approval_state_allowed":False,"shadow_only":True,"human_review_only":True,
    "attestation_set_consumption_authority":"NONE","memory_write_authority":"NONE","policy_update_allowed":False,"live_decision_feedback_allowed":False,
    "live_decision_use_allowed":False,"model_selection_use_allowed":False,"execution_authority":"NONE","can_trade":False,"capital_permission":"DENY","confers_authority":False}

def _bid(x):
    return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]

def build_external_assertion_replay_dual_state_atomicity_binding(*args,**kw):
    ah=_inputs(args,kw); x=_payload(args,ah,kw); x["binding_id"]=_bid(x); validate_external_assertion_replay_dual_state_atomicity_binding(x,*args,**kw); return x

def validate_external_assertion_replay_dual_state_atomicity_binding(binding,*args,**kw):
    ah=_inputs(args,kw)
    if not isinstance(binding,dict) or set(binding)!=BINDING_KEYS: raise ValueError("dual-state atomicity binding key set mismatch")
    if binding.get("schema")!=BINDING_SCHEMA: raise ValueError("unsupported dual-state atomicity binding schema")
    _id(binding.get("binding_id"),"binding_id"); e=_payload(args,ah,kw)
    for k,v in e.items():
        if binding.get(k)!=v or type(binding.get(k)) is not type(v): raise ValueError(f"dual-state atomicity binding mismatch: {k}")
    if binding["binding_id"]!=_bid(binding): raise ValueError("binding_id binding mismatch")
