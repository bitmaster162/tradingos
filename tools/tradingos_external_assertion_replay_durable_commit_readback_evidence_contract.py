"""TradingOS R91 retained commit/readback evidence binding."""
from __future__ import annotations
import hashlib, re
from tools import tradingos_external_assertion_replay_dual_state_atomicity_contract as r90
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes, stable_sha256

BINDING_SCHEMA="tradingos.external_assertion_replay_durable_commit_readback_evidence_binding.v1"
EVIDENCE_SCHEMA="control_center.external_assertion_replay_commit_readback_evidence.v1"
POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_DURABLE_COMMIT_READBACK_EVIDENCE_POLICY_V1"
VERSION="1.0.0"
_ID=re.compile(r"^[0-9a-f]{24}$"); _SHA=re.compile(r"^[0-9a-f]{64}$")
OUTPUT_PERMISSIONS={"execution_authority":"NONE","signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","confers_authority":False}

POLICY_KEYS=set("""schema_version policy_id mode input_r90_binding_schema commit_readback_evidence_schema
require_full_r90_validation require_expected_commit_readback_evidence_digest require_exact_r90_transition_binding
require_commit_receipt_index_matches_r90_next_candidate require_readback_index_matches_r90_next_candidate require_receipt_identity_bound
require_read_after_write_match require_retained_commit_receipt require_retained_readback network_access_in_core_allowed
credential_access_in_core_allowed registry_write_allowed lease_registry_write_allowed receipt_index_write_allowed backend_write_allowed
backend_commit_authenticity_inference_allowed backend_identity_inference_allowed live_backend_observation_inference_allowed durable_commit_inference_allowed
durable_dual_state_atomicity_inference_allowed global_current_state_inference_allowed concurrent_writer_exclusion_inference_allowed
freshness_inference_allowed liveness_inference_allowed writer_authority_root_trust_inference_allowed verifier_trust_inference_allowed
reviewer_identity_inference_allowed consensus_inference_allowed approval_state_allowed recommendations_allowed policy_update_allowed
live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed persistence_in_core_allowed human_review_only shadow_only
attestation_set_consumption_authority memory_write_authority output_permissions""".split())
P_TRUE=set("""require_full_r90_validation require_expected_commit_readback_evidence_digest require_exact_r90_transition_binding
require_commit_receipt_index_matches_r90_next_candidate require_readback_index_matches_r90_next_candidate require_receipt_identity_bound
require_read_after_write_match require_retained_commit_receipt require_retained_readback human_review_only shadow_only""".split())
P_FALSE=POLICY_KEYS-P_TRUE-{"schema_version","policy_id","mode","input_r90_binding_schema","commit_readback_evidence_schema",
"attestation_set_consumption_authority","memory_write_authority","output_permissions"}

EVIDENCE_KEYS=set("""schema evidence_scope r90_binding_id r90_binding_sha256 authority_anchor_sha256 writer_authority_root_sha256
writer_lease_sha256 prior_receipt_index_sha256 commit_receipt_index_sha256 readback_receipt_index_sha256 lease_lineage_sha256
commit_id idempotency_key_sha256 external_commit_receipt_sha256 readback_state_sha256 readback_evidence_sha256
receipt_identity_bound read_after_write_match commit_receipt_retained readback_retained backend_commit_authenticity_verified
backend_identity_verified live_backend_observed durable_commit_proven durable_dual_state_atomicity_proven write_performed
global_current_state_verified concurrent_writer_exclusion_proven registry_write_performed lease_registry_write_performed
receipt_index_write_performed backend_write_performed execution_authority can_execute apply_allowed confers_authority""".split())
E_FALSE=set("""backend_commit_authenticity_verified backend_identity_verified live_backend_observed durable_commit_proven
durable_dual_state_atomicity_proven write_performed global_current_state_verified concurrent_writer_exclusion_proven
registry_write_performed lease_registry_write_performed receipt_index_write_performed backend_write_performed can_execute apply_allowed confers_authority""".split())

