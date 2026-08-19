# P0 TORTURE / REPLAY R9 — Dual-State Atomicity + Lease Epoch Lineage

Status: DRAFT CANDIDATE / SHADOW ONLY / NO EFFECT

R9 is a verification membrane over R8.1. It does not enable a writer.

Control Center provides exact retained evidence for lease epoch lineage and a two-record atomicity candidate covering Human Gate state plus paired receipt index. Mixed readback states fail closed.

TradingOS adds:

`bitevo.shadow_dual_state_atomicity_closure.v1`

The closure independently consumes:

1. exact R8.1 writer-fencing recovery closure digest;
2. exact Control Center R9 atomicity-verification digest;
3. exact retained authority-root digest.

It cross-binds case id/SHA, challenge id, current writer lease, prior paired receipt index and authority root.

Required guards:

- `dual_state_atomicity_verified=true`
- `split_state_rejected=true`
- `lease_epoch_lineage_verified=true`
- `aba_guard_verified=true`
- `authority_root_retained=true`
- `status=DUAL_STATE_ATOMICITY_BOUND_SHADOW_ONLY`
- `decision=HOLD`
- `action=WAIT`
- `durable_commit_proven=false`
- `human_gate_write_performed=false`
- `execution_authority=NONE`

Adversarial coverage includes mixed Human Gate/receipt-index readback, skipped lease epoch, non-increasing fencing token, ABA lease-id reuse, wrong retained R9 digest, rehashed authority-root substitution, cross-case substitution, missing lineage guards, R8.1 HOLD-to-PASS widening and durable-write overclaims.

## Evidence ceiling

R9 verifies transaction shape, coherent pair readback and lease lineage at contract level only. It does not prove a live transaction store, fsync/consensus semantics, live read-after-write, an issued durable receipt, a live lease registry, current truth or execution permission.

Fixed ceiling: `merge=false`, `deploy=false`, `runtime_activation=false`, `runtime_registration=false`, `human_gate_write=false`, `credential_registry_write=false`, `nonce_registry_write=false`, `lease_registry_write=false`, `commit_receipt_registry_write=false`, `backend_write=false`, `current_truth_apply=false`, `registry_write=false`, `ledger_write=false`, `return_index_write=false`, `executor_dispatch=false`, `signal=false`, `order=false`, `capital_effect=false`, `execution_authority=NONE`, `can_trade=false`, `capital_permission=DENY`.
