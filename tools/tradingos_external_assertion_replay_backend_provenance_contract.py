"TradingOS R92 external assertion replay backend provenance binding."
from __future__ import annotations
import hashlib,re
from tools import tradingos_external_assertion_replay_durable_commit_readback_evidence_contract as r91
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes,stable_sha256

BINDING_SCHEMA="tradingos.external_assertion_replay_backend_provenance_binding.v1"
BACKEND_REGISTRY_SCHEMA="control_center.external_assertion_replay_backend_registry_snapshot.v1"
PROVENANCE_SCHEMA="control_center.external_assertion_replay_backend_provenance_verification.v1"
POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_PROVENANCE_POLICY_V1"; VERSION="1.0.0"
_ID=re.compile(r"^[0-9a-f]{24}$"); _SHA=re.compile(r"^[0-9a-f]{64}$")
OUTPUT_PERMISSIONS={"execution_authority":"NONE","signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","confers_authority":False}

POLICY_KEYS=set("""schema_version policy_id mode input_r91_binding_schema backend_registry_schema backend_provenance_verification_schema
require_full_r91_validation require_expected_backend_registry_digest require_expected_backend_authority_root_digest
require_expected_backend_provenance_verification_digest require_exact_r91_evidence_binding require_unique_backend_metadata_match
require_same_backend_metadata_claim require_commit_receipt_backend_metadata_binding require_readback_backend_metadata_binding
network_access_in_core_allowed credential_access_in_core_allowed backend_registry_write_allowed backend_write_allowed
backend_authenticity_inference_allowed readback_authenticity_inference_allowed backend_identity_inference_allowed backend_trust_root_inference_allowed
backend_registry_operator_identity_inference_allowed live_backend_observation_inference_allowed durable_commit_inference_allowed
durable_dual_state_atomicity_inference_allowed durable_single_use_inference_allowed global_current_state_inference_allowed
concurrent_writer_exclusion_inference_allowed freshness_inference_allowed liveness_inference_allowed writer_authority_root_trust_inference_allowed
verifier_trust_inference_allowed reviewer_identity_inference_allowed consensus_inference_allowed approval_state_allowed recommendations_allowed
policy_update_allowed live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed persistence_in_core_allowed
human_review_only shadow_only attestation_set_consumption_authority memory_write_authority output_permissions""".split())
P_TRUE=set("""require_full_r91_validation require_expected_backend_registry_digest require_expected_backend_authority_root_digest
require_expected_backend_provenance_verification_digest require_exact_r91_evidence_binding require_unique_backend_metadata_match
require_same_backend_metadata_claim require_commit_receipt_backend_metadata_binding require_readback_backend_metadata_binding
human_review_only shadow_only""".split())
P_FALSE=POLICY_KEYS-P_TRUE-{"schema_version","policy_id","mode","input_r91_binding_schema","backend_registry_schema",
"backend_provenance_verification_schema","attestation_set_consumption_authority","memory_write_authority","output_permissions"}

REGISTRY_KEYS=set("schema registry_scope backend_authority_root_sha256 entries backend_trust_root_verified backend_registry_operator_identity_verified backend_registry_write_performed confers_authority".split())
ENTRY_KEYS=set("backend_id backend_key_id backend_metadata_sha256 backend_kind receipt_format readback_format".split())
PROVENANCE_KEYS=set("""schema provenance_scope r91_binding_id r91_binding_sha256 backend_registry_sha256 backend_authority_root_sha256 selected_backend_entry_sha256
external_commit_receipt_sha256 readback_evidence_sha256 readback_state_sha256 backend_id backend_key_id backend_metadata_sha256 backend_kind receipt_format readback_format
same_backend_metadata_claim_bound commit_receipt_backend_metadata_bound readback_backend_metadata_bound backend_provenance_match
backend_commit_authenticity_verified readback_authenticity_verified backend_identity_verified backend_trust_root_verified
backend_registry_operator_identity_verified live_backend_observed durable_commit_proven durable_dual_state_atomicity_proven write_performed
global_current_state_verified concurrent_writer_exclusion_proven execution_authority can_execute apply_allowed confers_authority""".split())
PR_FALSE=set("""backend_commit_authenticity_verified readback_authenticity_verified backend_identity_verified backend_trust_root_verified
backend_registry_operator_identity_verified live_backend_observed durable_commit_proven durable_dual_state_atomicity_proven write_performed
global_current_state_verified concurrent_writer_exclusion_proven can_execute apply_allowed confers_authority""".split())

