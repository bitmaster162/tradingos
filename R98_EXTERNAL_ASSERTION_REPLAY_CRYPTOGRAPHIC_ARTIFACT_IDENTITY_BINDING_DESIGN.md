# TradingOS R98 — Cryptographic Artifact Identity Binding R1

## Objective

R98 adds one deterministic offline evidence-binding layer above one exact fully validated R97 backend authority-root trust-assertion binding.

R98 does not perform signature verification and does not retrieve raw cryptographic artifacts. It binds one exact externally retained cryptographic-artifact identity record, supplied with an independently retained SHA-256, to the exact R97 evidence chain.

The record identifies only digest-level artifacts: the exact inherited backend public-key digest, one commit-signature digest, one readback-signature digest, the exact commit signature target digest equal to the inherited external commit-receipt SHA-256, the exact readback signature target digest equal to the inherited readback-evidence SHA-256, and the exact inherited readback-state SHA-256.

## Deterministic challenge

The R98 challenge binds the exact R97 binding ID/SHA, inherited R93 binding ID/SHA, inherited backend-authenticity assertion/challenge digests, backend ID/key ID, public-key SHA-256, algorithm, exact commit/readback artifact digests, commit ID, idempotency-key SHA-256, and exact R98 policy SHA-256.

The challenge contains no nonce and no timestamp. R98 therefore makes no freshness or liveness claim.

## External artifact identity record boundary

The record must match the exact challenge and exact inherited backend/key/public-key/algorithm values. It must bind a SHA-256 digest for the retained commit-signature artifact and a SHA-256 digest for the retained readback-signature artifact. Its signature-target digests must equal the exact inherited external commit-receipt and readback-evidence digests, and its readback-state digest must equal the exact inherited R97 value.

Raw signature bytes and raw public-key bytes are forbidden in R98 core. Artifact retrieval, signature math, signature validity verification, backend authentication, key-possession proof, backend identity proof, durability, current-state inference and authority promotion are forbidden.

## Precise claim

`ONE_EXACT_EXTERNALLY_RETAINED_CRYPTOGRAPHIC_ARTIFACT_IDENTITY_RECORD_BOUND_COMMIT_SIGNATURE_READBACK_SIGNATURE_PUBLIC_KEY_AND_SIGNATURE_TARGET_DIGEST_IDENTITIES_TO_THE_EXACT_R97_BACKEND_EVIDENCE_CHAIN`

Digest identity binding does not prove that either signature is valid, that the public key is possessed by the backend, that the backend is authentic, or that any artifact is current or durable.

## Authority ceiling

R98 may set only bounded evidence flags such as `cryptographic_artifact_identity_record_bound=true` and per-artifact digest-identity binding flags. Inherited `backend_commit_authenticity_verified=false`, `readback_authenticity_verified=false`, `backend_key_possession_proven=false`, `backend_identity_verified=false`, `durable_commit_proven=false`, `global_current_state_verified=false`, `execution_authority=NONE`, `can_trade=false`, `capital_permission=DENY`, and `confers_authority=false` remain unchanged.
