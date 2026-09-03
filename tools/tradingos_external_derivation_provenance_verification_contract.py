"""TradingOS R100 external derivation provenance verification binding."""
from __future__ import annotations
import hashlib,re
from tools import tradingos_external_assertion_replay_committed_readback_equality_contract as r99
from tools.tradingos_external_assertion_replay_committed_readback_equality_contract import stable_json_bytes,stable_sha256

BINDING_SCHEMA="tradingos.external_derivation_provenance_verification_binding.v1"
PROVENANCE_RECORD_SCHEMA="control_center.external_derivation_provenance_verification_record.v1"
POLICY_ID="TRADINGOS_EXTERNAL_DERIVATION_PROVENANCE_VERIFICATION_POLICY_V1"
VERSION="1.0.0"
_ID=re.compile(r"^[0-9a-f]{24}$")
_SHA=re.compile(r"^[0-9a-f]{64}$")
OUTPUT_PERMISSIONS={"execution_authority":"NONE","signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","confers_authority":False}

SAFETY_EXPECTED=dict(r99.SAFETY_EXPECTED)
POLICY_KEYS=set("""schema_version policy_id mode input_r99_binding_schema provenance_record_schema allowed_recomputation_methods require_full_r99_validation require_expected_provenance_record_digest require_exact_r99_record_digest_lineage require_exact_source_artifact_lineage require_exact_projection_lineage require_exact_canonicalization_lineage require_exact_derivation_tool_lineage require_out_of_process_recomputation_claims external_provenance_input_allowed expected_digest_independence_inference_allowed external_provenance_digest_independence_inference_allowed external_provenance_retention_inference_allowed verifier_identity_inference_allowed verifier_trust_inference_allowed provider_honesty_inference_allowed durable_commit_inference_allowed global_current_state_inference_allowed concurrent_writer_exclusion_inference_allowed network_access_in_core_allowed credential_access_in_core_allowed persistence_in_core_allowed backend_write_allowed registry_write_allowed live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed policy_update_allowed recommendations_allowed human_review_only shadow_only attestation_set_consumption_authority memory_write_authority output_permissions""".split())
P_TRUE=set("""require_full_r99_validation require_expected_provenance_record_digest require_exact_r99_record_digest_lineage require_exact_source_artifact_lineage require_exact_projection_lineage require_exact_canonicalization_lineage require_exact_derivation_tool_lineage require_out_of_process_recomputation_claims external_provenance_input_allowed human_review_only shadow_only""".split())
P_FALSE=set("""expected_digest_independence_inference_allowed external_provenance_digest_independence_inference_allowed external_provenance_retention_inference_allowed verifier_identity_inference_allowed verifier_trust_inference_allowed provider_honesty_inference_allowed durable_commit_inference_allowed global_current_state_inference_allowed concurrent_writer_exclusion_inference_allowed network_access_in_core_allowed credential_access_in_core_allowed persistence_in_core_allowed backend_write_allowed registry_write_allowed live_decision_feedback_allowed live_decision_use_allowed model_selection_use_allowed policy_update_allowed recommendations_allowed""".split())

RECORD_KEYS=set("""schema record_id r99_binding_id r99_binding_sha256 committed_derivation_record_sha256 readback_derivation_record_sha256 equality_record_sha256 committed_source_artifact_sha256 readback_source_artifact_sha256 projection_schema_id projection_schema_version canonicalization_id canonicalization_version derivation_tool_sha256 recomputation_method verifier_id verifier_key_id committed_derivation_digest_recomputed readback_derivation_digest_recomputed equality_record_digest_recomputed projected_state_equality_recomputed out_of_process_recomputation_claimed verification_scope confers_authority""".split())

