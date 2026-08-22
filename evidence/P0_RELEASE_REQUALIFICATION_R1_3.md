# TradingOS / BitEvo P0 — R1.3 fresh read-only requalification

**Status:** `QUALIFIED_FOR_NEW_INDEPENDENT_FINAL_REVIEW_WITH_CONDITIONS`

**Decision:** `HOLD`  
**Action:** `WAIT`

This is a fresh provider-state recalculation. Historical R1.1 manifest/review remain immutable evidence and are not rewritten.

## What materially changed

- Candidate partition moved from **2 green / 7 blocked** to **4 green / 5 blocked**.
- `CONTROL_CENTER_PROVIDER_CAPTURE_STALE` is resolved by current provider reseal PR #83 / default head `141ac07193f7bb23a59a23de4f3eb72b2027455f`.
- `ARCHIVEOS_BLOCKED_REVERIFY_STALE` is resolved; `archiveos/master` is exactly `b92ea79ce591dff307d3062fb099be07b660b1ea`.
- VisionAssist integrity defect is repaired on PR #3 candidate `e121f72b1606bf46103c3b79f84cc54d123c7474`, exact-head CI green and independent semantic review PASS.
- OKX R90 cancel acknowledgement defect is repaired on PR #114 candidate `eeb26fbccd5665bd1ad13cfcbaf25713f6fdcee9`, exact-head CI green and independent semantic review PASS.
- The historical TRIAXIS R1.1 final review predates all of those changes and must not be treated as a current final review.

## Still blocking release qualification

Five original federation surfaces still lack executable CI proof:
1. Control Center P0 wrapper
2. HANRI P0
3. TradingOS R1.1 wrapper
4. TRIAXIS P0
5. Return Broker P0

Structural proofs still absent:
- live writer backend
- durable commit
- crash-safe persistence
- P0 runtime deployment

Additional current-tree caveats:
- SCT candidate is green, but the integrated merge tree contains 91 additional base commits.
- ContinuityOS history green merge is preserved, while current master later advanced 32 Sovereign Twin/runtime commits.
- VisionAssist and OKX repairs are green candidates, not integrated base/master state.

## R1.3 snapshot

SHA-256: `82359aa5d3365a7c98c373699eba6cec093f8ca8de605c9dbce885d211381dd8`

Global safety remains:
- `can_trade=false`
- `capital_permission=DENY`
- `execution_authority=NONE`
- no merge/deploy/runtime/current-truth effect
