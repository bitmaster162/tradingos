# P0 Cognition Proposal Plane R1

Status: DRAFT CANDIDATE / PROPOSAL ONLY / NO CASE INFLUENCE / NO EFFECT

## Purpose

The 63-node System Universe previously accounted for cognition-side systems but did not give each one a typed boundary. P0 now binds all eleven `COGNITION_SIDE_ACCOUNTED` nodes without letting any of them become a second control plane, memory authority or voting council.

```text
BitEvo Runtime
Reflex Layer
OpenClaw
Arbiter Content Engine
DTaaP
Sovereign Agent Core
GPT-S:CORE SDK
LifeOS
MIND
PFI / Brain / Fabric
Human Coevolution Layer
        ↓
bitevo.shadow_cognition_proposal_receipt.v1
        ↓
bitevo.shadow_cognition_proposal_ledger.v1
        ↓
bitevo.unified_shadow_closure.v9
```

## Plane invariant

Every cognition-side node is:

```text
typed_contract_bound=true
proposal_only=true
case_influence_enabled=false
decision_vote=false
gate_effect=NONE
may_widen_gate=false
current_truth_authority=NONE
memory_authority=NONE
execution_authority=NONE
external_runtime_invoked=false
```

Even a future `proof_complete=true` receipt does not activate case influence in P0. Promotion from proposal-only to any decision influence requires a separate explicit contract and owner gate.

## Roles and proof debts

### BitEvo Runtime / Cognitive Orchestrator
Role: thin cognitive-orchestration adapter candidate, not a universal workflow/control framework.

Proof debt:
- source identity;
- live API receipt;
- deployment receipt;
- integration receipt;
- budget/retry/timeout semantics;
- tool security boundary.

Forbidden ownership: current truth, durable memory and effect authority.

### Reflex Layer
Role: monitoring and bounded-response proposal profile.

Proof debt:
- source identity;
- current live runner;
- observation provenance;
- deterministic rule profile;
- receipt lineage;
- kill boundary.

Historical scheduler evidence is not current runtime proof. P0 performs no automated response.

### OpenClaw
Role: agent-harness/tool-adapter candidate.

Proof debt:
- source/vendor identity;
- installed version;
- runtime config;
- sandbox/tool permissions;
- secret boundary;
- maintenance/failure receipt.

An installed or running harness is not governance, canonical memory or effect authority.

### Arbiter Content Engine
Role: multi-model synthesis/challenge proposal candidate.

Proof debt:
- source identity;
- best single-model baseline;
- blind benchmark;
- actual independence semantics;
- cost/latency accounting;
- source provenance.

It cannot implement fake independent-agent voting or become final-truth authority.

### DTaaP
Role: digital-twin product-wrapper candidate.

Proof debt:
- source identity;
- explicit boundary from SCT;
- current tests;
- deployment evidence;
- one external user;
- one measurable twin use case.

A product wrapper does not own SCT person identity, current truth or execution.

### Sovereign Agent Core
Role: agent-trust pattern library, `MERGE_CONCEPTS_ONLY`.

Current bounded byte audit already distinguishes the historical v5.9 snapshot from active CORE v6.3.3 and found no pre-execution enforcement or cryptographic action receipts in the historical snapshot. The project must not become a second authority root or separate control product.

### GPT-S:CORE SDK
Role: historical CORE SDK component evidence.

Proof debt:
- historical snapshot identity;
- active CORE manifest identity;
- explicit version boundary;
- licensing authority;
- component reuse map.

Historical v5.9 is not normative CORE v6.3.x authority.

### LifeOS
Role: identity and personal-memory policy candidate.

Proof debt:
- source identity;
- identity lifecycle contract;
- private/persistent scope policy;
- memory-admission boundary;
- consent/revocation.

LifeOS may define policy candidates but P0 grants no identity mutation, durable-memory write or current-truth authority.

### MIND
Role: hypothesis and cognitive-state candidate.

Proof debt:
- source identity;
- hypothesis-state schema;
- evidence linkage;
- commitment boundary;
- update/rollback semantics.

Hypothesis state is not commitment authority or accepted truth.

### PFI / Brain / Fabric
Role: evidence-linked frontier-intelligence family candidate with three separate responsibilities:

```text
PFI    = source registry / claims / scopes / freshness / rebuild contract
Brain  = extraction / ranking / retrieval proposals
Fabric = optional typed transport only when justified
```

Proof debt:
- source manifest;
- PFI/Brain/Fabric relation map;
- claim/provenance schema;
- scope/authz policy;
- ArchiveOS/ContinuityOS/LifeOS/MIND interfaces;
- one source→claim→session→rebuild proof.

Forbidden ownership includes exact source bytes, accepted truth, identity lifecycle, personal-memory policy, canonical event history, semantic acceptance and effects.

### Human Coevolution Layer
Role: bounded human-agent-environment update-proposal protocol.

Canonical P0 form:

```text
human intent/constraints
→ agent proposal
→ bounded action/simulation
→ environment outcome
→ external evaluator
→ typed update proposal
→ human approval
→ canary/rollback
```

P0 stops before human approval, canary, deployment and any effect. This is functional adaptation, not autonomous self-development.

## Current P0 result

Current bounded evidence does not prove the full implementation/source/runtime proof sets for these eleven roles. That is acceptable: P0 binds semantics and negative authority without pretending implementation reality.

The cognition plane preserves the incoming v8 decision exactly. For the current frozen fixture:

```text
incoming gate = HOLD
incoming action = WAIT
outgoing gate = HOLD
outgoing action = WAIT
```

## Semantic ladder

```text
model/harness output
!= cognitive proposal
!= evidence
!= accepted hypothesis
!= durable memory
!= current truth
!= human approval
!= permission
!= effect
```

## Fixed ceiling

```text
case_influence=false
decision_vote=false
gate_effect=NONE
model_call=false
tool_call=false
memory_write=false
current_truth_write=false
human_approval=false
canary=false
deploy=false
runtime_activation=false
signal=false
order=false
capital_effect=false
execution_authority=NONE
can_trade=false
capital_permission=DENY
```
