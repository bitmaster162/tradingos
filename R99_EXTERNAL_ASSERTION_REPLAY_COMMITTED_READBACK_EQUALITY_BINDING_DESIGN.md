# TradingOS R99 — Independently Derived Committed-State / Readback Equality Binding R1

## Objective

R99 defines a fail-closed source capability above exact R98 for binding independently derived committed-state and readback-state projections. It does **not** assert that current repository fixtures are real provider material.

The current R90→R98 fixture chain is explicitly denied as provider evidence. A successful R99 binding requires material whose exact source digests are not the known synthetic fixture placeholders and whose commit identifier is not the R90 fixture commit.

## Required evidence

1. One committed-state derivation record with source artifact digest, projection schema, canonicalization, derivation-tool digest, canonical projected-state digest, provenance digest, and independently supplied expected record SHA-256.
2. One readback-state derivation record with the same derivation identities; its source artifact SHA-256 must equal the exact inherited R98 `readback_state_sha256`.
3. One equality record binding the exact R98 binding SHA/ID, exact inherited commit-receipt digest, exact inherited readback-state digest, both derivation-record digests, both projected-state digests, and an independently supplied expected equality-record SHA-256.

## Fail-closed substitutions

R99 rejects known fixture digests, the fixture commit id `commit-r90-0001`, using the commit-receipt digest as the committed-state source digest, mismatched projection/canonicalization/tool identities, projected-state inequality, malformed or self-inconsistent record digests, and boolean-only match claims.

## Precise claim

`ONE_EXACT_INDEPENDENTLY_DIGEST_BOUND_COMMITTED_STATE_DERIVATION_RECORD_AND_ONE_EXACT_INDEPENDENTLY_DIGEST_BOUND_READBACK_STATE_DERIVATION_RECORD_WERE_BOUND_TO_ONE_EXACT_R98_CHAIN_AND_THEIR_CANONICAL_PROJECTED_STATE_SHA256_VALUES_MATCHED`

This is an evidence-binding claim only. It does not by itself prove backend authenticity, durability, current global state, live writer exclusion, execution authority, or trading permission.

## Current materialization status

For the currently known R98 fixture lineage, R99 full positive integration must fail closed because the inherited material is synthetic fixture data. The source capability may be statically validated, but no live/provider R99 evidence instance exists yet.
