# TradingOS R84 — Reviewer Key-Possession Assertion Binding R1

## Objective

R84 adds a deterministic offline **binding layer for an externally verified asymmetric-signature assertion** over one exact R83 attestation binding.

It does **not** perform signature mathematics locally, authenticate a reviewer identity, prove physical-human presence, prove assertion freshness, infer that one key equals one human, infer that different keys equal different humans, count distinct reviewers, reconcile disagreement, compute consensus, approve anything, update policy/model state, or participate in a live decision path.

Pipeline:

`valid R83 evidence set + exact attestation binding → canonical R84 challenge → independently retained external verifier assertion → exact assertion SHA-256 → deterministic R84 binding → human/archive`

## Canonical challenge

R84 first performs full canonical R83 validation against the exact R83 evidence set, evidence triples, and R83 set policy.

The challenge is deterministic and binds:
- exact `evidence_set_id`;
- SHA-256 of the complete R83 evidence-set manifest;
- exact `attestation_id`;
- exact `attestation_sha256`;
- exact `shadow_report_id`;
- exact `shadow_report_sha256`;
- exact `review_policy_sha256`;
- the fixed purpose `R84_REVIEWER_KEY_POSSESSION_BINDING_ONLY`.

The challenge contains no nonce and therefore R84 makes **no freshness claim**.

## External verifier assertion boundary

R84 accepts one exact externally produced assertion record. Core R84:
- checks the assertion has the exact bounded schema;
- checks its `challenge_sha256` equals the canonical R84 challenge digest;
- checks its complete canonical SHA-256 equals an independently supplied expected assertion digest;
- requires `signature_verified_by_external_asymmetric_verifier=true`;
- requires `local_signature_math_verified=false`;
- binds only digests/identifiers and bounded metadata, never raw signature bytes or raw public-key bytes.

The truthfulness, provenance, trustworthiness, and compromise status of the external verifier are outside R84 core. Digest binding protects against substitution after the expected digest is established; it does not make a false external assertion true.

## Identity and multiplicity ceiling

A valid R84 record establishes only:

`AN_EXACT_EXTERNAL_VERIFIER_ASSERTION_ABOUT_A_KEY_WAS_BOUND_TO_AN_EXACT_R83_ATTESTATION_BINDING`

It does not establish a civil/person identity or a distinct human. Specifically:
- `review_identity_verified=false`;
- same key across records does not prove same human;
- different keys across records do not prove different humans;
- `distinct_reviewer_count_allowed=false`;
- `physical_human_presence_proven=false`;
- `assertion_freshness_verified=false`.

R84 does not aggregate dispositions/reasons and does not create votes, quorum, majority, consensus, approval, recommendation, rank, confidence, P&L, price, return, signal, order, or capital fields.

## Authority ceiling

R84 remains offline evidence only:
- `shadow_only=true`
- `human_review_only=true`
- `review_identity_verified=false`
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

No persistence, network/provider transport, credential access, signature generation, local signature verification, process execution, deployment, runtime registration, model selection, signals, orders, wallet, or capital effect exists in R84 core.
