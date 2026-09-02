from __future__ import annotations
import hashlib,re
from tools import tradingos_external_assertion_replay_backend_authority_root_trust_assertion_contract as r97
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes,stable_sha256
BINDING_SCHEMA="tradingos.external_assertion_replay_cryptographic_artifact_identity_binding.v1";RECORD_SCHEMA="control_center.external_assertion_replay_cryptographic_artifact_identity_record.v1";CHALLENGE_SCHEMA="tradingos.external_assertion_replay_cryptographic_artifact_identity_challenge.v1";POLICY_ID="TRADINGOS_EXTERNAL_ASSERTION_REPLAY_CRYPTOGRAPHIC_ARTIFACT_IDENTITY_POLICY_V1";VERSION="1.0.0";POLICY_SHA256="74a4ba5e6cae3c6fc6814d263c7064de3b6d4bd1fdc32d2dd4805882ba87433c"
I=re.compile(r"^[0-9a-f]{24}$");S=re.compile(r"^[0-9a-f]{64}$");RKH="f0c19cd67238550ffe2c1916bb61f4bf63ce013f402f165d9b4fd22fb345bc1b"
NF=set("""schema binding_id r97_binding_id r97_binding_sha256 cryptographic_artifact_identity_policy_sha256 cryptographic_artifact_identity_challenge_sha256 cryptographic_artifact_identity_challenge_bound cryptographic_artifact_identity_record_sha256 cryptographic_artifact_identity_record_digest_consumed cryptographic_artifact_identity_record_id cryptographic_artifact_identity_scope commit_signature_sha256 readback_signature_sha256 commit_signature_target_sha256 readback_signature_target_sha256 cryptographic_artifact_identity_record_bound commit_signature_artifact_identity_bound readback_signature_artifact_identity_bound public_key_artifact_identity_bound commit_signature_target_identity_bound readback_signature_target_identity_bound local_cryptographic_artifact_verification_performed cryptographic_artifact_bytes_retrieved""".split());BINDING_KEYS=(set(r97.BINDING_KEYS)-{"schema","binding_id"})|NF
UK=set(r97.KW);KW=UK|{"expected_cryptographic_artifact_identity_record_sha256","cryptographic_artifact_identity_policy"}
def _kh(d):return hashlib.sha256("\n".join(sorted(d)).encode()).hexdigest()
def _s(v,n):
 if type(v) is not str or S.fullmatch(v) is None:raise ValueError(n)
 return v
def _i(v,n):
 if type(v) is not str or I.fullmatch(v) is None:raise ValueError(n)
 return v
def _t(v,n):
 if type(v) is not str or v!=v.strip() or not 1<=len(v)<=128 or any(not 33<=ord(c)<=126 for c in v):raise ValueError(n)
 return v
def validate_cryptographic_artifact_identity_policy(p):
 if type(p) is not dict or type(p.get("schema_version")) is not int or p.get("schema_version")!=1 or p.get("policy_id")!=POLICY_ID:raise ValueError("unsupported cryptographic artifact identity policy")
 if stable_sha256(p)!=POLICY_SHA256:raise ValueError("unsafe cryptographic artifact identity policy")
def build_cryptographic_artifact_identity_challenge(b,p):
 validate_cryptographic_artifact_identity_policy(p);c={"schema":CHALLENGE_SCHEMA,"purpose":"R98_CRYPTOGRAPHIC_ARTIFACT_IDENTITY_BINDING_ONLY","r97_binding_id":_i(b.get("binding_id"),"r97_binding_id"),"r97_binding_sha256":stable_sha256(b),"r93_binding_id":_i(b.get("r93_binding_id"),"r93_binding_id"),"backend_authenticity_challenge_sha256":_s(b.get("challenge_sha256"),"challenge_sha256"),"policy_sha256":stable_sha256(p)}
 for k in ("r93_binding_sha256","backend_authenticity_assertion_sha256","public_key_sha256","external_commit_receipt_sha256","readback_evidence_sha256","readback_state_sha256","idempotency_key_sha256"):c[k]=_s(b.get(k),k)
 for k in ("backend_id","backend_key_id","algorithm","commit_id"):c[k]=_t(b.get(k),k)
 return c
