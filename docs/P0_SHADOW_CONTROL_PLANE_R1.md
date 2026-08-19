# P0 Shadow Control Plane R1

Status: DRAFT CANDIDATE / OFFLINE ONLY / NO EFFECT

## Purpose

P0 previously proved portfolio-wide accounting and a typed trading transaction, but the authority/continuity plane was still mostly registry-level. This slice makes that plane an explicit, hash-bound part of the same decision transaction without writing to any control or runtime surface.

The control receipt composes these responsibilities:

```text
Control Center / State Authority
        ↓
HANRI freshness + contradiction
        ↓
ANTI_AMNESIA exact context binding
        ↓
ContinuityOS / Return / Archive dry-run boundary
        ↓
Executor Network hard deny
        ↓
bitevo.shadow_control_plane_receipt.v1
        ↓
bitevo.unified_shadow_transaction.v2
```

## Fresh source bindings used by this P0 fixture

The control-plane fixture is pinned to the fresh GitHub reads performed before this write wave:

- Control Center PR #30 head: `9c3f3642211501867b8f089decb3b9b6166de350`;
- ContinuityOS SCT P0 PR #91 head: `a0a244d40f0a2aa500df45b1f846f0d863a77749`.

Both are required to remain `OPEN / DRAFT / UNMERGED` for this P0 fixture. The receipt rejects a merged or non-draft source because that would silently change the authority meaning of the evidence used by the offline composition proof.

## Freshness result

The current Control Center review lane records an exact external provider capture at:

`2026-08-12T04:59:00+07:00`

with lease expiry:

`2026-08-12T10:59:00+07:00`.

The P0 control fixture evaluates that evidence at:

`2026-08-19T23:50:00+07:00`.

Therefore the provider-backed control truth is classified:

`STALE`

This does not mutate Control Center. It means the composed P0 transaction must fail closed at the control layer:

```text
control_gate=HOLD
control_plane_action=WAIT
hanri.attention_required=true
```

The system can still produce and inspect the offline transaction. It cannot promote stale external control evidence into current truth or permission.

## Control invariants

- Robert remains `HUMAN_SOVEREIGN`.
- A shadow projection is never equivalent to Control Center current truth.
- Stale authority evidence forces `HOLD`.
- Any explicit control conflict forces `HOLD` even if freshness is otherwise valid.
- `ANTI_AMNESIA` binds the exact TradeCase hash, DecisionPacket hash and context hash.
- Control Center projection apply is always `false` in P0.
- Command Queue, Decision Ledger and Human Gate are not mutated.
- Continuity event append, checkpoint write, replay write, Return write and Archive write are all `false`.
- Runtime activation is `false`.
- Executor Network remains disabled.
- All effect counters remain exactly zero.
- `can_trade=false` and `capital_permission=DENY` remain unchanged.

## Why this matters

The P0 system now demonstrates a stronger separation:

```text
model output
!= prediction
!= evidence
!= current truth
!= authority
!= permission
!= effect
```

A capable model can participate in a decision transaction while stale authority, unresolved conflict or a failed continuity binding still prevents the transaction from crossing the control boundary.

## No-effect ceiling

```text
merge=false
deploy=false
runtime_registration=false
current_truth_apply=false
continuity_write=false
archive_write=false
return_write=false
external_model_call=false
exchange_call=false
signal=false
order=false
credential_mutation=false
executor_enabled=false
can_trade=false
capital_permission=DENY
execution_authority=NONE
```