BINDING_KEYS=set("""schema binding_id r90_binding_id r90_binding_sha256 r89_binding_id r88_binding_id r87_binding_id r86_binding_id r85_binding_id r84_binding_id
commit_readback_policy_sha256 commit_readback_evidence_sha256 commit_readback_evidence_digest_consumed verifier_authority_root_sha256
verifier_authority_root_digest_consumed writer_authority_root_sha256 writer_authority_root_digest_consumed authority_anchor_sha256 writer_lease_sha256
prior_receipt_index_sha256 commit_receipt_index_sha256 readback_receipt_index_sha256 lease_lineage_sha256 commit_id idempotency_key_sha256
external_commit_receipt_sha256 readback_state_sha256 readback_evidence_sha256 external_commit_receipt_evidence_bound read_after_write_evidence_bound
receipt_identity_bound read_after_write_match commit_receipt_retained readback_retained backend_commit_authenticity_verified backend_identity_verified
live_backend_observed durable_commit_proven durable_dual_state_atomicity_proven durable_single_use_enforced write_performed global_current_state_verified
concurrent_writer_exclusion_proven registry_write_performed lease_registry_write_performed receipt_index_write_performed backend_write_performed
assertion_freshness_verified liveness_verified writer_authority_root_verified verifier_trust_root_verified review_identity_verified
distinct_reviewer_count_allowed consensus_inference_allowed approval_state_allowed shadow_only human_review_only attestation_set_consumption_authority
memory_write_authority policy_update_allowed live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed execution_authority
can_trade capital_permission confers_authority""".split())

KW=set("""expected_external_assertion_sha256 key_possession_policy expected_verifier_registry_sha256 expected_verifier_authority_root_sha256
expected_writer_authority_root_sha256 provenance_policy expected_replay_registry_sha256 replay_guard_policy expected_atomic_verification_sha256 atomic_cas_policy
expected_recovery_verification_sha256 writer_fencing_recovery_policy expected_authority_anchor_sha256 writer_authority_anchor_policy
expected_atomicity_verification_sha256 dual_state_atomicity_policy expected_commit_readback_evidence_sha256 commit_readback_evidence_policy""".split())

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

def validate_commit_readback_evidence_policy(policy):
    p=_exact(policy,POLICY_KEYS,"commit/readback policy key set mismatch")
    if type(p.get("schema_version")) is not int or p["schema_version"]!=1 or p.get("policy_id")!=POLICY_ID:
        raise ValueError("unsupported commit/readback evidence policy")
    if p.get("mode")!="OFFLINE_EXTERNAL_ASSERTION_REPLAY_DURABLE_COMMIT_READBACK_EVIDENCE_BINDING_ONLY":
        raise ValueError("commit/readback evidence policy mode drift")
    if p.get("input_r90_binding_schema")!=r90.BINDING_SCHEMA: raise ValueError("input R90 binding schema drift")
    if p.get("commit_readback_evidence_schema")!=EVIDENCE_SCHEMA: raise ValueError("commit/readback evidence schema drift")
    for f in P_TRUE:
        if p.get(f) is not True: raise ValueError(f"required commit/readback evidence guard disabled: {f}")
    for f in P_FALSE:
        if p.get(f) is not False: raise ValueError(f"unsafe commit/readback evidence policy: {f}")
    if p.get("attestation_set_consumption_authority")!="NONE": raise ValueError("attestation-set consumption authority must remain NONE")
    if p.get("memory_write_authority")!="NONE": raise ValueError("memory write authority must remain NONE")
    o=_exact(p.get("output_permissions"),set(OUTPUT_PERMISSIONS),"unsafe commit/readback output permissions")
    for k,e in OUTPUT_PERMISSIONS.items():
        if o.get(k)!=e or type(o.get(k)) is not type(e): raise ValueError("unsafe commit/readback output permissions")

