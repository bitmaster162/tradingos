# TradingOS R96 — Backend Authenticity Replay Guard Binding R1

## Objective

R96 adds one deterministic offline replay-guard candidate layer above one exact, fully validated R95 backend-key provenance binding.

R95 closes provenance between the claimed backend public-key digest and exact backend metadata/key identifiers. R96 does not make the R93 backend-authenticity assertion fresh. It binds the exact inherited R93 backend-authenticity assertion digest and exact inherited R93 challenge digest to one exact externally retained replay-registry snapshot, proves only that both digests are absent from that snapshot, and deterministically constructs the next replay-registry-state candidate.

Pipeline:

`valid exact R95 + exact external backend-authenticity replay-registry snapshot + independently supplied registry SHA-256 + exact R96 policy -> exact assertion/challenge absence check -> deterministic next-state candidate -> deterministic R96 binding -> human/archive`

## Exact replay anchors

R96 full-validates the complete R95/R94/R93 chain and uses the exact inherited:

- `backend_authenticity_assertion_sha256`;
- `challenge_sha256`.

The replay snapshot must have exact bounded keys, sorted/unique lowercase SHA-256 sets, a bounded integer generation, no durability/write/apply/authority overclaim, and a complete canonical digest equal to an independently supplied expected replay-registry SHA-256.

The exact backend-authenticity assertion digest and exact challenge digest must both be absent.

## Deterministic next-state candidate

For one valid unused assertion/challenge pair, R96 constructs exactly one candidate with:

- same `registry_id`;
- `prior_registry_sha256` equal to the exact retained replay snapshot digest;
- `prior_generation=N`;
- `next_generation=N+1`;
- exact appended backend-authenticity assertion digest;
- exact appended backend-authenticity challenge digest;
- sorted/unique next assertion/challenge digest sets;
- `candidate_status=BACKEND_AUTHENTICITY_REPLAY_GUARD_CANDIDATE_ONLY_NOT_DURABLY_ENFORCED`;
- `durable_commit_proven=false`;
- `write_performed=false`;
- `apply_allowed=false`;
- `confers_authority=false`.

R96 performs no replay-registry write.

## Precise claim

A valid R96 artifact establishes only:

`THE_EXACT_R93_BACKEND_AUTHENTICITY_ASSERTION_AND_CHALLENGE_DIGESTS_INHERITED_THROUGH_R95_WERE_ABSENT_FROM_ONE_EXACT_EXTERNALLY_BOUND_REPLAY_SNAPSHOT_AND_ONE_DETERMINISTIC_NEXT_REPLAY_STATE_CANDIDATE_WAS_BOUND`

It does not establish:

- assertion freshness;
- assertion liveness;
- durable single-use enforcement;
- that the next candidate was written;
- backend authenticity;
- readback authenticity;
- backend key possession as ground truth;
- backend identity;
- backend or verifier trust-root validity;
- live backend observation;
- durable commit/atomicity;
- global current state;
- concurrent-writer exclusion;
- execution/trading/capital authority.

The original R93 challenge has no nonce and no timestamp. Replay absence in one retained snapshot therefore cannot be promoted into a freshness claim.

## Authority ceiling

R96 preserves every inherited R95 field exactly and adds only replay-evidence fields:

- `backend_authenticity_assertion_absent_in_expected_replay_registry=true`;
- `backend_authenticity_challenge_absent_in_expected_replay_registry=true`;
- `backend_authenticity_replay_absence_bound=true`;
- `backend_authenticity_replay_guard_candidate_bound=true`;
- `backend_authenticity_replay_registry_digest_consumed=true`;
- `backend_authenticity_replay_registry_write_performed=false`;
- `backend_authenticity_replay_candidate_write_performed=false`;
- `backend_authenticity_replay_candidate_apply_allowed=false`.

Inherited negatives remain unchanged, including:

- `assertion_freshness_verified=false`;
- `liveness_verified=false`;
- `durable_single_use_enforced=false`;
- `backend_commit_authenticity_verified=false`;
- `readback_authenticity_verified=false`;
- `backend_key_possession_proven=false`;
- `backend_identity_verified=false`;
- `backend_trust_root_verified=false`;
- `backend_authenticity_verifier_trust_root_verified=false`;
- durability/current-state/write flags remain false;
- `execution_authority=NONE`;
- `can_trade=false`;
- `capital_permission=DENY`;
- `confers_authority=false`.

No merge, PR, Actions trigger/rerun, workflow edit, deployment, runtime registration, credential access, network/provider call, backend mutation, registry mutation, signal, order, wallet or capital effect belongs to R96 core.
