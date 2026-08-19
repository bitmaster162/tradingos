# P0 TORTURE / REPLAY R8 — Writer Lease / Fencing / Crash Recovery

Status: DRAFT CANDIDATE / SHADOW ONLY / NO EFFECT

R8 consumes the exact R7 Human Gate consume closure plus the exact Control Center writer-fencing/crash-recovery verification.

## New closure

`bitevo.shadow_writer_fencing_recovery_closure.v1`

Required chain:

```text
R6.1 asymmetric custody
  -> R7 CAS/single-use protocol
  -> Control Center writer lease snapshot
  -> monotonic fencing token
  -> fenced commit attempt
  -> durable-receipt candidate shape
  -> crash recovery readback + receipt-index dedup
  -> R8 writer fencing/recovery closure
```

## Independent TradingOS checks

TradingOS requires independently expected digests for both:

- `bitevo.shadow_human_gate_consume_closure.v1`;
- `control_center.shadow_human_gate_crash_recovery_verification.v1`.

It then checks exact case id/SHA, challenge id, R6.1 approval digest and R7 atomic-consume digest across both planes.

The Control Center recovery receipt must preserve:

- `MONOTONIC_FENCING_TOKEN_PLUS_LEASE_DIGEST`;
- `READBACK_PLUS_RECEIPT_INDEX_DEDUP`;
- same-token split-brain rejection;
- no blind retry;
- no live-writer claim;
- no durable-commit claim;
- no Human Gate/current-truth/runtime/trading/capital effect.

Accepted recovery statuses are deliberately bounded to shadow protocol outcomes:

- `STALE_WRITER_FENCED_REACQUIRE_REQUIRED`;
- `NO_WRITE_OBSERVED_RETRY_REQUIRES_FRESH_CAS`;
- `WRITE_OBSERVED_RECEIPT_ABSENT_HOLD`;
- `RECEIPT_INDEXED_DEDUP_NO_RETRY`.

Even the only retry-eligible state means **recompare before retry**; blind retry remains forbidden.

## Adversarial coverage

R8 attacks:

- wrong independently retained recovery digest;
- cross-case recovery receipt after local rehash;
- durable-commit overclaim after rehash;
- missing split-brain guard after rehash;
- upstream R7 HOLD -> PASS widening;
- stale writer token versus newer current fencing token;
- same fencing token assigned to a different lease/writer;
- lease expiry before attempted commit;
- write observed without receipt;
- duplicate receipt/idempotency identity after ACK crash;
- receipt-candidate issuance/durability overclaim.

## Evidence ceiling

R8 proves only the **protocol semantics** of future writer fencing and crash recovery. It does not prove:

- a live lease store;
- a real single-writer backend;
- a durable commit;
- an issued durable receipt;
- live read-after-write;
- fsync/transaction durability;
- crash-safe production recovery;
- current truth;
- permission to execute.

The resulting closure is always:

```text
status = WRITER_FENCING_RECOVERY_BOUND_SHADOW_ONLY
decision = HOLD
action = WAIT
live_writer_backend_proven = false
durable_commit_proven = false
execution_authority = NONE
```

Control Center may render it only as `NON_AUTHORITY_WRITER_RECOVERY_PROJECTION`.

Fixed ceiling:

`merge=false`, `deploy=false`, `runtime=false`, `human_gate_write=false`, `credential_registry_write=false`, `nonce_registry_write=false`, `lease_registry_write=false`, `commit_receipt_registry_write=false`, `backend_write=false`, `current_truth_apply=false`, `executor=false`, `signal=false`, `order=false`, `can_trade=false`, `capital_permission=DENY`.
