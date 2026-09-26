# R100 full-lifecycle time-fold boundaries

Status: stacked research candidate. No runtime deployment.

Base: R99 time-fold/block-bootstrap head
`11e78012786b5d18816f4c7cde17ef734d18c53c`.

R99 corrected equal-trade-count folds into equal market-time/bar windows.
This audit found one remaining leak: a trade was assigned to a fold by entry
time only, so its exit could occur in the next fold and still influence the
earlier fold's stability/expectancy.

R100 requires both entry and exit to remain inside the same half-open
time window. Trades whose exit crosses the fold end are excluded and counted
as `excluded_cross_boundary`. Invalid exit-before-entry chronology fails.

The last fold has no later in-window boundary because the evaluation window
itself already defines the terminal data boundary.

This improves fold accounting only. It does not turn modeled fills into observed
fills, remove configuration-selection bias, or authorize trading.

research_only=true; can_trade=false; capital_permission=DENY.
