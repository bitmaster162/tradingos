"""TradingOS R94 backend authenticity verifier provenance binding."""
from __future__ import annotations
import hashlib,re
from tools import tradingos_external_assertion_replay_backend_authenticity_assertion_contract as r93
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes,stable_sha256

BINDING_SCHEMA="tradingos.external_assertion_replay_backend_authenticity_verifier_provenance_binding.v1"
VERIFIER_REGISTRY_SCHEMA="control_center.external_assertion_replay_backend_authenticity_verifier_registry_snapshot.v1"
POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_VERIFIER_PROVENANCE_POLICY_V1"
VERSION="1.0.0"
_ID=re.compile(r"^[0-9a-f]{24}$"); _SHA=re.compile(r"^[0-9a-f]{64}$")
OUTPUT_PERMISSIONS={"execution_authority":"NONE","signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","confers_authority":False}

POLICY_KEYS=set("""schema_version policy_id mode input_r93_binding_schema verifier_registry_schema allowed_algorithms
require_full_r93_validation require_expected_verifier_registry_digest require_expected_authority_root_digest require_exact_registry_entry_match
network_access_in_core_allowed credential_access_in_core_allowed raw_signature_bytes_in_core_allowed raw_public_key_bytes_in_core_allowed
registry_write_allowed backend_write_allowed backend_registry_write_allowed verifier_trust_inference_allowed authority_root_trust_inference_allowed
registry_operator_identity_inference_allowed verifier_identity_inference_allowed backend_authenticity_inference_allowed readback_authenticity_inference_allowed
backend_key_possession_inference_allowed backend_identity_inference_allowed backend_trust_root_inference_allowed freshness_inference_allowed
liveness_inference_allowed durable_commit_inference_allowed durable_dual_state_atomicity_inference_allowed durable_single_use_inference_allowed
global_current_state_inference_allowed concurrent_writer_exclusion_inference_allowed recommendations_allowed policy_update_allowed
live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed persistence_in_core_allowed
human_review_only shadow_only attestation_set_consumption_authority memory_write_authority output_permissions""".split())
P_TRUE=set("""require_full_r93_validation require_expected_verifier_registry_digest require_expected_authority_root_digest require_exact_registry_entry_match
human_review_only shadow_only""".split())
P_FALSE=POLICY_KEYS-P_TRUE-{"schema_version","policy_id","mode","input_r93_binding_schema","verifier_registry_schema","allowed_algorithms","attestation_set_consumption_authority","memory_write_authority","output_permissions"}

REGISTRY_KEYS={"schema","registry_id","authority_root_sha256","entries","registry_scope","trust_root_verified","confers_authority"}
REGISTRY_ENTRY_KEYS={"verifier_id","verifier_key_id","verified_public_key_sha256","algorithm"}
R94_FIELDS=set("""schema binding_id r93_binding_id r93_binding_sha256 backend_authenticity_verifier_provenance_policy_sha256
backend_authenticity_verifier_registry_sha256 backend_authenticity_verifier_registry_digest_consumed backend_authenticity_verifier_registry_id
backend_authenticity_verifier_authority_root_sha256 backend_authenticity_verifier_authority_root_digest_consumed
backend_authenticity_verifier_registry_entry_sha256 backend_authenticity_verifier_registry_entry_exact_match
backend_authenticity_verifier_provenance_bound backend_authenticity_verifier_identity_verified
backend_authenticity_verifier_registry_operator_identity_verified""".split())
BINDING_KEYS=(set(r93.BINDING_KEYS)-{"schema","binding_id"})|R94_FIELDS
UPSTREAM_KW=set(r93.KW)
KW=UPSTREAM_KW|{"expected_backend_authenticity_verifier_registry_sha256","expected_backend_authenticity_verifier_authority_root_sha256","backend_authenticity_verifier_provenance_policy"}

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

