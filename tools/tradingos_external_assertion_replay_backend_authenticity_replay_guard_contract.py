"""TradingOS R96 backend-authenticity replay-guard candidate binding."""
from __future__ import annotations
import hashlib,re
from tools import tradingos_external_assertion_replay_backend_key_provenance_contract as r95
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes,stable_sha256

BINDING_SCHEMA="tradingos.external_assertion_replay_backend_authenticity_replay_guard_binding.v1"
REPLAY_REGISTRY_SCHEMA="control_center.external_assertion_replay_backend_authenticity_registry_snapshot.v1"
NEXT_CANDIDATE_SCHEMA="control_center.external_assertion_replay_backend_authenticity_registry_candidate.v1"
POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_REPLAY_GUARD_POLICY_V1"
VERSION="1.0.0"
_ID=re.compile(r"^[0-9a-f]{24}$"); _SHA=re.compile(r"^[0-9a-f]{64}$")
OUTPUT_PERMISSIONS={"execution_authority":"NONE","signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","confers_authority":False}

POLICY_KEYS=set("""allowed_algorithms attestation_set_consumption_authority backend_authenticity_inference_allowed backend_authenticity_verifier_trust_inference_allowed backend_identity_inference_allowed backend_key_possession_inference_allowed backend_registry_write_allowed backend_trust_root_inference_allowed backend_write_allowed credential_access_in_core_allowed durable_single_use_inference_allowed freshness_inference_allowed human_review_only input_r95_binding_schema live_decision_feedback_allowed live_decision_use_allowed liveness_inference_allowed memory_write_authority mode model_selection_use_allowed network_access_in_core_allowed next_candidate_schema output_permissions persistence_in_core_allowed policy_id policy_update_allowed raw_public_key_bytes_in_core_allowed raw_signature_bytes_in_core_allowed readback_authenticity_inference_allowed recommendations_allowed registry_write_allowed replay_registry_current_state_inference_allowed replay_registry_schema require_assertion_absent require_challenge_absent require_exact_backend_authenticity_assertion_sha256_from_r95 require_exact_backend_authenticity_challenge_sha256_from_r95 require_exact_generation_increment require_expected_replay_registry_digest require_full_r95_validation require_sorted_unique_digest_sets schema_version shadow_only""".split())
P_TRUE=set("""human_review_only require_assertion_absent require_challenge_absent require_exact_backend_authenticity_assertion_sha256_from_r95 require_exact_backend_authenticity_challenge_sha256_from_r95 require_exact_generation_increment require_expected_replay_registry_digest require_full_r95_validation require_sorted_unique_digest_sets shadow_only""".split())
P_FALSE=set("""backend_authenticity_inference_allowed backend_authenticity_verifier_trust_inference_allowed backend_identity_inference_allowed backend_key_possession_inference_allowed backend_registry_write_allowed backend_trust_root_inference_allowed backend_write_allowed credential_access_in_core_allowed durable_single_use_inference_allowed freshness_inference_allowed live_decision_feedback_allowed live_decision_use_allowed liveness_inference_allowed model_selection_use_allowed network_access_in_core_allowed persistence_in_core_allowed policy_update_allowed raw_public_key_bytes_in_core_allowed raw_signature_bytes_in_core_allowed readback_authenticity_inference_allowed recommendations_allowed registry_write_allowed replay_registry_current_state_inference_allowed""".split())

REGISTRY_KEYS=set("""schema registry_id generation previous_registry_sha256 used_backend_authenticity_assertion_sha256s used_backend_authenticity_challenge_sha256s
registry_scope durable_commit_proven write_allowed apply_allowed confers_authority""".split())
NEXT_CANDIDATE_KEYS=set("""schema registry_id prior_registry_sha256 prior_generation next_generation
append_backend_authenticity_assertion_sha256 append_backend_authenticity_challenge_sha256
used_backend_authenticity_assertion_sha256s used_backend_authenticity_challenge_sha256s candidate_status durable_commit_proven write_performed apply_allowed confers_authority""".split())
R96_FIELDS=set("""backend_authenticity_assertion_absent_in_expected_replay_registry backend_authenticity_challenge_absent_in_expected_replay_registry backend_authenticity_next_replay_registry_candidate_sha256 backend_authenticity_replay_absence_bound backend_authenticity_replay_candidate_apply_allowed backend_authenticity_replay_candidate_status backend_authenticity_replay_candidate_write_performed backend_authenticity_replay_guard_candidate_bound backend_authenticity_replay_guard_policy_sha256 backend_authenticity_replay_next_generation backend_authenticity_replay_prior_generation backend_authenticity_replay_registry_digest_consumed backend_authenticity_replay_registry_id backend_authenticity_replay_registry_sha256 backend_authenticity_replay_registry_write_performed backend_registry_sha256 binding_id r95_binding_id r95_binding_sha256 schema""".split())
BINDING_KEYS=(set(r95.BINDING_KEYS)-{"schema","binding_id"})|R96_FIELDS
UPSTREAM_KW=set(r95.KW)
KW=UPSTREAM_KW|{"expected_backend_authenticity_replay_registry_sha256","backend_authenticity_replay_guard_policy"}

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
def _generation(v,n,*,allow_max=False):
    maximum=2147483647 if allow_max else 2147483646
    if type(v) is not int or not 0<=v<=maximum: raise ValueError(f"{n} invalid")
    return v

