# TradingOS R92 — External Assertion Replay Backend Provenance Binding R1

## Objective

R92 adds a deterministic offline backend-metadata provenance binding above one exact, fully validated R91 retained commit/readback evidence binding.

R91 binds one exact externally retained commit receipt and readback evidence record to the exact R90 transition candidate, while explicitly refusing backend authenticity, backend identity, live-observation, or durability claims. R92 does not contact or authenticate any backend. It binds the backend metadata claimed for the exact R91 commit/readback artifacts to one exact entry in one exact externally retained backend-registry snapshot under one independently supplied backend-authority-root digest.

Pipeline:

`valid exact R91 + exact backend registry snapshot + independently supplied registry SHA-256 + independently supplied backend authority-root SHA-256 + exact backend provenance verification + independently supplied provenance SHA-256 + exact R92 policy -> deterministic R92 binding -> human/archive`

## Exact provenance binding

R92 requires:

- complete exact R91 binding ID and SHA-256;
- exact R91 `external_commit_receipt_sha256`;
- exact R91 `readback_evidence_sha256`;
- exact R91 `readback_state_sha256`;
- one exact externally retained backend-registry snapshot;
- complete registry SHA-256 equal to an independently supplied expected digest;
- registry `backend_authority_root_sha256` equal to an independently supplied backend-root digest;
- one and only one registry entry matching the provenance record on:
  - `backend_id`;
  - `backend_key_id`;
  - `backend_metadata_sha256`;
  - `backend_kind`;
  - `receipt_format`;
  - `readback_format`;
- exact SHA-256 of that selected registry entry;
- one exact externally retained backend-provenance verification record whose complete SHA-256 equals an independently supplied expected digest;
- the provenance record must bind the exact registry SHA/root/entry SHA and the exact R91 commit/readback artifact digests.

The verifier-provenance root, writer-authority root, and backend-authority root are separate trust domains and may differ.

## Precise claim

A valid R92 artifact establishes only:

`THE_BACKEND_METADATA_CLAIMED_FOR_THE_EXACT_R91_COMMIT_RECEIPT_AND_READBACK_ARTIFACTS_MATCHED_ONE_EXACT_ENTRY_IN_ONE_EXACT_EXTERNALLY_BOUND_BACKEND_REGISTRY_SNAPSHOT_UNDER_ONE_EXACT_EXTERNALLY_BOUND_BACKEND_AUTHORITY_ROOT_DIGEST`

This is metadata provenance binding only.

R92 does not establish:

- that the backend is authentic;
- that the backend authority root is trusted;
- who operates the registry;
- that the commit receipt or readback is cryptographically authentic;
- that either artifact was observed live;
- that a durable transaction committed;
- that the readback state is globally current;
- that concurrent writers were excluded;
- that R92 itself performed any write.

## Authority ceiling

R92 remains offline evidence only:

- `backend_provenance_bound=true`
- `commit_receipt_backend_metadata_bound=true`
- `readback_backend_metadata_bound=true`
- `same_backend_metadata_claim_bound=true`
- `backend_provenance_match=true`
- `backend_commit_authenticity_verified=false`
- `readback_authenticity_verified=false`
- `backend_identity_verified=false`
- `backend_trust_root_verified=false`
- `backend_registry_operator_identity_verified=false`
- `live_backend_observed=false`
- `durable_commit_proven=false`
- `durable_dual_state_atomicity_proven=false`
- `durable_single_use_enforced=false`
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

No network/provider transport, credential access, backend lookup, registry mutation, backend mutation, runtime registration, signal, order, wallet, or capital effect exists in R92 core.
