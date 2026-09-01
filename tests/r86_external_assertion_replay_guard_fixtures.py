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
from r85_external_verifier_provenance_fixtures import (
    AUTHORITY_ROOT_SHA256,
    m as r85m,
    provenance_policy,
    verifier_registry,
)

HERE = Path(__file__).resolve().parent
POLICY = ROOT / "configs" / "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_GUARD_POLICY_V1.json"
CONTRACT = ROOT / "tools" / "tradingos_external_assertion_replay_guard_contract.py"

s = importlib.util.spec_from_file_location("r86c", CONTRACT)
assert s and s.loader
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


def replay_policy():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def r85_context():
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
    registry = verifier_registry(r84_binding)
    r85_binding = r85m.build_external_verifier_provenance_binding(
        r84_binding,
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        registry,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(registry),
        expected_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
    )
    return items, manifest, aid, assertion, r84_binding, registry, r85_binding


def replay_registry(r84_binding=None):
    if r84_binding is None:
        *_, r84_binding, _, _ = r85_context()
    return {
        "schema": m.REPLAY_REGISTRY_SCHEMA,
        "registry_id": "offline-replay-registry-r86-01",
        "generation": 7,
        "previous_registry_sha256": "9" * 64,
        "used_external_assertion_sha256s": ["1" * 64, "2" * 64],
        "used_challenge_sha256s": ["3" * 64, "4" * 64],
        "registry_scope": "EXTERNAL_ASSERTION_AND_CHALLENGE_REPLAY_GUARD_ONLY",
        "durable_commit_proven": False,
        "write_allowed": False,
        "apply_allowed": False,
        "confers_authority": False,
    }


def build(registry_snapshot=None):
    items, manifest, aid, assertion, r84_binding, verifier_reg, r85_binding = r85_context()
    registry_snapshot = replay_registry(r84_binding) if registry_snapshot is None else registry_snapshot
    return m.build_external_assertion_replay_guard_binding(
        r85_binding,
        r84_binding,
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        verifier_reg,
        registry_snapshot,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=m.stable_sha256(registry_snapshot),
        replay_guard_policy=replay_policy(),
    )


def clone(value):
    return copy.deepcopy(value)
