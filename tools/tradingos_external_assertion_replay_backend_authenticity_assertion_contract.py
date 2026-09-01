"""TradingOS R93 backend authenticity assertion binding."""
from __future__ import annotations
import hashlib,re
from tools import tradingos_external_assertion_replay_backend_provenance_contract as r92
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes,stable_sha256

BINDING_SCHEMA="tradingos.external_assertion_replay_backend_authenticity_assertion_binding.v1"
CHALLENGE_SCHEMA="tradingos.external_assertion_replay_backend_authenticity_challenge.v1"
ASSERTION_SCHEMA="control_center.external_assertion_replay_backend_authenticity_assertion.v1"
POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_ASSERTION_POLICY_V1"
VERSION="1.0.0"
_ID=re.compile(r"^[0-9a-f]{24}$"); _SHA=re.compile(r"^[0-9a-f]{64}$")
OUTPUT_PERMISSIONS={"execution_authority":"NONE","signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","confers_authority":False}

POLICY_KEYS=set("""schema_version policy_id mode input_r92_binding_schema challenge_schema backend_authenticity_assertion_schema allowed_algorithms
require_full_r92_validation require_canonical_challenge require_expected_backend_authenticity_assertion_digest require_exact_r92_provenance_binding
require_backend_key_id_match_r92 require_commit_signature_assertion require_readback_signature_assertion require_same_backend_key_claim
require_no_local_signature_math network_access_in_core_allowed credential_access_in_core_allowed signature_generation_in_core_allowed
local_signature_verification_in_core_allowed raw_signature_bytes_allowed raw_public_key_bytes_allowed backend_authenticity_inference_allowed
readback_authenticity_inference_allowed backend_key_possession_inference_allowed backend_identity_inference_allowed backend_trust_root_inference_allowed
backend_authenticity_verifier_trust_inference_allowed live_backend_observation_inference_allowed durable_commit_inference_allowed
durable_dual_state_atomicity_inference_allowed durable_single_use_inference_allowed global_current_state_inference_allowed
concurrent_writer_exclusion_inference_allowed freshness_inference_allowed liveness_inference_allowed recommendations_allowed policy_update_allowed
live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed persistence_in_core_allowed backend_write_allowed backend_registry_write_allowed
human_review_only shadow_only attestation_set_consumption_authority memory_write_authority output_permissions""".split())
P_TRUE=set("""require_full_r92_validation require_canonical_challenge require_expected_backend_authenticity_assertion_digest require_exact_r92_provenance_binding
require_backend_key_id_match_r92 require_commit_signature_assertion require_readback_signature_assertion require_same_backend_key_claim require_no_local_signature_math
human_review_only shadow_only""".split())
P_FALSE=POLICY_KEYS-P_TRUE-{"schema_version","policy_id","mode","input_r92_binding_schema","challenge_schema","backend_authenticity_assertion_schema","allowed_algorithms","attestation_set_consumption_authority","memory_write_authority","output_permissions"}

CHALLENGE_KEYS=set("""schema purpose r92_binding_id r92_binding_sha256 selected_backend_entry_sha256 backend_authority_root_sha256 backend_id backend_key_id
backend_metadata_sha256 backend_kind receipt_format readback_format external_commit_receipt_sha256 readback_evidence_sha256 readback_state_sha256
commit_id idempotency_key_sha256 backend_authenticity_assertion_policy_sha256""".split())
ASSERTION_KEYS=set("""schema challenge_sha256 backend_id backend_key_id backend_metadata_sha256 public_key_sha256 algorithm verifier_id verifier_key_id
commit_signature_verified_by_external_asymmetric_verifier readback_signature_verified_by_external_asymmetric_verifier same_backend_key_claim_bound
local_signature_math_verified assertion_scope backend_commit_authenticity_verified readback_authenticity_verified backend_key_possession_proven
backend_identity_verified backend_trust_root_verified backend_authenticity_verifier_trust_root_verified assertion_freshness_verified confers_authority""".split())

