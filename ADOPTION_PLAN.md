# TradingOS Canonical Source Adoption Plan

## Current decision

This is a non-executable proposal. R3 stops before adoption.

- authority status: `CANDIDATE_ONLY`
- human approval: `PENDING`
- proposed future root:
  `C:\Users\coins\TradingOS\Source\tradingos-canonical`
- writable remote: `NONE`
- Active wiring: `NONE`

## Preconditions for a separate integrator work order

1. Robert records explicit source-registration approval.
2. The R3 ZIP, SHA256 sidecar, READY receipt, Git bundle, final commit, and
   final tree are independently verified.
3. A disposable clone reproduces the complete test suite.
4. The candidate validator returns `PASS` from a clean checkout.
5. The target root is absent or empty, non-nested, and unambiguous.
6. NTFS ownership and future repository owner are explicitly recorded.
7. Remote policy remains no writable remote unless separately approved.
8. Active remains a read-only runtime projection with no automatic sync.

## Future bounded sequence

Only a separately authorized integrator may:

1. clone the accepted bundle into the approved source root;
2. verify exact commit/tree and clean status;
3. replay tests and the authority validator;
4. record the new source registry outside the repository;
5. stop without touching Active unless a second deployment work order exists.

Source registration is not runtime installation and is not permission to
trade.
