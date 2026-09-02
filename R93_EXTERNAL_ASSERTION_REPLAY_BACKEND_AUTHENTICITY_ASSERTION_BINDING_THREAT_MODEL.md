# TradingOS R93 — Backend Authenticity Assertion Binding Threat Model R1

## Trust boundaries

1. Exact fully validated R92 binding and complete upstream chain.
2. Exact R93 policy.
3. Canonical deterministic R93 challenge.
4. One externally retained asymmetric-verifier assertion.
5. Independently supplied SHA-256 of the complete assertion.
6. Human/archive consumer.

## Threats and controls

| Threat | R93 control |
| --- | --- |
| Tampered R92 admitted | full canonical R92 validation before challenge/assertion binding |
| Challenge transplanted | challenge binds exact R92 SHA/ID, backend-registry snapshot SHA, selected entry, backend metadata and exact artifact digests |
| Assertion substituted | complete assertion must match independently supplied SHA-256 |
| Assertion for another challenge replayed | exact `challenge_sha256` required |
| Different backend/key metadata claimed | backend ID/key ID/metadata SHA must equal exact R92 fields |
| Only one artifact signature claimed | both commit and readback external-verifier assertion flags must be true |
| Different backend keys silently used | `same_backend_key_claim_bound=true` required |
| Local signature math silently claimed | `local_signature_math_verified=false` required |
| Unsupported algorithm | exact policy allowlist |
| Raw cryptographic material injected | exact assertion key set has no raw signature/public-key byte fields |
| External assertion treated as authenticity proof | authenticity outputs remain false |
| External assertion treated as key-possession proof | `backend_key_possession_proven=false` |
| Verifier treated as trusted | `backend_authenticity_verifier_trust_root_verified=false` |
| Backend root treated as trusted | `backend_trust_root_verified=false` |
| No-nonce challenge treated as fresh | `assertion_freshness_verified=false` |
| Assertion treated as live backend observation | `live_backend_observed=false` |
| Assertion treated as durable commit proof | durable flags remain false |
| Hidden claims | exact key sets; extra fields fail closed |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risks

R93 cannot establish truthfulness or provenance of the external verifier assertion, external-verifier trust, backend key compromise status, backend identity, backend-root trust, freshness/liveness, actual backend observation, durable storage semantics, transaction commit truth, global current state, or writer exclusion. Those require separately governed evidence layers.
