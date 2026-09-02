# TradingOS R97 — Backend Authority-Root Trust Assertion Binding R1

## Objective

R97 adds one deterministic offline binding layer for one exact externally retained backend-authority-root trust assertion above one exact, fully validated R96 replay-guard binding.

R96 establishes replay absence/candidate evidence for the backend-authenticity assertion but deliberately leaves `backend_trust_root_verified=false`. R97 does not make the backend authority root trusted. It binds one exact external evaluator assertion claiming trust in the exact backend-authority-root digest already bound through R92-R96 to one deterministic challenge derived from the exact R96 chain.

Pipeline:

`valid exact R96 + deterministic R97 trust challenge + exact externally retained trust assertion + independently supplied assertion SHA-256 + exact R97 policy -> deterministic R97 binding -> human/archive`

## Deterministic trust challenge

The challenge binds exact R96 ID/SHA, backend authority root, backend and backend-key registry digests and selected entry digests, backend ID/key ID/metadata SHA, claimed public-key SHA, algorithm, and exact R97 policy SHA. It has fixed purpose `R97_BACKEND_AUTHORITY_ROOT_TRUST_ASSERTION_BINDING_ONLY`.

The challenge contains no nonce and no timestamp. R97 therefore makes no freshness or liveness claim about the trust assertion.

## External trust assertion boundary

The assertion must match the exact challenge, backend root, backend/backend-key registries, backend ID/key ID, exact `ED25519` metadata, assert root trust, declare `local_trust_evaluation_performed=false`, use scope `BACKEND_AUTHORITY_ROOT_TRUST_ASSERTION_ONLY`, and set `confers_authority=false`. Its complete digest must equal an independently supplied expected SHA-256.

Evaluator ID/key ID are retained metadata only. R97 does not verify evaluator identity, evaluator key possession, evaluator trust root, certification path, compromise status, revocation state, or policy authority.

## Precise claim

`ONE_EXACT_EXTERNALLY_RETAINED_EVALUATOR_ASSERTION_CLAIMING_TRUST_IN_THE_EXACT_BACKEND_AUTHORITY_ROOT_WAS_DIGEST_BOUND_TO_THE_EXACT_R96_BACKEND_EVIDENCE_CHAIN`

Digest binding does not make that trust claim true.

## Authority ceiling

R97 adds only trust-assertion evidence: `backend_authority_root_trust_assertion_bound=true`, `backend_authority_root_trust_asserted_by_external_evaluator=true`, and `backend_authority_root_trust_challenge_bound=true`. Evaluator identity/trust, trust-assertion freshness, and local trust evaluation remain false. Inherited `backend_trust_root_verified=false`, authenticity/key-possession/identity/durability/current-state/write flags remain false, with `execution_authority=NONE`, `can_trade=false`, `capital_permission=DENY`, `confers_authority=false`.

No merge, PR, Actions trigger/rerun, workflow edit, deployment, runtime registration, credential access, local trust evaluation, backend/registry mutation, signal, order, wallet or capital effect belongs to R97 core.
