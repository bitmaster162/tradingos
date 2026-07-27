# TradingOS Active Relationship Contract

## Status

This document is a proposal-only contract for
`CODEX02-R3-RECOVER-OR-BUILD-TRADINGOS-CANONICAL-SOURCE`.

- Candidate status: `CANDIDATE_ONLY`
- Human approval: `PENDING`
- Self-application: `false`
- Trading permission: `false`

## Roles

`C:\Users\coins\TradingOS\Active` is a runtime projection. It is not a Git
source repository and it is not source authority.

The R3 repository is an isolated source-authority candidate. Building or
reviewing it does not register it, install it, deploy it, or connect it to
Active.

## Hard boundary

The candidate and every validator in this proposal must treat Active as
read-only. The following actions are prohibited:

1. copying candidate files into Active;
2. pulling Active content into the candidate as an authority decision;
3. automatic synchronization in either direction;
4. changing Active manifests, locks, runtime state, processes, services, or
   Scheduler tasks;
5. using source registration as implied deployment approval.

Any future source-to-Active change requires a separate human-authorized
integration work order, a reviewed file-level plan, pre/post integrity
snapshots, test replay, and an explicit rollback boundary.

## Failure rule

Path overlap, a writable Active policy, automatic sync, runtime wiring, or
deployment permission is a hard validation failure.