R100_FIELDS=set("""binding_id canonicalization_id canonicalization_version committed_derivation_digest_recomputation_claim_bound committed_derivation_record_sha256 committed_projected_state_sha256 committed_readback_projected_state_equality_bound committed_source_artifact_sha256 derivation_tool_sha256 equality_record_digest_recomputation_claim_bound equality_record_sha256 expected_digest_independence_verified expected_provenance_digest_bound expected_record_digests_bound external_derivation_provenance_verification_policy_sha256 external_derivation_provenance_verification_record_digest_consumed external_derivation_provenance_verification_record_id external_derivation_provenance_verification_record_sha256 external_provenance_digest_independence_verified external_provenance_record_retention_verified full_r98_safety_ceiling_preserved full_r98_validation_consumed full_r99_safety_ceiling_preserved full_r99_validation_consumed external_derivation_provenance_record_bound external_derivation_verifier_id external_derivation_verifier_identity_verified external_derivation_verifier_key_id external_derivation_verifier_trust_root_verified out_of_process_recomputation_claim_bound projected_state_equality_recomputation_claim_bound projection_schema_id projection_schema_version provider_honesty_verified r99_binding_id r99_binding_sha256 r99_equality_policy_sha256 readback_derivation_digest_recomputation_claim_bound readback_derivation_record_sha256 readback_projected_state_sha256 readback_source_artifact_sha256 recomputation_method schema""".split())
BINDING_KEYS=set(SAFETY_EXPECTED)|R100_FIELDS
UPSTREAM_KW=set(r99.KW)
KW=UPSTREAM_KW|{"expected_external_derivation_provenance_verification_record_sha256","external_derivation_provenance_verification_policy"}

def _sha(v,n):
    if type(v) is not str or _SHA.fullmatch(v) is None: raise ValueError(f"{n} must be lowercase sha256")
    return v

def _id(v,n):
    if type(v) is not str or _ID.fullmatch(v) is None: raise ValueError(f"{n} invalid")
    return v

def _token(v,n):
    if type(v) is not str or v!=v.strip() or not 1<=len(v)<=128 or any(ord(c)<33 or ord(c)>126 for c in v):
        raise ValueError(f"{n} invalid")
    return v

def _int(v,n):
    if type(v) is not int or not 1<=v<=2147483647: raise ValueError(f"{n} invalid")
    return v

def _exact(v,keys,msg):
    if type(v) is not dict or set(v)!=keys: raise ValueError(msg)
    return v

def validate_external_derivation_provenance_verification_policy(policy):
    p=_exact(policy,POLICY_KEYS,"external derivation provenance policy key set mismatch")
    if type(p.get("schema_version")) is not int or p["schema_version"]!=1 or p.get("policy_id")!=POLICY_ID:
        raise ValueError("unsupported external derivation provenance policy")
    if p.get("mode")!="OFFLINE_EXTERNAL_DERIVATION_PROVENANCE_VERIFICATION_BINDING_ONLY":
        raise ValueError("external derivation provenance policy mode drift")
    if p.get("input_r99_binding_schema")!=r99.BINDING_SCHEMA:
        raise ValueError("input R99 binding schema drift")
    if p.get("provenance_record_schema")!=PROVENANCE_RECORD_SCHEMA:
        raise ValueError("external derivation provenance record schema drift")
    if p.get("allowed_recomputation_methods")!=["SHA256_CANONICAL_RECORD_RECOMPUTATION"]:
        raise ValueError("external derivation provenance recomputation allowlist drift")
    for k in P_TRUE:
        if p.get(k) is not True: raise ValueError(f"required external derivation provenance guard disabled: {k}")
    for k in P_FALSE:
        if p.get(k) is not False: raise ValueError(f"unsafe external derivation provenance policy: {k}")
    if p.get("attestation_set_consumption_authority")!="NONE" or p.get("memory_write_authority")!="NONE":
        raise ValueError("external derivation provenance authority must remain NONE")
    o=_exact(p.get("output_permissions"),set(OUTPUT_PERMISSIONS),"unsafe external derivation provenance output permissions")
    for k,e in OUTPUT_PERMISSIONS.items():
        if o.get(k)!=e or type(o.get(k)) is not type(e):
            raise ValueError("unsafe external derivation provenance output permissions")

