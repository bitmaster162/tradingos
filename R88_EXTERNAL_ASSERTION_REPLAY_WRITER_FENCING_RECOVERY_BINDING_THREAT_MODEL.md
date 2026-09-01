# TradingOS R88 — External Assertion Replay Writer Fencing Recovery Binding Threat Model R1

## Trust boundaries

1. Canonical R81-R86 review/replay evidence validated through R87.
2. Exact R87 atomic-CAS policy, atomic verification record, and exact R87 binding.
3. Exact R88 writer-fencing/recovery policy.
4. Externally produced writer-fencing/crash-recovery verification record.
5. Independently supplied expected SHA-256 of the complete external recovery record.
6. R88 deterministic verifier/builder.
7. Human/archive consumer.

## Threats and R1 controls

| Threat | R1 control |
| --- | --- |
| Invalid/substituted R87 evidence admitted | full canonical R87 validation before recovery binding |
| R87 binding changed | complete exact R87 SHA-256 carried in R88 |
| External recovery record changed after retention | complete record SHA-256 must equal independently supplied expected digest |
| Recovery record transplanted to another R87 transition | exact R87 id/SHA, atomic verification SHA, prior/next registry SHA and CAS generations must match |
| Writer lease substitution inside retained record | exact bounded `writer_lease_sha256` carried into R88; complete retained-record digest prevents post-retention mutation |
| Receipt/index substitution | exact receipt-candidate and current receipt-index SHA-256 carried into R88 |
| Fencing semantics weakened | fixed monotonic fencing-token plus lease-digest model; attempt token may not exceed current token |
| Stale writer not actually fenced in record semantics | stale status requires attempt token lower than current token |
| Blind retry after uncertain outcome | `blind_retry_allowed=false`; retry status is limited to fresh-CAS reacquisition |
| Split-brain same-token acceptance | `split_brain_same_token_rejected=true` |
| Recovery outcome smuggles an unbounded action | exact bounded status -> action map |
| Recovery evidence silently treated as live backend proof | `live_writer_backend_proven=false` |
| Recovery evidence silently treated as durable commit | `durable_commit_proven=false` and all write flags false |
| Candidate silently upgraded to durable single-use/current state/real writer exclusion | all remain false |
| Recovery evidence treated as freshness/liveness | both remain false |
| Recovery evidence upgraded to verifier trust/reviewer identity | trust and identity remain false |
| Consensus/approval backdoor | no vote/count/quorum/consensus/approval semantics; hard-denied |
| Policy/model/live-decision update | hard-denied |
| Core side effect | no filesystem/database/network/provider/credential client |
| Output mutation | deterministic `binding_id` over complete output except the ID itself |
| Execution escalation | exact NONE / DENY / false ceiling |

## Residual trust and deliberately deferred

R88 binds a retained external writer-fencing/crash-recovery verification record; it does not itself contact, authenticate, lease, fence, read from, or write to a storage backend. Therefore it does not prove a live authoritative writer lease, global current state, real concurrent-writer exclusion, durable commit, or durable single-use.

A future stage may bind an independently retained durable commit receipt and independent read-after-write evidence. Any actual writer effect, durable single-use enforcement, freshness/liveness upgrade, identity, consensus/approval, or live/model/policy use requires a separately designed and separately authorized boundary.
