# TradingOS R87 — External Assertion Replay Atomic CAS Binding R1

## Objective

R87 adds a deterministic offline binding layer above one exact, fully validated R86 replay-guard candidate.

R86 proves only replay absence against one exact externally retained replay-registry snapshot and constructs one deterministic next-state candidate. R87 does not commit that candidate. It binds an exact externally produced atomic/CAS verification record to the exact R86 prior state, exact R86 next candidate, and exact generation transition.

Pipeline:

`valid exact R86 binding + exact external atomic/CAS verification + independently supplied verification SHA-256 + exact R87 policy -> deterministic R87 atomic-transition binding -> human/archive`

## Exact CAS binding

The external atomic/CAS verification must bind exactly:

- complete exact R86 binding SHA-256 and `r86_binding_id`;
- exact R86 `replay_registry_sha256`;
- exact R86 `next_registry_candidate_sha256`;
- exact R84 `external_assertion_sha256`;
- exact R84 `challenge_sha256`;
- `cas_generation_from == R86.prior_generation`;
- `cas_generation_to == R86.next_generation`;
- `cas_generation_to == cas_generation_from + 1`;
- `toctou_guard_model=COMPARE_AND_SWAP_PRECONDITION`;
- `atomic_scope=EXTERNAL_ASSERTION_REPLAY_REGISTRY_ONLY`.

The complete external verification record must hash to the independently supplied expected SHA-256.

## Evidence ceiling

A valid R87 artifact establishes only:

`AN_EXACT_EXTERNALLY_RETAINED_ATOMIC_CAS_VERIFICATION_RECORD_WAS_BOUND_TO_THE_EXACT_R86_PRIOR_STATE_AND_NEXT_STATE_CANDIDATE_WITH_AN_EXACT_ONE_STEP_GENERATION_TRANSITION`

R87 does not establish that a durable writer committed anything, that the supplied prior state is globally current, or that concurrent writers were actually excluded.

The external verification must explicitly state:

- `atomicity_status=PROTOCOL_VERIFIED_NO_DURABLE_COMMIT`;
- `single_use_status=CANDIDATE_ONLY_NOT_DURABLY_ENFORCED`;
- `commit_performed=false`;
- `registry_write_performed=false`;
- `durable_commit_proven=false`;
- `global_current_state_verified=false`;
- `concurrent_writer_exclusion_proven=false`;
- `execution_authority=NONE`;
- `can_execute=false`;
- `apply_allowed=false`;
- `confers_authority=false`.

## Authority ceiling

R87 remains offline evidence only:

- `atomic_transition_candidate_verified=true`
- `cas_precondition_bound=true`
- `durable_commit_proven=false`
- `durable_single_use_enforced=false`
- `global_current_state_verified=false`
- `concurrent_writer_exclusion_proven=false`
- `registry_write_performed=false`
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

No registry write, persistence, network/provider transport, credential access, process execution, deployment, runtime registration, model selection, signal, order, wallet, or capital effect exists in R87 core.
