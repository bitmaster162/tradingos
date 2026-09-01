# TradingOS R88 — External Assertion Replay Writer Fencing Recovery Binding R1

## Objective

R88 adds a deterministic offline writer-fencing and crash-recovery evidence-binding layer above one exact, fully validated R87 atomic-CAS transition candidate.

R87 proves only that an exact externally retained atomic/CAS verification record was bound to the exact replay-registry prior state, exact next-state candidate, and exact one-step generation transition. R88 does not perform a write and does not prove a live writer backend. It binds an exact externally produced writer-fencing/crash-recovery verification record to the exact R87 transition candidate.

Pipeline:

`valid exact R87 binding + exact external writer-fencing/crash-recovery verification + independently supplied verification SHA-256 + exact R88 policy -> deterministic R88 recovery binding -> human/archive`

## Exact recovery binding

The external recovery verification must bind exactly:

- complete exact R87 binding SHA-256 and `r87_binding_id`;
- exact R87 `atomic_verification_sha256`;
- exact replay-registry prior SHA-256;
- exact replay-registry next-candidate SHA-256;
- exact CAS generation transition;
- exact writer-lease SHA-256;
- exact receipt-candidate SHA-256;
- exact current receipt-index SHA-256;
- bounded attempt/current fencing tokens;
- `fencing_model=MONOTONIC_FENCING_TOKEN_PLUS_LEASE_DIGEST`;
- `crash_recovery_protocol=READBACK_PLUS_RECEIPT_INDEX_DEDUP`;
- `blind_retry_allowed=false`;
- `split_brain_same_token_rejected=true`.

The complete external verification record must hash to the independently supplied expected SHA-256.

## Recovery statuses

R88 accepts only bounded recovery outcomes:

- `STALE_WRITER_FENCED_REACQUIRE_REQUIRED -> REACQUIRE_LEASE`;
- `NO_WRITE_OBSERVED_RETRY_REQUIRES_FRESH_CAS -> RETRY_WITH_FRESH_CAS`;
- `WRITE_OBSERVED_RECEIPT_ABSENT_HOLD -> HOLD`;
- `RECEIPT_INDEXED_DEDUP_NO_RETRY -> DEDUP_NO_RETRY`.

A stale-writer result requires `attempt_fencing_token < current_fencing_token`. Non-stale results require equality. A higher attempt token is rejected.

## Evidence ceiling

A valid R88 artifact establishes only:

`AN_EXACT_EXTERNALLY_RETAINED_WRITER_FENCING_AND_CRASH_RECOVERY_VERIFICATION_RECORD_WAS_BOUND_TO_THE_EXACT_R87_ATOMIC_TRANSITION_CANDIDATE`

It does not establish that:

- the writer lease came from a live authoritative backend;
- the receipt index is globally current;
- concurrent writers were excluded in reality;
- any registry write or durable commit occurred;
- durable single-use is enforced;
- assertion freshness/liveness or reviewer identity is established.

The writer lease, receipt candidate, receipt index, fencing tokens, crash point and recovery status are evidence fields from the exact retained external verification record. Their binding does not make the external verifier truthful.

## Authority ceiling

R88 remains offline evidence only:

- `writer_fencing_recovery_evidence_bound=true`
- `lease_digest_bound=true`
- `fencing_protocol_bound=true`
- `crash_recovery_protocol_bound=true`
- `live_writer_backend_proven=false`
- `durable_commit_proven=false`
- `durable_single_use_enforced=false`
- `global_current_state_verified=false`
- `concurrent_writer_exclusion_proven=false`
- `registry_write_performed=false`
- `lease_registry_write_performed=false`
- `receipt_index_write_performed=false`
- `backend_write_performed=false`
- `assertion_freshness_verified=false`
- `liveness_verified=false`
- `verifier_trust_root_verified=false`
- `review_identity_verified=false`
- `physical_human_presence_proven=false`
- `distinct_reviewer_count_allowed=false`
- `consensus_inference_allowed=false`
- `approval_state_allowed=false`
- `shadow_only=true`
- `human_review_only=true`
- `attestation_set_consumption_authority=NONE`
- `memory_write_authority=NONE`
- `policy_update_allowed=false`
- `live_decision_feedback_allowed=false`
- `live_decision_use_allowed=false`
- `model_selection_use_allowed=false`
- `execution_authority=NONE`
- `can_trade=false`
- `capital_permission=DENY`
- `confers_authority=false`

No registry/lease/receipt/backend write, persistence, network/provider transport, credential access, process execution, deployment, runtime registration, model selection, signal, order, wallet, or capital effect exists in R88 core.
