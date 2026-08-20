# P0 Human Interface Plane R1

Status: DRAFT CANDIDATE / PRESENTATION ONLY / NO EFFECT

## Scope

The human-facing navigation and operator surfaces are now bound as one presentation plane:

```text
HANRI / Control Center Unified Dashboard
Work Cockpit / Operator CRM
BitEvo Universe Hub
        ↓
bitevo.shadow_human_interface_receipt.v1
        ↓
bitevo.shadow_human_interface_ledger.v1
        ↓
bitevo.unified_shadow_closure.v10
```

## Core invariant

The Hub is a federated presentation environment, not a source of truth. The Work Cockpit is a decision/draft surface, not an external-message sender. The Unified Dashboard renders bounded snapshots, not operational reality invented from stale data.

All three interfaces are fixed to:

```text
presentation_only=true
source_of_truth=false
decision_vote=false
gate_effect=NONE
may_widen_gate=false
may_grant_approval=false
may_send_external_message=false
may_execute_action=false
execution_authority=NONE
```

## Unified Dashboard

Role: render Control Center/HANRI snapshots with evidence and freshness.

Proof debt:
- source identity;
- snapshot contract;
- freshness rendering;
- UNKNOWN/DEGRADED behavior;
- current bytes.

A stale or unavailable adapter must render `UNKNOWN` or `DEGRADED`, never `OPERATIONAL` by fallback.

## Work Cockpit / Operator CRM

Role: operator queue, briefs, drafts, evidence and follow-up tracking.

Proof debt:
- source identity;
- decision-queue contract;
- evidence linkage;
- draft-only external-message boundary;
- human review gate.

A draft is not a send. External communication requires explicit human review outside this P0 plane.

## Universe Hub

Role: federated navigation/observation surface over grounded systems and their real cockpits.

Proof debt:
- source identity;
- machine-readable system manifest;
- route resolver;
- snapshot provenance;
- no-fake-metrics validation;
- read-only action boundary.

The architecture remains:

```text
/universe
→ grounded system card
→ real system cockpit
→ evidence/freshness/status
→ proposal/draft surface only
```

The Hub may navigate, read, analyze and draft. It may not directly call production effects.

## Current P0 result

The incoming v9 gate/action is preserved exactly. For the frozen fixture:

```text
HOLD / WAIT
→ interface plane
→ HOLD / WAIT
```

No UI rendering, navigation event or draft can alter authority.

## Semantic ladder

```text
snapshot
!= rendered status
!= source truth
navigation
!= approval
draft
!= send
button
!= effect authority
```

## Fixed ceiling

```text
current_truth_write=false
approval=false
external_message=false
runtime_action=false
signal=false
order=false
capital_effect=false
execution_authority=NONE
can_trade=false
capital_permission=DENY
merge=false
deploy=false
```