BINDING_KEYS=set("""schema binding_id r91_binding_id r91_binding_sha256 r90_binding_id r89_binding_id r88_binding_id r87_binding_id r86_binding_id r85_binding_id r84_binding_id
backend_provenance_policy_sha256 backend_registry_sha256 backend_registry_digest_consumed backend_authority_root_sha256 backend_authority_root_digest_consumed
backend_provenance_verification_sha256 backend_provenance_verification_digest_consumed selected_backend_entry_sha256
verifier_authority_root_sha256 verifier_authority_root_digest_consumed writer_authority_root_sha256 writer_authority_root_digest_consumed
backend_id backend_key_id backend_metadata_sha256 backend_kind receipt_format readback_format external_commit_receipt_sha256 readback_evidence_sha256 readback_state_sha256
commit_id idempotency_key_sha256 backend_provenance_bound commit_receipt_backend_metadata_bound readback_backend_metadata_bound same_backend_metadata_claim_bound
backend_provenance_match backend_commit_authenticity_verified readback_authenticity_verified backend_identity_verified backend_trust_root_verified
backend_registry_operator_identity_verified live_backend_observed durable_commit_proven durable_dual_state_atomicity_proven durable_single_use_enforced write_performed
global_current_state_verified concurrent_writer_exclusion_proven registry_write_performed lease_registry_write_performed receipt_index_write_performed backend_write_performed
assertion_freshness_verified liveness_verified writer_authority_root_verified verifier_trust_root_verified review_identity_verified distinct_reviewer_count_allowed
consensus_inference_allowed approval_state_allowed shadow_only human_review_only attestation_set_consumption_authority memory_write_authority policy_update_allowed
live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed execution_authority can_trade capital_permission confers_authority""".split())

UPSTREAM_KW=set("""expected_external_assertion_sha256 key_possession_policy expected_verifier_registry_sha256 expected_verifier_authority_root_sha256
expected_writer_authority_root_sha256 provenance_policy expected_replay_registry_sha256 replay_guard_policy expected_atomic_verification_sha256 atomic_cas_policy
expected_recovery_verification_sha256 writer_fencing_recovery_policy expected_authority_anchor_sha256 writer_authority_anchor_policy
expected_atomicity_verification_sha256 dual_state_atomicity_policy expected_commit_readback_evidence_sha256 commit_readback_evidence_policy""".split())
KW=UPSTREAM_KW|{"expected_backend_registry_sha256","expected_backend_authority_root_sha256","expected_backend_provenance_verification_sha256","backend_provenance_policy"}

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

def validate_backend_provenance_policy(policy):
    p=_exact(policy,POLICY_KEYS,"backend provenance policy key set mismatch")
    if type(p.get("schema_version")) is not int or p["schema_version"]!=1 or p.get("policy_id")!=POLICY_ID:
        raise ValueError("unsupported backend provenance policy")
    if p.get("mode")!="OFFLINE_EXTERNAL_ASSERTION_REPLAY_BACKEND_PROVENANCE_BINDING_ONLY":
        raise ValueError("backend provenance policy mode drift")
    if p.get("input_r91_binding_schema")!=r91.BINDING_SCHEMA: raise ValueError("input R91 binding schema drift")
    if p.get("backend_registry_schema")!=BACKEND_REGISTRY_SCHEMA: raise ValueError("backend registry schema drift")
    if p.get("backend_provenance_verification_schema")!=PROVENANCE_SCHEMA: raise ValueError("backend provenance verification schema drift")
    for f in P_TRUE:
        if p.get(f) is not True: raise ValueError(f"required backend provenance guard disabled: {f}")
    for f in P_FALSE:
        if p.get(f) is not False: raise ValueError(f"unsafe backend provenance policy: {f}")
    if p.get("attestation_set_consumption_authority")!="NONE": raise ValueError("attestation-set consumption authority must remain NONE")
    if p.get("memory_write_authority")!="NONE": raise ValueError("memory write authority must remain NONE")
    o=_exact(p.get("output_permissions"),set(OUTPUT_PERMISSIONS),"unsafe backend provenance output permissions")
    for k,e in OUTPUT_PERMISSIONS.items():
        if o.get(k)!=e or type(o.get(k)) is not type(e): raise ValueError("unsafe backend provenance output permissions")

