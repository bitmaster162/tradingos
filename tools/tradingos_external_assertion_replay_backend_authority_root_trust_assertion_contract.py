"""TradingOS R97 backend authority-root trust assertion binding."""
from __future__ import annotations
import hashlib,re
from tools import tradingos_external_assertion_replay_backend_authenticity_replay_guard_contract as r96
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes,stable_sha256

BINDING_SCHEMA="tradingos.external_assertion_replay_backend_authority_root_trust_assertion_binding.v1"
TRUST_ASSERTION_SCHEMA="control_center.external_assertion_replay_backend_authority_root_trust_assertion.v1"
CHALLENGE_SCHEMA="tradingos.external_assertion_replay_backend_authority_root_trust_challenge.v1"
POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHORITY_ROOT_TRUST_ASSERTION_POLICY_V1"
VERSION="1.0.0"
_ID=re.compile(r"^[0-9a-f]{24}$"); _SHA=re.compile(r"^[0-9a-f]{64}$")
OUTPUT_PERMISSIONS={"execution_authority":"NONE","signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","confers_authority":False}
POLICY_KEYS=set("""allowed_algorithms attestation_set_consumption_authority backend_authenticity_inference_allowed backend_authority_root_trust_evaluator_identity_inference_allowed backend_authority_root_trust_evaluator_trust_inference_allowed backend_identity_inference_allowed backend_key_possession_inference_allowed backend_registry_write_allowed backend_trust_root_inference_allowed backend_write_allowed credential_access_in_core_allowed durable_commit_inference_allowed durable_dual_state_atomicity_inference_allowed durable_single_use_inference_allowed external_trust_assertion_input_allowed freshness_inference_allowed human_review_only input_r96_binding_schema live_decision_feedback_allowed live_decision_use_allowed liveness_inference_allowed local_trust_evaluation_allowed memory_write_authority mode model_selection_use_allowed network_access_in_core_allowed output_permissions persistence_in_core_allowed policy_id policy_update_allowed raw_public_key_bytes_in_core_allowed raw_signature_bytes_in_core_allowed readback_authenticity_inference_allowed recommendations_allowed registry_write_allowed require_backend_authority_root_trust_asserted require_exact_backend_authority_root_from_r96 require_exact_backend_key_registry_digest_from_r96 require_exact_backend_registry_digest_from_r96 require_exact_challenge_digest require_expected_external_trust_assertion_digest require_external_trust_assertion require_full_r96_validation require_local_trust_evaluation_false schema_version shadow_only trust_assertion_freshness_inference_allowed trust_assertion_schema""".split())
P_TRUE=set("""external_trust_assertion_input_allowed human_review_only require_backend_authority_root_trust_asserted require_exact_backend_authority_root_from_r96 require_exact_backend_key_registry_digest_from_r96 require_exact_backend_registry_digest_from_r96 require_exact_challenge_digest require_expected_external_trust_assertion_digest require_external_trust_assertion require_full_r96_validation require_local_trust_evaluation_false shadow_only""".split())
P_FALSE=POLICY_KEYS-P_TRUE-{"allowed_algorithms","attestation_set_consumption_authority","input_r96_binding_schema","memory_write_authority","mode","output_permissions","policy_id","schema_version","trust_assertion_schema"}
CHALLENGE_KEYS=set("""schema purpose r96_binding_id r96_binding_sha256 backend_authority_root_sha256 backend_registry_sha256 backend_key_registry_sha256 selected_backend_entry_sha256 backend_key_registry_entry_sha256 backend_id backend_key_id backend_metadata_sha256 public_key_sha256 algorithm policy_sha256""".split())
ASSERTION_KEYS=set("""schema assertion_id challenge_sha256 backend_authority_root_sha256 backend_registry_sha256 backend_key_registry_sha256 backend_id backend_key_id trust_evaluator_id trust_evaluator_key_id algorithm backend_authority_root_trust_asserted local_trust_evaluation_performed assertion_scope confers_authority""".split())
R97_FIELDS=set("""schema binding_id r96_binding_id r96_binding_sha256 backend_authority_root_trust_assertion_policy_sha256 backend_authority_root_trust_challenge_sha256 backend_authority_root_trust_challenge_bound backend_authority_root_trust_assertion_sha256 backend_authority_root_trust_assertion_digest_consumed backend_authority_root_trust_assertion_id backend_authority_root_trust_assertion_scope backend_authority_root_trust_evaluator_id backend_authority_root_trust_evaluator_key_id backend_authority_root_trust_evaluator_algorithm backend_authority_root_trust_assertion_bound backend_authority_root_trust_asserted_by_external_evaluator backend_authority_root_trust_evaluator_identity_verified backend_authority_root_trust_evaluator_trust_root_verified backend_authority_root_trust_assertion_freshness_verified local_backend_authority_root_trust_evaluation_performed""".split())
BINDING_KEYS=(set(r96.BINDING_KEYS)-{"schema","binding_id"})|R97_FIELDS
UPSTREAM_KW=set(r96.KW)
KW=UPSTREAM_KW|{"expected_backend_authority_root_trust_assertion_sha256","backend_authority_root_trust_assertion_policy"}
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
def validate_backend_authority_root_trust_assertion_policy(policy):
    p=_exact(policy,POLICY_KEYS,"backend authority-root trust assertion policy key set mismatch")
    if type(p.get("schema_version")) is not int or p["schema_version"]!=1 or p.get("policy_id")!=POLICY_ID: raise ValueError("unsupported backend authority-root trust assertion policy")
    if p.get("mode")!="OFFLINE_EXTERNAL_ASSERTION_REPLAY_BACKEND_AUTHORITY_ROOT_TRUST_ASSERTION_BINDING_ONLY": raise ValueError("backend authority-root trust assertion policy mode drift")
    if p.get("input_r96_binding_schema")!=r96.BINDING_SCHEMA: raise ValueError("input R96 binding schema drift")
    if p.get("trust_assertion_schema")!=TRUST_ASSERTION_SCHEMA: raise ValueError("backend authority-root trust assertion schema drift")
    if p.get("allowed_algorithms")!=["ED25519"]: raise ValueError("backend authority-root trust assertion algorithm allowlist drift")
    for f in P_TRUE:
        if p.get(f) is not True: raise ValueError(f"required backend authority-root trust assertion guard disabled: {f}")
    for f in P_FALSE:
        if p.get(f) is not False: raise ValueError(f"unsafe backend authority-root trust assertion policy: {f}")
    if p.get("attestation_set_consumption_authority")!="NONE" or p.get("memory_write_authority")!="NONE": raise ValueError("backend authority-root trust assertion authority must remain NONE")
    o=_exact(p.get("output_permissions"),set(OUTPUT_PERMISSIONS),"unsafe backend authority-root trust assertion output permissions")
    for k,e in OUTPUT_PERMISSIONS.items():
        if o.get(k)!=e or type(o.get(k)) is not type(e): raise ValueError("unsafe backend authority-root trust assertion output permissions")
