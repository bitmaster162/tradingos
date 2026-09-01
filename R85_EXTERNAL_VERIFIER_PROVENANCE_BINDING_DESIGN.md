# TradingOS R85 — External Verifier Provenance Binding R1

## Objective

R85 adds a deterministic offline provenance-binding layer above one exact, fully validated R84 reviewer key-possession binding.

R84 proves only that an exact external asymmetric-verifier assertion was bound to exact upstream review evidence. R85 does not make that verifier trusted. It binds the verifier metadata already present in R84 to one exact entry in one exact externally retained verifier-registry snapshot and to an independently supplied authority-root digest.

Pipeline:

`valid exact R84 binding + exact external verifier registry snapshot + independently supplied registry SHA-256 + independently supplied authority-root SHA-256 + exact R85 policy -> deterministic R85 provenance binding -> human/archive`

## Exact provenance match

The selected registry entry must match the R84 binding on all four fields:

- `verifier_id`;
- `verifier_key_id`;
- `public_key_sha256`;
- `algorithm`.

Exactly one matching entry is required. Duplicate registry entries and ambiguous matches fail closed.

R85 also binds:

- SHA-256 of the complete validated R84 binding;
- exact `r84_binding_id`;
- exact `evidence_set_id` and `attestation_id` carried by R84;
- SHA-256 of the complete verifier-registry snapshot;
- SHA-256 of the exact selected registry entry;
- exact independently supplied `authority_root_sha256`;
- SHA-256 of the exact R85 provenance policy.

## External registry boundary

The verifier registry is external evidence. R85 core:

- requires its exact bounded schema;
- requires its complete canonical SHA-256 to equal an independently supplied expected registry digest;
- requires its `authority_root_sha256` to equal an independently supplied expected root digest;
- requires `registry_scope=VERIFIER_METADATA_PROVENANCE_ONLY`;
- requires `trust_root_verified=false`;
- requires `confers_authority=false`;
- performs no registry write and no network/provider lookup.

Digest binding protects against substitution after the expected digests are established. It does not prove that the registry operator, registry content, or authority root is trustworthy.

## Precise claim

A valid R85 artifact establishes only:

`THE_EXACT_R84_EXTERNAL_VERIFIER_METADATA_MATCHES_ONE_EXACT_ENTRY_IN_ONE_EXACT_EXTERNALLY_BOUND_VERIFIER_REGISTRY_SNAPSHOT_UNDER_ONE_EXACT_EXTERNALLY_BOUND_AUTHORITY_ROOT_DIGEST`

It does not establish:

- that the verifier is trusted;
- that the authority root is trusted;
- who operated the registry;
- reviewer civil/person identity;
- physical-human presence;
- assertion freshness or liveness;
- distinct-human multiplicity;
- consensus, quorum, approval, recommendation, or execution authority.

## Authority ceiling

R85 remains offline evidence only:

- `shadow_only=true`
- `human_review_only=true`
- `verifier_trust_root_verified=false`
- `registry_operator_identity_verified=false`
- `review_identity_verified=false`
- `physical_human_presence_proven=false`
- `assertion_freshness_verified=false`
- `distinct_reviewer_count_allowed=false`
- `consensus_inference_allowed=false`
- `approval_state_allowed=false`
- `attestation_set_consumption_authority=NONE`
- `memory_write_authority=NONE`
- `policy_update_allowed=false`
- `live_decision_feedback_allowed=false`
- `live_decision_use_allowed=false`
- `model_selection_use_allowed=false`
- `execution_authority=NONE`
- `can_trade=false`
- `capital_permission=DENY`
- `confers_authority=false`

No persistence, registry mutation, network/provider transport, credential access, raw signature/public-key handling, signature generation or verification, process execution, deployment, runtime registration, model selection, signals, orders, wallet, or capital effect exists in R85 core.
