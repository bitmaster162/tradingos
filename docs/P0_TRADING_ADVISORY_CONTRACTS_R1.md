# P0 Trading Advisory Contracts R1

Status: DRAFT CANDIDATE / OFFLINE ONLY / NARROW-ONLY / NO EFFECT

## Goal

Move five trading-advisory systems from simple universe accounting to explicit typed evidence contracts without inventing current runtime proof and without granting a trading vote.

Typed nodes:

1. `portfolio:edge-research-lab`
2. `portfolio:arb-radar`
3. `portfolio:grid-os`
4. `portfolio:delist-drs`
5. `portfolio:sovereign-api-core-bot`

## Universal rule

```text
registered
!= typed
!= admitted
!= source verified
!= runtime verified
!= trading vote
!= execution authority
```

A typed contract only defines what proof would be required before that subsystem may narrow a shadow decision. It cannot widen `HOLD`, create `PASS`, emit a signal/order, or authorize capital.

## Edge Research contract

Role: preregistered hypothesis discovery and falsification.

Admission requires:

- source identity verified;
- preregistered hypothesis receipt verified;
- strict return verified;
- independent replay verified.

`KEEP` is evidence only and cannot widen the gate. `KILL` or `INSUFFICIENT_DATA`, once fully admitted, may narrow to `HOLD`.

Current P0 posture: `RETURN_RESOLUTION_REQUIRED`; no current influence is admitted.

## Arb Radar contract

Role: read-only arbitrage/funding/carry evidence.

Admission requires:

- source identity verified;
- measurement semantics verified;
- cost model verified;
- entry/exit semantics verified;
- bounded paper comparison verified;
- freshness verified.

Current P0 posture: `MEASUREMENT_REPAIR_REQUIRED`; no current influence is admitted.

## Grid OS contract

Role: paper-only grid policy and evidence.

Admission requires:

- source identity verified;
- paper-only boundary verified;
- policy schema verified;
- stop/inventory policy verified;
- PnL evidence ledger verified;
- replay verified.

Current P0 posture: `CURRENT_IMPLEMENTATION_UNVERIFIED`; no current influence is admitted.

## Delist DRS contract

Role: explainable continuity-risk monitoring, not a trade-signal service.

Admission requires:

- source identity verified;
- endpoint verified;
- watchlist freshness verified;
- reason codes verified;
- timestamp/provenance verified;
- event taxonomy verified.

Current P0 posture: `EXISTING_SURFACE_FRESHNESS_UNVERIFIED`; no current influence is admitted.

## Sovereign API / Core Bot contract

Role: read-only API façade for status, provenance, snapshots/decision packets and exports.

Admission requires:

- source identity verified;
- `/status` / health evidence verified;
- auth boundary verified;
- null/stale/degraded semantics verified;
- integration receipt verified;
- runtime lineage verified.

Current P0 posture: `CURRENT_STATE_RECAPTURE_REQUIRED`; no current influence is admitted.

## Final P0 closure

```text
closure v6
  -> five typed advisory receipts
  -> shadow_trading_advisory_ledger.v1
  -> unified_shadow_closure.v7
```

Each receipt is hash-bound to the same transaction and closure. Missing any required proof makes the node `NOT_ADMITTED` rather than silently treating stale or historical evidence as current.

The final advisory layer is one-way:

```text
PASS_SHADOW -> HOLD    allowed only by a fully admitted risk/falsification receipt
HOLD -> HOLD           always preserved
HOLD -> PASS_SHADOW    forbidden
```

## Fixed safety ceiling

```text
merge=false
deploy=false
runtime_activation=false
external_runtime_invoked=false
trading_vote=false
signal=false
order=false
capital_effect=false
current_truth_apply=false
execution_authority=NONE
can_trade=false
capital_permission=DENY
```
