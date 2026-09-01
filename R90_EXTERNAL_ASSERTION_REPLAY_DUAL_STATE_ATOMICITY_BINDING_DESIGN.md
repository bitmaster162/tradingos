# TradingOS R90 — External Assertion Replay Dual-State Atomicity Binding R1

## Objective

R90 adds a deterministic offline dual-state atomicity evidence-binding layer above one exact, fully validated R89 R2 writer-authority-anchor binding.

R89 binds exact writer-authority anchor evidence to writer lease / receipt-index state while keeping verifier-provenance and writer-authority root domains separate. R90 does not write either logical record. It binds an exact externally produced dual-state atomicity verification record to the exact R89 authority anchor, writer lease, prior receipt index, and independently retained verification digest.

Pipeline:

`valid exact R89 R2 + exact external dual-state atomicity verification + independently supplied atomicity SHA-256 + separate verifier root + separate writer root + exact R90 policy -> deterministic R90 binding -> human/archive`

## Exact dual-state binding

The external atomicity verification must bind exactly:

- complete R89 binding ID and SHA-256;
- exact R89 `authority_anchor_sha256`;
- exact writer-authority root SHA-256;
- exact R89 `writer_lease_sha256`;
- exact R89 prior receipt-index SHA-256;
- exact next receipt-index candidate SHA-256;
- exact lease-lineage SHA-256;
- bounded `commit_id`;
- exact `idempotency_key_sha256`;
- `dual_state_atomicity_model=ONE_TRANSACTION_TWO_LOGICAL_RECORDS`;
- `split_state_rejected=true`;
- `lease_epoch_lineage_verified=true`;
- `aba_guard_verified=true`;
- `durability_status=PROTOCOL_VERIFIED_NO_DURABLE_BACKEND`.

The complete external verification record must hash to an independently supplied expected SHA-256.

## Root-domain separation

R90 preserves R89 R2 root separation:

- `expected_verifier_authority_root_sha256` is consumed only by full upstream R89/R88/R85 verifier-provenance validation;
- `expected_writer_authority_root_sha256` is consumed by the writer-authority chain and must equal the writer root carried by the R89 authority anchor.

The two roots may differ. No trust or equality inference is allowed.

## Precise claim

A valid R90 artifact establishes only:

`AN_EXACT_EXTERNALLY_RETAINED_DUAL_STATE_ATOMICITY_VERIFICATION_RECORD_WAS_BOUND_TO_THE_EXACT_R89_WRITER_AUTHORITY_ANCHOR_AND_PRIOR_RECEIPT_INDEX_UNDER_A_FIXED_NO_DURABLE_BACKEND_PROTOCOL`

R90 does not establish that either logical state was written, that a durable transaction committed, that a backend was observed, or that current global state / writer exclusion / reviewer identity / freshness was proven.

## Authority ceiling

R90 remains offline evidence only:

- `dual_state_atomicity_evidence_bound=true`
- `write_performed=false`
- `live_backend_observed=false`
- `durable_dual_state_atomicity_proven=false`
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
- `writer_authority_root_verified=false`
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

No registry / lease / receipt-index / backend write, persistence, provider transport, credential access, runtime registration, signal, order, wallet, or capital effect exists in R90 core.