def _materialize_safety(r99b):
    if type(r99b) is not dict: raise ValueError("r99 binding")
    out={}
    for k,e in SAFETY_EXPECTED.items():
        if k not in r99b or r99b[k]!=e or type(r99b[k]) is not type(e):
            raise ValueError("r99 safety ceiling drift")
        out[k]=r99b[k]
    return out

def _validate_r99_frontier(r99b):
    required_true=("expected_record_digests_bound","committed_readback_projected_state_equality_bound","full_r98_validation_consumed","full_r98_safety_ceiling_preserved")
    for k in required_true:
        if r99b.get(k) is not True: raise ValueError(f"required R99 frontier invariant missing: {k}")
    if r99b.get("expected_digest_independence_verified") is not False:
        raise ValueError("R99 expected-digest independence must remain unverified")

def _record(record,r99b,kw):
    r=_exact(record,RECORD_KEYS,"external derivation provenance record key set mismatch")
    if r.get("schema")!=PROVENANCE_RECORD_SCHEMA: raise ValueError("unsupported external derivation provenance record schema")
    _id(r.get("record_id"),"record_id")
    if _id(r.get("r99_binding_id"),"r99_binding_id")!=r99b.get("binding_id"): raise ValueError("R99 binding id lineage mismatch")
    if _sha(r.get("r99_binding_sha256"),"r99_binding_sha256")!=stable_sha256(r99b): raise ValueError("R99 binding digest lineage mismatch")
    for k in ("committed_derivation_record_sha256","readback_derivation_record_sha256","equality_record_sha256",
              "committed_source_artifact_sha256","readback_source_artifact_sha256","derivation_tool_sha256"):
        if _sha(r.get(k),k)!=r99b.get(k): raise ValueError(f"external derivation provenance lineage mismatch: {k}")
    for k in ("projection_schema_id","canonicalization_id"):
        if _token(r.get(k),k)!=r99b.get(k): raise ValueError(f"external derivation provenance lineage mismatch: {k}")
    for k in ("projection_schema_version","canonicalization_version"):
        if _int(r.get(k),k)!=r99b.get(k): raise ValueError(f"external derivation provenance lineage mismatch: {k}")
    method=_token(r.get("recomputation_method"),"recomputation_method")
    if method not in kw["external_derivation_provenance_verification_policy"]["allowed_recomputation_methods"]:
        raise ValueError("unsupported recomputation method")
    _token(r.get("verifier_id"),"verifier_id"); _token(r.get("verifier_key_id"),"verifier_key_id")
    for k in ("committed_derivation_digest_recomputed","readback_derivation_digest_recomputed","equality_record_digest_recomputed",
              "projected_state_equality_recomputed","out_of_process_recomputation_claimed"):
        if r.get(k) is not True: raise ValueError(f"required external recomputation claim missing: {k}")
    if r.get("verification_scope")!="R99_DERIVATION_DIGEST_AND_PROJECTED_EQUALITY_RECOMPUTATION_ASSERTION_ONLY":
        raise ValueError("external derivation provenance verification scope invalid")
    if r.get("confers_authority") is not False: raise ValueError("external derivation provenance authority overclaim")
    digest=stable_sha256(r)
    expected=_sha(kw["expected_external_derivation_provenance_verification_record_sha256"],"expected_external_derivation_provenance_verification_record_sha256")
    if digest!=expected: raise ValueError("external derivation provenance record digest mismatch")
    return digest,r

def _inputs(args,kw):
    if len(args)!=40: raise ValueError("external derivation provenance positional input mismatch")
    if set(kw)!=KW: raise ValueError("external derivation provenance keyword set mismatch")
    p=kw["external_derivation_provenance_verification_policy"]
    validate_external_derivation_provenance_verification_policy(p)
    r99b=args[0]
    r99.validate_external_assertion_replay_committed_readback_equality_binding(
        r99b,*args[1:39],**{k:kw[k] for k in UPSTREAM_KW})
    safety=_materialize_safety(r99b)
    _validate_r99_frontier(r99b)
    digest,record=_record(args[39],r99b,kw)
    return r99b,safety,digest,record,p

