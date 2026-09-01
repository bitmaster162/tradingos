# TradingOS R89 — External Assertion Replay Writer Authority Anchor Binding Threat Model R2

## Trust boundaries

1. Canonical R81-R87 evidence validated through R88.
2. Exact R88 writer-fencing/recovery policy, retained recovery evidence, and exact R88 binding.
3. Exact R89 authority-anchor policy.
4. Externally retained writer-authority-anchor record.
5. Independently supplied expected SHA-256 of that complete anchor.
6. Independently supplied expected writer-authority-root SHA-256; upstream verifier-provenance root remains a separate R88 validation input.
7. R89 deterministic authority-anchor verifier/builder.
8. Human/archive consumer.

## Threats and R2 controls

| Threat | R2 control |
| --- | --- |
| Invalid/substituted R88 evidence admitted | full canonical R88 validation before anchor binding |
| R88 binding changed | complete exact R88 SHA-256 carried in R89 |
| Authority anchor changed after retention | complete anchor SHA-256 must equal independently supplied expected digest |
| Authority root substituted | anchor root must equal independently supplied expected root SHA-256 |
| Anchor transplanted to another writer state | exact R88 id/SHA, recovery SHA, lease, receipt-index, receipt-candidate and fencing token must match |
| Anchor contains hidden fields | exact anchor key set |
| Anchor scope widened | fixed `WRITER_LEASE_AND_RECEIPT_INDEX_ONLY` |
| Retained-reference guard removed | `retained_reference_required=true` |
| Root digest silently upgraded to trust | input `root_trust_verified=false`; output `writer_authority_root_verified=false` |
| Cross-domain root substitution | verifier-provenance and writer-authority roots are separate parameters; each is validated only against its own retained evidence |
| Anchor operator silently authenticated | `authority_anchor_operator_identity_verified=false` |
| Anchor silently treated as live backend proof | `live_writer_backend_proven=false` |
| Anchor silently treated as durable/current state | durable/current-state/write flags remain false |
| Anchor treated as freshness/liveness, identity, consensus, or approval | hard-denied |
| Policy/model/live-decision update | hard-denied |
| Registry/backend side effect | core has no filesystem/database/network/provider/credential client |
| Output mutation | deterministic `binding_id` over complete output except the ID itself |
| Execution escalation | exact NONE / DENY / false ceiling |

## Residual trust and deliberately deferred

R89 proves only deterministic digest-bound consistency between an exact R88 writer state and one exact retained authority-anchor record under one exact externally supplied authority-root digest. It does not establish trustworthiness of that authority root or anchor operator, and it does not prove a live backend, globally current state, durable commit, or writer exclusion.

Any future layer that binds durable commit receipts/read-after-write evidence or upgrades root/operator trust requires a separately designed and separately authorized boundary.