def build_backend_authority_root_trust_challenge(r96_binding,policy):
    validate_backend_authority_root_trust_assertion_policy(policy)
    return {"schema":CHALLENGE_SCHEMA,"purpose":"R97_BACKEND_AUTHORITY_ROOT_TRUST_ASSERTION_BINDING_ONLY","r96_binding_id":_id(r96_binding.get("binding_id"),"r96_binding_id"),"r96_binding_sha256":stable_sha256(r96_binding),"backend_authority_root_sha256":_sha(r96_binding.get("backend_authority_root_sha256"),"backend_authority_root_sha256"),"backend_registry_sha256":_sha(r96_binding.get("backend_registry_sha256"),"backend_registry_sha256"),"backend_key_registry_sha256":_sha(r96_binding.get("backend_key_registry_sha256"),"backend_key_registry_sha256"),"selected_backend_entry_sha256":_sha(r96_binding.get("selected_backend_entry_sha256"),"selected_backend_entry_sha256"),"backend_key_registry_entry_sha256":_sha(r96_binding.get("backend_key_registry_entry_sha256"),"backend_key_registry_entry_sha256"),"backend_id":_token(r96_binding.get("backend_id"),"backend_id"),"backend_key_id":_token(r96_binding.get("backend_key_id"),"backend_key_id"),"backend_metadata_sha256":_sha(r96_binding.get("backend_metadata_sha256"),"backend_metadata_sha256"),"public_key_sha256":_sha(r96_binding.get("public_key_sha256"),"public_key_sha256"),"algorithm":_token(r96_binding.get("algorithm"),"algorithm"),"policy_sha256":stable_sha256(policy)}
