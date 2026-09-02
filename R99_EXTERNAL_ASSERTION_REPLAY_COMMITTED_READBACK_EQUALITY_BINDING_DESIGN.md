# TradingOS R99 R2 — Expected-Digest-Bound Committed-State / Readback Equality Binding

## Objective

R99 R2 remediates the R99 R1 independent-review blockers without widening authority.

R2 no longer claims that caller-supplied expected record digests are independently supplied or independently verified. It binds exact expected digest inputs while `expected_digest_independence_verified=false` remains mandatory until a separate external provenance/verifier layer establishes that property.

R2 also re-materializes the inherited R98 safety ceiling and fails closed if any inherited safety invariant drifts.

## Required evidence

The committed-state and readback-state derivation records remain exact, role-separated, projection-bound, canonicalization-bound and derivation-tool-bound. The readback source digest must equal the inherited exact R98 readback-state digest. The equality record binds exact R98 lineage plus both derivation-record digests and both canonical projected-state digests.

Expected SHA-256 values for the three records must match their complete canonical records. This protects against substitution relative to those supplied expected values; it does not prove the provenance or independence of those values.

## Full R98 boundary

The exact R98 validator is consumed first. R2 additionally materializes the complete inherited R98 safety-ceiling subset and rejects any drift before processing R99 equality evidence.

A repository integration regression is included to exercise the real R98 validator/fixture chain when the complete R84→R98 repository context is available. The known synthetic lineage must still fail closed at the R99 frontier.

## Precise claim

`ONE_EXACT_EXPECTED_DIGEST_BOUND_COMMITTED_STATE_DERIVATION_RECORD_AND_ONE_EXACT_EXPECTED_DIGEST_BOUND_READBACK_STATE_DERIVATION_RECORD_WERE_BOUND_TO_ONE_EXACT_R98_CHAIN_AND_THEIR_CANONICAL_PROJECTED_STATE_SHA256_VALUES_MATCHED`

R2 does not prove expected-digest independence, provider honesty, backend authenticity, durability, global current state, concurrent-writer exclusion, execution authority, or trading permission.