def _entry(v):
    e=_exact(v,ENTRY_KEYS,"backend registry entry key set mismatch")
    return {
        "backend_id":_token(e["backend_id"],"backend_id"),
        "backend_key_id":_token(e["backend_key_id"],"backend_key_id"),
        "backend_metadata_sha256":_sha(e["backend_metadata_sha256"],"backend_metadata_sha256"),
        "backend_kind":_token(e["backend_kind"],"backend_kind"),
        "receipt_format":_token(e["receipt_format"],"receipt_format"),
        "readback_format":_token(e["readback_format"],"readback_format"),
    }

def _registry(reg,kw):
    r=_exact(reg,REGISTRY_KEYS,"backend registry key set mismatch")
    if r.get("schema")!=BACKEND_REGISTRY_SCHEMA: raise ValueError("unsupported backend registry schema")
    if r.get("registry_scope")!="COMMIT_READBACK_BACKEND_METADATA_PROVENANCE_ONLY": raise ValueError("backend registry scope invalid")
    expected_root=_sha(kw["expected_backend_authority_root_sha256"],"expected_backend_authority_root_sha256")
    if r.get("backend_authority_root_sha256")!=expected_root: raise ValueError("backend authority root digest mismatch")
    if r.get("backend_trust_root_verified") is not False: raise ValueError("backend trust-root overclaim")
    if r.get("backend_registry_operator_identity_verified") is not False: raise ValueError("backend registry operator overclaim")
    if r.get("backend_registry_write_performed") is not False: raise ValueError("backend registry write overclaim")
    if r.get("confers_authority") is not False: raise ValueError("backend registry authority overclaim")
    entries=r.get("entries")
    if not isinstance(entries,list) or not 1<=len(entries)<=256: raise ValueError("backend registry entries invalid")
    norm=[_entry(x) for x in entries]
    if norm!=sorted(norm,key=lambda x:(x["backend_id"],x["backend_key_id"],x["backend_metadata_sha256"],x["backend_kind"],x["receipt_format"],x["readback_format"])):
        raise ValueError("backend registry entries must be sorted")
    digests=[stable_sha256(x) for x in norm]
    if len(set(digests))!=len(digests): raise ValueError("duplicate backend registry entry")
    d=stable_sha256(r)
    if d!=_sha(kw["expected_backend_registry_sha256"],"expected_backend_registry_sha256"): raise ValueError("backend registry digest mismatch")
    return d,norm

def _provenance(p,r91b,registry_sha,entries,kw):
    p=_exact(p,PROVENANCE_KEYS,"backend provenance verification key set mismatch")
    if p.get("schema")!=PROVENANCE_SCHEMA: raise ValueError("unsupported backend provenance verification schema")
    d=stable_sha256(p)
    if d!=_sha(kw["expected_backend_provenance_verification_sha256"],"expected_backend_provenance_verification_sha256"):
        raise ValueError("backend provenance verification digest mismatch")
    if p.get("provenance_scope")!="R91_COMMIT_AND_READBACK_BACKEND_METADATA_ONLY": raise ValueError("backend provenance scope invalid")
    expected={
        "r91_binding_id":r91b["binding_id"],
        "r91_binding_sha256":stable_sha256(r91b),
        "backend_registry_sha256":registry_sha,
        "backend_authority_root_sha256":_sha(kw["expected_backend_authority_root_sha256"],"expected_backend_authority_root_sha256"),
        "external_commit_receipt_sha256":r91b["external_commit_receipt_sha256"],
        "readback_evidence_sha256":r91b["readback_evidence_sha256"],
        "readback_state_sha256":r91b["readback_state_sha256"],
    }
    for k,v in expected.items():
        if p.get(k)!=v or type(p.get(k)) is not type(v): raise ValueError(f"backend provenance R91 mismatch: {k}")
    meta={k:p[k] for k in ENTRY_KEYS}
    meta=_entry(meta)
    matches=[e for e in entries if e==meta]
    if len(matches)!=1: raise ValueError("backend provenance unique metadata match required")
    entry_sha=stable_sha256(matches[0])
    if p.get("selected_backend_entry_sha256")!=entry_sha: raise ValueError("selected backend entry digest mismatch")
    for f in ("same_backend_metadata_claim_bound","commit_receipt_backend_metadata_bound","readback_backend_metadata_bound","backend_provenance_match"):
        if p.get(f) is not True: raise ValueError(f"required backend provenance guard missing: {f}")
    for f in PR_FALSE:
        if p.get(f) is not False: raise ValueError(f"backend provenance overclaim: {f}")
    if p.get("execution_authority")!="NONE": raise ValueError("backend provenance execution authority overclaim")
    return d,entry_sha,meta

