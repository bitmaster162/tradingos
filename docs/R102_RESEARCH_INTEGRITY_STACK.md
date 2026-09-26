# R102 research integrity stack

Status: consolidated research candidate. No runtime deployment and no trading authority.

Baseline chain:
- R97 local paper baseline: `f4ccbe4228dc7aa59a0d36f41bab45b5f0a1dce6`
- R98 OOS open-once: `aa1e831cfb79af805760105d69651519276a9456`
- R99 time folds + moving-block bootstrap: `11e78012786b5d18816f4c7cde17ef734d18c53c`
- R100 gap/censor/hold: `4d99edaed96cf5cfe7783c2cd00f96bee452bb45`
- R101 full basket coverage: `8a02971250421a03623a919824379e187ccada94`
- R100 lifecycle-fold sibling merged into this stack.

Combined guarantees:
1. Grid search cannot inspect OOS before one validation winner is frozen.
2. OOS is opened once for that frozen id and cannot re-rank the grid.
3. Stability folds are equal market-time/bar windows rather than equal trade counts.
4. Session bootstrap uses moving contiguous trade blocks, not IID single trades.
5. Adverse stop gaps fill at observed bar open; incomplete hold horizon is censored.
6. max_hold_bars has exact inclusive-count semantics.
7. Relative-strength baskets cannot silently shrink when a configured constituent is unavailable.
8. Fold statistics require entry and exit inside the same half-open time fold.

Limits deliberately NOT solved:
- historical named symbols are not a verified point-in-time/survivorship-free universe;
- OHLC replay is not observed execution;
- funding, queue position and dynamic spread are not fully modeled here;
- trade-R drawdown is not portfolio equity without sizing/capital allocation;
- repeated human inspection of an already-seen historical OOS cannot make that period pristine again.

Therefore historical results remain research evidence. A prospectively precommitted quote-time cohort
is still the preferred independent proof path.

research_only=true
can_trade=false
capital_permission=DENY
