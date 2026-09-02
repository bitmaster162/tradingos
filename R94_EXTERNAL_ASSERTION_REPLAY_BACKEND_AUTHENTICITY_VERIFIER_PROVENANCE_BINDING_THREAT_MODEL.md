# TradingOS R94 — Backend Authenticity Verifier Provenance Binding Threat Model R1

## Trust boundaries

1. Exact fully validated R93 binding and complete upstream R84-R92 chain.
2. Exact R94 provenance policy.
3. One exact externally retained backend-authenticity-verifier registry snapshot.
4. Independently supplied SHA-256 of the complete registry snapshot.
5. Independently supplied R94 authenticity-verifier authority-root SHA-256.
6. Human/archive consumer.

## Threats and controls

| Threat | R94 control |
| --- | --- |
| Tampered R93 admitted | full canonical R93 validation before provenance binding |
| Registry snapshot substituted | complete registry must match independently supplied SHA-256 |
| R94 authority root substituted | registry root must match independently supplied R94 authenticity-verifier root digest |
| Old verifier/writer/backend root transplanted into R94 root slot | independent R94 root parameter and exact registry-root equality; fixture roots are cross-domain distinct |
| Wrong verifier ID or verifier key ID | exact R93 metadata match required |
| Wrong claimed verified backend public-key digest | `verified_public_key_sha256` must equal exact R93 `public_key_sha256` |
| Algorithm drift or widening | exact R94 allowlist is `ED25519` only and registry entry must match exact R93 algorithm |
| Duplicate/ambiguous registry match | duplicate exact rows rejected; exactly one target match required |
| Extra hidden registry claims | exact registry and entry key sets; extra fields fail closed |
| Registry/root treated as trusted | trust-root output remains false |
| Registry operator treated as identified | authenticity-verifier registry-operator identity remains false |
| Provenance treated as backend authenticity | commit/readback authenticity outputs remain false |
| Provenance treated as backend key-possession truth | `backend_key_possession_proven=false` |
| Provenance treated as freshness/liveness | freshness and liveness remain false |
| Provenance treated as durable backend truth | durability/current-state/writer-exclusion flags remain false |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risks

R94 cannot establish truthfulness of the R93 external assertion, authenticity-verifier compromise status, authenticity-verifier identity or trust, registry-operator identity, authority-root trust, backend identity, backend key possession as ground truth, freshness/liveness, actual backend observation, durable storage semantics, global current state, or writer exclusion.

A future layer that promotes any of those claims requires separately governed evidence and must not infer them from R94 provenance binding alone.
