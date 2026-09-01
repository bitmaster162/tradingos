# TradingOS R91 — Durable Commit Readback Evidence Binding Threat Model R1

## Trust boundaries

1. Exact fully validated R90 binding and its complete upstream chain.
2. Exact R91 policy.
3. One externally retained commit/readback evidence record.
4. Independently supplied expected SHA-256 of the complete evidence record.
5. Independently retained references to the external commit receipt and readback artifacts.
6. Human/archive consumer.

## Threats and controls

| Threat | R91 control |
| --- | --- |
| Tampered R90 admitted | full canonical R90 validation before R91 evidence binding |
| Evidence record substituted | complete evidence record must match independently supplied expected SHA-256 |
| Different R90 transition transplanted | exact R90 binding ID/SHA, anchor, lease, lineage and prior index required |
| Different receipt-index claimed | commit-receipt and readback receipt-index must both equal R90 next candidate |
| Different operation replayed | exact R90 commit ID and idempotency-key SHA required |
| Commit receipt silently treated as authentic | `backend_commit_authenticity_verified=false` |
| Readback silently treated as authenticated backend observation | `backend_identity_verified=false`, `live_backend_observed=false` |
| Matching readback silently upgraded to durable commit | `durable_commit_proven=false` |
| Atomicity silently upgraded to durable | `durable_dual_state_atomicity_proven=false` |
| Evidence interpreted as a write by R91 | all write-performed flags remain false |
| Evidence interpreted as globally current | `global_current_state_verified=false` |
| Evidence interpreted as writer exclusion | `concurrent_writer_exclusion_proven=false` |
| Root-domain aliasing | verifier and writer roots are independent exact inputs |
| Hidden authority fields | exact key sets; extra fields fail closed |
| Policy weakening | exact policy keys and fixed deny ceiling |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risks

R91 cannot establish backend authenticity, backend identity, durable storage semantics, transaction commit truth, readback freshness, global current state, or liveness. Those require a separately governed external trust/authentication boundary and cannot be inferred from matching retained digests alone.
