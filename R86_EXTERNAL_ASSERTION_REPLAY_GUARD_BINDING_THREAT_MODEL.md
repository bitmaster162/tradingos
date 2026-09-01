# TradingOS R86 — External Assertion Replay Guard Binding Threat Model R1

## Trust boundaries

1. Canonical R81-R84 review evidence validated through R85.
2. Exact R85 verifier-provenance policy, registry evidence, and exact R85 binding.
3. Exact R86 replay-guard policy.
4. Externally retained replay-registry snapshot.
5. Independently supplied expected SHA-256 of that complete replay snapshot.
6. R86 deterministic replay matcher and next-state candidate builder/validator.
7. Human/archive consumer.

## Threats and R1 controls

| Threat | R1 control |
| --- | --- |
| Invalid/substituted R85 evidence admitted | full canonical R85 validation before replay processing |
| R85 binding changed | exact complete R85 binding SHA-256 carried in R86 |
| Replay snapshot changed after retention | complete canonical snapshot SHA-256 must equal independently supplied expected digest |
| Assertion replay | exact R84 `external_assertion_sha256` must be absent |
| Challenge replay | exact R84 `challenge_sha256` must be absent |
| Duplicate/ambiguous registry state | snapshot digest lists must be sorted and unique |
| Registry generation ambiguity | bounded integer generation; candidate increments exactly by one |
| Next-state substitution | complete deterministic candidate SHA-256 carried in R86 |
| Candidate silently treated as durable state | `durable_single_use_enforced=false`, `registry_write_performed=false`, candidate status explicitly non-durable |
| Candidate treated as freshness/liveness | `assertion_freshness_verified=false`, `liveness_verified=false` |
| Replay evidence upgraded to verifier trust | `verifier_trust_root_verified=false` |
| Replay evidence upgraded to reviewer identity | `review_identity_verified=false` |
| Replay evidence upgraded to physical-human proof | `physical_human_presence_proven=false` |
| One/many digests become distinct-human counts | `distinct_reviewer_count_allowed=false` |
| Consensus/approval backdoor | no vote/count/quorum/consensus/approval fields; hard-denied |
| Policy/model/live-decision update | hard-denied |
| Registry write/persistence side effect | core has no filesystem/database/network/provider/credential client |
| Output mutation | deterministic `binding_id` over complete output except the ID itself |
| Execution escalation | exact NONE / DENY / false ceiling |

## Residual trust and deliberately deferred

R86 is only a replay-absence proof against one exact external snapshot plus a deterministic next-state candidate. It does not prove the snapshot is the globally current state, does not serialize concurrent writers, does not provide CAS/lease/fencing, does not prove a durable commit, and therefore does not provide durable single-use enforcement.

A future stage may bind an independently retained atomic/CAS verification candidate, but any durable writer, lease/fencing protocol, read-after-write verification, freshness/liveness upgrade, identity, consensus/approval, or live/model/policy use requires a separately designed and separately authorized boundary.
