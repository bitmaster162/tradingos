# TradingOS R100 — External Derivation Provenance Verification Binding

## Objective

R100 adds a distinct provenance/verifier layer after the exact R99 committed/readback equality binding. It does not change R99's independence result.

The layer consumes the full exact R99 validator first, then binds one expected-digest-bound external derivation-provenance verification record. That record carries an out-of-process recomputation assertion for the exact R99 committed derivation record digest, readback derivation record digest, equality record digest, and projected-state equality result.

## Required lineage

The provenance record must exactly bind:

- the exact R99 binding ID and complete R99 binding SHA-256;
- committed and readback derivation-record SHA-256 values;
- equality-record SHA-256;
- committed/readback source-artifact SHA-256 values;
- projection schema ID/version;
- canonicalization ID/version;
- derivation-tool SHA-256;
- one allowlisted deterministic recomputation method;
- bounded verifier identifiers;
- explicit positive recomputation claims;
- `confers_authority=false`.

The complete provenance record must match one supplied expected SHA-256.

## Evidence ceiling

Binding a record that claims out-of-process recomputation is stronger provenance structure than R99 alone, but it does not prove who produced the record, whether the verifier is trusted, whether the record was durably retained by an independent party, or whether the supplied expected digest itself has independent provenance.

Therefore R100 must keep:

- `expected_digest_independence_verified=false`;
- `external_provenance_digest_independence_verified=false`;
- `external_provenance_record_retention_verified=false`;
- `independent_derivation_verifier_identity_verified=false`;
- `independent_derivation_verifier_trust_root_verified=false`;
- `provider_honesty_verified=false`;
- `durable_commit_proven=false`;
- `global_current_state_verified=false`;
- `execution_authority=NONE`;
- `can_trade=false`;
- `capital_permission=DENY`;
- `confers_authority=false`.

## Precise claim

`ONE_EXACT_EXPECTED_DIGEST_BOUND_EXTERNAL_DERIVATION_PROVENANCE_VERIFICATION_RECORD_WAS_BOUND_TO_ONE_EXACT_FULLY_VALIDATED_R99_BINDING_AND_CLAIMED_OUT_OF_PROCESS_RECOMPUTATION_OF_THE_EXACT_R99_DERIVATION_AND_EQUALITY_DIGESTS_WITHOUT_PROMOTING_DIGEST_INDEPENDENCE_VERIFIER_TRUST_PROVIDER_HONESTY_DURABILITY_OR_AUTHORITY`