def _payload(args,kw):
    r99b,safety,digest,record,p=_inputs(args,kw)
    x=dict(safety)
    x.update({
        "schema":BINDING_SCHEMA,
        "r99_binding_id":r99b["binding_id"],
        "r99_binding_sha256":stable_sha256(r99b),
        "r99_equality_policy_sha256":r99b["equality_policy_sha256"],
        "committed_derivation_record_sha256":r99b["committed_derivation_record_sha256"],
        "readback_derivation_record_sha256":r99b["readback_derivation_record_sha256"],
        "equality_record_sha256":r99b["equality_record_sha256"],
        "committed_source_artifact_sha256":r99b["committed_source_artifact_sha256"],
        "readback_source_artifact_sha256":r99b["readback_source_artifact_sha256"],
        "committed_projected_state_sha256":r99b["committed_projected_state_sha256"],
        "readback_projected_state_sha256":r99b["readback_projected_state_sha256"],
        "projection_schema_id":r99b["projection_schema_id"],
        "projection_schema_version":r99b["projection_schema_version"],
        "canonicalization_id":r99b["canonicalization_id"],
        "canonicalization_version":r99b["canonicalization_version"],
        "derivation_tool_sha256":r99b["derivation_tool_sha256"],
        "expected_record_digests_bound":True,
        "expected_digest_independence_verified":False,
        "committed_readback_projected_state_equality_bound":True,
        "full_r98_validation_consumed":True,
        "full_r98_safety_ceiling_preserved":True,
        "full_r99_validation_consumed":True,
        "full_r99_safety_ceiling_preserved":True,
        "external_derivation_provenance_verification_policy_sha256":stable_sha256(p),
        "external_derivation_provenance_verification_record_sha256":digest,
        "external_derivation_provenance_verification_record_digest_consumed":True,
        "external_derivation_provenance_verification_record_id":record["record_id"],
        "external_derivation_provenance_record_bound":True,
        "expected_provenance_digest_bound":True,
        "external_provenance_digest_independence_verified":False,
        "external_provenance_record_retention_verified":False,
        "out_of_process_recomputation_claim_bound":True,
        "committed_derivation_digest_recomputation_claim_bound":True,
        "readback_derivation_digest_recomputation_claim_bound":True,
        "equality_record_digest_recomputation_claim_bound":True,
        "projected_state_equality_recomputation_claim_bound":True,
        "recomputation_method":record["recomputation_method"],
        "external_derivation_verifier_id":record["verifier_id"],
        "external_derivation_verifier_key_id":record["verifier_key_id"],
        "external_derivation_verifier_identity_verified":False,
        "external_derivation_verifier_trust_root_verified":False,
        "provider_honesty_verified":False,
    })
    return x

def _bid(x):
    return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]

def build_external_derivation_provenance_verification_binding(*args,**kw):
    x=_payload(args,kw); x["binding_id"]=_bid(x)
    validate_external_derivation_provenance_verification_binding(x,*args,**kw)
    return x

def validate_external_derivation_provenance_verification_binding(binding,*args,**kw):
    if type(binding) is not dict or set(binding)!=BINDING_KEYS or binding.get("schema")!=BINDING_SCHEMA:
        raise ValueError("external derivation provenance binding key set mismatch")
    _id(binding.get("binding_id"),"binding_id")
    expected=_payload(args,kw)
    for k,v in expected.items():
        if binding.get(k)!=v or type(binding.get(k)) is not type(v):
            raise ValueError(f"external derivation provenance binding mismatch: {k}")
    if binding["binding_id"]!=_bid(binding): raise ValueError("binding_id binding mismatch")