def validate_backend_authenticity_replay_guard_policy(policy):
    p=_exact(policy,POLICY_KEYS,"backend authenticity replay policy key set mismatch")
    if type(p.get("schema_version")) is not int or p["schema_version"]!=1 or p.get("policy_id")!=POLICY_ID:
        raise ValueError("unsupported backend authenticity replay policy")
    if p.get("mode")!="OFFLINE_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_REPLAY_GUARD_CANDIDATE_ONLY":
        raise ValueError("backend authenticity replay policy mode drift")
    if p.get("input_r95_binding_schema")!=r95.BINDING_SCHEMA: raise ValueError("input R95 binding schema drift")
    if p.get("replay_registry_schema")!=REPLAY_REGISTRY_SCHEMA: raise ValueError("backend authenticity replay registry schema drift")
    if p.get("next_candidate_schema")!=NEXT_CANDIDATE_SCHEMA: raise ValueError("backend authenticity replay candidate schema drift")
    if p.get("allowed_algorithms")!=["ED25519"]: raise ValueError("backend authenticity replay algorithm allowlist drift")
    for f in P_TRUE:
        if p.get(f) is not True: raise ValueError(f"required backend authenticity replay guard disabled: {f}")
    for f in P_FALSE:
        if p.get(f) is not False: raise ValueError(f"unsafe backend authenticity replay policy: {f}")
    if p.get("attestation_set_consumption_authority")!="NONE" or p.get("memory_write_authority")!="NONE":
        raise ValueError("backend authenticity replay authority must remain NONE")
    o=_exact(p.get("output_permissions"),set(OUTPUT_PERMISSIONS),"unsafe backend authenticity replay output permissions")
    for k,e in OUTPUT_PERMISSIONS.items():
        if o.get(k)!=e or type(o.get(k)) is not type(e): raise ValueError("unsafe backend authenticity replay output permissions")

def _digest_set(v,n):
    if not isinstance(v,list) or len(v)>4096: raise ValueError(f"{n} invalid")
    out=[_sha(x,n) for x in v]
    if out!=sorted(out) or len(set(out))!=len(out): raise ValueError(f"{n} must be sorted and unique")
    return out

def _registry(registry,r95b,kw):
    r=_exact(registry,REGISTRY_KEYS,"backend authenticity replay registry key set mismatch")
    if r.get("schema")!=REPLAY_REGISTRY_SCHEMA: raise ValueError("unsupported backend authenticity replay registry schema")
    _token(r.get("registry_id"),"registry_id")
    generation=_generation(r.get("generation"),"generation")
    _sha(r.get("previous_registry_sha256"),"previous_registry_sha256")
    if r.get("registry_scope")!="BACKEND_AUTHENTICITY_ASSERTION_AND_CHALLENGE_REPLAY_GUARD_ONLY": raise ValueError("backend authenticity replay registry scope invalid")
    if r.get("durable_commit_proven") is not False: raise ValueError("backend authenticity replay registry durability overclaim")
    if r.get("write_allowed") is not False: raise ValueError("backend authenticity replay registry write overclaim")
    if r.get("apply_allowed") is not False: raise ValueError("backend authenticity replay registry apply overclaim")
    if r.get("confers_authority") is not False: raise ValueError("backend authenticity replay registry authority overclaim")
    expected=_sha(kw["expected_backend_authenticity_replay_registry_sha256"],"expected_backend_authenticity_replay_registry_sha256")
    digest=stable_sha256(r)
    if digest!=expected: raise ValueError("backend authenticity replay registry digest mismatch")
    used_a=_digest_set(r.get("used_backend_authenticity_assertion_sha256s"),"used_backend_authenticity_assertion_sha256s")
    used_c=_digest_set(r.get("used_backend_authenticity_challenge_sha256s"),"used_backend_authenticity_challenge_sha256s")
    assertion=_sha(r95b["backend_authenticity_assertion_sha256"],"backend_authenticity_assertion_sha256")
    challenge=_sha(r95b["challenge_sha256"],"challenge_sha256")
    if assertion in used_a: raise ValueError("backend authenticity assertion replay detected")
    if challenge in used_c: raise ValueError("backend authenticity challenge replay detected")
    return digest,generation,used_a,used_c

