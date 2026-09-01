# TradingOS R87 — External Assertion Replay Atomic CAS Binding Threat Model R1

## Trust boundaries

1. Canonical R81-R85 review evidence validated through R86.
2. Exact R86 replay policy, replay snapshot, deterministic next-state candidate digest, and exact R86 binding.
3. Exact R87 atomic-CAS policy.
4. Externally produced atomic/CAS verification record.
5. Independently supplied expected SHA-256 of the complete external verification record.
6. R87 deterministic verifier/builder.
7. Human/archive consumer.

## Threats and R1 controls

| Threat | R1 control |
| --- | --- |
| Invalid/substituted R86 evidence admitted | full canonical R86 validation before atomic/CAS binding |
| R86 binding changed | complete exact R86 SHA-256 carried in R87 |
| External atomic record changed after retention | complete record SHA-256 must equal independently supplied expected digest |
| Atomic record transplanted to another R86 candidate | exact R86 id/SHA, prior registry SHA, next candidate SHA, assertion SHA and challenge SHA must match |
| Wrong generation transition | exact `from`/`to` equality to R86 plus `to = from + 1` |
| CAS semantics silently weakened | fixed `COMPARE_AND_SWAP_PRECONDITION` |
| Atomic record claims durable commit | mandatory `PROTOCOL_VERIFIED_NO_DURABLE_COMMIT`; commit/write/durable flags false |
| Candidate silently upgraded to durable single-use | output `durable_single_use_enforced=false` |
| Snapshot silently treated as globally current | `global_current_state_verified=false` |
| CAS candidate silently treated as real writer exclusion | `concurrent_writer_exclusion_proven=false` |
| Atomic evidence treated as freshness/liveness | both remain false |
| Atomic evidence upgraded to verifier trust or reviewer identity | trust and identity remain false |
| Consensus/approval backdoor | no vote/count/quorum/consensus/approval semantics; hard-denied |
| Policy/model/live-decision update | hard-denied |
| Registry write/persistence side effect | core has no filesystem/database/network/provider/credential client |
| Output mutation | deterministic `binding_id` over complete output except the ID itself |
| Execution escalation | exact NONE / DENY / false ceiling |

## Residual trust and deliberately deferred

R87 binds an externally retained atomic/CAS verification record; it does not itself contact or authenticate a storage backend. Therefore it does not prove that a globally current writer state was observed, that a durable commit happened, or that concurrent writers were excluded in reality.

A future stage may bind lease/fencing or durable commit receipts and independent read-after-write evidence. Any actual writer effect, durable single-use enforcement, freshness/liveness upgrade, identity, consensus/approval, or live/model/policy use requires a separately designed and separately authorized boundary.
