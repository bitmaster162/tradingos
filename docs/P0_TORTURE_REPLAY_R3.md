# P0 Torture / Replay R3 — History, Forks, Duplicates

Status: `DRAFT CANDIDATE / OFFLINE ADVERSARIAL HISTORY VERIFICATION / NO EFFECT`

## Scope

R1 attacked case/Twin integrity. R2 attacked temporal admissibility and replay-root substitution. R3 attacks the history of the federation itself.

R3 is not `closure v14`. Structural System Universe closure remains `v13`; R3 is an additional verification membrane over replay/admission/history/return lineage.

## Cross-plane path

```text
R2 trusted replay input
        ↓
ContinuityOS replay registry snapshot
        ↓
shadow replay admission candidate
        ↓
per-case append-only event chain
        ↓
CASE_QUALIFIED
→ TWIN_COMMITTED
→ DECISION_PACKET
→ HUMAN_REVEAL
→ OUTCOME_RECEIPT
→ RETURN_INTAKE
        ↓
Control Return Broker physical + dedup candidate
        ↓
bitevo.shadow_history_replay_verification.v1
```

## ContinuityOS R3 contracts

ContinuityOS PR #94 owns the generic history rules:

- `continuityos.shadow_replay_registry_snapshot.v1`;
- `continuityos.shadow_replay_admission_candidate.v1`;
- `continuityos.shadow_case_ledger_snapshot.v1`;
- `continuityos.shadow_case_event.v1`;
- `continuityos.shadow_case_append_candidate.v1`.

The global replay registry rejects:

```text
same case_binding again              -> duplicate replay
same case bytes under new case_id    -> alias replay
same case_id with new binding        -> case-history fork
same ledger_id reused                -> ledger collision
```

The per-case ledger rejects:

```text
second HUMAN_REVEAL
second RETURN_INTAKE
reused idempotency key
sequence regression
lifecycle reordering
forged previous-event link
rollback to an older externally-unexpected ledger snapshot
```

The result of each operation is a candidate only. No canonical registry/ledger write occurs in P0.

## Return Broker R3 contract

Control Return Broker PR #2 adds a read-only return-index/dedup boundary:

- `control_return_broker.shadow_return_index_snapshot.v1`;
- `control_return_broker.shadow_return_dedup_candidate.v1`.

It rejects:

```text
same intake receipt again
same ZIP under another work order
same transaction with another ZIP
same work order with another transaction / ZIP
```

Physical verification still does not imply semantic acceptance.

## TradingOS cross-plane verifier

`tools/unified_shadow_history_replay.py` independently checks the portable receipts before considering the history verified for an offline replay.

It verifies:

- admission candidate self hash and exact TradeCase binding;
- external initial ledger digest and head;
- every append candidate self hash;
- previous-ledger and previous-head linkage;
- event self hashes, sequence, idempotency and lifecycle order;
- exact prefix preservation between successive ledger candidates;
- exactly one reveal and one return intake;
- externally expected final ledger digest and head;
- Return Broker dedup candidate binds the same RETURN_INTAKE receipt;
- no registry, ledger, Return index, current truth, runtime, executor, signal, order or capital effect.

Successful result:

`bitevo.shadow_history_replay_verification.v1`

with:

```text
status=HISTORY_CHAIN_VERIFIED_SHADOW_ONLY
history_write_performed=false
semantic_acceptance=NOT_PERFORMED
execution_authority=NONE
can_trade=false
capital_permission=DENY
```

## R3 adversarial matrix

R3 now attacks:

```text
T13 old case rewrapped under new case id
T14 same case id forked to new case binding
T15 stale external replay-registry snapshot
T16 second human reveal
T17 duplicate Return intake
T18 event lifecycle reorder
T19 forged previous-event link with local rehash
T20 rollback to older but internally valid ledger snapshot
T21 outcome before reveal
T22 return before outcome
T23 same physical ZIP replayed under new work order
T24 same transaction returned with different ZIP
T25 same work order returned with conflicting bytes
T26 cross-plane ledger prefix rewrite
T27 Return dedup receipt does not bind RETURN_INTAKE event
T28 rehashed Return semantic-acceptance overclaim
```

Expected result is fail-closed rejection or zero influence.

## Trust ceiling

An append-only hash chain is meaningful only relative to an independently retained expected registry snapshot / ledger head / authority root. R3 detects rollback and forks relative to those expected values; it does not claim that a self-issued hash is authentic.

## Fixed safety ceiling

```text
merge=false
deploy=false
runtime_activation=false
runtime_registration=false
current_truth_apply=false
registry_write=false
ledger_write=false
return_index_write=false
executor_dispatch=false
signal=false
order=false
capital_effect=false
execution_authority=NONE
can_trade=false
capital_permission=DENY
```
