# TradingOS R91 — External Assertion Replay Durable Commit Readback Evidence Binding R1

## Objective

R91 adds a deterministic offline retained commit-receipt and read-after-write evidence binding above one exact, fully validated R90 dual-state atomicity binding.

R90 proves only that one exact externally retained dual-state atomicity protocol record was bound under a no-durable-backend ceiling. R91 still performs no write and observes no backend directly. It binds one exact externally retained commit/readback evidence record to the exact R90 transition candidate.

Pipeline:

`valid exact R90 + exact retained commit/readback evidence + independently supplied complete evidence SHA-256 + separate verifier root + separate writer root + exact R91 policy -> deterministic R91 binding -> human/archive`

## Required exact evidence

The retained evidence record must bind exactly:

- exact R90 binding ID and SHA-256;
- exact R90 authority-anchor SHA-256;
- exact writer-authority root SHA-256;
- exact writer lease SHA-256;
- exact prior receipt-index SHA-256;
- commit-receipt claimed receipt-index SHA-256 equal to the R90 next receipt-index candidate;
- readback receipt-index SHA-256 equal to the same R90 next receipt-index candidate;
- exact R90 lease-lineage SHA-256;
- exact R90 commit ID;
- exact R90 idempotency-key SHA-256;
- exact external commit-receipt SHA-256;
- exact readback-state SHA-256;
- exact readback-evidence SHA-256;
- `receipt_identity_bound=true`;
- `read_after_write_match=true`;
- `commit_receipt_retained=true`;
- `readback_retained=true`.

The complete evidence record itself must hash to an independently supplied expected SHA-256.

## Precise claim

A valid R91 artifact establishes only:

`ONE_EXACT_EXTERNALLY_RETAINED_COMMIT_RECEIPT_AND_READBACK_EVIDENCE_RECORD_WAS_DIGEST_BOUND_TO_THE_EXACT_R90_TRANSITION_CANDIDATE_AND_ITS_CLAIMED_RECEIPT_INDEX_MATCHED_THE_RETAINED_READBACK_INDEX`

This is evidence binding, not backend authentication.

R91 does not prove:
- that the backend producing either artifact is authentic;
- that a durable transaction actually committed;
- that the retained readback came from the claimed backend;
- that the readback is globally current;
- that a writer exclusion guarantee held;
- that R91 itself performed any write.

## Root-domain separation

R91 preserves separate verifier-provenance and writer-authority root inputs. They may differ and are routed only to their respective upstream validation domains.

## Authority ceiling

R91 remains offline evidence only:

- `external_commit_receipt_evidence_bound=true`
- `read_after_write_evidence_bound=true`
- `receipt_identity_bound=true`
- `read_after_write_match=true`
- `backend_commit_authenticity_verified=false`
- `backend_identity_verified=false`
- `live_backend_observed=false`
- `durable_commit_proven=false`
- `durable_dual_state_atomicity_proven=false`
- `write_performed=false`
- `global_current_state_verified=false`
- `concurrent_writer_exclusion_proven=false`
- `registry_write_performed=false`
- `lease_registry_write_performed=false`
- `receipt_index_write_performed=false`
- `backend_write_performed=false`
- `assertion_freshness_verified=false`
- `liveness_verified=false`
- `writer_authority_root_verified=false`
- `verifier_trust_root_verified=false`
- `review_identity_verified=false`
- `consensus_inference_allowed=false`
- `approval_state_allowed=false`
- `shadow_only=true`
- `human_review_only=true`
- `execution_authority=NONE`
- `can_trade=false`
- `capital_permission=DENY`
- `confers_authority=false`

No provider transport, credential access, backend mutation, registry mutation, runtime registration, signal, order, wallet or capital effect exists in R91 core.