def _record(r,b,c,kw):
 if type(r) is not dict or _kh(r)!=RKH:raise ValueError("record key set mismatch")
 if r.get("schema")!=RECORD_SCHEMA:raise ValueError("record schema")
 _i(r.get("record_id"),"record_id")
 if _s(r.get("challenge_sha256"),"challenge_sha256")!=stable_sha256(c):raise ValueError("challenge digest mismatch")
 for k in ("backend_id","backend_key_id"):
  if _t(r.get(k),k)!=b[k]:raise ValueError("backend mismatch")
 if _s(r.get("public_key_sha256"),"public_key_sha256")!=b["public_key_sha256"]:raise ValueError("public key mismatch")
 if _t(r.get("algorithm"),"algorithm")!=b["algorithm"] or r["algorithm"] not in kw["cryptographic_artifact_identity_policy"]["allowed_algorithms"]:raise ValueError("algorithm mismatch")
 cs=_s(r.get("commit_signature_sha256"),"commit_signature_sha256");rs=_s(r.get("readback_signature_sha256"),"readback_signature_sha256")
 for a,z in (("commit_signature_target_sha256","external_commit_receipt_sha256"),("readback_signature_target_sha256","readback_evidence_sha256"),("readback_state_sha256","readback_state_sha256")):
  if _s(r.get(a),a)!=b[z]:raise ValueError("target mismatch")
 if r.get("artifact_scope")!="BACKEND_COMMIT_READBACK_CRYPTOGRAPHIC_ARTIFACT_IDENTITY_ONLY":raise ValueError("scope")
 for k in ("local_signature_math_verified","cryptographic_artifact_bytes_retrieved","backend_commit_authenticity_verified","readback_authenticity_verified","backend_key_possession_proven","backend_identity_verified","confers_authority"):
  if r.get(k) is not False:raise ValueError("overclaim")
 d=stable_sha256(r)
 if d!=_s(kw["expected_cryptographic_artifact_identity_record_sha256"],"expected_record"):raise ValueError("record digest mismatch")
 return d,r,cs,rs
def _in(a,kw):
 if len(a)!=34 or set(kw)!=KW:raise ValueError("inputs")
 p=kw["cryptographic_artifact_identity_policy"];validate_cryptographic_artifact_identity_policy(p);b=a[0];r97.validate_external_assertion_replay_backend_authority_root_trust_assertion_binding(b,*a[1:33],**{k:kw[k] for k in UK});c=build_cryptographic_artifact_identity_challenge(b,p);d,r,cs,rs=_record(a[33],b,c,kw);return c,d,r,cs,rs
TF=("cryptographic_artifact_identity_challenge_bound","cryptographic_artifact_identity_record_digest_consumed","cryptographic_artifact_identity_record_bound","commit_signature_artifact_identity_bound","readback_signature_artifact_identity_bound","public_key_artifact_identity_bound","commit_signature_target_identity_bound","readback_signature_target_identity_bound")
def _p(a,c,d,r,cs,rs,kw):
 b=a[0];x={k:b[k] for k in r97.BINDING_KEYS if k not in {"schema","binding_id"}};x.update({"schema":BINDING_SCHEMA,"r97_binding_id":b["binding_id"],"r97_binding_sha256":stable_sha256(b),"cryptographic_artifact_identity_policy_sha256":stable_sha256(kw["cryptographic_artifact_identity_policy"]),"cryptographic_artifact_identity_challenge_sha256":stable_sha256(c),"cryptographic_artifact_identity_record_sha256":d,"cryptographic_artifact_identity_record_id":r["record_id"],"cryptographic_artifact_identity_scope":r["artifact_scope"],"commit_signature_sha256":cs,"readback_signature_sha256":rs,"commit_signature_target_sha256":r["commit_signature_target_sha256"],"readback_signature_target_sha256":r["readback_signature_target_sha256"],"local_cryptographic_artifact_verification_performed":False,"cryptographic_artifact_bytes_retrieved":False})
 for k in TF:x[k]=True
 return x
def _bid(x):return hashlib.sha256(f"{BINDING_SCHEMA}:{VERSION}:".encode()+stable_json_bytes({k:x[k] for k in BINDING_KEYS if k!="binding_id"})).hexdigest()[:24]
def build_external_assertion_replay_cryptographic_artifact_identity_binding(*a,**kw):
 c,d,r,cs,rs=_in(a,kw);x=_p(a,c,d,r,cs,rs,kw);x["binding_id"]=_bid(x);validate_external_assertion_replay_cryptographic_artifact_identity_binding(x,*a,**kw);return x
def validate_external_assertion_replay_cryptographic_artifact_identity_binding(b,*a,**kw):
 c,d,r,cs,rs=_in(a,kw)
 if type(b) is not dict or set(b)!=BINDING_KEYS or b.get("schema")!=BINDING_SCHEMA:raise ValueError("binding")
 _i(b.get("binding_id"),"binding_id");e=_p(a,c,d,r,cs,rs,kw)
 if any(b.get(k)!=v or type(b.get(k)) is not type(v) for k,v in e.items()) or b["binding_id"]!=_bid(b):raise ValueError("binding mismatch")