BINDING_KEYS=set("""schema binding_id r92_binding_id r92_binding_sha256 r91_binding_id r90_binding_id r89_binding_id r88_binding_id r87_binding_id r86_binding_id r85_binding_id r84_binding_id
backend_authenticity_assertion_policy_sha256 challenge_sha256 backend_authenticity_assertion_sha256 backend_authenticity_assertion_digest_consumed
verifier_authority_root_sha256 verifier_authority_root_digest_consumed writer_authority_root_sha256 writer_authority_root_digest_consumed
backend_authority_root_sha256 backend_authority_root_digest_consumed selected_backend_entry_sha256 backend_id backend_key_id backend_metadata_sha256 backend_kind
receipt_format readback_format external_commit_receipt_sha256 readback_evidence_sha256 readback_state_sha256 commit_id idempotency_key_sha256 public_key_sha256 algorithm
backend_authenticity_verifier_id backend_authenticity_verifier_key_id backend_authenticity_assertion_bound commit_signature_assertion_bound
readback_signature_assertion_bound backend_key_possession_assertion_bound same_backend_key_claim_bound local_signature_math_verified
backend_commit_authenticity_verified readback_authenticity_verified backend_key_possession_proven backend_identity_verified backend_trust_root_verified
backend_registry_operator_identity_verified backend_authenticity_verifier_trust_root_verified live_backend_observed durable_commit_proven
durable_dual_state_atomicity_proven durable_single_use_enforced write_performed global_current_state_verified concurrent_writer_exclusion_proven
registry_write_performed lease_registry_write_performed receipt_index_write_performed backend_write_performed assertion_freshness_verified liveness_verified
writer_authority_root_verified verifier_trust_root_verified review_identity_verified distinct_reviewer_count_allowed consensus_inference_allowed approval_state_allowed
shadow_only human_review_only attestation_set_consumption_authority memory_write_authority policy_update_allowed live_decision_feedback_allowed
live_decision_use_allowed model_selection_use_allowed execution_authority can_trade capital_permission confers_authority""".split())

UPSTREAM_KW=set("""expected_external_assertion_sha256 key_possession_policy expected_verifier_registry_sha256 expected_verifier_authority_root_sha256
expected_writer_authority_root_sha256 provenance_policy expected_replay_registry_sha256 replay_guard_policy expected_atomic_verification_sha256 atomic_cas_policy
expected_recovery_verification_sha256 writer_fencing_recovery_policy expected_authority_anchor_sha256 writer_authority_anchor_policy
expected_atomicity_verification_sha256 dual_state_atomicity_policy expected_commit_readback_evidence_sha256 commit_readback_evidence_policy
expected_backend_registry_sha256 expected_backend_authority_root_sha256 expected_backend_provenance_verification_sha256 backend_provenance_policy""".split())
KW=UPSTREAM_KW|{"expected_backend_authenticity_assertion_sha256","backend_authenticity_assertion_policy"}

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

def validate_backend_authenticity_assertion_policy(policy):
    p=_exact(policy,POLICY_KEYS,"backend authenticity assertion policy key set mismatch")
    if type(p.get("schema_version")) is not int or p["schema_version"]!=1 or p.get("policy_id")!=POLICY_ID:
        raise ValueError("unsupported backend authenticity assertion policy")
    if p.get("mode")!="OFFLINE_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_ASSERTION_BINDING_ONLY":
        raise ValueError("backend authenticity assertion policy mode drift")
    if p.get("input_r92_binding_schema")!=r92.BINDING_SCHEMA: raise ValueError("input R92 binding schema drift")
    if p.get("challenge_schema")!=CHALLENGE_SCHEMA: raise ValueError("backend authenticity challenge schema drift")
    if p.get("backend_authenticity_assertion_schema")!=ASSERTION_SCHEMA: raise ValueError("backend authenticity assertion schema drift")
    alg=p.get("allowed_algorithms")
    if not isinstance(alg,list) or alg!=sorted(set(alg)) or not alg or any(not isinstance(x,str) or not x for x in alg):
        raise ValueError("backend authenticity allowed algorithms invalid")
    for f in P_TRUE:
        if p.get(f) is not True: raise ValueError(f"required backend authenticity guard disabled: {f}")
    for f in P_FALSE:
        if p.get(f) is not False: raise ValueError(f"unsafe backend authenticity assertion policy: {f}")
    if p.get("attestation_set_consumption_authority")!="NONE" or p.get("memory_write_authority")!="NONE":
        raise ValueError("backend authenticity authority must remain NONE")
    o=_exact(p.get("output_permissions"),set(OUTPUT_PERMISSIONS),"unsafe backend authenticity output permissions")
    for k,e in OUTPUT_PERMISSIONS.items():
        if o.get(k)!=e or type(o.get(k)) is not type(e): raise ValueError("unsafe backend authenticity output permissions")

