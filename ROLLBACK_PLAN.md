# TradingOS Canonical Source Rollback Plan

## R3 rollback

R3 performs no adoption. Its rollback is therefore non-destructive:

1. mark the candidate or return package `REJECTED` or `SUPERSEDED`;
2. retain immutable evidence if review traceability is required;
3. remove or quarantine only the isolated candidate under a separately
   authorized cleanup action;
4. leave `C:\Users\coins\TradingOS\Active` unchanged.

## Future registration rollback

If a later work order registers this candidate as source authority but does not
deploy it:

1. freeze writes to the source root;
2. revoke the external authority registry entry;
3. preserve the rejected commit/tree and decision receipt;
4. restore the previous registry state or return to `NO_REGISTERED_SOURCE`;
5. verify Active against the pre-registration snapshot.

No rollback step may use `git reset --hard`, rewrite divergent WO-009 history,
or treat Active as a source backup. Any runtime rollback requires its own
deployment-specific plan and authorization.
