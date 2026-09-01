# TradingOS R89 — External Assertion Replay Writer Authority Anchor Binding R2

## Objective

R89 adds a deterministic offline writer-authority-anchor binding layer above one exact, fully validated R88 writer-fencing/crash-recovery binding.

R88 binds retained writer-fencing and recovery evidence but does not establish that the writer lease, receipt index, or backend authority came from a trusted root. R89 still does not make that root trusted. It binds an exact externally retained writer-authority anchor to exact R88 writer evidence and to an independently supplied authority-root digest.

Pipeline:

`valid exact R88 binding + exact external writer-authority anchor + independently supplied anchor SHA-256 + independently supplied authority-root SHA-256 + exact R89 policy -> deterministic R89 authority-anchor binding -> human/archive`

## Exact authority-anchor binding

The external anchor must bind exactly:

- complete exact R88 binding SHA-256 and `r88_binding_id`;
- exact R88 `recovery_verification_sha256`;
- exact `writer_lease_sha256`;
- exact `current_receipt_index_sha256`;
- exact `receipt_candidate_sha256`;
- exact `current_fencing_token`;
- exact independently supplied writer `authority_root_sha256`;
- `anchor_scope=WRITER_LEASE_AND_RECEIPT_INDEX_ONLY`;
- `retained_reference_required=true`.

The complete external anchor must hash to an independently supplied expected SHA-256.

## Root-domain separation

R89 consumes two independent root-domain inputs and never aliases them:

- `expected_verifier_authority_root_sha256` is used only while fully validating the upstream R88/R85 verifier-provenance chain;
- `expected_writer_authority_root_sha256` is used only for the R89 writer-authority anchor.

The two digests may differ. R89 does not require equality and does not infer any relationship between the verifier-provenance root and the writer-authority root. Supplying either domain's digest in the other domain's slot fails closed against that domain's retained evidence.

## Precise claim

A valid R89 artifact establishes only:

`ONE_EXACT_EXTERNALLY_RETAINED_WRITER_AUTHORITY_ANCHOR_WAS_BOUND_TO_THE_EXACT_R88_WRITER_EVIDENCE_UNDER_ONE_EXACT_EXTERNALLY_BOUND_AUTHORITY_ROOT_DIGEST`

It does not establish that:

- the authority root is trusted;
- the anchor issuer/operator identity is verified;
- the writer backend is live or authoritative;
- the writer lease is globally current;
- concurrent writers were excluded in reality;
- any write or durable commit occurred;
- durable single-use is enforced;
- assertion freshness/liveness or reviewer identity is established.

## Authority ceiling

R89 remains offline evidence only:

- `writer_authority_anchor_bound=true`
- `authority_root_digest_consumed=true`
- `writer_authority_root_verified=false`
- `authority_anchor_operator_identity_verified=false`
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

No registry/lease/receipt/backend write, persistence, network/provider transport, credential access, process execution, deployment, runtime registration, model selection, signal, order, wallet, or capital effect exists in R89 core.
