# R99 time-partition folds and dependence-aware bootstrap

Status: isolated candidate, stacked on R98 strict OOS open-once. No runtime deployment.

Base: aa1e831cfb79af805760105d69651519276a9456

## Findings

The Phase-A "stable folds" were equal chunks of the ordered TRADE LIST. A regime with all
trades concentrated in one short market period could therefore be distributed across several
apparently stable folds. That is not time stability.

Session-volatility train qualification also used an IID bootstrap that independently sampled
single trades. Serial/regime dependence was therefore not retained by that resampling gate.

## Candidate

- Stability folds are equal bar/time windows over the research period.
- Empty calendar/time folds remain empty and can no longer be hidden by equal-trade-count slicing.
- Session bootstrap uses contiguous moving blocks of trade outcomes.
- Default block length is round(sqrt(N)), matching the existing Strategy Lab moving-block heuristic.
- Caller may override with --bootstrap-block-trades; 0 means the documented auto heuristic.
- Reports identify fold_partition and bootstrap_method/block size.

The sqrt(N) block length is a heuristic, not a universal optimal block-size theorem.
This candidate reduces one independence assumption; it does not prove independence, remove
validation-set multiplicity, model portfolio capital, or repair execution-causality issues.

research_only=true; can_trade=false; capital_permission=DENY.