def build_backend_authenticity_challenge(r92_binding,policy):
    validate_backend_authenticity_assertion_policy(policy)
    if not isinstance(r92_binding,dict) or r92_binding.get("schema")!=r92.BINDING_SCHEMA:
        raise ValueError("invalid R92 binding for challenge")
    return {
        "schema":CHALLENGE_SCHEMA,
        "purpose":"R93_BACKEND_AUTHENTICITY_ASSERTION_BINDING_ONLY",
        "r92_binding_id":r92_binding["binding_id"],
        "r92_binding_sha256":stable_sha256(r92_binding),
        "selected_backend_entry_sha256":r92_binding["selected_backend_entry_sha256"],
        "backend_authority_root_sha256":r92_binding["backend_authority_root_sha256"],
        "backend_id":r92_binding["backend_id"],"backend_key_id":r92_binding["backend_key_id"],
        "backend_metadata_sha256":r92_binding["backend_metadata_sha256"],"backend_kind":r92_binding["backend_kind"],
        "receipt_format":r92_binding["receipt_format"],"readback_format":r92_binding["readback_format"],
        "external_commit_receipt_sha256":r92_binding["external_commit_receipt_sha256"],
        "readback_evidence_sha256":r92_binding["readback_evidence_sha256"],"readback_state_sha256":r92_binding["readback_state_sha256"],
        "commit_id":r92_binding["commit_id"],"idempotency_key_sha256":r92_binding["idempotency_key_sha256"],
        "backend_authenticity_assertion_policy_sha256":stable_sha256(policy),
    }

def _assertion(a,challenge,r92b,kw):
    a=_exact(a,ASSERTION_KEYS,"backend authenticity assertion key set mismatch")
    if a.get("schema")!=ASSERTION_SCHEMA: raise ValueError("unsupported backend authenticity assertion schema")
    d=stable_sha256(a)
    if d!=_sha(kw["expected_backend_authenticity_assertion_sha256"],"expected_backend_authenticity_assertion_sha256"):
        raise ValueError("backend authenticity assertion digest mismatch")
    if a.get("challenge_sha256")!=stable_sha256(challenge): raise ValueError("backend authenticity challenge mismatch")
    for k in ("backend_id","backend_key_id","backend_metadata_sha256"):
        if a.get(k)!=r92b[k] or type(a.get(k)) is not type(r92b[k]): raise ValueError(f"backend authenticity R92 mismatch: {k}")
    _sha(a.get("public_key_sha256"),"public_key_sha256")
    _token(a.get("backend_key_id"),"backend_key_id")
    algorithm=_token(a.get("algorithm"),"algorithm")
    if algorithm not in kw["backend_authenticity_assertion_policy"]["allowed_algorithms"]: raise ValueError("unsupported backend authenticity algorithm")
    _token(a.get("verifier_id"),"verifier_id"); _token(a.get("verifier_key_id"),"verifier_key_id")
    if a.get("commit_signature_verified_by_external_asymmetric_verifier") is not True: raise ValueError("commit signature external-verifier assertion missing")
    if a.get("readback_signature_verified_by_external_asymmetric_verifier") is not True: raise ValueError("readback signature external-verifier assertion missing")
    if a.get("same_backend_key_claim_bound") is not True: raise ValueError("same backend key claim missing")
    if a.get("local_signature_math_verified") is not False: raise ValueError("local signature math overclaim")
    if a.get("assertion_scope")!="BACKEND_COMMIT_AND_READBACK_SIGNATURE_ASSERTION_ONLY": raise ValueError("backend authenticity assertion scope invalid")
    for f in ("backend_commit_authenticity_verified","readback_authenticity_verified","backend_key_possession_proven","backend_identity_verified","backend_trust_root_verified","backend_authenticity_verifier_trust_root_verified","assertion_freshness_verified","confers_authority"):
        if a.get(f) is not False: raise ValueError(f"backend authenticity assertion overclaim: {f}")
    return d

def _inputs(args,kw):
    if len(args)!=24: raise ValueError("backend authenticity positional input mismatch")
    if set(kw)!=KW: raise ValueError("backend authenticity keyword set mismatch")
    validate_backend_authenticity_assertion_policy(kw["backend_authenticity_assertion_policy"])
    r92b=args[0]
    r92.validate_external_assertion_replay_backend_provenance_binding(r92b,*args[1:23],**{k:kw[k] for k in UPSTREAM_KW})
    challenge=build_backend_authenticity_challenge(r92b,kw["backend_authenticity_assertion_policy"])
    assertion_sha=_assertion(args[23],challenge,r92b,kw)
    return challenge,assertion_sha

