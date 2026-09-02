# TradingOS R95 — Backend Key Provenance Binding R1

## Objective

R95 adds one deterministic offline backend-key provenance layer above one exact, fully validated R94 backend-authenticity-verifier provenance binding.

R94 proves only that exact R93 authenticity-verifier metadata and one claimed verified backend public-key digest matched one exact entry in an externally retained authenticity-verifier registry. R95 does not turn that verifier claim into backend-key truth. It binds the exact R94 backend identity/key metadata and exact claimed backend public-key digest to one unique entry in one exact externally retained backend-key registry snapshot under the exact backend-authority-root digest already bound upstream.

Pipeline:

`valid exact R94 + exact external backend-key registry snapshot + independently supplied registry SHA-256 + exact R95 policy -> deterministic R95 backend-key provenance binding -> human/archive`

## Exact key-to-backend match

The selected backend-key registry entry must match R94 exactly on all five fields:

- `backend_id`;
- `backend_key_id`;
- `public_key_sha256`;
- `algorithm`;
- `backend_metadata_sha256`.

Exactly one matching entry is required. Duplicate exact entries and ambiguous matches fail closed.

This closes only the provenance gap between the key digest claimed in R93/R94 and the already bound backend metadata/key identifier. It does not prove live possession of the private key or the truthfulness of the registry.

## Backend authority-root domain

The backend-key registry must carry `backend_authority_root_sha256` exactly equal to the backend-authority-root digest already bound by R92 and inherited through R94.

R95 intentionally does not introduce a fifth trust-root domain. The key registry is asserted to live under the same backend authority domain as the R92 backend metadata registry. Equality of root digests is bound evidence only; it does not establish that the root is trusted, uncompromised, current, or operated by a verified identity.

Verifier, writer, and authenticity-verifier roots cannot be substituted into the backend-key registry root slot.

## Registry boundary

The backend-key registry is external evidence. R95 core:

- requires its exact bounded schema;
- requires the complete canonical registry SHA-256 to equal an independently supplied expected digest;
- requires the registry root to equal exact inherited `backend_authority_root_sha256`;
- requires `registry_scope=BACKEND_KEY_TO_BACKEND_METADATA_PROVENANCE_ONLY`;
- requires `backend_trust_root_verified=false`;
- requires `backend_registry_operator_identity_verified=false`;
- requires `backend_key_registry_write_performed=false`;
- requires `confers_authority=false`;
- allows only exact upstream algorithm `ED25519`;
- handles only digests/identifiers, never raw public-key or signature bytes;
- performs no registry/backend/network/provider write or lookup.

## Precise claim

A valid R95 artifact establishes only:

`THE_EXACT_R94_CLAIMED_BACKEND_PUBLIC_KEY_DIGEST_AND_BACKEND_KEY_ID_MATCHED_ONE_EXACT_KEY_ENTRY_FOR_THE_EXACT_BACKEND_METADATA_IN_ONE_EXACT_EXTERNALLY_BOUND_BACKEND_KEY_REGISTRY_SNAPSHOT_UNDER_THE_EXACT_ALREADY_BOUND_BACKEND_AUTHORITY_ROOT_DIGEST`

It does not establish:

- backend key possession as ground truth;
- backend identity as ground truth;
- backend trust-root validity;
- backend registry-operator identity;
- authenticity-verifier trust or identity;
- commit or readback authenticity;
- assertion freshness or liveness;
- cryptographic artifact identity beyond the bound digests;
- durable commit or durable atomicity;
- current/global state;
- concurrent-writer exclusion;
- execution/trading/capital authority.

## Authority ceiling

R95 preserves every inherited R94 field exactly and adds only bounded provenance fields:

- `backend_key_registry_digest_consumed=true`;
- `backend_key_registry_authority_root_matches_backend_authority_root=true`;
- `backend_key_registry_entry_exact_match=true`;
- `backend_key_provenance_bound=true`;
- `backend_key_to_backend_metadata_bound=true`;
- `backend_public_key_digest_bound=true`;
- `backend_key_registry_operator_identity_verified=false`;
- `backend_key_registry_write_performed=false`.

Inherited negatives remain unchanged, including:

- `backend_key_possession_proven=false`;
- `backend_identity_verified=false`;
- `backend_trust_root_verified=false`;
- `backend_commit_authenticity_verified=false`;
- `readback_authenticity_verified=false`;
- `backend_authenticity_verifier_trust_root_verified=false`;
- `assertion_freshness_verified=false`;
- `liveness_verified=false`;
- durability/current-state/write flags remain false;
- `execution_authority=NONE`;
- `can_trade=false`;
- `capital_permission=DENY`;
- `confers_authority=false`.

No merge, PR, Actions trigger/rerun, workflow edit, deployment, runtime registration, credential access, network/provider call, backend mutation, registry mutation, signal, order, wallet or capital effect belongs to R95 core.