def validate_backend_authenticity_verifier_provenance_policy(policy):
    p=_exact(policy,POLICY_KEYS,"backend authenticity verifier provenance policy key set mismatch")
    if type(p.get("schema_version")) is not int or p["schema_version"]!=1 or p.get("policy_id")!=POLICY_ID:
        raise ValueError("unsupported backend authenticity verifier provenance policy")
    if p.get("mode")!="OFFLINE_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHENTICITY_VERIFIER_PROVENANCE_BINDING_ONLY":
        raise ValueError("backend authenticity verifier provenance policy mode drift")
    if p.get("input_r93_binding_schema")!=r93.BINDING_SCHEMA: raise ValueError("input R93 binding schema drift")
    if p.get("verifier_registry_schema")!=VERIFIER_REGISTRY_SCHEMA: raise ValueError("backend authenticity verifier registry schema drift")
    if p.get("allowed_algorithms")!=["ED25519"]: raise ValueError("backend authenticity verifier algorithm allowlist drift")
    for f in P_TRUE:
        if p.get(f) is not True: raise ValueError(f"required backend authenticity verifier provenance guard disabled: {f}")
    for f in P_FALSE:
        if p.get(f) is not False: raise ValueError(f"unsafe backend authenticity verifier provenance policy: {f}")
    if p.get("attestation_set_consumption_authority")!="NONE" or p.get("memory_write_authority")!="NONE":
        raise ValueError("backend authenticity verifier provenance authority must remain NONE")
    o=_exact(p.get("output_permissions"),set(OUTPUT_PERMISSIONS),"unsafe backend authenticity verifier provenance output permissions")
    for k,e in OUTPUT_PERMISSIONS.items():
        if o.get(k)!=e or type(o.get(k)) is not type(e): raise ValueError("unsafe backend authenticity verifier provenance output permissions")

def _registry_entry(entry,policy):
    e=_exact(entry,REGISTRY_ENTRY_KEYS,"backend authenticity verifier registry entry key set mismatch")
    out={"verifier_id":_token(e.get("verifier_id"),"verifier_id"),"verifier_key_id":_token(e.get("verifier_key_id"),"verifier_key_id"),
         "verified_public_key_sha256":_sha(e.get("verified_public_key_sha256"),"verified_public_key_sha256"),
         "algorithm":_token(e.get("algorithm"),"algorithm")}
    if out["algorithm"] not in policy["allowed_algorithms"]: raise ValueError("unsupported backend authenticity verifier registry algorithm")
    return out

def _registry(registry,r93b,kw):
    r=_exact(registry,REGISTRY_KEYS,"backend authenticity verifier registry key set mismatch")
    if r.get("schema")!=VERIFIER_REGISTRY_SCHEMA: raise ValueError("unsupported backend authenticity verifier registry schema")
    _token(r.get("registry_id"),"registry_id")
    expected_root=_sha(kw["expected_backend_authenticity_verifier_authority_root_sha256"],"expected_backend_authenticity_verifier_authority_root_sha256")
    root=_sha(r.get("authority_root_sha256"),"backend_authenticity_verifier_authority_root_sha256")
    if root!=expected_root: raise ValueError("backend authenticity verifier authority root digest mismatch")
    if r.get("registry_scope")!="BACKEND_AUTHENTICITY_VERIFIER_PROVENANCE_ONLY": raise ValueError("backend authenticity verifier registry scope invalid")
    if r.get("trust_root_verified") is not False: raise ValueError("backend authenticity verifier registry trust-root overclaim")
    if r.get("confers_authority") is not False: raise ValueError("backend authenticity verifier registry authority overclaim")
    expected_digest=_sha(kw["expected_backend_authenticity_verifier_registry_sha256"],"expected_backend_authenticity_verifier_registry_sha256")
    digest=stable_sha256(r)
    if digest!=expected_digest: raise ValueError("backend authenticity verifier registry digest mismatch")
    rows=r.get("entries")
    if not isinstance(rows,list) or not 1<=len(rows)<=1024: raise ValueError("backend authenticity verifier registry entries invalid")
    rows=[_registry_entry(x,kw["backend_authenticity_verifier_provenance_policy"]) for x in rows]
    digests=[stable_sha256(x) for x in rows]
    if len(set(digests))!=len(digests): raise ValueError("duplicate backend authenticity verifier registry entry")
    target={"verifier_id":r93b["backend_authenticity_verifier_id"],"verifier_key_id":r93b["backend_authenticity_verifier_key_id"],
            "verified_public_key_sha256":r93b["public_key_sha256"],"algorithm":r93b["algorithm"]}
    matches=[x for x in rows if x==target]
    if len(matches)!=1: raise ValueError("R93 backend authenticity verifier registry entry must match exactly once")
    return digest,matches[0]

