# TradingOS R84 — Reviewer Key-Possession Assertion Binding Threat Model R2

## Trust boundaries

1. Canonical R81 shadow report bytes.
2. Canonical R82 review-policy and human-review attestation bytes.
3. Canonical R83 evidence-set manifest plus exact R82 evidence triples and R83 set policy.
4. Exact R84 key-possession policy bytes.
5. R84 deterministic challenge builder.
6. Externally produced asymmetric-verifier assertion.
7. Independently supplied expected SHA-256 of that complete external assertion.
8. R84 deterministic assertion-binding builder/validator.
9. Human/archive consumer.

## Threats and R2 controls

| Threat | R2 control |
| --- | --- |
| Invalid/substituted R83 evidence admitted | full canonical R83 validation before challenge construction |
| Assertion attached to wrong attestation | canonical challenge binds exact R83 attestation binding |
| Assertion attached to substituted evidence set | challenge binds full R83 manifest SHA-256 and `evidence_set_id` |
| Assertion produced under substituted R84 policy | challenge and final binding both carry exact `key_possession_policy_sha256` |
| External assertion changed after retention | complete canonical assertion SHA-256 must equal independently supplied expected digest |
| Assertion contains unbounded/hidden fields | exact assertion key set |
| Unsupported algorithm metadata | bounded allowlist (`ED25519`, `ES256`) |
| Raw signature/public-key material enters core | exact schema contains only SHA-256/key identifiers, no raw byte fields |
| Local crypto capability is overclaimed | `local_signature_math_verified=false` is mandatory |
| External verifier claim is silently upgraded to reviewer identity | `review_identity_verified=false`; no reviewer identity field |
| Same key is treated as same human | explicit inference prohibition |
| Different keys are treated as different humans | explicit inference prohibition |
| Key assertion is treated as proof of physical human presence | `physical_human_presence_proven=false` |
| Deterministic challenge is treated as fresh liveness proof | `assertion_freshness_verified=false`; no nonce/freshness claim |
| Offline/shadow ceiling disappears in downstream serialization | final binding and schema require `shadow_only=true` and `human_review_only=true` |
| Distinct reviewer count backdoor | `distinct_reviewer_count_allowed=false` |
| Consensus/majority backdoor | no vote/count/quorum/consensus fields; hard-denied |
| Approval/recommendation backdoor | no approval/recommendation fields; hard-denied |
| Policy/model update | hard-denied |
| Live feedback/use | hard-denied |
| Output mutation | deterministic `binding_id` over the complete output payload except the ID itself |
| Persistence/network/credential side effect | core has no filesystem/database/network/process/provider/credential client |
| Execution escalation | exact NONE / DENY / false ceiling |

## Residual trust and deliberately deferred

R84 does **not** verify signature mathematics. A compromised, dishonest, or incorrectly configured external verifier can emit a false assertion; R84 only validates and binds that assertion record to exact upstream evidence and an independently supplied digest.

R84 also does not provide nonce issuance/replay prevention, assertion freshness, hardware attestation, credential lifecycle/revocation, reviewer authentication, civil identity, physical-human proof, distinct-human counting, quorum, consensus, dispute resolution, approval, statistical calibration, model/provider comparison, policy adaptation, persistent storage, runtime activation, or trading.

Any stage that upgrades an external verifier assertion into authenticated identity, freshness/liveness, distinct-human counting, consensus/approval, or live/model/policy use requires a separately designed and separately authorized boundary.
