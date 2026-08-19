# P0 RELEASE QUALIFICATION R1 — Global Invariant Manifest

Status: DRAFT CANDIDATE / SHADOW ONLY / PASS WITH CONDITIONS

This qualification closes the current P0 architecture for candidate review. It does not make the system production-qualified and does not authorize merge, deploy, runtime activation, current-truth promotion, Human Gate writes, trading, or capital effects.

## Frozen cross-repo snapshot

The immutable manifest is `evidence/p0_release_candidate_manifest_r1.json`.

It binds exact open/draft/unmerged PR heads for Control Center authority/P0/HANRI, TradingOS, SCT, ContinuityOS history, TRIAXIS, VisionAssist and Return Broker. The manifest is evaluated only relative to its independently retained SHA-256.

The TradingOS input parent intentionally remains the pre-qualification head recorded in the manifest. The later qualification commit is evidence tooling over that frozen input; it does not rewrite the qualified snapshot.

## R1 → R9 compatibility matrix

The manifest binds the schema families for:
- R1 frozen TradeCase / DecisionPacket / TRIAXIS / SCT;
- R2 temporal evidence and trusted replay;
- R3 append-only history and Return dedup;
- R4 domain subject binding and Human Reveal;
- R5 custody-bound reveal;
- R6.1 hardened asymmetric custody;
- R7 CAS single-use protocol;
- R8 writer fencing / crash recovery;
- R8.1 paired receipt identity + authority-root anchor;
- R9 dual-state atomicity + lease epoch lineage.

`tools/p0_release_qualification.py` fails closed on schema drift, missing conditions, head substitution, authority/effect widening, surface merge/readiness, and regression of already-green continuity surfaces.

## Global invariants

The candidate requires:
- HOLD cannot widen to PASS;
- HOLD implies WAIT;
- all effect paths remain false;
- no current-truth/Human Gate/lease/receipt/backend write;
- no runtime/executor/signal/order/capital effect;
- no merge/deploy/runtime activation permission;
- `execution_authority=NONE`;
- `can_trade=false`;
- `capital_permission=DENY`.

## Known conditions retained

The candidate remains conditional because:
- Control Center provider evidence is stale;
- ArchiveOS is `BLOCKED_REVERIFY / STALE`;
- multiple exact-head CI jobs are blocked before executable steps;
- no live writer backend or durable commit is proven;
- no crash-safe persistence proof exists;
- no runtime deployment proof exists;
- no merge authorization exists.

Therefore the deterministic release receipt is:

`P0_RELEASE_CANDIDATE_QUALIFIED_WITH_CONDITIONS`

with `decision=HOLD`, `action=WAIT`, `production_qualified=false`, `release_ready=false`, `merge_ready=false`, `deploy_ready=false`, `runtime_ready=false`.

## Evidence ceiling

Architecture closed for candidate review != production release qualified.

Immutable manifest SHA-256 is the retained input reference. Rehashing a modified snapshot does not replace that expected digest.

Fixed ceiling: `merge=false`, `deploy=false`, `runtime_activation=false`, `current_truth_apply=false`, `human_gate_write=false`, `lease_registry_write=false`, `commit_receipt_registry_write=false`, `backend_write=false`, `executor_dispatch=false`, `signal=false`, `order=false`, `can_trade=false`, `capital_permission=DENY`.