def _inputs(args,kw):
    if len(args)!=26: raise ValueError("backend authenticity verifier provenance positional input mismatch")
    if set(kw)!=KW: raise ValueError("backend authenticity verifier provenance keyword set mismatch")
    validate_backend_authenticity_verifier_provenance_policy(kw["backend_authenticity_verifier_provenance_policy"])
    r93b=args[0]
    r93.validate_external_assertion_replay_backend_authenticity_assertion_binding(r93b,*args[1:25],**{k:kw[k] for k in UPSTREAM_KW})
    return _registry(args[25],r93b,kw)

def _payload(args,registry,digest,entry,kw):
    r93b=args[0]
    x={k:r93b[k] for k in r93.BINDING_KEYS if k not in {"schema","binding_id"}}
    x.update({
        "schema":BINDING_SCHEMA,
        "r93_binding_id":r93b["binding_id"],"r93_binding_sha256":stable_sha256(r93b),
        "backend_authenticity_verifier_provenance_policy_sha256":stable_sha256(kw["backend_authenticity_verifier_provenance_policy"]),
        "backend_authenticity_verifier_registry_sha256":digest,"backend_authenticity_verifier_registry_digest_consumed":True,
        "backend_authenticity_verifier_registry_id":registry["registry_id"],
        "backend_authenticity_verifier_authority_root_sha256":registry["authority_root_sha256"],
        "backend_authenticity_verifier_authority_root_digest_consumed":True,
        "backend_authenticity_verifier_registry_entry_sha256":stable_sha256(entry),
        "backend_authenticity_verifier_registry_entry_exact_match":True,
        "backend_authenticity_verifier_provenance_bound":True,
        "backend_authenticity_verifier_identity_verified":False,
        "backend_authenticity_verifier_registry_operator_identity_verified":False,
    })
    return x

def _bid(x):
    return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]

def build_external_assertion_replay_backend_authenticity_verifier_provenance_binding(*args,**kw):
    digest,entry=_inputs(args,kw); x=_payload(args,args[25],digest,entry,kw); x["binding_id"]=_bid(x)
    validate_external_assertion_replay_backend_authenticity_verifier_provenance_binding(x,*args,**kw); return x

def validate_external_assertion_replay_backend_authenticity_verifier_provenance_binding(binding,*args,**kw):
    digest,entry=_inputs(args,kw)
    if not isinstance(binding,dict) or set(binding)!=BINDING_KEYS: raise ValueError("backend authenticity verifier provenance binding key set mismatch")
    if binding.get("schema")!=BINDING_SCHEMA: raise ValueError("unsupported backend authenticity verifier provenance binding schema")
    _id(binding.get("binding_id"),"binding_id"); expected=_payload(args,args[25],digest,entry,kw)
    for k,v in expected.items():
        if binding.get(k)!=v or type(binding.get(k)) is not type(v): raise ValueError(f"backend authenticity verifier provenance binding mismatch: {k}")
    if binding["binding_id"]!=_bid(binding): raise ValueError("binding_id binding mismatch")
