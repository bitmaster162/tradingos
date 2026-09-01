# TradingOS R86 — External Assertion Replay Guard Binding R1

## Objective

R86 adds a deterministic offline replay-guard **candidate** above one exact, fully validated R85 verifier-provenance binding.

R86 does not make an assertion fresh. It proves only that the exact R84 external-assertion digest and exact R84 challenge digest are absent from one exact externally retained replay-registry snapshot, and deterministically constructs the next registry-state candidate that would record both digests.

Pipeline:

`valid exact R85 binding + exact external replay-registry snapshot + independently supplied registry SHA-256 + exact R86 policy -> replay absence check -> deterministic next-state candidate -> deterministic R86 binding -> human/archive`

## Exact replay anchors

R86 revalidates the complete R85/R84 chain and binds:

- exact complete R85 binding SHA-256;
- exact `r85_binding_id`;
- exact R84 `external_assertion_sha256`;
- exact R84 `challenge_sha256`;
- SHA-256 of the exact R86 replay-guard policy;
- SHA-256 of the complete externally retained replay-registry snapshot;
- exact prior registry generation;
- SHA-256 of the complete deterministic next-state candidate.

The prior snapshot must contain sorted, unique lowercase SHA-256 lists. The exact R84 assertion and challenge digests must be absent.

## Deterministic next-state candidate

For a valid unused assertion/challenge pair, R86 constructs one candidate:

- same `registry_id`;
- `prior_registry_sha256` equal to the exact retained snapshot digest;
- `prior_generation=N`;
- `next_generation=N+1`;
- exact appended assertion/challenge digests;
- next assertion/challenge sets sorted and unique;
- `candidate_status=REPLAY_GUARD_CANDIDATE_ONLY_NOT_DURABLY_ENFORCED`;
- `durable_commit_proven=false`;
- `write_performed=false`;
- `apply_allowed=false`;
- `confers_authority=false`.

R86 performs no registry write.

## Precise claim

A valid R86 artifact establishes only:

`THE_EXACT_R84_ASSERTION_AND_CHALLENGE_DIGESTS_WERE_ABSENT_FROM_ONE_EXACT_EXTERNALLY_RETAINED_REPLAY_SNAPSHOT_AND_ONE_DETERMINISTIC_NEXT_STATE_CANDIDATE_WAS_BOUND`

It does not establish:

- that the candidate was written;
- durable single-use enforcement;
- assertion freshness or liveness;
- verifier trust;
- reviewer identity or physical-human presence;
- distinct-human multiplicity;
- consensus, approval, recommendation, or execution authority.

## Authority ceiling

R86 remains offline evidence only:

- `shadow_only=true`
- `human_review_only=true`
- `replay_guard_candidate_bound=true`
- `durable_single_use_enforced=false`
- `registry_write_performed=false`
- `assertion_freshness_verified=false`
- `liveness_verified=false`
- `verifier_trust_root_verified=false`
- `review_identity_verified=false`
- `physical_human_presence_proven=false`
- `distinct_reviewer_count_allowed=false`
- `consensus_inference_allowed=false`
- `approval_state_allowed=false`
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

No persistence, registry mutation, network/provider transport, credentials, signature generation/verification, process execution, deployment, runtime registration, signals, orders, wallet, or capital effect exists in R86 core.
