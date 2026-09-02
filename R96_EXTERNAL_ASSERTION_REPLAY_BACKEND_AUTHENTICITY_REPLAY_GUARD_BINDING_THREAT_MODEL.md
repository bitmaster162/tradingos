# TradingOS R96 — Backend Authenticity Replay Guard Binding Threat Model R1

## Trust boundaries

1. Exact fully validated R95 binding and complete upstream R84-R94 chain.
2. Exact R96 replay-guard policy.
3. One exact externally retained backend-authenticity replay-registry snapshot.
4. Independently supplied SHA-256 of the complete replay-registry snapshot.
5. Human/archive consumer.

## Threats and controls

| Threat | R96 control |
| --- | --- |
| Tampered R95 admitted | full canonical R95 validation before replay binding |
| Replay snapshot substituted | complete snapshot must match independently supplied SHA-256 |
| Already-used backend-authenticity assertion | exact inherited assertion digest must be absent |
| Already-used backend-authenticity challenge | exact inherited challenge digest must be absent |
| Unsorted/duplicate digest history | exact sorted unique lowercase SHA-256 sets required |
| Generation type confusion / bool-as-int | strict integer generation validation |
| Generation overflow | prior generation bounded so next generation is representable |
| Candidate skips/reuses generation | deterministic `next_generation=prior_generation+1` |
| Hidden replay-registry claims | exact registry key set; extra fields fail closed |
| Snapshot treated as durable/current | durability/write/apply flags must remain false |
| Replay absence treated as freshness | `assertion_freshness_verified=false`; R93 challenge has no nonce/timestamp |
| Replay candidate treated as durable single-use | `durable_single_use_enforced=false` |
| Replay evidence treated as backend authenticity | commit/readback authenticity remain false |
| Replay evidence treated as backend key possession | `backend_key_possession_proven=false` |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risks

R96 cannot establish global replay-registry currentness, durable registry persistence, cross-writer exclusion, freshness/liveness, backend/verifier trust, backend identity, live private-key possession, actual backend observation, cryptographic artifact identity, independently derived committed-state equality, durable storage semantics, or execution authority.

A future layer that promotes freshness requires cryptographically time/nonce-bound evidence rather than merely replay absence. A future layer that promotes authenticity must also separately establish the remaining trust/artifact/state-equality gaps.
