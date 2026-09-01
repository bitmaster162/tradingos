# TradingOS R90 — External Assertion Replay Dual-State Atomicity Binding Threat Model R1

## Trust boundaries

1. Canonical R81-R88 evidence fully validated through R89 R2.
2. Exact R89 writer-authority-anchor policy, writer anchor, separate verifier/writer roots, and R89 binding.
3. Exact R90 dual-state atomicity policy.
4. Externally produced dual-state atomicity verification record.
5. Independently supplied expected SHA-256 of the complete atomicity record.
6. R90 deterministic verifier/builder.
7. Human/archive consumer.

## Threats and R1 controls

| Threat | R1 control |
| --- | --- |
| Invalid/substituted R89 evidence admitted | full R89 R2 validation before dual-state processing |
| Verifier/writer root domains re-aliased | separate root parameters retained end-to-end; both digests carried in R90 |
| Atomicity record changed after retention | complete record digest must equal independently supplied expected digest |
| Atomicity record transplanted to another R89 artifact | exact R89 ID/SHA, authority anchor, writer root, lease and prior receipt index must match |
| Split-state semantics silently accepted | `split_state_rejected=true` mandatory |
| Two independent writes disguised as one transaction | fixed `ONE_TRANSACTION_TWO_LOGICAL_RECORDS` |
| Lease ABA / epoch lineage omitted | both `lease_epoch_lineage_verified=true` and `aba_guard_verified=true` mandatory |
| Receipt-index lineage substituted | exact prior index from R89 and bounded next-index candidate digest |
| Atomicity evidence silently treated as durable | fixed `PROTOCOL_VERIFIED_NO_DURABLE_BACKEND`; write/live/durable fields false |
| Durable single-use/current state/writer exclusion inferred | hard-denied in policy and output |
| Writer/verifier trust inferred | both trust flags remain false |
| Reviewer identity / freshness / liveness inferred | all remain false |
| Consensus/approval backdoor | hard-denied |
| Policy/model/live-decision update | hard-denied |
| Registry/backend side effects | core has no I/O clients and all write permissions are false |
| Output mutation | deterministic binding ID over complete output except the ID itself |
| Execution escalation | exact NONE / DENY / false ceiling |

## Residual trust and deliberately deferred

R90 binds protocol evidence for a two-logical-record atomicity candidate. It does not observe a durable backend, prove a committed transaction, prove globally current state, or prove real writer exclusion.

A future stage may bind durable commit receipts and independent read-after-write evidence. Those remain separately designed and separately authorized boundaries.