def _inputs(args,kw):
    if len(args)!=22: raise ValueError("backend provenance positional input mismatch")
    if set(kw)!=KW: raise ValueError("backend provenance keyword set mismatch")
    validate_backend_provenance_policy(kw["backend_provenance_policy"])
    r91b=args[0]
    r91.validate_external_assertion_replay_durable_commit_readback_evidence_binding(
        r91b,*args[1:20],**{k:kw[k] for k in UPSTREAM_KW})
    registry_sha,entries=_registry(args[20],kw)
    prov_sha,entry_sha,meta=_provenance(args[21],r91b,registry_sha,entries,kw)
    return registry_sha,prov_sha,entry_sha,meta

def _payload(args,registry_sha,prov_sha,entry_sha,meta,kw):
    r91b,r90b,r89b,r88b,r87b,r86b,r85b,r84b,*_=args
    return {
        "schema":BINDING_SCHEMA,
        "r91_binding_id":r91b["binding_id"],"r91_binding_sha256":stable_sha256(r91b),
        "r90_binding_id":r90b["binding_id"],"r89_binding_id":r89b["binding_id"],"r88_binding_id":r88b["binding_id"],
        "r87_binding_id":r87b["binding_id"],"r86_binding_id":r86b["binding_id"],"r85_binding_id":r85b["binding_id"],"r84_binding_id":r84b["binding_id"],
        "backend_provenance_policy_sha256":stable_sha256(kw["backend_provenance_policy"]),
        "backend_registry_sha256":registry_sha,"backend_registry_digest_consumed":True,
        "backend_authority_root_sha256":_sha(kw["expected_backend_authority_root_sha256"],"backend_authority_root_sha256"),
        "backend_authority_root_digest_consumed":True,
        "backend_provenance_verification_sha256":prov_sha,"backend_provenance_verification_digest_consumed":True,
        "selected_backend_entry_sha256":entry_sha,
        "verifier_authority_root_sha256":_sha(kw["expected_verifier_authority_root_sha256"],"verifier_authority_root_sha256"),
        "verifier_authority_root_digest_consumed":True,
        "writer_authority_root_sha256":_sha(kw["expected_writer_authority_root_sha256"],"writer_authority_root_sha256"),
        "writer_authority_root_digest_consumed":True,
        **meta,
        "external_commit_receipt_sha256":r91b["external_commit_receipt_sha256"],
        "readback_evidence_sha256":r91b["readback_evidence_sha256"],"readback_state_sha256":r91b["readback_state_sha256"],
        "commit_id":r91b["commit_id"],"idempotency_key_sha256":r91b["idempotency_key_sha256"],
        "backend_provenance_bound":True,"commit_receipt_backend_metadata_bound":True,"readback_backend_metadata_bound":True,
        "same_backend_metadata_claim_bound":True,"backend_provenance_match":True,
        "backend_commit_authenticity_verified":False,"readback_authenticity_verified":False,"backend_identity_verified":False,
        "backend_trust_root_verified":False,"backend_registry_operator_identity_verified":False,"live_backend_observed":False,
        "durable_commit_proven":False,"durable_dual_state_atomicity_proven":False,"durable_single_use_enforced":False,"write_performed":False,
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

def build_external_assertion_replay_backend_provenance_binding(*args,**kw):
    rs,ps,es,meta=_inputs(args,kw); x=_payload(args,rs,ps,es,meta,kw); x["binding_id"]=_bid(x)
    validate_external_assertion_replay_backend_provenance_binding(x,*args,**kw); return x

def validate_external_assertion_replay_backend_provenance_binding(binding,*args,**kw):
    rs,ps,es,meta=_inputs(args,kw)
    if not isinstance(binding,dict) or set(binding)!=BINDING_KEYS: raise ValueError("backend provenance binding key set mismatch")
    if binding.get("schema")!=BINDING_SCHEMA: raise ValueError("unsupported backend provenance binding schema")
    _id(binding.get("binding_id"),"binding_id")
    expected=_payload(args,rs,ps,es,meta,kw)
    for k,v in expected.items():
        if binding.get(k)!=v or type(binding.get(k)) is not type(v): raise ValueError(f"backend provenance binding mismatch: {k}")
    if binding["binding_id"]!=_bid(binding): raise ValueError("binding_id binding mismatch")