def _next_candidate(registry,digest,generation,used_a,used_c,r95b):
    assertion=_sha(r95b["backend_authenticity_assertion_sha256"],"backend_authenticity_assertion_sha256")
    challenge=_sha(r95b["challenge_sha256"],"challenge_sha256")
    x={
        "schema":NEXT_CANDIDATE_SCHEMA,"registry_id":registry["registry_id"],"prior_registry_sha256":digest,
        "prior_generation":generation,"next_generation":generation+1,
        "append_backend_authenticity_assertion_sha256":assertion,
        "append_backend_authenticity_challenge_sha256":challenge,
        "used_backend_authenticity_assertion_sha256s":sorted([*used_a,assertion]),
        "used_backend_authenticity_challenge_sha256s":sorted([*used_c,challenge]),
        "candidate_status":"BACKEND_AUTHENTICITY_REPLAY_GUARD_CANDIDATE_ONLY_NOT_DURABLY_ENFORCED",
        "durable_commit_proven":False,"write_performed":False,"apply_allowed":False,"confers_authority":False,
    }
    return _exact(x,NEXT_CANDIDATE_KEYS,"backend authenticity replay next candidate key set mismatch")

def _inputs(args,kw):
    if len(args)!=30: raise ValueError("backend authenticity replay positional input mismatch")
    if set(kw)!=KW: raise ValueError("backend authenticity replay keyword set mismatch")
    validate_backend_authenticity_replay_guard_policy(kw["backend_authenticity_replay_guard_policy"])
    r95b=args[0]
    r95.validate_external_assertion_replay_backend_key_provenance_binding(r95b,*args[1:29],**{k:kw[k] for k in UPSTREAM_KW})
    digest,generation,used_a,used_c=_registry(args[29],r95b,kw)
    candidate=_next_candidate(args[29],digest,generation,used_a,used_c,r95b)
    return digest,generation,candidate

def _payload(args,registry,digest,generation,candidate,kw):
    r95b=args[0]
    x={k:r95b[k] for k in r95.BINDING_KEYS if k not in {"schema","binding_id"}}
    x.update({
        "schema":BINDING_SCHEMA,"r95_binding_id":r95b["binding_id"],"r95_binding_sha256":stable_sha256(r95b),
        "backend_registry_sha256":_sha(kw["expected_backend_registry_sha256"],"expected_backend_registry_sha256"),
        "backend_authenticity_replay_guard_policy_sha256":stable_sha256(kw["backend_authenticity_replay_guard_policy"]),
        "backend_authenticity_replay_registry_sha256":digest,"backend_authenticity_replay_registry_digest_consumed":True,
        "backend_authenticity_replay_registry_id":registry["registry_id"],
        "backend_authenticity_replay_prior_generation":generation,
        "backend_authenticity_assertion_absent_in_expected_replay_registry":True,
        "backend_authenticity_challenge_absent_in_expected_replay_registry":True,
        "backend_authenticity_next_replay_registry_candidate_sha256":stable_sha256(candidate),
        "backend_authenticity_replay_next_generation":candidate["next_generation"],
        "backend_authenticity_replay_guard_candidate_bound":True,
        "backend_authenticity_replay_absence_bound":True,
        "backend_authenticity_replay_candidate_status":candidate["candidate_status"],
        "backend_authenticity_replay_registry_write_performed":False,
        "backend_authenticity_replay_candidate_write_performed":False,
        "backend_authenticity_replay_candidate_apply_allowed":False,
    })
    return x

def _bid(x):
    return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]

def build_external_assertion_replay_backend_authenticity_replay_guard_binding(*args,**kw):
    digest,generation,candidate=_inputs(args,kw); x=_payload(args,args[29],digest,generation,candidate,kw); x["binding_id"]=_bid(x)
    validate_external_assertion_replay_backend_authenticity_replay_guard_binding(x,*args,**kw); return x

def validate_external_assertion_replay_backend_authenticity_replay_guard_binding(binding,*args,**kw):
    digest,generation,candidate=_inputs(args,kw)
    if not isinstance(binding,dict) or set(binding)!=BINDING_KEYS: raise ValueError("backend authenticity replay binding key set mismatch")
    if binding.get("schema")!=BINDING_SCHEMA: raise ValueError("unsupported backend authenticity replay binding schema")
    _id(binding.get("binding_id"),"binding_id"); expected=_payload(args,args[29],digest,generation,candidate,kw)
    for k,v in expected.items():
        if binding.get(k)!=v or type(binding.get(k)) is not type(v): raise ValueError(f"backend authenticity replay binding mismatch: {k}")
    if binding["binding_id"]!=_bid(binding): raise ValueError("binding_id binding mismatch")
