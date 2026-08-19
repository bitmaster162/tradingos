# P0 TORTURE / REPLAY R8.1 — Paired Receipt Identity + Authority-Root Anchor

Status: DRAFT CANDIDATE / SHADOW ONLY / NO EFFECT

R8.1 is a verification hardening membrane over R8 v1. It does not add an authority plane and does not enable a writer.

Control Center provides:

- `control_center.shadow_human_gate_commit_receipt_index_snapshot.v2` with first-class `(commit_id, idempotency_key_sha256, receipt_reference_sha256)` entries;
- `control_center.shadow_human_gate_writer_authority_anchor.v1` binding an independently retained authority-root digest to the exact writer lease, legacy receipt index and paired v2 receipt index;
- `control_center.shadow_human_gate_crash_recovery_verification.v2`, which requires both pair identity and authority-root anchor consumption.

TradingOS adds:

`bitevo.shadow_writer_fencing_recovery_closure.v2`

The v2 closure independently consumes four retained references:

1. exact R8 v1 closure digest;
2. exact Control Center R8.1 recovery-verification digest;
3. exact Control Center authority-anchor digest;
4. exact authority-root digest.

It then cross-binds case id/SHA, challenge id, current writer-lease digest, legacy receipt-index digest, paired receipt-index digest, receipt-candidate digest and recovery outcome.

Required guards include:

- `paired_receipt_identity_verified=true`;
- `authority_root_anchor_consumed=true`;
- `cross_plane_anchor_verified=true`;
- `status=WRITER_FENCING_RECOVERY_HARDENED_SHADOW_ONLY`;
- `decision=HOLD`;
- `action=WAIT`;
- `durable_commit_proven=false`;
- `human_gate_write_performed=false`;
- `execution_authority=NONE`.

Adversarial coverage includes wrong retained anchor/root, rehashed cross-case recovery, missing paired-identity guard, R8 HOLD-to-PASS widening and durable-commit overclaim.

## Evidence ceiling

R8.1 closes the two explicit R8 v1 contract gaps: ambiguous parallel receipt identity and missing separately retained cross-plane authority-root anchor. It still does not prove a live lease/index store, a real atomic writer, an issued durable receipt, crash-safe persistence, live read-after-write, current truth or execution permission.

Fixed ceiling: `merge=false`, `deploy=false`, `runtime_activation=false`, `runtime_registration=false`, `human_gate_write=false`, `credential_registry_write=false`, `nonce_registry_write=false`, `lease_registry_write=false`, `commit_receipt_registry_write=false`, `backend_write=false`, `current_truth_apply=false`, `registry_write=false`, `ledger_write=false`, `return_index_write=false`, `executor_dispatch=false`, `signal=false`, `order=false`, `capital_effect=false`, `execution_authority=NONE`, `can_trade=false`, `capital_permission=DENY`.
