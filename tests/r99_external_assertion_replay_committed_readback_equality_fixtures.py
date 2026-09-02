from __future__ import annotations
import copy,hashlib,json

def h(s): return hashlib.sha256(s.encode()).hexdigest()
def stable(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()

GOOD_R98={
 "binding_id":"98"*12,
 "commit_id":"provider-commit-20260902-0001",
 "external_commit_receipt_sha256":h("provider commit receipt"),
 "readback_state_sha256":h("provider readback state"),
}

def derivation(role,source,projected=None):
    return {
      "schema":("control_center.committed_state_derivation_record.v1" if role=="COMMITTED_STATE" else "control_center.readback_state_derivation_record.v1"),"record_id":(("c0" if role=="COMMITTED_STATE" else "d0")*12),"source_role":role,"source_artifact_sha256":source,"source_provenance_sha256":h(role+" provenance"),
      "projection_schema_id":"TRADINGOS_STATE_PROJECTION_V1","projection_schema_version":1,"canonicalization_id":"RFC8785_JSON_V1","canonicalization_version":1,"derivation_tool_sha256":h("independent derivation tool v1"),"canonical_projected_state_sha256":projected or h("same projected state")
    }

def equality(r98b,c,r):
    return {
      "schema":"control_center.committed_readback_equality_record.v1","record_id":"e0"*12,"r98_binding_id":r98b["binding_id"],"r98_binding_sha256":stable(r98b),"external_commit_receipt_sha256":r98b["external_commit_receipt_sha256"],"readback_state_sha256":r98b["readback_state_sha256"],
      "committed_derivation_record_sha256":stable(c),"readback_derivation_record_sha256":stable(r),"projection_schema_id":c["projection_schema_id"],"projection_schema_version":c["projection_schema_version"],"canonicalization_id":c["canonicalization_id"],"canonicalization_version":c["canonicalization_version"],"derivation_tool_sha256":c["derivation_tool_sha256"],"committed_projected_state_sha256":c["canonical_projected_state_sha256"],"readback_projected_state_sha256":r["canonical_projected_state_sha256"]
    }

def clone(v): return copy.deepcopy(v)
