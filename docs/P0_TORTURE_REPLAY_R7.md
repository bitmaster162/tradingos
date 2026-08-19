# P0 TORTURE / REPLAY R7 — Human Gate Atomic Consume / TOCTOU

Status: DRAFT CANDIDATE / SHADOW ONLY / NO EFFECT

R7 binds the R6.1 asymmetric reveal closure to the Control Center atomic Human Gate consume protocol.

New TradingOS schema:

`bitevo.shadow_human_gate_consume_closure.v1`

Required chain:

```text
R6.1 asymmetric reveal closure v2
  -> exact independently expected closure digest
  -> Control Center atomic consume verification
  -> exact independently expected atomic verification digest
  -> case/challenge/approval equality
  -> exact CAS generation transition
  -> R7 consume closure
  -> HOLD / WAIT
```

TradingOS requires:

- R6.1 `INDEPENDENT_ASSERTION_AND_APPROVAL_DIGESTS_BOUND` trust upgrade;
- exact R6.1 closure self-hash and external expected digest;
- exact Control Center atomic verification self-hash and external expected digest;
- same case id/SHA;
- same challenge id;
- same asymmetric approval verification digest;
- `COMPARE_AND_SWAP_PRECONDITION`;
- generation increment by exactly one;
- `PROTOCOL_VERIFIED_NO_DURABLE_COMMIT`;
- `CANDIDATE_ONLY_NOT_DURABLY_ENFORCED`;
- no Human Gate/credential/nonce/current-truth/runtime/trading/capital writes;
- no execution authority.

Adversarial coverage includes wrong retained atomic digest, cross-case atomic receipt, durable-commit overclaim after rehash and generation skip.

## Evidence ceiling

R7 proves a bounded CAS transition candidate and semantic binding to the authenticated R6.1 reveal. It does not prove a durable Human Gate writer committed the transition.

The exact conclusion remains:

`authenticated reveal + valid CAS candidate != durable single-use != current truth != execution permission`.

A future effect-enabled phase would require a real single-writer/CAS backend, lease/fencing semantics, durable commit receipt, crash recovery and independent read-after-write verification. None of those writes are authorized in P0.

Fixed ceiling:

`merge=false`, `deploy=false`, `runtime=false`, `human_gate_write=false`, `credential_registry_write=false`, `nonce_registry_write=false`, `current_truth_apply=false`, `executor=false`, `signal=false`, `order=false`, `can_trade=false`, `capital_permission=DENY`.
