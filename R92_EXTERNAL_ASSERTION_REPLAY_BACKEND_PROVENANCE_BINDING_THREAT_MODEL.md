# TradingOS R92 — Backend Provenance Binding Threat Model R1

## Trust boundaries

1. Exact fully validated R91 binding and its complete upstream chain.
2. Exact R92 policy.
3. One externally retained backend-registry snapshot.
4. Independently supplied SHA-256 of the complete registry snapshot.
5. Independently supplied backend-authority-root SHA-256.
6. One externally retained backend-provenance verification record.
7. Independently supplied SHA-256 of the complete provenance record.
8. Human/archive consumer.

## Threats and controls

| Threat | R92 control |
| --- | --- |
| Tampered R91 admitted | full canonical R91 validation before provenance binding |
| Registry substituted | complete registry must match independently supplied SHA-256 |
| Backend root substituted | registry root must match independently supplied backend-root SHA-256 |
| Ambiguous backend entry | unique exact metadata match required; duplicate entries rejected |
| Registry ordering ambiguity | canonical sorted entry order required |
| Provenance record substituted | complete record must match independently supplied SHA-256 |
| Different R91 artifacts transplanted | exact R91 ID/SHA and exact commit/readback artifact digests required |
| Different registry entry transplanted | selected entry SHA and all six metadata fields required |
| Commit and readback attributed to different metadata | same exact backend metadata claim required for both |
| Backend metadata match treated as authentication | authenticity flags remain false |
| Backend root treated as trusted | `backend_trust_root_verified=false` |
| Registry operator inferred | `backend_registry_operator_identity_verified=false` |
| Matching metadata treated as live backend observation | `live_backend_observed=false` |
| Provenance treated as durable commit proof | durable flags remain false |
| Evidence interpreted as a write | all write flags remain false |
| Provenance interpreted as globally current | `global_current_state_verified=false` |
| Provenance interpreted as writer exclusion | `concurrent_writer_exclusion_proven=false` |
| Root-domain aliasing | verifier, writer, and backend roots are independent exact inputs |
| Hidden claims | exact key sets; extra fields fail closed |
| Policy weakening | exact policy keys and deny ceiling |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risks

R92 cannot establish backend authenticity, backend-root trust, registry-operator identity, cryptographic authenticity of commit/readback artifacts, live observation, durable storage semantics, transaction commit truth, readback freshness, global current state, or liveness. Those require separately governed external authentication and durability evidence.
