# TradingOS R85 — External Verifier Provenance Binding Threat Model R1

## Trust boundaries

1. Canonical R81-R83 review evidence validated through R84.
2. Exact R84 key-possession policy and exact R84 binding.
3. Exact R85 provenance policy.
4. Externally retained verifier-registry snapshot.
5. Independently supplied expected SHA-256 of that complete registry snapshot.
6. Independently supplied expected authority-root SHA-256.
7. R85 deterministic registry-entry matcher and provenance-binding builder/validator.
8. Human/archive consumer.

## Threats and R1 controls

| Threat | R1 control |
| --- | --- |
| Invalid or substituted R84 evidence admitted | full canonical R84 validation before provenance construction |
| R84 binding changed after validation | exact complete R84 binding SHA-256 carried in R85 |
| Verifier registry changed after retention | complete canonical registry SHA-256 must equal independently supplied expected digest |
| Registry supplied under a substituted authority-root digest | snapshot root must equal independently supplied expected authority-root SHA-256 |
| Wrong verifier entry selected | exact four-field match on verifier id, verifier-key id, public-key SHA-256, and algorithm |
| Duplicate/ambiguous registry match | duplicate entries rejected; exactly one matching entry required |
| Registry contains hidden fields | exact registry and entry key sets |
| Unsupported algorithm metadata | bounded allowlist (`ED25519`, `ES256`) |
| Registry claims are silently upgraded to trust | `trust_root_verified=false`; output `verifier_trust_root_verified=false` |
| Registry operator is silently authenticated | `registry_operator_identity_verified=false` |
| Provenance is silently upgraded to reviewer identity | `review_identity_verified=false` |
| Provenance is treated as physical-human proof | `physical_human_presence_proven=false` |
| Provenance is treated as freshness/liveness | `assertion_freshness_verified=false`; R85 adds no nonce or time source |
| One/many keys become distinct-human counts | `distinct_reviewer_count_allowed=false` |
| Consensus/approval backdoor | no vote/count/quorum/consensus/approval fields; hard-denied |
| Policy/model/live decision update | hard-denied |
| Registry write or persistence side effect | core has no filesystem/database/network/provider/credential client |
| Output mutation | deterministic `binding_id` over the complete output payload except the ID itself |
| Execution escalation | exact NONE / DENY / false ceiling |

## Residual trust and deliberately deferred

R85 does not verify that the authority root is trustworthy, that a registry operator is authentic, that registry issuance was authorized, that a verifier key is uncompromised or unrevoked, or that an R84 external assertion is fresh. The independently supplied registry and root digests are provenance anchors, not trust proofs.

Any future stage that upgrades these digest-bound provenance anchors into a verified trust root, registry-operator identity, credential lifecycle/revocation, freshness/liveness, reviewer identity, distinct-human counting, consensus/approval, or live/model/policy use requires a separately designed and separately authorized boundary.