def _assertion(assertion,r96b,challenge,kw):
    a=_exact(assertion,ASSERTION_KEYS,"backend authority-root trust assertion key set mismatch")
    if a.get("schema")!=TRUST_ASSERTION_SCHEMA: raise ValueError("unsupported backend authority-root trust assertion schema")
    _id(a.get("assertion_id"),"assertion_id")
    if _sha(a.get("challenge_sha256"),"challenge_sha256")!=stable_sha256(challenge): raise ValueError("backend authority-root trust challenge digest mismatch")
    for field in ("backend_authority_root_sha256","backend_registry_sha256","backend_key_registry_sha256"):
        if _sha(a.get(field),field)!=r96b[field]: raise ValueError(f"backend authority-root trust assertion mismatch: {field}")
    for field in ("backend_id","backend_key_id"):
        if _token(a.get(field),field)!=r96b[field]: raise ValueError(f"backend authority-root trust assertion mismatch: {field}")
    if _token(a.get("algorithm"),"algorithm")!=r96b["algorithm"] or a["algorithm"] not in kw["backend_authority_root_trust_assertion_policy"]["allowed_algorithms"]: raise ValueError("backend authority-root trust assertion algorithm mismatch")
    _token(a.get("trust_evaluator_id"),"trust_evaluator_id"); _token(a.get("trust_evaluator_key_id"),"trust_evaluator_key_id")
    if a.get("backend_authority_root_trust_asserted") is not True: raise ValueError("backend authority-root trust assertion must assert trust")
    if a.get("local_trust_evaluation_performed") is not False: raise ValueError("local backend authority-root trust evaluation forbidden")
    if a.get("assertion_scope")!="BACKEND_AUTHORITY_ROOT_TRUST_ASSERTION_ONLY": raise ValueError("backend authority-root trust assertion scope invalid")
    if a.get("confers_authority") is not False: raise ValueError("backend authority-root trust assertion authority overclaim")
    expected=_sha(kw["expected_backend_authority_root_trust_assertion_sha256"],"expected_backend_authority_root_trust_assertion_sha256"); digest=stable_sha256(a)
    if digest!=expected: raise ValueError("backend authority-root trust assertion digest mismatch")
    return digest,a
def _inputs(args,kw):
    if len(args)!=32: raise ValueError("backend authority-root trust assertion positional input mismatch")
    if set(kw)!=KW: raise ValueError("backend authority-root trust assertion keyword set mismatch")
    p=kw["backend_authority_root_trust_assertion_policy"]; validate_backend_authority_root_trust_assertion_policy(p); r96b=args[0]
    r96.validate_external_assertion_replay_backend_authenticity_replay_guard_binding(r96b,*args[1:31],**{k:kw[k] for k in UPSTREAM_KW})
    challenge=build_backend_authority_root_trust_challenge(r96b,p)
    if set(challenge)!=CHALLENGE_KEYS: raise ValueError("backend authority-root trust challenge key set mismatch")
    digest,a=_assertion(args[31],r96b,challenge,kw); return challenge,digest,a
def _payload(args,challenge,digest,a,kw):
    r96b=args[0]; x={k:r96b[k] for k in r96.BINDING_KEYS if k not in {"schema","binding_id"}}
    x.update({"schema":BINDING_SCHEMA,"r96_binding_id":r96b["binding_id"],"r96_binding_sha256":stable_sha256(r96b),"backend_authority_root_trust_assertion_policy_sha256":stable_sha256(kw["backend_authority_root_trust_assertion_policy"]),"backend_authority_root_trust_challenge_sha256":stable_sha256(challenge),"backend_authority_root_trust_challenge_bound":True,"backend_authority_root_trust_assertion_sha256":digest,"backend_authority_root_trust_assertion_digest_consumed":True,"backend_authority_root_trust_assertion_id":a["assertion_id"],"backend_authority_root_trust_assertion_scope":a["assertion_scope"],"backend_authority_root_trust_evaluator_id":a["trust_evaluator_id"],"backend_authority_root_trust_evaluator_key_id":a["trust_evaluator_key_id"],"backend_authority_root_trust_evaluator_algorithm":a["algorithm"],"backend_authority_root_trust_assertion_bound":True,"backend_authority_root_trust_asserted_by_external_evaluator":True,"backend_authority_root_trust_evaluator_identity_verified":False,"backend_authority_root_trust_evaluator_trust_root_verified":False,"backend_authority_root_trust_assertion_freshness_verified":False,"local_backend_authority_root_trust_evaluation_performed":False})
    return x
def _bid(x): return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]
def build_external_assertion_replay_backend_authority_root_trust_assertion_binding(*args,**kw):
    challenge,digest,a=_inputs(args,kw); x=_payload(args,challenge,digest,a,kw); x["binding_id"]=_bid(x); validate_external_assertion_replay_backend_authority_root_trust_assertion_binding(x,*args,**kw); return x
def validate_external_assertion_replay_backend_authority_root_trust_assertion_binding(binding,*args,**kw):
    challenge,digest,a=_inputs(args,kw)
    if not isinstance(binding,dict) or set(binding)!=BINDING_KEYS: raise ValueError("backend authority-root trust assertion binding key set mismatch")
    if binding.get("schema")!=BINDING_SCHEMA: raise ValueError("unsupported backend authority-root trust assertion binding schema")
    _id(binding.get("binding_id"),"binding_id"); expected=_payload(args,challenge,digest,a,kw)
    for k,v in expected.items():
        if binding.get(k)!=v or type(binding.get(k)) is not type(v): raise ValueError(f"backend authority-root trust assertion binding mismatch: {k}")
    if binding["binding_id"]!=_bid(binding): raise ValueError("binding_id binding mismatch")
