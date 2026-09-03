from __future__ import annotations
import copy,hashlib,json
from pathlib import Path
from tools import tradingos_external_derivation_provenance_verification_contract as m

ROOT=Path(__file__).resolve().parents[1]
POLICY=ROOT/"configs"/"TRADINGOS_EXTERNAL_DERIVATION_PROVENANCE_VERIFICATION_POLICY_V1.json"

def h(s): return hashlib.sha256(s.encode()).hexdigest()
def stable(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
def policy(): return json.loads(POLICY.read_text(encoding="utf-8"))

def r99_binding():
    x=dict(m.SAFETY_EXPECTED)
    x.update({
        "schema":m.r99.BINDING_SCHEMA,
        "binding_id":"99"*12,
        "equality_policy_sha256":h("r99 equality policy"),
        "committed_derivation_record_sha256":h("committed derivation record"),
        "readback_derivation_record_sha256":h("readback derivation record"),
        "equality_record_sha256":h("equality record"),
        "committed_source_artifact_sha256":h("committed source artifact"),
        "readback_source_artifact_sha256":h("readback source artifact"),
        "committed_projected_state_sha256":h("projected state"),
        "readback_projected_state_sha256":h("projected state"),
        "projection_schema_id":"TRADINGOS_STATE_PROJECTION_V1",
        "projection_schema_version":1,
        "canonicalization_id":"RFC8785_JSON_V1",
        "canonicalization_version":1,
        "derivation_tool_sha256":h("independent derivation tool v1"),
        "expected_record_digests_bound":True,
        "expected_digest_independence_verified":False,
        "committed_readback_projected_state_equality_bound":True,
        "full_r98_validation_consumed":True,
        "full_r98_safety_ceiling_preserved":True,
    })
    return x

def provenance_record(r99b=None):
    b=r99_binding() if r99b is None else r99b
    return {
        "schema":m.PROVENANCE_RECORD_SCHEMA,
        "record_id":"a0"*12,
        "r99_binding_id":b["binding_id"],
        "r99_binding_sha256":stable(b),
        "committed_derivation_record_sha256":b["committed_derivation_record_sha256"],
        "readback_derivation_record_sha256":b["readback_derivation_record_sha256"],
        "equality_record_sha256":b["equality_record_sha256"],
        "committed_source_artifact_sha256":b["committed_source_artifact_sha256"],
        "readback_source_artifact_sha256":b["readback_source_artifact_sha256"],
        "projection_schema_id":b["projection_schema_id"],
        "projection_schema_version":b["projection_schema_version"],
        "canonicalization_id":b["canonicalization_id"],
        "canonicalization_version":b["canonicalization_version"],
        "derivation_tool_sha256":b["derivation_tool_sha256"],
        "recomputation_method":"SHA256_CANONICAL_RECORD_RECOMPUTATION",
        "verifier_id":"external-derivation-verifier-r100",
        "verifier_key_id":"external-derivation-verifier-key-r100",
        "committed_derivation_digest_recomputed":True,
        "readback_derivation_digest_recomputed":True,
        "equality_record_digest_recomputed":True,
        "projected_state_equality_recomputed":True,
        "out_of_process_recomputation_claimed":True,
        "verification_scope":"R99_DERIVATION_DIGEST_AND_PROJECTED_EQUALITY_RECOMPUTATION_ASSERTION_ONLY",
        "confers_authority":False,
    }

def args(r99b=None,record=None):
    b=r99_binding() if r99b is None else r99b
    r=provenance_record(b) if record is None else record
    return (b,*([None]*38),r)

def kw(record=None,policy_override=None,expected_record_sha=None):
    b=r99_binding()
    r=provenance_record(b) if record is None else record
    out={k:None for k in m.r99.KW}
    out.update(
        expected_external_derivation_provenance_verification_record_sha256=stable(r) if expected_record_sha is None else expected_record_sha,
        external_derivation_provenance_verification_policy=policy() if policy_override is None else policy_override,
    )
    return out

def clone(v): return copy.deepcopy(v)
