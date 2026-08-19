# P0 Trading Advisory — 9/9 Typed Admission Boundaries

Status: DRAFT CANDIDATE / SHADOW ONLY / NO EFFECT

## Scope

Every node in the `TRADING_ADVISORY_ACCOUNTED` class now has an explicit typed contract. Registry presence alone still creates no influence.

```text
Edge Research
Arb Radar
Grid OS
Delist DRS
Sovereign API / Core Bot
Claude Bitunix Evidence Lane
BTCUSDT Binance Futures Bot
Confluence Trading Bot
MAX+BitEvo Trading Tools
        ↓
bitevo.shadow_trading_advisory_receipt.v2
        ↓
bitevo.shadow_trading_advisory_ledger.v2
        ↓
bitevo.unified_shadow_closure.v8
```

## Universal admission rule

Project-specific proof is not sufficient. Any advisory evidence that may influence a frozen TradeCase must also prove:

```text
case_relevance_verified=true
pre_freeze_evidence_verified=true
```

Therefore:

```text
strong evidence about another venue != relevant evidence
historical proof != current-case evidence
post-freeze evidence != admissible evidence
registry membership != influence
```

Missing either universal field means `gate_effect=NONE`.

## Existing five contracts

### Edge Research
Role: preregistered hypothesis discovery/falsification.

Project proof debt:
- source identity;
- preregistration receipt;
- strict return;
- independent replay.

Only a fully admitted `RISK_FLAG`, `KILL`, or `INSUFFICIENT_DATA` may narrow a case to HOLD.

### Arb Radar
Role: read-only arbitrage/funding/carry evidence.

Project proof debt:
- source identity;
- measurement semantics;
- cost model;
- entry/exit semantics;
- bounded paper comparison;
- freshness.

### Grid OS
Role: paper-only grid policy/evidence.

Project proof debt:
- source identity;
- paper-only boundary;
- policy schema;
- stop/inventory policy;
- PnL evidence ledger;
- replay.

### Delist DRS
Role: explainable continuity-risk monitoring.

Project proof debt:
- source identity;
- endpoint;
- watchlist freshness;
- reason codes;
- timestamp provenance;
- event taxonomy.

### Sovereign API / Core Bot
Role: read-only status/provenance/export API facade candidate.

Project proof debt:
- source identity;
- health/status;
- auth boundary;
- null/stale/degraded semantics;
- integration receipt;
- runtime lineage.

## Newly typed four contracts

### Claude Bitunix Evidence Lane
Bounded internal posture:
- public read-only venue evidence lane;
- operational eligibility is not proven;
- its intended empirical gate is a bounded public observation, not execution approval.

Project proof debt:
- explicit user dispatch for the observation;
- frozen observation protocol;
- bounded observation completed;
- official/public endpoint semantics verified;
- freshness/completeness verified;
- sealed observation receipt.

Allowed research outcome vocabulary:
`PASS_SHADOW / HOLD / FAIL / UNRESOLVED`.

Even `PASS_SHADOW` is public-data eligibility only and is not venue execution approval.

### BTCUSDT Binance Futures Bot
Bounded internal posture:
- historical candidate;
- historical inventory truth class;
- not current TradingOS;
- current repo/runtime capture required before integration claims.

Project proof debt:
- source identity;
- versioned market-data semantics;
- fill realism;
- separation of strategy, execution and risk;
- forward-paper protocol;
- independent replay.

Allowed disposition vocabulary:
`PRESERVE_RESEARCH_COMPONENTS / RECAPTURE_AND_TEST / ARCHIVE / KILL / UNRESOLVED`.

A historical bot is never promoted into current strategy authority by this contract.

### Confluence Trading Bot
Bounded internal posture:
- historical confluence/regime hypothesis candidate;
- current code reality unadjudicated;
- automatic merge into BitEvo is forbidden.

Project proof debt:
- source identity;
- explicit hypothesis layers;
- correlated-feature/double-count controls;
- preregistered ablation;
- explicit invalidation;
- transaction-cost/fill/timestamp controls;
- true-forward evidence.

Allowed disposition vocabulary:
`KEEP_AS_RESEARCH_HYPOTHESIS / MERGE_INTO_EDGE_LAB / REVISE / ARCHIVE / KILL / UNRESOLVED`.

The contract treats confluence as a falsifiable research hypothesis, not indicator consensus.

### MAX+BitEvo Trading Tools
Bounded internal posture:
- legacy toolkit archaeology;
- historical active/production claims are not current runtime proof;
- source recapture precedes consolidation decisions.

Project proof debt:
- source recapture manifest;
- component inventory;
- dependency/security audit;
- overlap map against current systems;
- reproducibility evidence;
- component-level adjudication.

Allowed disposition vocabulary:
`CONSOLIDATE / PRESERVE_COMPONENTS / HOLD / ARCHIVE / KILL / UNRESOLVED`.

This toolkit contract can inform architecture disposition only. It is intentionally unable to narrow or widen a frozen market decision merely because old components exist.

## One-way influence

For market-relevant advisory systems, a fully proof-admitted and case-relevant/pre-freeze `RISK_FLAG` may narrow:

```text
PASS_SHADOW -> HOLD
```

No advisory contract may perform:

```text
HOLD -> PASS_SHADOW
```

`NO_OBJECTION` is evidence only. It is not a trade authorization.

## Current P0 result

The current bounded evidence defaults do not satisfy the full proof sets and do not establish case relevance/pre-freeze admissibility. Therefore all nine current default receipts remain non-influential.

This is expected fail-closed behavior.

## Fixed ceiling

```text
trading_vote=false
may_widen_gate=false
external_runtime_invoked=false
runtime_activation=false
signal=false
order=false
capital_effect=false
current_truth_apply=false
execution_authority=NONE
can_trade=false
capital_permission=DENY
merge=false
deploy=false
```
