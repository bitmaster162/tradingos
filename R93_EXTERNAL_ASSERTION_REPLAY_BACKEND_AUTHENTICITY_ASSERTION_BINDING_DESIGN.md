# TradingOS R93 — External Assertion Replay Backend Authenticity Assertion Binding R1

## Objective

R93 adds a deterministic offline binding layer for one externally verified asymmetric-signature assertion over one exact, fully validated R92 backend-provenance binding.

R92 establishes metadata provenance only. R93 does not perform signature mathematics locally, authenticate a backend, trust a backend root, prove key possession, prove freshness, observe a backend live, or prove durability. It binds one exact externally retained assertion record to one canonical R93 challenge derived from exact R92 evidence.

Pipeline:

`valid exact R92 + canonical R93 challenge + independently retained external authenticity assertion + independently supplied complete assertion SHA-256 + exact R93 policy -> deterministic R93 binding -> human/archive`

## Canonical challenge

The challenge deterministically binds:

- exact R92 binding ID and complete SHA-256;
- exact selected backend-registry entry SHA-256;
- exact backend-authority-root SHA-256;
- exact backend ID, backend key ID, backend metadata SHA-256 and backend kind;
- exact receipt/readback format identifiers;
- exact external commit-receipt SHA-256;
- exact readback-evidence SHA-256;
- exact readback-state SHA-256;
- exact commit ID and idempotency-key SHA-256;
- exact R93 policy SHA-256;
- fixed purpose `R93_BACKEND_AUTHENTICITY_ASSERTION_BINDING_ONLY`.

The challenge contains no nonce or time source. R93 therefore makes no freshness or liveness claim.

## External assertion boundary

R93 accepts one exact externally produced assertion record. Core R93:

- requires its exact bounded schema;
- requires its complete canonical SHA-256 to equal an independently supplied expected digest;
- requires its `challenge_sha256` to equal the canonical R93 challenge digest;
- requires backend ID/key ID/metadata SHA to equal exact R92 metadata;
- requires `commit_signature_verified_by_external_asymmetric_verifier=true`;
- requires `readback_signature_verified_by_external_asymmetric_verifier=true`;
- requires `same_backend_key_claim_bound=true`;
- requires `local_signature_math_verified=false`;
- accepts only a bounded algorithm allowlist from the exact policy;
- binds only public-key/signature metadata digests and identifiers; raw signature bytes and raw public-key bytes are forbidden.

The truthfulness, provenance, trustworthiness and compromise status of the external verifier are outside R93 core. Digest binding does not make a false external assertion true.

## Precise claim

A valid R93 artifact establishes only:

`ONE_EXACT_EXTERNALLY_RETAINED_ASYMMETRIC_VERIFIER_ASSERTION_CLAIMING_COMMIT_AND_READBACK_SIGNATURE_VERIFICATION_UNDER_ONE_CLAIMED_BACKEND_KEY_WAS_DIGEST_BOUND_TO_THE_EXACT_R92_BACKEND_PROVENANCE_BINDING`

It does not establish backend authenticity, readback authenticity, backend key possession, backend identity, verifier trust, backend-root trust, freshness, live observation, durable commit, global current state, or execution permission.

## Authority ceiling

- `backend_authenticity_assertion_bound=true`
- `commit_signature_assertion_bound=true`
- `readback_signature_assertion_bound=true`
- `backend_key_possession_assertion_bound=true`
- `same_backend_key_claim_bound=true`
- `local_signature_math_verified=false`
- `backend_commit_authenticity_verified=false`
- `readback_authenticity_verified=false`
- `backend_key_possession_proven=false`
- `backend_identity_verified=false`
- `backend_trust_root_verified=false`
- `backend_authenticity_verifier_trust_root_verified=false`
- `live_backend_observed=false`
- `durable_commit_proven=false`
- `durable_dual_state_atomicity_proven=false`
- `durable_single_use_enforced=false`
- `global_current_state_verified=false`
- `concurrent_writer_exclusion_proven=false`
- `write_performed=false`
- `backend_write_performed=false`
- `registry_write_performed=false`
- `assertion_freshness_verified=false`
- `liveness_verified=false`
- `shadow_only=true`
- `human_review_only=true`
- `execution_authority=NONE`
- `can_trade=false`
- `capital_permission=DENY`
- `confers_authority=false`

No network/provider transport, credential access, local signature verification, backend lookup/mutation, registry mutation, runtime registration, signal, order, wallet or capital effect exists in R93 core.
