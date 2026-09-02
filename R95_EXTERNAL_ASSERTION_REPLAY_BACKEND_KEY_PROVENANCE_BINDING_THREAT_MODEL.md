# TradingOS R95 — Backend Key Provenance Binding Threat Model R1

## Trust boundaries

1. Exact fully validated R94 binding and complete upstream R84-R93 chain.
2. Exact R95 backend-key provenance policy.
3. One exact externally retained backend-key registry snapshot.
4. Independently supplied SHA-256 of the complete backend-key registry snapshot.
5. Exact inherited backend-authority-root digest.
6. Human/archive consumer.

## Threats and controls

| Threat | R95 control |
| --- | --- |
| Tampered R94 admitted | full canonical R94 validation before key provenance binding |
| Backend-key registry substituted | complete registry must match independently supplied SHA-256 |
| Verifier/writer/authenticity-verifier root transplanted | registry root must equal exact inherited backend-authority root |
| Wrong backend ID | exact R94 `backend_id` match required |
| Wrong backend key ID | exact R94 `backend_key_id` match required |
| Wrong claimed backend public key | exact R94 `public_key_sha256` match required |
| Key mapped to different backend metadata | exact R94 `backend_metadata_sha256` match required |
| Algorithm drift or widening | exact R95 allowlist is `ED25519` and entry must equal R94 algorithm |
| Duplicate/ambiguous key mapping | duplicate rows rejected and exactly one full target match required |
| Extra hidden registry claims | exact registry and entry key sets; extra fields fail closed |
| Registry/root treated as trusted | `backend_trust_root_verified=false` inherited and required on registry |
| Registry operator treated as identified | registry input and output operator-identity flags remain false |
| Key provenance treated as key possession | `backend_key_possession_proven=false` |
| Key provenance treated as backend authenticity | commit/readback authenticity remain false |
| Provenance treated as freshness/liveness | freshness and liveness remain false |
| Provenance treated as durable/current backend truth | durability/current-state/writer-exclusion flags remain false |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risks

R95 cannot establish registry truthfulness, backend authority-root trust, backend registry-operator identity, live private-key possession, backend identity, verifier compromise status, freshness/liveness, actual backend observation, independently derived committed-state equality, durable storage semantics, global current state, or writer exclusion.

A future layer that promotes any of those claims requires separately governed evidence and must not infer them from R95 key provenance alone.