def _payload(args,challenge,assertion_sha,kw):
    r92b,r91b,r90b,r89b,r88b,r87b,r86b,r85b,r84b,*rest=args
    a=args[23]
    return {
        "schema":BINDING_SCHEMA,"r92_binding_id":r92b["binding_id"],"r92_binding_sha256":stable_sha256(r92b),
        "r91_binding_id":r91b["binding_id"],"r90_binding_id":r90b["binding_id"],"r89_binding_id":r89b["binding_id"],
        "r88_binding_id":r88b["binding_id"],"r87_binding_id":r87b["binding_id"],"r86_binding_id":r86b["binding_id"],
        "r85_binding_id":r85b["binding_id"],"r84_binding_id":r84b["binding_id"],
        "backend_authenticity_assertion_policy_sha256":stable_sha256(kw["backend_authenticity_assertion_policy"]),
        "challenge_sha256":stable_sha256(challenge),"backend_authenticity_assertion_sha256":assertion_sha,
        "backend_authenticity_assertion_digest_consumed":True,
        "verifier_authority_root_sha256":r92b["verifier_authority_root_sha256"],"verifier_authority_root_digest_consumed":True,
        "writer_authority_root_sha256":r92b["writer_authority_root_sha256"],"writer_authority_root_digest_consumed":True,
        "backend_authority_root_sha256":r92b["backend_authority_root_sha256"],"backend_authority_root_digest_consumed":True,
        "selected_backend_entry_sha256":r92b["selected_backend_entry_sha256"],"backend_id":r92b["backend_id"],"backend_key_id":r92b["backend_key_id"],
        "backend_metadata_sha256":r92b["backend_metadata_sha256"],"backend_kind":r92b["backend_kind"],"receipt_format":r92b["receipt_format"],
        "readback_format":r92b["readback_format"],"external_commit_receipt_sha256":r92b["external_commit_receipt_sha256"],
        "readback_evidence_sha256":r92b["readback_evidence_sha256"],"readback_state_sha256":r92b["readback_state_sha256"],
        "commit_id":r92b["commit_id"],"idempotency_key_sha256":r92b["idempotency_key_sha256"],"public_key_sha256":a["public_key_sha256"],
        "algorithm":a["algorithm"],"backend_authenticity_verifier_id":a["verifier_id"],"backend_authenticity_verifier_key_id":a["verifier_key_id"],
        "backend_authenticity_assertion_bound":True,"commit_signature_assertion_bound":True,"readback_signature_assertion_bound":True,
        "backend_key_possession_assertion_bound":True,"same_backend_key_claim_bound":True,"local_signature_math_verified":False,
        "backend_commit_authenticity_verified":False,"readback_authenticity_verified":False,"backend_key_possession_proven":False,
        "backend_identity_verified":False,"backend_trust_root_verified":False,"backend_registry_operator_identity_verified":False,
        "backend_authenticity_verifier_trust_root_verified":False,"live_backend_observed":False,"durable_commit_proven":False,
        "durable_dual_state_atomicity_proven":False,"durable_single_use_enforced":False,"write_performed":False,
        "global_current_state_verified":False,"concurrent_writer_exclusion_proven":False,"registry_write_performed":False,
        "lease_registry_write_performed":False,"receipt_index_write_performed":False,"backend_write_performed":False,
        "assertion_freshness_verified":False,"liveness_verified":False,"writer_authority_root_verified":False,"verifier_trust_root_verified":False,
        "review_identity_verified":False,"distinct_reviewer_count_allowed":False,"consensus_inference_allowed":False,"approval_state_allowed":False,
        "shadow_only":True,"human_review_only":True,"attestation_set_consumption_authority":"NONE","memory_write_authority":"NONE",
        "policy_update_allowed":False,"live_decision_feedback_allowed":False,"live_decision_use_allowed":False,"model_selection_use_allowed":False,
        "execution_authority":"NONE","can_trade":False,"capital_permission":"DENY","confers_authority":False,
    }

def _bid(x):
    return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]

def build_external_assertion_replay_backend_authenticity_assertion_binding(*args,**kw):
    ch,ash=_inputs(args,kw); x=_payload(args,ch,ash,kw); x["binding_id"]=_bid(x)
    validate_external_assertion_replay_backend_authenticity_assertion_binding(x,*args,**kw); return x

def validate_external_assertion_replay_backend_authenticity_assertion_binding(binding,*args,**kw):
    ch,ash=_inputs(args,kw)
    if not isinstance(binding,dict) or set(binding)!=BINDING_KEYS: raise ValueError("backend authenticity binding key set mismatch")
    if binding.get("schema")!=BINDING_SCHEMA: raise ValueError("unsupported backend authenticity binding schema")
    _id(binding.get("binding_id"),"binding_id"); expected=_payload(args,ch,ash,kw)
    for k,v in expected.items():
        if binding.get(k)!=v or type(binding.get(k)) is not type(v): raise ValueError(f"backend authenticity binding mismatch: {k}")
    if binding["binding_id"]!=_bid(binding): raise ValueError("binding_id binding mismatch")
