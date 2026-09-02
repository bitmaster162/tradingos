# TradingOS R94 — Backend Authenticity Verifier Provenance Binding R1

## Objective

R94 adds one deterministic offline provenance-binding layer above one exact, fully validated R93 backend-authenticity assertion binding.

R93 binds an exact external assertion claiming that commit and readback signatures were verified under one claimed backend public key. R94 does not make that assertion true and does not authenticate the backend or the external verifier. It binds the exact R93 authenticity-verifier metadata and exact claimed verified backend public-key digest to one unique entry in one exact externally retained authenticity-verifier registry snapshot under one independently supplied authenticity-verifier authority-root digest.

Pipeline:

`valid exact R93 + exact external authenticity-verifier registry snapshot + independently supplied registry SHA-256 + independently supplied authenticity-verifier authority-root SHA-256 + exact R94 policy -> deterministic R94 provenance binding -> human/archive`

## Exact provenance match

The selected registry entry must match R93 exactly on all four fields:

- `verifier_id == R93.backend_authenticity_verifier_id`;
- `verifier_key_id == R93.backend_authenticity_verifier_key_id`;
- `verified_public_key_sha256 == R93.public_key_sha256`;
- `algorithm == R93.algorithm`.

`verified_public_key_sha256` is the backend public-key digest that the external verifier claimed to verify against. It is not the external verifier's own signing-key digest and it does not prove backend key possession.

Exactly one matching registry entry is required. Duplicate entries and ambiguous exact matches fail closed.

R94 also binds:

- exact R93 binding ID and complete SHA-256;
- SHA-256 of the complete authenticity-verifier registry snapshot;
- SHA-256 of the exact selected registry entry;
- independently supplied `backend_authenticity_verifier_authority_root_sha256`;
- SHA-256 of the exact R94 provenance policy.

## Independent trust domain

`backend_authenticity_verifier_authority_root_sha256` is a separate semantic trust domain from:

- `verifier_authority_root_sha256`;
- `writer_authority_root_sha256`;
- `backend_authority_root_sha256`.

R94 never aliases these parameters. Supplying one existing root in the R94 authenticity-verifier-root slot does not satisfy a registry whose exact retained root digest is different.

Digest binding does not prove any root is trustworthy.

## Registry boundary

The registry is external evidence. R94 core:

- requires its exact bounded schema;
- requires its complete canonical SHA-256 to equal an independently supplied expected registry digest;
- requires its `authority_root_sha256` to equal the independently supplied R94 authenticity-verifier root digest;
- requires `registry_scope=BACKEND_AUTHENTICITY_VERIFIER_PROVENANCE_ONLY`;
- requires `trust_root_verified=false`;
- requires `confers_authority=false`;
- allows only the exact upstream algorithm set `ED25519`;
- performs no registry/backend/network/provider write or lookup.

## Precise claim

A valid R94 artifact establishes only:

`THE_EXACT_R93_BACKEND_AUTHENTICITY_VERIFIER_METADATA_AND_CLAIMED_VERIFIED_BACKEND_PUBLIC_KEY_DIGEST_MATCHED_ONE_EXACT_ENTRY_IN_ONE_EXACT_EXTERNALLY_BOUND_AUTHENTICITY_VERIFIER_REGISTRY_SNAPSHOT_UNDER_ONE_EXACT_EXTERNALLY_BOUND_AUTHENTICITY_VERIFIER_AUTHORITY_ROOT_DIGEST`

It does not establish:

- external-verifier identity as ground truth;
- external-verifier trust;
- authenticity-verifier registry-operator identity;
- backend identity;
- backend key possession;
- backend trust-root validity;
- commit or readback authenticity;
- assertion freshness or liveness;
- durable commit or durable atomicity;
- current/global state;
- concurrent-writer exclusion;
- execution/trading/capital authority.

## Authority ceiling

R94 preserves the complete validated R93 payload fields and adds only provenance-binding evidence. In particular:

- `backend_authenticity_verifier_provenance_bound=true`;
- `backend_authenticity_verifier_registry_entry_exact_match=true`;
- `backend_authenticity_verifier_registry_digest_consumed=true`;
- `backend_authenticity_verifier_authority_root_digest_consumed=true`;
- `backend_authenticity_verifier_identity_verified=false`;
- `backend_authenticity_verifier_registry_operator_identity_verified=false`;
- inherited `backend_authenticity_verifier_trust_root_verified=false`;
- inherited `backend_commit_authenticity_verified=false`;
- inherited `readback_authenticity_verified=false`;
- inherited `backend_key_possession_proven=false`;
- inherited `backend_identity_verified=false`;
- inherited `backend_trust_root_verified=false`;
- inherited `assertion_freshness_verified=false`;
- inherited `liveness_verified=false`;
- inherited durability/current-state/write flags remain false;
- `shadow_only=true`;
- `human_review_only=true`;
- `execution_authority=NONE`;
- `can_trade=false`;
- `capital_permission=DENY`;
- `confers_authority=false`.

No merge, PR, Actions trigger, workflow edit, deployment, runtime registration, credential access, network/provider call, backend mutation, registry mutation, signal, order, wallet or capital effect belongs to R94 core.
