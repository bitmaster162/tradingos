from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from r83_attestation_set_fixtures import set_policy as r83_set_policy
from r84_reviewer_key_possession_fixtures import (
    ROOT,
    m as r84m,
    policy as r84_policy,
    upstream,
    external_assertion,
)

HERE = Path(__file__).resolve().parent
POLICY = ROOT / "configs" / "TRADINGOS_EXTERNAL_VERIFIER_PROVENANCE_BINDING_POLICY_V1.json"
CONTRACT = ROOT / "tools" / "tradingos_external_verifier_provenance_contract.py"
AUTHORITY_ROOT_SHA256 = "1" * 64

s = importlib.util.spec_from_file_location("r85c", CONTRACT)
assert s and s.loader
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


def provenance_policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def r84_context():
    items, manifest = upstream()
    aid = manifest["bindings"][0]["attestation_id"]
    assertion = external_assertion(aid)
    r84_binding = r84m.build_reviewer_key_possession_binding(
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
    )
    return items, manifest, aid, assertion, r84_binding


def verifier_registry(r84_binding=None):
    if r84_binding is None:
        *_, r84_binding = r84_context()
    return {
        "schema": m.VERIFIER_REGISTRY_SCHEMA,
        "registry_id": "offline-verifier-registry-r85-01",
        "authority_root_sha256": AUTHORITY_ROOT_SHA256,
        "entries": [
            {
                "verifier_id": r84_binding["verifier_id"],
                "verifier_key_id": r84_binding["verifier_key_id"],
                "public_key_sha256": r84_binding["public_key_sha256"],
                "algorithm": r84_binding["algorithm"],
            },
            {
                "verifier_id": "offline-verifier-02",
                "verifier_key_id": "verifier-key-02",
                "public_key_sha256": "b" * 64,
                "algorithm": "ES256",
            },
        ],
        "registry_scope": "VERIFIER_METADATA_PROVENANCE_ONLY",
        "trust_root_verified": False,
        "confers_authority": False,
    }


def binding(registry_snapshot=None):
    items, manifest, aid, assertion, r84_binding = r84_context()
    registry_snapshot = verifier_registry(r84_binding) if registry_snapshot is None else registry_snapshot
    return m.build_external_verifier_provenance_binding(
        r84_binding,
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        registry_snapshot,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=m.stable_sha256(registry_snapshot),
        expected_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
    )


def clone(value):
    return copy.deepcopy(value)