def _evidence(e,r90b,kw):
    e=_exact(e,EVIDENCE_KEYS,"commit/readback evidence key set mismatch")
    if e.get("schema")!=EVIDENCE_SCHEMA: raise ValueError("unsupported commit/readback evidence schema")
    d=stable_sha256(e)
    if d!=_sha(kw["expected_commit_readback_evidence_sha256"],"expected_commit_readback_evidence_sha256"):
        raise ValueError("commit/readback evidence digest mismatch")
    if e.get("evidence_scope")!="R90_RECEIPT_INDEX_COMMIT_AND_READBACK_ONLY":
        raise ValueError("commit/readback evidence scope invalid")
    expected={
        "r90_binding_id":r90b["binding_id"],
        "r90_binding_sha256":stable_sha256(r90b),
        "authority_anchor_sha256":r90b["authority_anchor_sha256"],
        "writer_authority_root_sha256":r90b["writer_authority_root_sha256"],
        "writer_lease_sha256":r90b["writer_lease_sha256"],
        "prior_receipt_index_sha256":r90b["prior_receipt_index_sha256"],
        "commit_receipt_index_sha256":r90b["next_receipt_index_candidate_sha256"],
        "readback_receipt_index_sha256":r90b["next_receipt_index_candidate_sha256"],
        "lease_lineage_sha256":r90b["lease_lineage_sha256"],
        "commit_id":r90b["commit_id"],
        "idempotency_key_sha256":r90b["idempotency_key_sha256"],
    }
    for k,v in expected.items():
        if e.get(k)!=v or type(e.get(k)) is not type(v): raise ValueError(f"commit/readback R90 mismatch: {k}")
    _sha(e.get("external_commit_receipt_sha256"),"external_commit_receipt_sha256")
    _sha(e.get("readback_state_sha256"),"readback_state_sha256")
    _sha(e.get("readback_evidence_sha256"),"readback_evidence_sha256")
    _token(e.get("commit_id"),"commit_id")
    for f in ("receipt_identity_bound","read_after_write_match","commit_receipt_retained","readback_retained"):
        if e.get(f) is not True: raise ValueError(f"required commit/readback evidence guard missing: {f}")
    for f in E_FALSE:
        if e.get(f) is not False: raise ValueError(f"commit/readback evidence overclaim: {f}")
    if e.get("execution_authority")!="NONE": raise ValueError("commit/readback execution authority overclaim")
    return d

def _inputs(args,kw):
    if len(args)!=19: raise ValueError("commit/readback positional input mismatch")
    if set(kw)!=KW: raise ValueError("commit/readback keyword set mismatch")
    validate_commit_readback_evidence_policy(kw["commit_readback_evidence_policy"])
    r90b,r89b,r88b,r87b,r86b,r85b,r84b,es,ei,sp,aid,ea,vrs,rrs,av,rv,aa,da,cr=args
    r90.validate_external_assertion_replay_dual_state_atomicity_binding(
        r90b,r89b,r88b,r87b,r86b,r85b,r84b,es,ei,sp,aid,ea,vrs,rrs,av,rv,aa,da,
        expected_external_assertion_sha256=kw["expected_external_assertion_sha256"],
        key_possession_policy=kw["key_possession_policy"],
        expected_verifier_registry_sha256=kw["expected_verifier_registry_sha256"],
        expected_verifier_authority_root_sha256=kw["expected_verifier_authority_root_sha256"],
        expected_writer_authority_root_sha256=kw["expected_writer_authority_root_sha256"],
        provenance_policy=kw["provenance_policy"],
        expected_replay_registry_sha256=kw["expected_replay_registry_sha256"],
        replay_guard_policy=kw["replay_guard_policy"],
        expected_atomic_verification_sha256=kw["expected_atomic_verification_sha256"],
        atomic_cas_policy=kw["atomic_cas_policy"],
        expected_recovery_verification_sha256=kw["expected_recovery_verification_sha256"],
        writer_fencing_recovery_policy=kw["writer_fencing_recovery_policy"],
        expected_authority_anchor_sha256=kw["expected_authority_anchor_sha256"],
        writer_authority_anchor_policy=kw["writer_authority_anchor_policy"],
        expected_atomicity_verification_sha256=kw["expected_atomicity_verification_sha256"],
        dual_state_atomicity_policy=kw["dual_state_atomicity_policy"])
    return _evidence(cr,r90b,kw)

