from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from r83_attestation_set_fixtures import evidence_items, set_policy as r83_set_policy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
POLICY = ROOT / "configs" / "TRADINGOS_REVIEWER_KEY_POSSESSION_ASSERTION_BINDING_POLICY_V1.json"
CONTRACT = ROOT / "tools" / "tradingos_reviewer_key_possession_contract.py"

s = importlib.util.spec_from_file_location("r84c", CONTRACT)
assert s and s.loader
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


def policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def upstream():
    items = evidence_items()
    manifest = m.r83.build_attestation_evidence_set(items, r83_set_policy())
    return items, manifest


def attestation_id():
    _, manifest = upstream()
    return manifest["bindings"][0]["attestation_id"]


def challenge(aid=None):
    items, manifest = upstream()
    if aid is None:
        aid = manifest["bindings"][0]["attestation_id"]
    return m.build_reviewer_key_possession_challenge(
        manifest, items, r83_set_policy(), aid, policy()
    )


def external_assertion(aid=None, public_key_sha256="a" * 64):
    c = challenge(aid)
    return {
        "schema": m.EXTERNAL_ASSERTION_SCHEMA,
        "challenge_sha256": m.stable_sha256(c),
        "public_key_sha256": public_key_sha256,
        "key_id": "review-key-01",
        "algorithm": "ED25519",
        "verifier_id": "offline-verifier-01",
        "verifier_key_id": "verifier-key-01",
        "signature_verified_by_external_asymmetric_verifier": True,
        "local_signature_math_verified": False,
        "assertion_scope": "REVIEWER_KEY_POSSESSION_ONLY",
        "review_identity_verified": False,
        "physical_human_presence_proven": False,
        "confers_authority": False,
    }


def binding(aid=None, public_key_sha256="a" * 64):
    items, manifest = upstream()
    if aid is None:
        aid = manifest["bindings"][0]["attestation_id"]
    assertion = external_assertion(aid, public_key_sha256)
    return m.build_reviewer_key_possession_binding(
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        expected_external_assertion_sha256=m.stable_sha256(assertion),
        key_possession_policy=policy(),
    )


def clone(value):
    return copy.deepcopy(value)