def _payload(args,evidence_sha,kw):
    r90b,r89b,r88b,r87b,r86b,r85b,r84b,*_,cr=args
    return {
        "schema":BINDING_SCHEMA,
        "r90_binding_id":r90b["binding_id"],"r90_binding_sha256":stable_sha256(r90b),
        "r89_binding_id":r89b["binding_id"],"r88_binding_id":r88b["binding_id"],"r87_binding_id":r87b["binding_id"],
        "r86_binding_id":r86b["binding_id"],"r85_binding_id":r85b["binding_id"],"r84_binding_id":r84b["binding_id"],
        "commit_readback_policy_sha256":stable_sha256(kw["commit_readback_evidence_policy"]),
        "commit_readback_evidence_sha256":evidence_sha,"commit_readback_evidence_digest_consumed":True,
        "verifier_authority_root_sha256":_sha(kw["expected_verifier_authority_root_sha256"],"verifier_authority_root_sha256"),
        "verifier_authority_root_digest_consumed":True,
        "writer_authority_root_sha256":_sha(kw["expected_writer_authority_root_sha256"],"writer_authority_root_sha256"),
        "writer_authority_root_digest_consumed":True,
        "authority_anchor_sha256":r90b["authority_anchor_sha256"],"writer_lease_sha256":r90b["writer_lease_sha256"],
        "prior_receipt_index_sha256":r90b["prior_receipt_index_sha256"],
        "commit_receipt_index_sha256":r90b["next_receipt_index_candidate_sha256"],
        "readback_receipt_index_sha256":r90b["next_receipt_index_candidate_sha256"],
        "lease_lineage_sha256":r90b["lease_lineage_sha256"],"commit_id":r90b["commit_id"],"idempotency_key_sha256":r90b["idempotency_key_sha256"],
        "external_commit_receipt_sha256":cr["external_commit_receipt_sha256"],"readback_state_sha256":cr["readback_state_sha256"],
        "readback_evidence_sha256":cr["readback_evidence_sha256"],"external_commit_receipt_evidence_bound":True,
        "read_after_write_evidence_bound":True,"receipt_identity_bound":True,"read_after_write_match":True,
        "commit_receipt_retained":True,"readback_retained":True,"backend_commit_authenticity_verified":False,
        "backend_identity_verified":False,"live_backend_observed":False,"durable_commit_proven":False,
        "durable_dual_state_atomicity_proven":False,"durable_single_use_enforced":False,"write_performed":False,
        "global_current_state_verified":False,"concurrent_writer_exclusion_proven":False,"registry_write_performed":False,
        "lease_registry_write_performed":False,"receipt_index_write_performed":False,"backend_write_performed":False,
        "assertion_freshness_verified":False,"liveness_verified":False,"writer_authority_root_verified":False,
        "verifier_trust_root_verified":False,"review_identity_verified":False,"distinct_reviewer_count_allowed":False,
        "consensus_inference_allowed":False,"approval_state_allowed":False,"shadow_only":True,"human_review_only":True,
        "attestation_set_consumption_authority":"NONE","memory_write_authority":"NONE","policy_update_allowed":False,
        "live_decision_feedback_allowed":False,"live_decision_use_allowed":False,"model_selection_use_allowed":False,
        "execution_authority":"NONE","can_trade":False,"capital_permission":"DENY","confers_authority":False,
    }

def _bid(x):
    return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]

def build_external_assertion_replay_durable_commit_readback_evidence_binding(*args,**kw):
    eh=_inputs(args,kw); x=_payload(args,eh,kw); x["binding_id"]=_bid(x)
    validate_external_assertion_replay_durable_commit_readback_evidence_binding(x,*args,**kw); return x

def validate_external_assertion_replay_durable_commit_readback_evidence_binding(binding,*args,**kw):
    eh=_inputs(args,kw)
    if not isinstance(binding,dict) or set(binding)!=BINDING_KEYS: raise ValueError("commit/readback binding key set mismatch")
    if binding.get("schema")!=BINDING_SCHEMA: raise ValueError("unsupported commit/readback binding schema")
    _id(binding.get("binding_id"),"binding_id"); expected=_payload(args,eh,kw)
    for k,v in expected.items():
        if binding.get(k)!=v or type(binding.get(k)) is not type(v): raise ValueError(f"commit/readback binding mismatch: {k}")
    if binding["binding_id"]!=_bid(binding): raise ValueError("binding_id binding mismatch")
